import json
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import needs_posix

from blockhost import network, tunnel


# ------------------------------------------------------------ fake router ---

DESC = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><device>
 <deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType>
 <deviceList><device><deviceList><device>
  <serviceList><service>
   <serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
   <controlURL>/ctl/IPConn</controlURL>
  </service></serviceList>
 </device></deviceList></device></deviceList>
</device></root>"""


class FakeRouter:
    def __init__(self, external_ip="93.184.216.34", permanent_only=False):
        self.external_ip, self.permanent_only = external_ip, permanent_only
        self.mappings: dict[int, dict] = {}
        router = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                b = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                self._send(200, DESC)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                action = self.headers["SOAPAction"].strip('"').split("#")[1]

                def arg(name):
                    return body.split(f"<{name}>")[1].split(f"</{name}>")[0]
                if action == "GetExternalIPAddress":
                    self._send(200, f"<r><NewExternalIPAddress>{router.external_ip}</NewExternalIPAddress></r>")
                elif action == "AddPortMapping":
                    if router.permanent_only and arg("NewLeaseDuration") != "0":
                        self._send(500, "<r><errorCode>725</errorCode><errorDescription>OnlyPermanentLeasesSupported"
                                        "</errorDescription></r>")
                        return
                    router.mappings[int(arg("NewExternalPort"))] = {
                        "client": arg("NewInternalClient"), "proto": arg("NewProtocol"),
                        "lease": arg("NewLeaseDuration")}
                    self._send(200, "<r/>")
                elif action == "DeletePortMapping":
                    router.mappings.pop(int(arg("NewExternalPort")), None)
                    self._send(200, "<r/>")
                else:
                    self._send(500, "<r><errorCode>401</errorCode></r>")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.location = f"http://127.0.0.1:{self.httpd.server_address[1]}/desc.xml"

    def close(self):
        self.httpd.shutdown()


@pytest.fixture()
def router():
    r = FakeRouter()
    yield r
    r.close()


def test_upnp_maps_and_removes(router):
    u = network.UPnP._from_description(router.location)
    assert u.control_url.endswith("/ctl/IPConn")
    assert u.external_ip() == "93.184.216.34"
    assert u.add_mapping(19132) == network.LEASE_SECONDS
    assert router.mappings[19132]["proto"] == "UDP" and router.mappings[19132]["client"] == "127.0.0.1"
    u.remove_mapping(19132)
    assert router.mappings == {}


def test_upnp_falls_back_to_permanent_lease():
    r = FakeRouter(permanent_only=True)
    try:
        u = network.UPnP._from_description(r.location)
        assert u.add_mapping(19132) == 0
        assert r.mappings[19132]["lease"] == "0"
    finally:
        r.close()


def test_forwarder_open_then_close(router):
    fw = network.PortForwarder(lambda: 19132, discover=lambda: network.UPnP._from_description(router.location),
                               gateway_fn=lambda: None, public_fn=lambda: "93.184.216.34")
    fw._map_once()
    st = fw.snapshot()
    assert st["status"] == "open" and st["method"] == "UPnP" and st["public_ip"] == "93.184.216.34"
    assert 19132 in router.mappings
    fw.stop(remove=True)
    assert router.mappings == {} and fw.snapshot()["status"] == "off"


def test_forwarder_detects_cgnat():
    r = FakeRouter(external_ip="100.72.1.5")
    try:
        fw = network.PortForwarder(lambda: 19132, discover=lambda: network.UPnP._from_description(r.location),
                                   gateway_fn=lambda: None, public_fn=lambda: "198.51.100.20")
        fw._map_once()
        st = fw.snapshot()
        assert st["status"] == "cgnat" and st["cgnat"] is True
    finally:
        r.close()


def test_forwarder_reports_failure_when_nothing_answers():
    def no_router():
        raise network.UPnPError("no UPnP router answered")
    fw = network.PortForwarder(lambda: 19132, discover=no_router, gateway_fn=lambda: None, public_fn=lambda: None)
    with pytest.raises(network.UPnPError) as exc:
        fw._map_once()
    assert "UPnP" in str(exc.value) and "NAT-PMP" in str(exc.value)


@needs_posix
def test_natpmp_against_fake_gateway(monkeypatch):
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    monkeypatch.setattr(network, "NATPMP_PORT", srv.getsockname()[1])
    seen = []

    def serve():
        for _ in range(2):
            data, addr = srv.recvfrom(64)
            seen.append(data)
            if data[1] == 0:
                srv.sendto(struct.pack("!BBHI", 0, 128, 0, 1) + socket.inet_aton("203.0.113.9"), addr)
            else:
                _v, _op, _r, internal, _ext, life = struct.unpack("!BBHHHI", data)
                srv.sendto(struct.pack("!BBHIHHI", 0, 129, 0, 1, internal, 40000, life), addr)
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    assert network.natpmp_external_ip("127.0.0.1") == "203.0.113.9"
    assert network.natpmp_map("127.0.0.1", 19132) == 40000
    t.join(2)
    srv.close()
    assert len(seen) == 2


def test_address_helpers():
    assert network.is_public("8.8.8.8")
    assert not network.is_public("192.168.1.5")
    assert not network.is_public("100.64.3.3")
    assert network.valid_ipv4("10.0.0.1") and not network.valid_ipv4("nope")


def test_lan_override(monkeypatch):
    monkeypatch.setenv("BLOCKHOST_LAN_ADDRESS", "192.168.50.10")
    assert network.lan_ip() == "192.168.50.10"


# ----------------------------------------------------------------- playit ---


def test_pick_tunnel_prefers_bedrock():
    data = {"tunnels": [
        {"tunnel_type": None, "port_type": "tcp", "display_address": "a.example:1"},
        {"tunnel_type": None, "port_type": "udp", "display_address": "b.example:2"},
        {"tunnel_type": "minecraft-bedrock", "port_type": "udp", "display_address": "c.example:3"},
    ]}
    assert tunnel.pick_tunnel(data)["display_address"] == "c.example:3"
    assert tunnel.pick_tunnel({"tunnels": data["tunnels"][:2]})["display_address"] == "b.example:2"
    assert tunnel.pick_tunnel({"tunnels": []}) is None


def test_playit_claim_writes_secret_and_reads_address(tmp_path, monkeypatch):
    calls = []
    replies = iter(["WaitingForUserVisit", "WaitingForUser", "UserAccepted"])

    def fake_api(path, body, secret=None, timeout=15):
        calls.append((path, body, secret))
        if path == "/claim/setup":
            return next(replies)
        if path == "/claim/exchange":
            return {"secret_key": "ab" * 32}
        if path == "/v1/agents/rundata":
            return {"tunnels": [{"tunnel_type": "minecraft-bedrock", "port_type": "udp",
                                 "display_address": "fun-cat.gl.at.ply.gg:12345", "name": "mc"}], "pending": []}
        raise AssertionError(path)

    monkeypatch.setattr(tunnel, "api_call", fake_api)
    t = tunnel.PlayitTunnel(str(tmp_path), str(tmp_path), lambda: 19132)
    monkeypatch.setattr(t, "ensure_agent", lambda: "/bin/true")
    monkeypatch.setattr(t._stop, "wait", lambda s=None: False)
    spawned = []
    monkeypatch.setattr(t, "_spawn", lambda agent: spawned.append(agent))

    secret = t._claim()
    assert secret == "ab" * 32
    assert calls[0][1]["agent_type"] == "assignable" and len(calls[0][1]["code"]) == 10
    t._write_secret(secret)
    assert t.linked() and t._read_secret() == "ab" * 32
    if hasattr(__import__("os"), "geteuid"):
        assert (tmp_path / "playit" / "playit.toml").stat().st_mode & 0o077 == 0
    t._refresh_address()
    snap = t.snapshot()
    assert snap["status"] == "online" and snap["address"] == "fun-cat.gl.at.ply.gg:12345"
    assert calls[-1][2] == "ab" * 32
    t.unlink()
    assert not t.linked()


def test_playit_needs_tunnel_message(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnel, "api_call", lambda *a, **k: {"tunnels": [], "pending": []})
    t = tunnel.PlayitTunnel(str(tmp_path), str(tmp_path), lambda: 19140)
    t._write_secret("cd" * 32)
    t._refresh_address()
    s = t.snapshot()
    assert s["status"] == "needs_tunnel" and "19140" in s["detail"]


def test_playit_rejects_bad_secret(tmp_path):
    t = tunnel.PlayitTunnel(str(tmp_path), str(tmp_path), lambda: 19132)
    with pytest.raises(tunnel.PlayitError):
        t._write_secret('x" \nevil = "1')


def test_playit_checksum_mismatch_is_refused(tmp_path, monkeypatch):
    if tunnel.agent_asset() is None:
        pytest.skip("no playit agent for this platform")

    class Resp:
        def __init__(self):
            self.done = False

        def read(self, n):
            if self.done:
                return b""
            self.done = True
            return b"not the real agent"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(tunnel.urllib.request, "urlopen", lambda *a, **k: Resp())
    t = tunnel.PlayitTunnel(str(tmp_path), str(tmp_path), lambda: 19132)
    with pytest.raises(tunnel.PlayitError, match="checksum"):
        t.ensure_agent()
    assert not (tmp_path / "playit" / tunnel.agent_asset()[0]).exists()


def test_api_call_parses_playit_envelopes(monkeypatch):
    class Resp:
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode()

        def read(self, n):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(tunnel.urllib.request, "urlopen", lambda *a, **k: Resp({"status": "success", "data": "UserAccepted"}))
    assert tunnel.api_call("/claim/setup", {}) == "UserAccepted"
    monkeypatch.setattr(tunnel.urllib.request, "urlopen", lambda *a, **k: Resp({"status": "error", "data": {"type": "auth", "message": "InvalidAgentKey"}}))
    with pytest.raises(tunnel.PlayitError, match="InvalidAgentKey"):
        tunnel.api_call("/v1/agents/rundata", {}, secret="ab")


# -------------------------------------------------------------------- app ---


@needs_posix
def test_network_api(running_app, router, monkeypatch):
    app, c, cfg = running_app
    c.req("POST", "/auth/setup", {"code": app.AUTH.setup_token, "password": "longenough"})
    monkeypatch.setenv("BLOCKHOST_LAN_ADDRESS", "192.168.1.20")
    info = c.req("GET", "/api/network")[1]
    assert info["local"] == {"host": "192.168.1.20", "port": 19132, "container": False}
    assert info["external"]["host"] == "" and info["upnp"]["enabled"] is False

    assert c.req("POST", "/network/external", {"address": "bad host!"})[0] == 400
    st, info = c.req("POST", "/network/external", {"address": "mc.example.com"})
    assert st == 200 and info["external"] == {"host": "mc.example.com", "port": 19132, "source": "manual", "note": ""}
    st, info = c.req("POST", "/network/external", {"address": ""})
    assert info["external"]["host"] == "" and cfg.get("external_address") == ""

    app.NET._discover = lambda: network.UPnP._from_description(router.location)
    app.NET._public_fn = lambda: "93.184.216.34"
    app.NET._gateway_fn = lambda: None
    assert c.req("POST", "/network/upnp", {"enabled": True})[0] == 200
    for _ in range(50):
        if app.NET.snapshot()["status"] == "open":
            break
        __import__("time").sleep(0.1)
    info = c.req("GET", "/api/network")[1]
    assert info["external"]["host"] == "93.184.216.34" and info["external"]["source"] == "upnp"
    assert cfg.get("upnp_enabled") is True
    c.req("POST", "/network/upnp", {"enabled": False})
    assert router.mappings == {} and cfg.get("upnp_enabled") is False

    assert c.req("POST", "/network/check", {})[0] == 400
    assert c.req("POST", "/network/tunnel", {"action": "nope"})[0] == 400


def test_split_address():
    from blockhost import app
    assert app.split_address("host.example:1234", 19132) == ("host.example", 1234)
    assert app.split_address("host.example", 19132) == ("host.example", 19132)
