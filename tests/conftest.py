import json
import os
import sys
import socket
import textwrap
import threading
import time
import urllib.request

import pytest

FAKE_BDS = textwrap.dedent('''\
    #!/usr/bin/env python3
    import sys, time
    print("[INFO] Version: 1.0.0.1", flush=True)
    print("[INFO] Server started.", flush=True)
    for line in sys.stdin:
        cmd = line.strip()
        if cmd == "stop":
            print("[INFO] Stopping server...", flush=True)
            break
        if cmd == "list":
            print("[INFO] There are 1/10 players online:", flush=True)
            print("Steve", flush=True)
        elif cmd.startswith("gamerule "):
            print(cmd.split()[1] + " = true", flush=True)
        else:
            print("[INFO] ran: " + cmd, flush=True)
''')

needs_posix = pytest.mark.skipif(sys.platform == "win32", reason="fake server is a POSIX script")


def install_fake_server(server_dir: str) -> None:
    os.makedirs(server_dir, exist_ok=True)
    exe = os.path.join(server_dir, "bedrock_server")
    with open(exe, "w") as fh:
        fh.write(FAKE_BDS)
    os.chmod(exe, 0o755)
    with open(os.path.join(server_dir, "blockhost-version.txt"), "w") as fh:
        fh.write("1.0.0.1")
    with open(os.path.join(server_dir, "server.properties"), "w") as fh:
        fh.write("# test\nlevel-name=level\ngamemode=survival\n")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    def __init__(self, base: str):
        self.base = base
        self.cookie = ""

    def req(self, method: str, path: str, body=None, *, header=True):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method)
        r.add_header("Content-Type", "application/json")
        if header:
            r.add_header("X-Blockhost", "1")
        if self.cookie:
            r.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                sc = resp.headers.get("Set-Cookie")
                if sc:
                    self.cookie = sc.split(";", 1)[0]
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


@pytest.fixture()
def running_app(tmp_path):
    """Blockhost serving a fake Bedrock server on a random port."""
    from blockhost import app
    from blockhost.config import Config

    cfg = Config(str(tmp_path / "data"))
    install_fake_server(cfg.server_dir)
    port = free_port()
    holder = {}
    t = threading.Thread(target=app.serve, args=(cfg, "127.0.0.1", port),
                         kwargs={"on_ready": lambda h: holder.setdefault("httpd", h)}, daemon=True)
    t.start()
    for _ in range(100):
        if "httpd" in holder and app.SERVER.ready:
            break
        time.sleep(0.1)
    yield app, Client(f"http://127.0.0.1:{port}"), cfg
    holder["httpd"].shutdown()
    app.SERVER.stop()
