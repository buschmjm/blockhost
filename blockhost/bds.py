"""Bedrock Dedicated Server (BDS): download, install/update, and run as a child process.

Blockhost never ships the server binary (Mojang's EULA forbids redistribution).
It downloads the official build from minecraft.net after the user accepts the
Minecraft EULA, and keeps worlds and settings across updates.
"""
from __future__ import annotations

import collections
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from datetime import datetime, timezone

LINKS_API = "https://net-secondary.web.minecraft-services.net/api/v1.0/download/links"
EULA_URL = "https://www.minecraft.net/eula"
PRIVACY_URL = "https://go.microsoft.com/fwlink/?LinkId=521839"
UA = "Mozilla/5.0 (compatible; Blockhost; +https://github.com/buschmjm/blockhost)"

# Files and folders that belong to the user and survive an update.
KEEP_ON_UPDATE = {
    "server.properties", "allowlist.json", "permissions.json", "worlds", "config",
    "development_behavior_packs", "development_resource_packs", "development_skin_packs",
    "blockhost-version.txt",
}
IS_WINDOWS = sys.platform == "win32"
EXE = "bedrock_server.exe" if IS_WINDOWS else "bedrock_server"
LEVEL_NAME = "level"


def platform_supported() -> str | None:
    """None if BDS can run here, else a human reason."""
    machine = platform.machine().lower()
    if sys.platform not in ("win32", "linux"):
        return "Mojang only publishes the Bedrock server for Windows and Linux."
    if machine not in ("x86_64", "amd64"):
        return f"Mojang only publishes the Bedrock server for 64-bit x86 CPUs (this machine is {machine})."
    return None


def _get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (fixed https URLs)


def latest_download(preview: bool = False) -> tuple[str, str]:
    """(version, url) of the newest official BDS build for this OS."""
    with _get(LINKS_API) as resp:
        links = json.load(resp)["result"]["links"]
    want = ("serverBedrockPreview" if preview else "serverBedrock") + ("Windows" if IS_WINDOWS else "Linux")
    for link in links:
        if link.get("downloadType") == want:
            url = link["downloadUrl"]
            m = re.search(r"bedrock-server-([\d.]+)\.zip", url)
            return (m.group(1) if m else "unknown"), url
    raise RuntimeError(f"no {want} download offered by Mojang right now")


