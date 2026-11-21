"""playit.gg tunnel: a public address for players when port forwarding can't work
(carrier-grade NAT, a router without UPnP, or no access to the router).

Flow: the owner presses "Set up tunnel", Blockhost downloads playit's agent from
playit's GitHub releases (pinned version, checked against a SHA-256), shows a
playit.gg claim link, and waits for the owner to approve it there. The agent's
secret key is stored in <data>/playit/playit.toml (owner-only permissions).
Blockhost then runs the agent as a child process and reads the tunnel's public
address from playit's API.

playit.gg is a third-party service with its own terms. Nothing here runs until
the owner turns it on.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from . import __version__

API = "https://api.playit.gg"
CLAIM_URL = "https://playit.gg/claim/"
DASHBOARD_URL = "https://playit.gg/account/tunnels"
AGENT_VERSION = "2.0.2"
RELEASE_URL = f"https://github.com/playit-cloud/playit-agent/releases/download/v{AGENT_VERSION}/"
# (sys.platform, machine) -> (release asset, sha256). Hashes taken from the v2.0.2 release.
AGENTS = {
    ("linux", "x86_64"): ("playit-linux-amd64",
                          "9d27a8dbb4d2d8a7107fcabfa33028c789a30a64c97a36447fe4afb94e08dde9"),
    ("win32", "amd64"): ("playitd-windows-x86_64-signed.exe",
                         "6e62da9f56a3bcf72e0c62c29e94609c847cc9b110c460f68b20035525985238"),
}
UA = f"Blockhost/{__version__} (+https://github.com/buschmjm/blockhost)"
CLAIM_TIMEOUT = 15 * 60
POLL_SECONDS = 30
IS_WINDOWS = sys.platform == "win32"


class PlayitError(Exception):
    pass


def agent_asset() -> tuple[str, str] | None:
    plat = "linux" if sys.platform.startswith("linux") else sys.platform
    return AGENTS.get((plat, platform.machine().lower()))


def api_call(path: str, body: dict, secret: str | None = None, timeout: float = 15):
    """POST to playit's API. Returns data on success; raises PlayitError otherwise."""
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if secret:
        headers["Authorization"] = f"Agent-Key {secret.strip()}"
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URL)
            payload = json.loads(resp.read(1 << 20))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(1 << 20))
        except ValueError:
            raise PlayitError(f"playit.gg answered HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise PlayitError(f"couldn't reach playit.gg ({exc})") from exc
    status = payload.get("status")
    if status == "success":
        return payload.get("data")
    data = payload.get("data")
    if isinstance(data, dict):
        data = data.get("message") or data.get("type") or data
    raise PlayitError(f"playit.gg: {data}" if status == "fail" else f"playit.gg error: {data}")


def pick_tunnel(rundata: dict) -> dict | None:
    """The tunnel players should use: a Bedrock tunnel first, then any UDP tunnel."""
    tunnels = [t for t in (rundata or {}).get("tunnels") or [] if isinstance(t, dict)]
    usable = [t for t in tunnels if not t.get("disabled_reason")]
    for pool in (usable, tunnels):
        for t in pool:
            if "bedrock" in str(t.get("tunnel_type") or "").lower():
                return t
        for t in pool:
            if t.get("port_type") in ("udp", "both"):
                return t
    return None


class PlayitTunnel:
    def __init__(self, data_dir: str, log_dir: str, port_fn):
        self.dir = os.path.join(data_dir, "playit")
        self.secret_path = os.path.join(self.dir, "playit.toml")
        self.log_path = os.path.join(log_dir, "playit.log")
        self.port_fn = port_fn
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._claim_code = ""
        self.state: dict = {"status": "off", "address": "", "claim_url": "", "error": "", "detail": "",
                            "tunnel_name": "", "at": 0}

    # -- state -----------------------------------------------------------

    def snapshot(self) -> dict:
        with self.lock:
            out = dict(self.state)
        out["linked"] = self.linked()
        out["supported"] = agent_asset() is not None
        out["agent_running"] = self.proc is not None and self.proc.poll() is None
        out["dashboard_url"] = DASHBOARD_URL
        out["local_port"] = self._port()
        return out

    def _set(self, **kw) -> None:
        with self.lock:
            self.state.update(kw, at=time.time())

    def _port(self) -> int:
        try:
            return int(self.port_fn())
        except (TypeError, ValueError):
            return 19132

    def linked(self) -> bool:
        return self._read_secret() is not None

    def _read_secret(self) -> str | None:
        try:
            with open(self.secret_path, encoding="utf-8") as fh:
                m = re.search(r'secret_key\s*=\s*"([0-9a-fA-F]+)"', fh.read())
        except OSError:
            return None
        return m.group(1) if m else None

    def _write_secret(self, secret: str) -> None:
        if not re.fullmatch(r"[0-9a-fA-F]+", secret or ""):
            raise PlayitError("playit.gg sent a secret key in an unexpected format")
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.secret_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f'secret_key = "{secret}"\n')
        os.replace(tmp, self.secret_path)

    # -- agent binary ------------------------------------------------------

    def agent_path(self) -> str:
        asset = agent_asset()
        return os.path.join(self.dir, asset[0] if asset else "playit-agent")

    def ensure_agent(self) -> str:
        asset = agent_asset()
        if not asset:
            raise PlayitError(f"playit's agent isn't available for this system ({sys.platform}/{platform.machine()})")
        name, sha = asset
        path = os.path.join(self.dir, name)
        if os.path.isfile(path) and _sha256(path) == sha:
            return path
        os.makedirs(self.dir, exist_ok=True)
        self._set(status="downloading", detail=f"downloading playit agent {AGENT_VERSION}")
        tmp = path + ".part"
        req = urllib.request.Request(RELEASE_URL + name, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:  # noqa: S310
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
        except (OSError, urllib.error.URLError) as exc:
            _unlink(tmp)
            raise PlayitError(f"couldn't download playit's agent ({exc})") from exc
        if _sha256(tmp) != sha:
            _unlink(tmp)
            raise PlayitError("the downloaded playit agent didn't match the expected checksum; not running it")
        if not IS_WINDOWS:
            os.chmod(tmp, 0o755)
        os.replace(tmp, path)
        return path

    # -- claim -------------------------------------------------------------

    def begin_setup(self) -> dict:
        """Start (or resume) linking. Returns the snapshot, with claim_url once ready."""
        with self.lock:
            busy = self._thread is not None and self._thread.is_alive()
        if busy:
            return self.snapshot()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="playit", daemon=True)
        self._set(status="starting", error="", detail="")
        self._thread.start()
        return self.snapshot()

    def _claim(self) -> str:
        code = secrets.token_hex(5)
        self._claim_code = code
        url = CLAIM_URL + code
        self._set(status="claiming", claim_url=url, detail="Open the link and approve Blockhost on playit.gg")
        end = time.time() + CLAIM_TIMEOUT
        while time.time() < end and not self._stop.is_set():
            try:
                res = api_call("/claim/setup", {"code": code, "agent_type": "assignable",
                                                "version": f"blockhost {__version__}"})
            except PlayitError as exc:
                self._set(detail=str(exc))
                self._stop.wait(3)
                continue
            if res == "UserAccepted":
                break
            if res == "UserRejected":
                raise PlayitError("the link was declined on playit.gg")
            self._set(detail="Waiting for you to approve Blockhost on playit.gg"
                      if res == "WaitingForUser" else "Open the link to link Blockhost to your playit.gg account")
            self._stop.wait(2)
        else:
            if self._stop.is_set():
                raise PlayitError("setup cancelled")
            raise PlayitError("the playit.gg link expired; press Set up again for a new one")
        for _ in range(30):
            try:
                data = api_call("/claim/exchange", {"code": code})
                secret = data.get("secret_key") if isinstance(data, dict) else None
                if secret:
                    return secret
            except PlayitError as exc:
                if "NotAccepted" not in str(exc) and "NotSetup" not in str(exc):
                    raise
            self._stop.wait(2)
        raise PlayitError("playit.gg approved the link but didn't hand over a key; try again")

    # -- run loop ------------------------------------------------------------

    def _run(self) -> None:
        try:
            agent = self.ensure_agent()
            if not self.linked():
                self._write_secret(self._claim())
            self._set(claim_url="", detail="")
            backoff = 2
            while not self._stop.is_set():
                if self.proc is None or self.proc.poll() is not None:
                    if self.proc is not None:
                        self._set(detail=f"playit agent exited (code {self.proc.returncode}); restarting")
                        self._stop.wait(backoff)
                        backoff = min(backoff * 2, 60)
                        if self._stop.is_set():
                            break
                    self._spawn(agent)
                self._refresh_address()
                self._stop.wait(POLL_SECONDS if self.state.get("address") else 8)
        except PlayitError as exc:
            self._set(status="error", error=str(exc), claim_url="")
        except Exception as exc:  # noqa: BLE001
            self._set(status="error", error=f"unexpected error: {exc}", claim_url="")
        finally:
            self._kill()

    def _spawn(self, agent: str) -> None:
        os.makedirs(self.dir, exist_ok=True)
        socket_path = r"\\.\pipe\blockhost-playitd" if IS_WINDOWS else os.path.join(self.dir, "playitd.sock")
        cmd = [agent, "--secret-path", self.secret_path, "--socket-path", socket_path, "--log-path", self.log_path]
        kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "cwd": self.dir}
        if IS_WINDOWS:
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(cmd, **kw)  # noqa: S603 (our own pinned, checksummed binary)
        except OSError as exc:
            raise PlayitError(f"couldn't start playit's agent ({exc})") from exc

    def _refresh_address(self) -> None:
        secret = self._read_secret()
        if not secret:
            raise PlayitError("the playit.gg key is missing; set it up again")
        try:
            data = api_call("/v1/agents/rundata", {}, secret=secret)
        except PlayitError as exc:
            if "InvalidAgentKey" in str(exc):
                raise PlayitError("playit.gg no longer accepts this link (was the agent deleted?). "
                                  "Unlink and set it up again.") from exc
            self._set(detail=str(exc))
            return
        tunnel = pick_tunnel(data if isinstance(data, dict) else {})
        if tunnel and tunnel.get("display_address"):
            disabled = tunnel.get("disabled_reason")
            self._set(status="disabled" if disabled else "online", address=str(tunnel["display_address"]),
                      tunnel_name=str(tunnel.get("name") or ""), error="",
                      detail=f"playit.gg disabled this tunnel: {disabled}" if disabled else "")
            return
        pending = [p for p in (data or {}).get("pending") or [] if isinstance(p, dict)]
        msg = pending[0].get("status_msg") if pending else ""
        self._set(status="needs_tunnel", address="", tunnel_name="",
                  detail=msg or f"Add a Minecraft Bedrock tunnel on playit.gg for local port {self._port()}")

    # -- stop / unlink ---------------------------------------------------------

    def _kill(self) -> None:
        p = self.proc
        self.proc = None
        if p is None or p.poll() is not None:
            return
        p.terminate()
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=12)
        self._kill()
        self._set(status="off", address="", claim_url="", detail="", error="")

    def unlink(self) -> None:
        self.stop()
        _unlink(self.secret_path)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
