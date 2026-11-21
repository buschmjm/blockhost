"""Join addresses and router setup.

- LAN address: the address other devices on the same network use.
- Public address: what the internet sees (asked from a public "what is my IP" service,
  only when the owner turns on a feature that needs it).
- Port forwarding: UPnP IGD first, NAT-PMP second. Both only run after the owner
  presses the button; nothing is opened on the router by default.

Standard library only.
"""
from __future__ import annotations

import ipaddress
import os
import random
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from . import __version__

UA = f"Blockhost/{__version__} (+https://github.com/buschmjm/blockhost)"
PUBLIC_IP_URL = "https://api.ipify.org"
STATUS_API = "https://api.mcsrvstat.us/bedrock/3/"
DEFAULT_PORT = 19132
LEASE_SECONDS = 3600
RENEW_SECONDS = 1200
MAPPING_NAME = "Blockhost Minecraft"
NATPMP_PORT = 5351

_CGNAT = ipaddress.ip_network("100.64.0.0/10")


# ------------------------------------------------------------- addresses ---


def valid_ipv4(value: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(value.strip()), ipaddress.IPv4Address)
    except ValueError:
        return False


def is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_global and addr not in _CGNAT


def in_container() -> bool:
    return os.path.exists("/.dockerenv") or bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def lan_ip() -> str | None:
    """This machine's address on the local network (the interface that routes outward).

    BLOCKHOST_LAN_ADDRESS overrides it, for containers where the detected address is
    the container's own and not the host's.
    """
    override = os.environ.get("BLOCKHOST_LAN_ADDRESS", "").strip()
    if override:
        return override
    # connect() on UDP sends nothing; it only picks the outgoing interface.
    for probe in ("192.0.2.1", "10.255.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))
                ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            continue
    return None


def default_gateway() -> str | None:
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/net/route", encoding="ascii") as fh:
                for line in fh.readlines()[1:]:
                    parts = line.split()
                    if len(parts) > 3 and parts[1] == "00000000" and int(parts[3], 16) & 2:
                        return socket.inet_ntoa(struct.pack("<I", int(parts[2], 16)))
        except (OSError, ValueError):
            return None
        return None
    if sys.platform == "win32":
        try:
            out = subprocess.run(["route", "print", "-4", "0.0.0.0"], capture_output=True, text=True,
                                 timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0" and valid_ipv4(parts[2]):
                return parts[2]
    return None


def public_ip(timeout: float = 6) -> str | None:
    req = urllib.request.Request(PUBLIC_IP_URL, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URL)
            ip = resp.read(64).decode("ascii", "replace").strip()
    except (OSError, urllib.error.URLError):
        return None
    return ip if valid_ipv4(ip) else None


def check_reachable(address: str, timeout: float = 20) -> tuple[bool | None, str]:
    """Ask a public Minecraft status service whether address answers from the internet.

    (True, "") reachable, (False, reason) not reachable, (None, reason) the check itself failed.
    """
    if not re.fullmatch(r"[A-Za-z0-9.\-]+(:\d{1,5})?", address or ""):
        return None, "not a valid address"
    req = urllib.request.Request(STATUS_API + address, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            import json
            data = json.loads(resp.read(256 * 1024))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return None, f"the checking service didn't answer ({exc})"
    if data.get("online"):
        return True, ""
    errs = (data.get("debug") or {}).get("errors") or []
    msg = "; ".join(str(e.get("message", "")) for e in errs if isinstance(e, dict) and e.get("message"))
    return False, msg or "no answer from the server"


# ------------------------------------------------------------------ UPnP ---

_WAN_SERVICES = (
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
)


class UPnPError(Exception):
    pass


class UPnP:
    """Minimal UPnP Internet Gateway Device client: discover, external IP, add/remove mapping."""

    def __init__(self, control_url: str, service: str, local_ip: str):
        self.control_url, self.service, self.local_ip = control_url, service, local_ip

    @classmethod
    def discover(cls, timeout: float = 3.0) -> UPnP:
        locations: list[str] = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            s.settimeout(0.5)
            for st in ("urn:schemas-upnp-org:device:InternetGatewayDevice:1",
                       "urn:schemas-upnp-org:device:InternetGatewayDevice:2"):
                msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                       f"MAN: \"ssdp:discover\"\r\nMX: 2\r\nST: {st}\r\n\r\n").encode()
                try:
                    s.sendto(msg, ("239.255.255.250", 1900))
                except OSError as exc:
                    raise UPnPError(f"couldn't send a discovery message ({exc})") from exc
            end = time.time() + timeout
            while time.time() < end:
                try:
                    data, _addr = s.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                m = re.search(rb"(?im)^location:\s*(\S+)", data)
                if m:
                    loc = m.group(1).decode("ascii", "replace")
                    if loc not in locations:
                        locations.append(loc)
        if not locations:
            raise UPnPError("no UPnP router answered (UPnP may be turned off on the router)")
        last = "router description had no internet connection service"
        for loc in locations:
            try:
                return cls._from_description(loc)
            except UPnPError as exc:
                last = str(exc)
        raise UPnPError(last)

    @classmethod
    def _from_description(cls, location: str) -> UPnP:
        if urlparse(location).scheme != "http":
            raise UPnPError("router description isn't on plain http")
        try:
            with urllib.request.urlopen(location, timeout=4) as resp:  # noqa: S310 (LAN router)
                xml = resp.read(512 * 1024)
                base = resp.geturl()
        except (OSError, urllib.error.URLError) as exc:
            raise UPnPError(f"couldn't read router description ({exc})") from exc
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError as exc:
            raise UPnPError("router description is not valid XML") from exc
        url_base = None
        for el in root.iter():
            if el.tag.endswith("URLBase") and (el.text or "").strip():
                url_base = el.text.strip()
        found: dict[str, str] = {}
        for svc in root.iter():
            if not svc.tag.endswith("service"):
                continue
            st = ctrl = None
            for child in svc:
                if child.tag.endswith("serviceType"):
                    st = (child.text or "").strip()
                elif child.tag.endswith("controlURL"):
                    ctrl = (child.text or "").strip()
            if st and ctrl:
                found[st] = ctrl
        for st in _WAN_SERVICES:
            if st in found:
                host = urlparse(location).hostname or ""
                local = _local_ip_towards(host) or lan_ip() or ""
                return cls(urljoin(url_base or base, found[st]), st, local)
        raise UPnPError("router description had no internet connection service")

    def _soap(self, action: str, args: dict[str, str]) -> dict[str, str]:
        body = "".join(f"<{k}>{_xml_escape(v)}</{k}>" for k, v in args.items())
        envelope = ('<?xml version="1.0"?>\r\n'
                    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                    f'<u:{action} xmlns:u="{self.service}">{body}</u:{action}></s:Body></s:Envelope>').encode()
        req = urllib.request.Request(self.control_url, data=envelope, method="POST", headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{self.service}#{action}"',
        })
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:  # noqa: S310 (LAN router)
                xml = resp.read(256 * 1024)
        except urllib.error.HTTPError as exc:
            detail = exc.read(64 * 1024)
            code = re.search(rb"<errorCode>\s*(\d+)\s*</errorCode>", detail)
            desc = re.search(rb"<errorDescription>\s*([^<]*)</errorDescription>", detail)
            raise UPnPError(f"router refused {action}" + (f" (error {code.group(1).decode()}" if code else "")
                            + (f": {desc.group(1).decode(errors='replace')})" if desc else (")" if code else ""))) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise UPnPError(f"router didn't answer {action} ({exc})") from exc
        out: dict[str, str] = {}
        try:
            for el in ElementTree.fromstring(xml).iter():
                tag = el.tag.split("}")[-1]
                if el.text is not None and not list(el):
                    out[tag] = el.text.strip()
        except ElementTree.ParseError as exc:
            raise UPnPError(f"router sent an unreadable answer to {action}") from exc
        return out

    def external_ip(self) -> str | None:
        ip = self._soap("GetExternalIPAddress", {}).get("NewExternalIPAddress", "")
        return ip if valid_ipv4(ip) else None

    def add_mapping(self, port: int, lease: int = LEASE_SECONDS) -> int:
        """Map UDP port -> this machine. Returns the lease actually used (0 = permanent)."""
        args = {"NewRemoteHost": "", "NewExternalPort": str(port), "NewProtocol": "UDP",
                "NewInternalPort": str(port), "NewInternalClient": self.local_ip, "NewEnabled": "1",
                "NewPortMappingDescription": MAPPING_NAME, "NewLeaseDuration": str(lease)}
        try:
            self._soap("AddPortMapping", args)
            return lease
        except UPnPError as exc:
            if "725" not in str(exc) or lease == 0:  # 725 OnlyPermanentLeasesSupported
                raise
        args["NewLeaseDuration"] = "0"
        self._soap("AddPortMapping", args)
        return 0

    def remove_mapping(self, port: int) -> None:
        self._soap("DeletePortMapping", {"NewRemoteHost": "", "NewExternalPort": str(port), "NewProtocol": "UDP"})


def _xml_escape(v: str) -> str:
    return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _local_ip_towards(host: str) -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 1900))
            return s.getsockname()[0]
    except OSError:
        return None