def installed_version(server_dir: str) -> str:
    try:
        with open(os.path.join(server_dir, "blockhost-version.txt"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def is_installed(server_dir: str) -> bool:
    return os.path.isfile(os.path.join(server_dir, EXE))


def install(server_dir: str, url: str, version: str, progress=lambda pct, msg: None) -> None:
    """Download url and install into server_dir, preserving user data."""
    os.makedirs(server_dir, exist_ok=True)
    fd, tmp_zip = tempfile.mkstemp(suffix=".zip", dir=os.path.dirname(server_dir))
    os.close(fd)
    try:
        with _get(url, timeout=60) as resp, open(tmp_zip, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    progress(int(done * 90 / total), f"downloading {done >> 20} of {total >> 20} MB")
        progress(92, "unpacking")
        fresh = not os.path.isfile(os.path.join(server_dir, "server.properties"))
        with zipfile.ZipFile(tmp_zip) as zf:
            for member in zf.infolist():
                top = member.filename.split("/", 1)[0]
                if not fresh and top in KEEP_ON_UPDATE:
                    continue
                target = os.path.realpath(os.path.join(server_dir, member.filename))
                if not target.startswith(os.path.realpath(server_dir) + os.sep):
                    continue  # zip-slip guard
                zf.extract(member, server_dir)
        exe = os.path.join(server_dir, EXE)
        if not IS_WINDOWS and os.path.isfile(exe):
            os.chmod(exe, 0o755)
        if fresh:
            props = Properties(server_dir)
            props.update({"level-name": LEVEL_NAME, "server-name": "Blockhost server",
                          "content-log-console-output-enabled": "true", "allow-list": "false"})
        with open(os.path.join(server_dir, "blockhost-version.txt"), "w", encoding="utf-8") as fh:
            fh.write(version)
        progress(100, "installed")
    finally:
        try:
            os.unlink(tmp_zip)
        except OSError:
            pass


# ------------------------------------------------------------ properties ---

class Properties:
    """Read/modify server.properties while keeping comments and order."""

    def __init__(self, server_dir: str):
        self.path = os.path.join(server_dir, "server.properties")

    def read(self) -> dict[str, str]:
        out: dict[str, str] = {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        out[k.strip()] = v.strip()
        except OSError:
            pass
        return out

    def update(self, values: dict[str, str | None]) -> None:
        values = {k: ("" if v is None else str(v)) for k, v in values.items()}
        for v in values.values():
            if "\n" in v or "\r" in v:
                raise ValueError("property values must be one line")
        try:
            with open(self.path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            lines = []
        seen = set()
        for i, line in enumerate(lines):
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k = s.split("=", 1)[0].strip()
                if k in values:
                    lines[i] = f"{k}={values[k]}"
                    seen.add(k)
        lines += [f"{k}={v}" for k, v in values.items() if k not in seen]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        os.replace(tmp, self.path)


# --------------------------------------------------------------- process ---

def _pid_alive_in(pid: int, server_dir: str) -> bool:
    """True if pid is a bedrock_server running from server_dir (Linux; best effort elsewhere)."""
    if sys.platform == "linux":
        try:
            return os.path.realpath(f"/proc/{pid}/cwd") == os.path.realpath(server_dir)
        except OSError:
            return False
    return False


def _windows_kill_with_parent(proc: subprocess.Popen):
    """Windows: put the server in a job object that dies when Blockhost exits, so closing
    the console window never leaves an orphaned server holding the game port."""
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                      "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
    k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(proc._handle)))  # noqa: SLF001
    return job  # keep a reference: the handle closing is what kills the server


class BedrockProcess:
    """Supervises one bedrock_server child: start/stop, stdin commands, log ring buffer."""

    def __init__(self, server_dir: str, log_path: str | None = None, *, max_lines: int = 5000):
        self.server_dir = server_dir
        self.log_path = log_path
        self.lines: collections.deque[str] = collections.deque(maxlen=max_lines)
        self.proc: subprocess.Popen | None = None
        self.want_running = False
        self.started_at: float | None = None
        self.ready = False
        self.restarts = 0
        self.seq = 0  # total lines ever logged; lets callers read "lines since"
        self._crashes: list[float] = []
        self._lock = threading.RLock()
        self._stdin_lock = threading.Lock()
        self._log_fh = None
        if log_path:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            self._rotate(log_path)
            self._log_fh = open(log_path, "a", encoding="utf-8", errors="replace")  # noqa: SIM115

    @staticmethod
    def _rotate(path: str, keep_bytes: int = 20 << 20) -> None:
        try:
            if os.path.getsize(path) > keep_bytes:
                os.replace(path, path + ".1")
        except OSError:
            pass

    def _log(self, line: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        entry = f"{stamp} {line}"
        self.lines.append(entry)
        self.seq += 1
        if self._log_fh:
            self._log_fh.write(entry + "\n")
            self._log_fh.flush()

    # -- lifecycle -------------------------------------------------------

    @property
    def _pid_path(self) -> str:
        return os.path.join(self.server_dir, "blockhost-server.pid")

    def _write_pid(self, pid: int | None) -> None:
        try:
            if pid is None:
                os.unlink(self._pid_path)
            else:
                with open(self._pid_path, "w") as fh:
                    fh.write(str(pid))
        except OSError:
            pass

    def reap_orphan(self) -> None:
        """A server left running by a Blockhost that was killed: ask it to stop, then force it."""
        try:
            with open(self._pid_path) as fh:
                pid = int(fh.read().strip())
        except (OSError, ValueError):
            return
        if _pid_alive_in(pid, self.server_dir):
            import signal
            self._log(f"[Blockhost] stopping a server left running from last time (pid {pid})")
            os.kill(pid, signal.SIGINT)
            for _ in range(60):
                time.sleep(0.5)
                if not _pid_alive_in(pid, self.server_dir):
                    break
            else:
                os.kill(pid, signal.SIGKILL)
        self._write_pid(None)

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> dict:
        """Same shape the UI already understands (want/pods/ready/restarts)."""
        up = self.running()
        return {"want": 1 if self.want_running else 0, "pods": 1 if up else 0,
                "ready": 1 if up and self.ready else 0, "restarts": self.restarts,
                "installed": is_installed(self.server_dir)}

    def start(self, wait_seconds: int = 180) -> str | None:
        with self._lock:
            self.want_running = True
            if self.running():
                return None
            if not is_installed(self.server_dir):
                return "the Bedrock server is not installed yet"
            env = dict(os.environ)
            if not IS_WINDOWS:
                env["LD_LIBRARY_PATH"] = "."
            # Own process group: a Ctrl+C or SIGTERM aimed at Blockhost must not kill the server
            # mid-save; Blockhost sends "stop" instead. On Linux the server also dies with us.
            extra: dict = {}
            if IS_WINDOWS:
                extra["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                extra["start_new_session"] = True
            self.ready = False
            self.proc = subprocess.Popen(  # noqa: S603 (fixed binary path in our own dir)
                [os.path.join(self.server_dir, EXE)], cwd=self.server_dir, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=0, **extra)
            self.started_at = time.time()
            if IS_WINDOWS:
                try:
                    self._job = _windows_kill_with_parent(self.proc)
                except (OSError, AttributeError):
                    self._job = None
            self._write_pid(self.proc.pid)
            self._log("[Blockhost] server process started")
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if self.ready:
                return None
            if not self.running():
                return "server exited during startup. Check the Problems list on the Activity tab"
            time.sleep(0.5)
        return "server started but is not healthy. Check the Problems list on the Activity tab for pack or script errors"

    def stop(self, wait_seconds: int = 60) -> str | None:
        with self._lock:
            self.want_running = False
            proc = self.proc
            if proc is None or proc.poll() is not None:
                return None
            self.send("stop")
        try:
            proc.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        self.ready = False
        time.sleep(1)  # let file handles close before world files are touched
        return None

    def send(self, cmd: str) -> str | None:
        if "\n" in cmd or "\r" in cmd:
            return "one command at a time"
        proc = self.proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return "server is not running"
        try:
            with self._stdin_lock:
                proc.stdin.write((cmd + "\n").encode())
                proc.stdin.flush()
        except OSError as exc:
            return f"command failed: {exc}"
        return None

    def _pump(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            self._log(line)
            if "Server started." in line:
                self.ready = True
        code = proc.wait()
        if proc is self.proc:
            self._write_pid(None)
        self._log(f"[Blockhost] server process exited with code {code}")
        self.ready = False
        if self.want_running and proc is self.proc:
            now = time.time()
            self._crashes = [t for t in self._crashes if now - t < 600] + [now]
            if len(self._crashes) > 5:
                self._log("[Blockhost] ERROR] server crashed 5 times in 10 minutes; not restarting it again")
                self.want_running = False
                return
            self.restarts += 1
            time.sleep(5)
            if self.want_running and not self.running():
                self._log("[Blockhost] restarting server after a crash")
                threading.Thread(target=self.start, daemon=True).start()

    # -- logs ------------------------------------------------------------

    def tail(self, n: int) -> list[str]:
        lines = list(self.lines)
        return lines[-n:] if n else lines

    def since(self, seq: int) -> list[str]:
        n = self.seq - seq
        return self.tail(n) if n > 0 else []

    def tail_text(self, n: int, *, timestamps: bool = False) -> str:
        lines = self.tail(n)
        if not timestamps:
            lines = [ln.split(" ", 1)[1] if " " in ln else ln for ln in lines]
        return "\n".join(lines)

    # -- metrics ---------------------------------------------------------

    def metrics(self) -> dict:
        """Best effort CPU/memory for the server process and machine (Linux /proc only)."""
        out: dict = {"pod": {}, "node": {}}
        if sys.platform != "linux":
            return out
        try:
            pid = self.proc.pid if self.running() else None
            hz = os.sysconf("SC_CLK_TCK")
            if pid:
                with open(f"/proc/{pid}/stat") as fh:
                    f = fh.read().rsplit(")", 1)[1].split()
                cpu_s = (int(f[11]) + int(f[12])) / hz
                with open(f"/proc/{pid}/status") as fh:
                    rss = next((int(ln.split()[1]) for ln in fh if ln.startswith("VmRSS:")), 0)
                prev = getattr(self, "_cpu_prev", None)
                now = time.monotonic()
                self._cpu_prev = (now, cpu_s)
                cores = ""
                if prev and now > prev[0]:
                    cores = f"{int((cpu_s - prev[1]) / (now - prev[0]) * 1000)}m"
                out["pod"] = {"cpu": cores, "mem": f"{rss // 1024}Mi", "mem_limit": ""}
            mem = {}
            with open("/proc/meminfo") as fh:
                for ln in fh:
                    k, v = ln.split(":", 1)
                    mem[k] = int(v.split()[0])
            total, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
            load1 = os.getloadavg()[0]
            ncpu = os.cpu_count() or 1
            out["node"] = {"name": platform.node(), "cpu": f"{load1:.1f} load",
                           "cpu_pct": f"{int(load1 * 100 / ncpu)}%",
                           "mem": f"{(total - avail) // 1024}Mi",
                           "mem_pct": f"{int((total - avail) * 100 / total) if total else 0}%"}
        except (OSError, ValueError, IndexError, StopIteration):
            pass
        return out

