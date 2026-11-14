"""Command line entry point: `blockhost` (or `python -m blockhost`)."""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import webbrowser

from . import __version__, app, bds
from .config import Config, default_data_dir


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y")


def _windows_console_close_saves_world() -> None:
    """Closing the console window, logging off or shutting down: stop the server cleanly
    (Windows allows a few seconds for this) instead of killing it mid-save."""
    import ctypes
    from ctypes import wintypes

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    def handler(event: int) -> bool:
        if event in (2, 5, 6):  # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT
            if app.SERVER is not None:
                app.SERVER.stop(wait_seconds=4)
            return True
        return False  # Ctrl+C / Ctrl+Break: Python's KeyboardInterrupt handles those

    _windows_console_close_saves_world.handler = handler  # keep the callback alive
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="blockhost", description="Host and manage a Minecraft Bedrock Edition server.")
    p.add_argument("--data-dir", default=default_data_dir(), help="where worlds, mods and settings live (env BLOCKHOST_DATA)")
    p.add_argument("--host", default=os.environ.get("BLOCKHOST_HOST", "127.0.0.1"),
                   help="address for the web page; 0.0.0.0 to allow other devices (env BLOCKHOST_HOST)")
    p.add_argument("--port", type=int, default=int(os.environ.get("BLOCKHOST_PORT", "8080")), help="web page port (env BLOCKHOST_PORT)")
    p.add_argument("--accept-eula", action="store_true", default=_env_bool("BLOCKHOST_ACCEPT_EULA"),
                   help="accept the Minecraft EULA and download the server without asking (env BLOCKHOST_ACCEPT_EULA=true)")
    p.add_argument("--auth", choices=("password", "none"), default=os.environ.get("BLOCKHOST_AUTH", "password"),
                   help="'none' only behind your own login proxy (env BLOCKHOST_AUTH)")
    p.add_argument("--no-browser", action="store_true", default=_env_bool("BLOCKHOST_NO_BROWSER"),
                   help="don't open a browser window on start")
    p.add_argument("--no-autostart", action="store_true", help="don't start the Bedrock server automatically")
    p.add_argument("--version", action="version", version=f"blockhost {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    cfg = Config(args.data_dir)

    env_pw = os.environ.get("BLOCKHOST_PASSWORD", "")
    if env_pw and not cfg.get("password_hash"):
        from .auth import MIN_PASSWORD, hash_password
        if len(env_pw) < MIN_PASSWORD:
            sys.exit(f"BLOCKHOST_PASSWORD must be at least {MIN_PASSWORD} characters")
        cfg.set(password_hash=hash_password(env_pw))
    if args.accept_eula:
        cfg.set(eula_accepted=True)

    # Docker/Kubernetes send SIGTERM; turn it into a clean shutdown so the world is saved.
    def _term(_sig, _frame):
        raise KeyboardInterrupt
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _term)
    if sys.platform == "win32":
        _windows_console_close_saves_world()

    if args.auth == "none" and args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: --auth none with a network address. Anyone who can reach this port controls your server.\n"
              "         Only do this behind a reverse proxy that does its own login.", flush=True)

    def on_ready(httpd) -> None:
        shown = "localhost" if args.host in ("0.0.0.0", "::", "127.0.0.1") else args.host
        url = f"http://{shown}:{args.port}/"
        print(f"Blockhost {__version__}", flush=True)
        print(f"  Data folder: {cfg.data_dir}", flush=True)
        print(f"  Open:        {url}", flush=True)
        reason = bds.platform_supported()
        if reason:
            print(f"  NOTE: {reason}", flush=True)
        code = app.AUTH.setup_token
        if code:
            print(f"  Setup code:  {code}   (enter it on the page to create your admin password)", flush=True)
            path = os.path.join(cfg.data_dir, "setup-code.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(code + "\n")
            if sys.platform != "win32":
                os.chmod(path, 0o600)
        else:
            try:
                os.unlink(os.path.join(cfg.data_dir, "setup-code.txt"))
            except OSError:
                pass
        if cfg.get("eula_accepted") and not bds.is_installed(cfg.server_dir) and not reason:
            if app.op_start("install server"):
                threading.Thread(target=app.run_install, daemon=True).start()
                print("  Downloading the Bedrock server from minecraft.net...", flush=True)
        if not args.no_browser:
            target = url + (f"?setup={code}" if code else "")
            threading.Timer(0.8, lambda: webbrowser.open(target)).start()
        print("Press Ctrl+C to stop.", flush=True)

    app.serve(cfg, args.host, args.port, auth_mode=args.auth, autostart=not args.no_autostart, on_ready=on_ready)


if __name__ == "__main__":
    main()