# --------------------------------------------------------------- NAT-PMP ---


class NatPmpError(Exception):
    pass


def _natpmp(gateway: str, payload: bytes, want_len: int, timeout: float = 2.0) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        wait = 0.25
        end = time.time() + timeout
        while time.time() < end:
            s.settimeout(wait)
            try:
                s.sendto(payload, (gateway, NATPMP_PORT))
                data, addr = s.recvfrom(64)
            except socket.timeout:
                wait = min(wait * 2, 1.0)
                continue
            except OSError as exc:
                raise NatPmpError(f"NAT-PMP failed ({exc})") from exc
            if addr[0] == gateway and len(data) >= want_len and data[1] == payload[1] + 128:
                result = struct.unpack("!H", data[2:4])[0]
                if result:
                    raise NatPmpError(f"router refused NAT-PMP (result {result})")
                return data
    raise NatPmpError("router didn't answer NAT-PMP")


def natpmp_external_ip(gateway: str) -> str:
    data = _natpmp(gateway, struct.pack("!BB", 0, 0), 12)
    return socket.inet_ntoa(data[8:12])


def natpmp_map(gateway: str, port: int, lease: int = LEASE_SECONDS) -> int:
    """Map UDP port (opcode 1). lease 0 removes it. Returns the public port the router gave."""
    data = _natpmp(gateway, struct.pack("!BBHHHI", 0, 1, 0, port, port, lease), 16)
    return struct.unpack("!H", data[10:12])[0]


# ----------------------------------------------------------- forwarder ---


class PortForwarder:
    """Keeps a UDP port mapping on the router while enabled. Renews it; removes it on stop."""

    def __init__(self, port_fn, *, discover=UPnP.discover, gateway_fn=default_gateway, public_fn=public_ip):
        self.port_fn, self._discover, self._gateway_fn, self._public_fn = port_fn, discover, gateway_fn, public_fn
        self.lock = threading.Lock()
        self.state: dict = {"status": "off", "method": "", "router_ip": "", "public_ip": "", "port": 0,
                            "public_port": 0, "error": "", "cgnat": False, "at": 0}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._upnp: UPnP | None = None
        self._gateway: str | None = None

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)

    def _set(self, **kw) -> None:
        with self.lock:
            self.state.update(kw, at=time.time())

    def start(self) -> None:
        with self.lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self.state.update(status="working", error="")
            self._thread = threading.Thread(target=self._run, name="port-forward", daemon=True)
            self._thread.start()

    def stop(self, *, remove: bool = True) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=10)
        if remove:
            self._remove()
        self._set(status="off", method="", error="")

    def _remove(self) -> None:
        port = self.state.get("port") or 0
        if not port:
            return
        try:
            if self._upnp:
                self._upnp.remove_mapping(port)
            elif self._gateway:
                natpmp_map(self._gateway, port, 0)
        except (UPnPError, NatPmpError, OSError):
            pass
        self._upnp = None
        self._gateway = None
        self._set(port=0, public_port=0)

    def _map_once(self) -> None:
        port = int(self.port_fn())
        errors: list[str] = []
        router_ip = ""
        try:
            if self._upnp is None:
                self._upnp = self._discover()
            lease = self._upnp.add_mapping(port)
            try:
                router_ip = self._upnp.external_ip() or ""
            except UPnPError:
                router_ip = ""
            self._set(method="UPnP", port=port, public_port=port, lease=lease)
        except (UPnPError, OSError) as exc:
            self._upnp = None
            errors.append(f"UPnP: {exc}")
            gw = self._gateway_fn()
            if not gw:
                raise UPnPError("; ".join(errors + ["NAT-PMP: couldn't find the router address"])) from exc
            try:
                public_port = natpmp_map(gw, port)
                self._gateway = gw
                try:
                    router_ip = natpmp_external_ip(gw)
                except NatPmpError:
                    router_ip = ""
                self._set(method="NAT-PMP", port=port, public_port=public_port or port, lease=LEASE_SECONDS)
            except (NatPmpError, OSError) as exc2:
                raise UPnPError("; ".join(errors + [f"NAT-PMP: {exc2}"])) from exc2
        pub = self._public_fn() or ""
        cgnat = bool(router_ip) and (not is_public(router_ip) or (bool(pub) and pub != router_ip))
        self._set(status="cgnat" if cgnat else "open", router_ip=router_ip, public_ip=pub or router_ip,
                  cgnat=cgnat, error="")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._map_once()
                wait = RENEW_SECONDS
            except UPnPError as exc:
                self._set(status="failed", error=str(exc))
                wait = 300
            except Exception as exc:  # noqa: BLE001 (keep the thread alive on surprises)
                self._set(status="failed", error=f"unexpected error: {exc}")
                wait = 300
            self._stop.wait(wait + random.uniform(0, 5))
