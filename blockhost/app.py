"""Blockhost web app: world library, mods, players and live controls for a
Minecraft Bedrock Dedicated Server that Blockhost runs as a child process.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import bds
from . import mod_settings as ms
from . import network
from .tunnel import PlayitTunnel
from .auth import Auth, cookie_header, read_cookie
from .config import Config

LEVEL_NAME = bds.LEVEL_NAME
ROLES = ("visitor", "member", "operator")
MAX_UPLOAD = 8 * 1024**3

# Set by configure(); module-level so the many helpers below can use them directly.
CFG: Config = None  # type: ignore[assignment]
AUTH: Auth = None  # type: ignore[assignment]
SERVER: bds.BedrockProcess = None  # type: ignore[assignment]
SERVER_DATA = LIBRARY = ACTIVE_WORLD_DIR = ACTIVE_POINTER = TMP_DIR = PACKS_DIR = ""
ALLOWLIST_PATH = PERMISSIONS_PATH = ""
TWEAKS: ms.Store = None  # type: ignore[assignment]
NET: network.PortForwarder = None  # type: ignore[assignment]
TUNNEL: PlayitTunnel = None  # type: ignore[assignment]


def configure(cfg: Config, auth_mode: str = "password") -> None:
    global CFG, AUTH, SERVER, SERVER_DATA, LIBRARY, ACTIVE_WORLD_DIR, ACTIVE_POINTER, TMP_DIR
    global PACKS_DIR, ALLOWLIST_PATH, PERMISSIONS_PATH, TWEAKS, NET, TUNNEL
    CFG = cfg
    AUTH = Auth(cfg, mode=auth_mode)
    SERVER_DATA = cfg.server_dir
    LIBRARY = cfg.library_dir
    ACTIVE_WORLD_DIR = os.path.join(SERVER_DATA, "worlds", LEVEL_NAME)
    ACTIVE_POINTER = os.path.join(LIBRARY, ".active")
    TMP_DIR = os.path.join(LIBRARY, ".tmp")
    PACKS_DIR = os.path.join(LIBRARY, "packs")
    ALLOWLIST_PATH = os.path.join(SERVER_DATA, "allowlist.json")
    PERMISSIONS_PATH = os.path.join(SERVER_DATA, "permissions.json")
    TWEAKS = ms.Store(LIBRARY, PACKS_DIR, None, None)
    SERVER = bds.BedrockProcess(SERVER_DATA, os.path.join(cfg.logs_dir, "server.log"))
    NET = network.PortForwarder(server_port)
    TUNNEL = PlayitTunnel(cfg.data_dir, cfg.logs_dir, server_port)


# ---------------------------------------------------------------- server ---


def server_status() -> dict:
    return SERVER.status()


def server_replicas() -> tuple[int, int]:
    s = server_status()
    return s["want"], s["pods"]


def scale_server(replicas: int, *, wait_seconds: int = 180) -> str | None:
    """Kept from the Kubernetes days: 0 stops the server, 1 starts it."""
    if replicas == 0:
        return SERVER.stop()
    return SERVER.start(wait_seconds=wait_seconds)


def send_command(cmd: str) -> str | None:
    return SERVER.send(cmd)


# World-generation settings -> server.properties keys.
_SETTINGS_SPEC: dict[str, tuple[str, object]] = {
    "cheats": ("allow-cheats", {"true", "false"}),
    "gamemode": ("gamemode", {"survival", "creative", "adventure"}),
    "difficulty": ("difficulty", {"peaceful", "easy", "normal", "hard"}),
    "level_type": ("level-type", {"DEFAULT", "FLAT", "LEGACY"}),
    "view_distance": ("view-distance", (5, 64)),
    "tick_distance": ("tick-distance", (4, 12)),
    "max_players": ("max-players", (1, 50)),
}


def validate_settings(data: dict) -> tuple[dict[str, str], str | None]:
    env: dict[str, str] = {}
    for key, (var, rule) in _SETTINGS_SPEC.items():
        if key not in data or data[key] in (None, ""):
            continue
        val = str(data[key]).strip()
        if isinstance(rule, set):
            if val not in rule:
                return {}, f"{key} must be one of {sorted(rule)}"
        else:
            lo, hi = rule
            if not val.lstrip("-").isdigit() or not lo <= int(val) <= hi:
                return {}, f"{key} must be {lo}-{hi}"
            val = str(int(val))
        env[var] = val
    return env, None


def set_server_env(env: dict[str, str | None]) -> str | None:
    """Write server.properties values (applied on next server start)."""
    if not env:
        return None
    try:
        bds.Properties(SERVER_DATA).update(env)
    except (OSError, ValueError) as exc:
        return f"failed to update server settings: {exc}"
    _ENV_C.bust()
    return None


# Bedrock game rules (per-cheat toggles). (kind, vanilla default).
_GAMERULES: dict[str, tuple[str, str]] = {
    "showcoordinates": ("bool", "false"),
    "showdaysplayed": ("bool", "false"),
    "keepinventory": ("bool", "false"),
    "mobgriefing": ("bool", "true"),
    "dodaylightcycle": ("bool", "true"),
    "doweathercycle": ("bool", "true"),
    "domobspawning": ("bool", "true"),
    "domobloot": ("bool", "true"),
    "dotiledrops": ("bool", "true"),
    "doentitydrops": ("bool", "true"),
    "dofiretick": ("bool", "true"),
    "tntexplodes": ("bool", "true"),
    "respawnblocksexplode": ("bool", "true"),
    "naturalregeneration": ("bool", "true"),
    "pvp": ("bool", "true"),
    "falldamage": ("bool", "true"),
    "firedamage": ("bool", "true"),
    "drowningdamage": ("bool", "true"),
    "freezedamage": ("bool", "true"),
    "doinsomnia": ("bool", "true"),
    "doimmediaterespawn": ("bool", "false"),
    "showdeathmessages": ("bool", "true"),
    "sendcommandfeedback": ("bool", "true"),
    "commandblockoutput": ("bool", "true"),
    "commandblocksenabled": ("bool", "true"),
    "showtags": ("bool", "true"),
    "recipesunlock": ("bool", "true"),
    "dolimitedcrafting": ("bool", "false"),
    "projectilescanbreakblocks": ("bool", "true"),
    "tntexplosiondropdecay": ("bool", "false"),
    "randomtickspeed": ("int", "1"),
    "spawnradius": ("int", "10"),
    "maxcommandchainlength": ("int", "65536"),
    "functioncommandlimit": ("int", "10000"),
    "playerssleepingpercentage": ("int", "100"),
}

_INT_RANGES: dict[str, tuple[int, int]] = {
    "randomtickspeed": (0, 4096),
    "spawnradius": (0, 128),
    "maxcommandchainlength": (1, 65536),
    "functioncommandlimit": (1, 1000000),
    "playerssleepingpercentage": (0, 100),
}


def validate_gamerules(data: dict) -> tuple[dict[str, str], str | None]:
    rules: dict[str, str] = {}
    for key, raw in (data or {}).items():
        key = str(key).lower()
        if key not in _GAMERULES:
            return {}, f"unknown game rule: {key}"
        kind, default = _GAMERULES[key]
        val = str(raw).strip().lower()
        if kind == "bool":
            if val not in ("true", "false"):
                return {}, f"{key} must be true/false"
        else:
            lo, hi = _INT_RANGES.get(key, (0, 4096))
            if not val.isdigit() or not lo <= int(val) <= hi:
                return {}, f"{key} must be {lo}-{hi}"
        if val != default:
            rules[key] = val
    return rules, None


def apply_gamerules(rules: dict[str, str]) -> str | None:
    errs = []
    for key, val in rules.items():
        err = send_command(f"gamerule {key} {val}")
        if err:
            errs.append(f"{key}: {err}")
        time.sleep(0.3)
    return "; ".join(errs) if errs else None


# ------------------------------------------------------------- live world ---

_CONSOLE_LOCK = threading.Lock()


def console_command(cmd: str, *, settle: float = 0.8, tail: int = 60) -> tuple[list[str], str | None]:
    """Send one console command and return the log lines it produced."""
    with _CONSOLE_LOCK:
        mark = SERVER.seq
        err = send_command(cmd)
        if err:
            return [], err
        time.sleep(settle)
        new = SERVER.since(mark)[-tail:]
    out = [re.sub(r"^\S+ ", "", ln, count=1) for ln in new]
    out = [re.sub(r"^\[[^\]]*\]\s*", "", ln) for ln in out]
    return [ln for ln in out if ln.strip() and not ln.startswith("[Blockhost]")], None


_RULE_ECHO_RE = re.compile(r"^(\w+)\s*=\s*(\w+)\s*$")
_TIME_RE = re.compile(r"(?:Daytime|Day time|Time) is (\d+)", re.I)
_DAYS_RE = re.compile(r"Day is (\d+)", re.I)


def read_live_gamerules_and_time() -> tuple[dict[str, str], int | None, int | None]:
    # BDS drops echoes when commands arrive faster than it logs them; 40ms pacing keeps every line.
    with _CONSOLE_LOCK:
        for k in _GAMERULES:
            send_command(f"gamerule {k}")
            time.sleep(0.04)
        send_command("time query daytime")
        time.sleep(0.04)
        send_command("time query day")
        time.sleep(0.5)
        text = SERVER.tail_text(len(_GAMERULES) * 2 + 60)
    values: dict[str, str] = {}
    t = d = None
    for ln in text.splitlines():
        ln = re.sub(r"^\[[^\]]*\]\s*", "", ln).strip()
        m = _RULE_ECHO_RE.match(ln)
        if m and m.group(1).lower() in _GAMERULES:
            values[m.group(1).lower()] = m.group(2).lower()
            continue
        m = _TIME_RE.search(ln)
        if m:
            t = int(m.group(1))
            continue
        m = _DAYS_RE.search(ln)
        if m:
            d = int(m.group(1))
    return values, t, d


# ---------------------------------------------------------------- caches ---

class _TTL:
    def __init__(self, ttl: float):
        self.ttl, self.at, self.val = ttl, 0.0, None
        self.lock = threading.Lock()

    def get(self, fn):
        with self.lock:
            if self.val is not None and time.time() - self.at < self.ttl:
                return self.val
        v = fn()
        with self.lock:
            self.val, self.at = v, time.time()
        return v

    def bust(self):
        with self.lock:
            self.at = 0.0


_STATUS_C = _TTL(3)
_ENV_C = _TTL(30)
_ALLOW_C = _TTL(10)
_WORLD_C = _TTL(20)
_DASH_C = _TTL(10)


def server_status_cached() -> dict:
    return _STATUS_C.get(server_status)


def deploy_env_props() -> dict[str, str]:
    def load():
        props = bds.Properties(SERVER_DATA).read()
        return {key: props[var] for key, (var, _rule) in _SETTINGS_SPEC.items() if var in props}
    return _ENV_C.get(load)


def read_world_state() -> dict:
    def load():
        out: dict = {"gamerules": {}, "time": None, "day": None, "props": {}}
        out["online"] = server_status_cached()["ready"] > 0
        if out["online"]:
            out["gamerules"], out["time"], out["day"] = read_live_gamerules_and_time()
        out["props"] = deploy_env_props()
        out["fetched_at"] = time.time()
        return out
    return _WORLD_C.get(load)


_WEATHER = {"clear", "rain", "thunder"}
_TIMES = {"day", "noon", "sunset", "night", "midnight", "sunrise"}
_DIFFICULTIES = {"peaceful", "easy", "normal", "hard"}
_GAMEMODES = {"survival", "creative", "adventure", "spectator"}


def set_world_time(value: str) -> str | None:
    v = value.strip().lower()
    if v not in _TIMES and not (v.isdigit() and 0 <= int(v) <= 24000):
        return "time must be day/noon/sunset/night/midnight/sunrise or 0-24000"
    return send_command(f"time set {v}")


def set_weather(value: str, duration: str = "") -> str | None:
    v = value.strip().lower()
    if v not in _WEATHER:
        return "weather must be clear/rain/thunder"
    cmd = f"weather {v}"
    if duration.strip():
        if not duration.strip().isdigit() or not 1 <= int(duration) <= 1000000:
            return "duration must be 1-1000000 seconds"
        cmd += f" {int(duration)}"
    return send_command(cmd)


def set_difficulty(value: str) -> str | None:
    v = value.strip().lower()
    if v not in _DIFFICULTIES:
        return "difficulty must be peaceful/easy/normal/hard"
    err = send_command(f"difficulty {v}")
    if err:
        return err
    return set_server_env({"difficulty": v})


def set_default_gamemode(value: str) -> str | None:
    v = value.strip().lower()
    if v not in _GAMEMODES - {"spectator"}:
        return "gamemode must be survival/creative/adventure"
    # Default gamemode is a server.properties value; takes effect after a restart.
    return set_server_env({"gamemode": v})


def set_player_gamemode(name: str, value: str) -> str | None:
    name = name.strip()
    v = value.strip().lower()
    if not name or len(name) > 32 or not re.fullmatch(r"[A-Za-z0-9 _\-]+", name):
        return "invalid gamertag"
    if v not in _GAMEMODES:
        return "gamemode must be survival/creative/adventure/spectator"
    lines, err = console_command(f'gamemode {v} "{name}"')
    if err:
        return err
    bad = [ln for ln in lines if "No targets matched" in ln or "Syntax error" in ln]
    return bad[0] if bad else None


_CONSOLE_BLOCKLIST = ("stop", "save", "op ", "deop", "allowlist", "whitelist", "permission",
                      "changesetting", "reload", "script")


def run_console(cmd: str) -> tuple[list[str], str | None]:
    cmd = cmd.strip().lstrip("/")
    if not cmd:
        return [], "command is empty"
    if len(cmd) > 512:
        return [], "command too long"
    if "\n" in cmd or "\r" in cmd:
        return [], "one command at a time"
    low = cmd.lower()
    for blocked in _CONSOLE_BLOCKLIST:
        if low == blocked.strip() or low.startswith(blocked):
            return [], f"'{blocked.strip()}' is managed elsewhere in this panel"
    return console_command(cmd, settle=1.0)


# ---------------------------------------------------------------- library ---


def chown_tree(path: str) -> None:
    """Blockhost runs the server as its own user, so ownership is already right."""
    return None


_SIZE_CACHE: dict[str, tuple[float, int]] = {}


def dir_size(path: str) -> int:
    hit = _SIZE_CACHE.get(path)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    _SIZE_CACHE[path] = (time.time(), total)
    return total


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-").lower()
    return slug[:48] or "world"


def entry_path(entry_id: str) -> str:
    safe = os.path.basename(entry_id)
    return os.path.join(LIBRARY, "worlds", safe)


def read_meta(entry_id: str) -> dict:
    try:
        with open(os.path.join(entry_path(entry_id), ".library.json")) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def write_meta(entry_id: str, meta: dict) -> None:
    with open(os.path.join(entry_path(entry_id), ".library.json"), "w") as fh:
        json.dump(meta, fh)


def get_active_id() -> str | None:
    try:
        with open(ACTIVE_POINTER) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def set_active_id(entry_id: str | None) -> None:
    if entry_id is None:
        try:
            os.unlink(ACTIVE_POINTER)
        except OSError:
            pass
    else:
        with open(ACTIVE_POINTER, "w") as fh:
            fh.write(entry_id)


def list_worlds() -> list[dict]:
    worlds_root = os.path.join(LIBRARY, "worlds")
    os.makedirs(worlds_root, exist_ok=True)
    active = get_active_id()
    out = []
    for entry_id in sorted(os.listdir(worlds_root)):
        path = os.path.join(worlds_root, entry_id)
        if not os.path.isdir(path):
            continue
        meta = read_meta(entry_id)
        out.append(
            {
                "id": entry_id,
                "name": meta.get("name", entry_id),
                "created": meta.get("created", ""),
                "source": meta.get("source", "unknown"),
                "seed": meta.get("seed", ""),
                "size": dir_size(path),
                "active": entry_id == active,
            }
        )
    return out


def new_entry_id(name: str) -> str:
    base = slugify(name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    entry_id = f"{base}-{stamp}"
    os.makedirs(entry_path(entry_id), exist_ok=False)
    return entry_id


def find_world_root(extract_dir: str) -> str | None:
    """Locate the directory containing level.dat (shallowest match)."""
    best: str | None = None
    for root, _dirs, files in os.walk(extract_dir):
        if "level.dat" in files:
            if best is None or root.count(os.sep) < best.count(os.sep):
                best = root
    return best


def import_mcworld(tmp_zip: str, name: str) -> tuple[str | None, str | None]:
    """Extract an uploaded .mcworld/.zip into a new library entry."""
    extract_dir = os.path.join(TMP_DIR, f"extract-{int(time.time()*1000)}")
    os.makedirs(extract_dir, exist_ok=True)
    try:
        try:
            with zipfile.ZipFile(tmp_zip) as zf:
                total = sum(i.file_size for i in zf.infolist())
                if total > MAX_UPLOAD:
                    return None, "archive expands too large"
                for info in zf.infolist():
                    target = os.path.realpath(os.path.join(extract_dir, info.filename))
                    if not target.startswith(os.path.realpath(extract_dir) + os.sep) and target != os.path.realpath(extract_dir):
                        return None, "archive contains unsafe paths"
                zf.extractall(extract_dir)
        except zipfile.BadZipFile:
            return None, "not a valid .mcworld/.zip archive"
        world_root = find_world_root(extract_dir)
        if not world_root:
            return None, "no level.dat found in the archive. Is this a Bedrock world export?"
        entry_id = new_entry_id(name)
        dest = entry_path(entry_id)
        shutil.rmtree(dest)
        shutil.copytree(world_root, dest)
        write_meta(
            entry_id,
            {"name": name, "created": datetime.now(timezone.utc).isoformat(), "source": "upload"},
        )
        return entry_id, None
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def world_display_name(world_dir: str, fallback: str) -> str:
    try:
        with open(os.path.join(world_dir, "levelname.txt")) as fh:
            name = fh.read().strip()
            return name or fallback
    except OSError:
        return fallback


def copy_world(src: str, dst: str) -> None:
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".library.json"))


def snapshot_active_to_library() -> str | None:
    """Sync the live world dir back to its library slot (or a new one)."""
    if not os.path.isdir(ACTIVE_WORLD_DIR):
        return None
    active = get_active_id()
    if active and os.path.isdir(entry_path(active)):
        meta = read_meta(active)
        copy_world(ACTIVE_WORLD_DIR, entry_path(active))
        write_meta(active, meta or {"name": active, "source": "snapshot", "created": datetime.now(timezone.utc).isoformat()})
        return active
    name = world_display_name(ACTIVE_WORLD_DIR, "recovered-world")
    entry_id = new_entry_id(name)
    copy_world(ACTIVE_WORLD_DIR, entry_path(entry_id))
    write_meta(
        entry_id,
        {"name": name, "created": datetime.now(timezone.utc).isoformat(), "source": "snapshot"},
    )
    return entry_id


# ------------------------------------------------------------- operations ---

OP_LOCK = threading.Lock()
OP: dict = {"running": False, "kind": "", "step": "", "error": "", "done_at": 0.0}


def op_update(**kw) -> None:
    with OP_LOCK:
        OP.update(kw)


def op_start(kind: str) -> bool:
    with OP_LOCK:
        if OP["running"]:
            return False
        OP.update({"running": True, "kind": kind, "step": "starting", "error": ""})
        return True


def op_finish(error: str = "") -> None:
    with OP_LOCK:
        OP.update({"running": False, "step": "done" if not error else "failed", "error": error, "done_at": time.time()})


def run_activate(entry_id: str) -> None:
    try:
        src = entry_path(entry_id)
        if not os.path.isdir(src):
            op_finish(f"world {entry_id!r} not found")
            return
        op_update(step="stopping server")
        err = scale_server(0)
        if err:
            op_finish(err)
            return
        op_update(step="saving current world to library")
        snapshot_active_to_library()
        op_update(step="installing selected world")
        copy_world(src, ACTIVE_WORLD_DIR)
        chown_tree(ACTIVE_WORLD_DIR)
        set_active_id(entry_id)
        saved = read_meta(entry_id).get("settings") or {}
        env, _err = validate_settings(saved)
        if env:
            op_update(step="applying world settings")
            set_server_env(env)
        op_update(step="starting server")
        err = scale_server(1)
        if not err:
            saved_rules, _e = validate_gamerules(read_meta(entry_id).get("gamerules") or {})
            if saved_rules:
                op_update(step="applying game rules")
                time.sleep(5)
                apply_gamerules(saved_rules)
        op_finish(err or "")
    except Exception as exc:  # noqa: BLE001, surface any failure to the UI
        op_finish(f"unexpected error: {exc}")
        scale_server(1)


def run_new_seed(name: str, seed: str, settings: dict, packs: set[str], gamerules: dict[str, str]) -> None:
    try:
        op_update(step="stopping server")
        err = scale_server(0)
        if err:
            op_finish(err)
            return
        op_update(step="saving current world to library")
        snapshot_active_to_library()
        op_update(step="configuring world settings")
        env, verr = validate_settings(settings)
        env["level-seed"] = seed if seed else None
        err = verr or set_server_env(env)
        if err:
            op_finish(err)
            scale_server(1)
            return
        if os.path.isdir(ACTIVE_WORLD_DIR):
            shutil.rmtree(ACTIVE_WORLD_DIR)
        if packs:
            # pre-seed pack manifests so the world generates with add-ons active
            op_update(step="installing add-ons")
            os.makedirs(ACTIVE_WORLD_DIR, exist_ok=True)
            apply_world_packs(ACTIVE_WORLD_DIR, packs)
        entry_id = new_entry_id(name)
        write_meta(
            entry_id,
            {
                "name": name,
                "created": datetime.now(timezone.utc).isoformat(),
                "source": "generated",
                "seed": seed,
                "settings": settings,
                "gamerules": gamerules,
            },
        )
        set_active_id(entry_id)
        op_update(step="starting server (generating world)")
        err = scale_server(1, wait_seconds=300)
        if not err and gamerules:
            op_update(step="applying game rules")
            time.sleep(5)
            gerr = apply_gamerules(gamerules)
            if gerr:
                err = f"world created, but some game rules failed: {gerr}"
        op_finish(err or "")
    except Exception as exc:  # noqa: BLE001
        op_finish(f"unexpected error: {exc}")
        scale_server(1)


# ------------------------------------------------------------- dashboard ---

_VERSION_CACHE = {"v": ""}


def _human_age(start_iso: str) -> str:
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    secs = int((datetime.now(timezone.utc) - start).total_seconds())
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return (f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m")


def read_dashboard() -> dict:
    st = server_status_cached()
    out = {"server": st, "players": [], "player_count": None, "max_players": None,
           "version": _VERSION_CACHE["v"], "uptime": "", "pod": {}, "node": {}, "world": {}}
    if SERVER.started_at and SERVER.running():
        out["uptime"] = _human_age(datetime.fromtimestamp(SERVER.started_at, timezone.utc).isoformat())
    if st["ready"] > 0:
        lines, _err = console_command("list", settle=0.5)
        for idx in range(len(lines) - 1, -1, -1):
            m = re.search(r"There are (\d+)/(\d+) players online", lines[idx])
            if m:
                out["player_count"], out["max_players"] = int(m.group(1)), int(m.group(2))
                if idx + 1 < len(lines):
                    names = lines[idx + 1].strip()
                    if names and "players online" not in names:
                        out["players"] = [n.strip() for n in names.split(",") if n.strip()][:20]
                break
    out["version"] = bds.installed_version(SERVER_DATA)
    out.update(SERVER.metrics())
    active = get_active_id()
    if active:
        meta = read_meta(active)
        out["world"] = {"name": meta.get("name", active),
                        "size": dir_size(ACTIVE_WORLD_DIR) if os.path.isdir(ACTIVE_WORLD_DIR) else 0}
    return out


# --------------------------------------------------------------- network ---


def server_port() -> int:
    try:
        port = int(bds.Properties(SERVER_DATA).read().get("server-port", network.DEFAULT_PORT))
    except (ValueError, OSError):
        return network.DEFAULT_PORT
    return port if 1 <= port <= 65535 else network.DEFAULT_PORT


def split_address(address: str, default_port: int) -> tuple[str, int]:
    """'host:port' -> (host, port). A bare host gets default_port."""
    host, sep, port = (address or "").strip().rpartition(":")
    if sep and port.isdigit() and host:
        return host, int(port)
    return (address or "").strip(), default_port


_ADDR_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?")


def validate_external_address(value: str) -> tuple[str, str | None]:
    value = (value or "").strip()
    if not value:
        return "", None
    host, port = split_address(value, server_port())
    if not _ADDR_RE.fullmatch(host) or not 1 <= port <= 65535:
        return "", "enter a host name or IP address, optionally with :port"
    return f"{host}:{port}", None


def network_info() -> dict:
    port = server_port()
    lan = network.lan_ip()
    fw = NET.snapshot()
    tn = TUNNEL.snapshot()
    manual = CFG.get("external_address", "") or ""
    local = {"host": lan or "", "port": port,
             "container": network.in_container() and not os.environ.get("BLOCKHOST_LAN_ADDRESS")}
    external: dict = {"host": "", "port": 0, "source": "", "note": ""}
    if manual:
        external["host"], external["port"] = split_address(manual, port)
        external["source"] = "manual"
    elif tn.get("status") == "online" and tn.get("address"):
        external["host"], external["port"] = split_address(tn["address"], port)
        external["source"] = "playit"
    elif fw.get("status") == "open" and fw.get("public_ip"):
        external.update(host=fw["public_ip"], port=fw.get("public_port") or port, source="upnp")
    elif fw.get("status") == "cgnat":
        external["note"] = ("Your internet provider shares one public address between customers (CGNAT), "
                            "so port forwarding can't reach you. Use the playit.gg tunnel instead.")
    return {"local": local, "external": external, "upnp": {**fw, "enabled": bool(CFG.get("upnp_enabled"))},
            "tunnel": {**tn, "enabled": bool(CFG.get("playit_enabled"))}, "manual": manual}


# -------------------------------------------------------------- allowlist ---


def _read_text(path: str) -> tuple[str, str | None]:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip(), None
    except FileNotFoundError:
        return "", None
    except OSError as exc:
        return "", f"failed to read {os.path.basename(path)}: {exc}"


def _write_bytes(path: str, payload: bytes) -> str | None:
    try:
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except OSError as exc:
        return f"failed to write {os.path.basename(path)}: {exc}"
    return None


def read_allowlist() -> tuple[list[str], str | None]:
    entries, err = allowlist_entries()
    if err:
        return [], err
    names = [str(e["name"]) for e in entries]
    return sorted(set(names), key=str.lower), None


# gamertag(lower) -> xuid, learned from allowlist entries and server logs
_XUID_CACHE: dict[str, str] = {}


def allowlist_entries() -> tuple[list[dict], str | None]:
    raw, err = _read_text(ALLOWLIST_PATH)
    if err:
        return [], err
    if not raw or raw == "null":
        return [], None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], f"invalid allowlist JSON: {exc}"
    entries = [e for e in data if isinstance(e, dict) and e.get("name")]
    for e in entries:
        if e.get("xuid"):
            _XUID_CACHE[str(e["name"]).lower()] = str(e["xuid"])
    return entries, None


def write_allowlist(names: list[str]) -> str | None:
    payload = json.dumps([{"name": n, "ignoresPlayerLimit": False} for n in names], separators=(",", ":")).encode()
    err = _write_bytes(ALLOWLIST_PATH, payload)
    if err:
        return err
    if SERVER.running():
        send_command("allowlist reload")
    return None


def add_player(name: str) -> str | None:
    name = name.strip()
    if not name:
        return "gamertag is required"
    if len(name) > 32:
        return "gamertag too long (max 32)"
    if not re.fullmatch(r"[A-Za-z0-9 _\-]+", name):
        return "invalid gamertag"
    names, err = read_allowlist()
    if err:
        return err
    if name not in names:
        names.append(name)
        return write_allowlist(names)
    return None


def remove_player(name: str) -> str | None:
    name = name.strip()
    if not name:
        return "gamertag is required"
    names, err = read_allowlist()
    if err:
        return err
    filtered = [n for n in names if n.lower() != name.lower()]
    if len(filtered) == len(names):
        return f"{name!r} is not on the allow list"
    return write_allowlist(filtered)


# ------------------------------------------------------------ player roles ---


def read_permissions() -> tuple[list[dict], str | None]:
    raw, err = _read_text(PERMISSIONS_PATH)
    if err:
        return [], err
    if not raw or raw == "null":
        return [], None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], f"invalid permissions JSON: {exc}"
    return [e for e in data if isinstance(e, dict) and e.get("xuid")], None


def player_roles_for(entries: list[dict]) -> dict[str, str]:
    """gamertag -> role for allowlisted players; no permissions entry = server default (member)."""
    perms, err = read_permissions()
    if err:
        return {}
    by_xuid = {str(p["xuid"]): str(p.get("permission", "member")) for p in perms}
    roles: dict[str, str] = {}
    for e in entries:
        name = str(e["name"])
        xuid = str(e.get("xuid") or _XUID_CACHE.get(name.lower(), ""))
        roles[name] = by_xuid.get(xuid, "member")
    return roles


_LOG_XUID_RE = re.compile(r"Player (?:connected|Spawned): (.+?),? xuid: (\d+)")


def _allowlist_and_roles() -> tuple[list[str], dict[str, str]]:
    entries, _err = allowlist_entries()
    names = sorted({str(e["name"]) for e in entries}, key=str.lower)
    return names, player_roles_for(entries)


def _xuid_from_logs(name: str) -> str | None:
    for m in _LOG_XUID_RE.finditer(SERVER.tail_text(0)):
        _XUID_CACHE[m.group(1).strip().lower()] = m.group(2)
    return _XUID_CACHE.get(name.lower())


def resolve_xuid(name: str) -> str | None:
    key = name.lower()
    if key not in _XUID_CACHE:
        allowlist_entries()  # refreshes cache from allowlist xuids
    if key not in _XUID_CACHE:
        _xuid_from_logs(name)
    return _XUID_CACHE.get(key)


def set_role(name: str, role: str) -> str | None:
    name = name.strip()
    if not name:
        return "gamertag is required"
    if role not in ROLES:
        return f"role must be one of {', '.join(ROLES)}"
    xuid = resolve_xuid(name)
    if not xuid:
        return f"no xuid known for {name!r} yet \u2014 they must join the server once first"
    perms, err = read_permissions()
    if err:
        return err
    perms = [p for p in perms if str(p.get("xuid")) != xuid]
    perms.append({"permission": role, "xuid": xuid})
    payload = json.dumps(perms, separators=(",", ":")).encode()
    err = _write_bytes(PERMISSIONS_PATH, payload)
    if err:
        return err
    if SERVER.running():
        send_command("permission reload")
    return None


# ------------------------------------------------------------ server log ---

_EVENT_RE = re.compile(r"Player (connected|disconnected|Spawned): (.+?)(?:,| xuid|$)")
_ISSUE_RE = re.compile(r"\b(WARN|ERROR)\]\s*(.+)")


def read_server_logs(tail: int = 400) -> dict:
    lines = SERVER.tail(tail)
    if not lines:
        return {"lines": [], "events": [], "issues": [], "error": "no server output yet (is the server running?)"}
    events = []
    issues: list[dict] = []
    seen_issues = set()
    for line in lines:
        m = _EVENT_RE.search(line)
        if m:
            kind = {"connected": "join", "disconnected": "leave", "Spawned": "spawn"}[m.group(1)]
            when = line.split(" ", 1)[0][:19].replace("T", " ")
            events.append({"time": when, "type": kind, "player": m.group(2).strip()})
        mi = _ISSUE_RE.search(line)
        if mi:
            msg = mi.group(2).strip()
            if msg not in seen_issues:
                seen_issues.add(msg)
                issues.append({"level": mi.group(1), "msg": msg[:300],
                               "time": line.split(" ", 1)[0][:19].replace("T", " ")})
    return {"lines": lines[-tail:], "events": events[-50:], "issues": issues[-30:], "error": ""}


# ------------------------------------------------------------ pack library ---

_WORLD_PACK_FILES = {"behavior": "world_behavior_packs.json", "resource": "world_resource_packs.json"}
_EMBED_DIRS = {"behavior": "behavior_packs", "resource": "resource_packs"}


def all_world_dirs() -> list[str]:
    """The running world plus every library copy (each holds embedded packs)."""
    out = [ACTIVE_WORLD_DIR] if os.path.isdir(ACTIVE_WORLD_DIR) else []
    for w in list_worlds():
        out.append(entry_path(w["id"]))
    return out


def norm_version(v) -> list[int]:
    """Mod manifests often use "1.0.31" strings; BDS wants [1,0,31]."""
    if isinstance(v, list):
        nums = [int(x) for x in v if isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit())]
        if nums:
            return (nums + [0, 0, 0])[:3]
    if isinstance(v, (str, int, float)):
        parts = re.findall(r"\d+", str(v))
        if parts:
            return ([int(x) for x in parts] + [0, 0, 0])[:3]
    return [1, 0, 0]


def load_pack_manifest(pack_dir: str) -> dict | None:
    try:
        with open(os.path.join(pack_dir, "manifest.json"), encoding="utf-8-sig", errors="replace") as fh:
            return json.loads(fh.read())
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def pack_type_of(manifest: dict) -> str:
    for mod in manifest.get("modules", []):
        t = str(mod.get("type", "")).lower()
        if t == "resources":
            return "resource"
        if t in ("data", "script", "javascript"):
            return "behavior"
    return "behavior"


def pack_display_name(pack_dir: str, manifest: dict) -> str:
    name = str(manifest.get("header", {}).get("name", "")).strip()
    if name and not name.startswith("pack."):
        return name
    try:
        with open(os.path.join(pack_dir, "texts", "en_US.lang"), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip().startswith("pack.name"):
                    return line.split("=", 1)[1].strip() or name or os.path.basename(pack_dir)
    except OSError:
        pass
    return name or os.path.basename(pack_dir)


def pack_dependencies(manifest: dict) -> list[str]:
    return [
        str(d.get("uuid", "")).lower()
        for d in manifest.get("dependencies", [])
        if isinstance(d, dict) and d.get("uuid")
    ]


_PACKS_C: dict = {"sig": None, "val": None, "at": 0.0}
_PACKS_LOCK = threading.Lock()


def _packs_signature() -> tuple:
    try:
        names = sorted(os.listdir(PACKS_DIR))
    except OSError:
        return ()
    return (os.stat(PACKS_DIR).st_mtime_ns, tuple(names))


def list_packs() -> list[dict]:
    # Re-walk only when the packs dir changed or every 60s (world assignments affect in_use).
    sig = _packs_signature()
    with _PACKS_LOCK:
        if _PACKS_C["val"] is not None and _PACKS_C["sig"] == sig and time.time() - _PACKS_C["at"] < 60:
            return _PACKS_C["val"]
    val = _list_packs_uncached()
    with _PACKS_LOCK:
        _PACKS_C.update(sig=sig, val=val, at=time.time())
    return val


def bust_packs_cache() -> None:
    with _PACKS_LOCK:
        _PACKS_C["at"] = 0.0


def _list_packs_uncached() -> list[dict]:
    os.makedirs(PACKS_DIR, exist_ok=True)
    out = []
    deps_map: dict[str, list[str]] = {}
    for pid in sorted(os.listdir(PACKS_DIR)):
        pack_dir = os.path.join(PACKS_DIR, pid)
        man = load_pack_manifest(pack_dir)
        if not man:
            continue
        h = man.get("header", {})
        uuid_ = str(h.get("uuid", pid)).lower()
        deps_map[uuid_] = pack_dependencies(man)
        out.append({
            "uuid": uuid_,
            "name": pack_display_name(pack_dir, man),
            "type": pack_type_of(man),
            "version": norm_version(h.get("version")),
            "min_engine": norm_version(h.get("min_engine_version")) if h.get("min_engine_version") else None,
            "size": dir_size(pack_dir),
        })
    have = set(deps_map)
    for p in out:
        p["missing_deps"] = [u for u in deps_map.get(p["uuid"], []) if u not in have]
    mark_superseded(out)
    return out


def pack_family_key(name: str, ptype: str) -> str:
    """Same mod re-uploaded under a new uuid: match on colour-stripped, version-stripped name."""
    n = re.sub(r"\u00a7.", "", name).lower()
    n = re.sub(r"[`'\u2019]s\b", "s", n)  # farmer's / farmer`s -> farmers
    n = re.sub(r"\b(v|ver|version)?\s*\d+(\.\d+)*[a-z]?\b", " ", n)  # drop version tokens
    n = re.sub(r"\[(bp|rp|b|r|behavior|resource)[^\]]*\]", " ", n)
    n = re.sub(r"\b(bp|rp|beh|res|behavior pack|resource pack|behaviorpack|resourcepack|texture pack|texturepack|resources?|stable|hotfix|add[\s-]?on|addon)\b", " ", n)
    n = re.sub(r"[^a-z0-9]+", " ", n).strip()
    return f"{ptype}:{n}"


def packs_in_use() -> set[str]:
    used: set[str] = set()
    for w in list_worlds():
        for lst in read_world_pack_entries(world_dir_for(w["id"])).values():
            for e in lst:
                pid = str(e.get("pack_id", "")).lower()
                if pid:
                    used.add(pid)
    return used


def mark_superseded(packs: list[dict]) -> None:
    """Flag every pack that has a newer same-family sibling; sort list by name."""
    fam: dict[str, list[dict]] = {}
    for p in packs:
        p["family"] = pack_family_key(p["name"], p["type"])
        p["mod"] = p["family"].split(":", 1)[1]  # type-agnostic key so BP/RP halves group together
        fam.setdefault(p["family"], []).append(p)
    used = packs_in_use()
    for group in fam.values():
        group.sort(key=lambda p: (p["version"], p["uuid"]), reverse=True)
        for idx, p in enumerate(group):
            p["superseded"] = idx > 0
            p["in_use"] = p["uuid"] in used
            p["siblings"] = len(group)
    packs.sort(key=lambda p: (re.sub(r"\u00a7.", "", p["name"]).lower(), p["type"], [-x for x in p["version"]]))


def remove_superseded_packs() -> tuple[list[str], list[str]]:
    """Delete older duplicate versions that no world references. Returns (removed, kept_in_use)."""
    removed: list[str] = []
    kept: list[str] = []
    for p in list_packs():
        if not p.get("superseded"):
            continue
        if p.get("in_use"):
            kept.append(f"{p['name']} {'.'.join(map(str, p['version']))}")
            continue
        target = os.path.join(PACKS_DIR, p["uuid"])
        if os.path.isdir(target):
            shutil.rmtree(target)
            removed.append(f"{p['name']} {'.'.join(map(str, p['version']))}")
    return removed, kept


def import_packs(tmp_zip: str) -> tuple[list[dict], str | None]:
    """Extract .mcaddon/.mcpack into the pack library. Returns per-pack results."""
    extract_dir = os.path.join(TMP_DIR, f"packs-{int(time.time()*1000)}")
    os.makedirs(extract_dir, exist_ok=True)
    try:
        try:
            with zipfile.ZipFile(tmp_zip) as zf:
                for info in zf.infolist():
                    target = os.path.realpath(os.path.join(extract_dir, info.filename))
                    if not target.startswith(os.path.realpath(extract_dir) + os.sep) and target != os.path.realpath(extract_dir):
                        return [], "archive contains unsafe paths"
                zf.extractall(extract_dir)
        except zipfile.BadZipFile:
            return [], "not a valid .mcaddon/.mcpack archive"
        # .mcaddon commonly nests .mcpack zips, expand one level
        for root, _dirs, files in os.walk(extract_dir):
            for f in files:
                if f.lower().endswith((".mcpack", ".zip")):
                    sub = os.path.join(root, f)
                    try:
                        with zipfile.ZipFile(sub) as zf:
                            zf.extractall(sub + ".x")
                    except zipfile.BadZipFile:
                        continue
        found: dict[str, tuple[str, dict]] = {}
        for root, _dirs, files in os.walk(extract_dir):
            if "manifest.json" not in files:
                continue
            man = load_pack_manifest(root)
            uuid_ = str((man or {}).get("header", {}).get("uuid", "")).lower()
            if man and uuid_:
                found[uuid_] = (root, man)
        if not found:
            return [], "no packs found (no manifest.json in archive)"
        existing = {p["uuid"]: p for p in list_packs()}
        results = []
        for uuid_, (root, man) in found.items():
            name = pack_display_name(root, man)
            version = norm_version(man.get("header", {}).get("version"))
            prior = existing.get(uuid_)
            if prior and list(prior.get("version", [])) == list(version):
                results.append({"name": name, "uuid": uuid_, "version": version, "status": "duplicate", "missing_deps": []})
                continue
            dest = os.path.join(PACKS_DIR, uuid_)
            if os.path.exists(dest):
                shutil.rmtree(dest)
            shutil.copytree(root, dest, ignore=shutil.ignore_patterns("*.mcpack", "*.zip", "*.x"))
            results.append({
                "name": name, "uuid": uuid_, "version": version,
                "status": "updated" if prior else "added", "missing_deps": [],
            })
        # dependency check against the post-import library
        have = {p["uuid"] for p in list_packs()}
        for r in results:
            man = load_pack_manifest(os.path.join(PACKS_DIR, r["uuid"]))
            if man:
                r["missing_deps"] = [u for u in pack_dependencies(man) if u not in have]
        return results, None
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def world_dir_for(entry_id: str) -> str:
    if entry_id == get_active_id() and os.path.isdir(ACTIVE_WORLD_DIR):
        return ACTIVE_WORLD_DIR
    return entry_path(entry_id)


def read_world_pack_entries(world_dir: str) -> dict[str, list[dict]]:
    out = {}
    for ptype, fname in _WORLD_PACK_FILES.items():
        try:
            with open(os.path.join(world_dir, fname)) as fh:
                data = json.load(fh)
            out[ptype] = data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            out[ptype] = []
    return out


def embedded_pack_uuids(world_dir: str) -> set[str]:
    found = set()
    for d in _EMBED_DIRS.values():
        base = os.path.join(world_dir, d)
        if not os.path.isdir(base):
            continue
        for sub in os.listdir(base):
            man = load_pack_manifest(os.path.join(base, sub))
            if man:
                found.add(str(man.get("header", {}).get("uuid", "")).lower())
    return found


def world_pack_summary(entry_id: str, have: set[str] | None = None) -> tuple[int, int]:
    world_dir = world_dir_for(entry_id)
    refs = {
        str(e.get("pack_id", "")).lower()
        for lst in read_world_pack_entries(world_dir).values()
        for e in lst
    }
    refs.discard("")
    if have is None:
        have = {p["uuid"] for p in list_packs()}
    have = have | embedded_pack_uuids(world_dir)
    return len(refs), len(refs - have)


def world_pack_status(entry_id: str) -> list[dict]:
    """Union of library packs and the world's referenced packs."""
    world_dir = world_dir_for(entry_id)
    lib = {p["uuid"]: p for p in list_packs()}
    embedded = embedded_pack_uuids(world_dir)
    out, seen = [], set()
    for ptype, lst in read_world_pack_entries(world_dir).items():
        for e in lst:
            uuid_ = str(e.get("pack_id", "")).lower()
            if not uuid_ or uuid_ in seen:
                continue
            seen.add(uuid_)
            p = lib.get(uuid_)
            out.append({
                "uuid": uuid_,
                "name": p["name"] if p else uuid_,
                "type": p["type"] if p else ptype,
                "assigned": True,
                "available": bool(p) or uuid_ in embedded,
                "mod": p["mod"] if p else uuid_,
                "version": p["version"] if p else [],
                "superseded": bool(p and p.get("superseded")),
            })
    for uuid_, p in lib.items():
        if uuid_ not in seen:
            out.append({"uuid": uuid_, "name": p["name"], "type": p["type"], "assigned": False, "available": True,
                        "missing_deps": p.get("missing_deps", []), "mod": p["mod"], "version": p["version"],
                        "superseded": bool(p.get("superseded"))})
    assigned = {item["uuid"] for item in out if item["assigned"]} | embedded
    for item in out:
        if "missing_deps" not in item:
            item["missing_deps"] = list((lib.get(item["uuid"]) or {}).get("missing_deps", []))
        if item["assigned"]:
            man = load_pack_manifest(os.path.join(PACKS_DIR, item["uuid"])) or {}
            item["missing_deps"] += [d for d in pack_dependencies(man) if d not in assigned and d not in item["missing_deps"]]
    out.sort(key=lambda x: (x["mod"], x["type"], [-v for v in (x["version"] or [])]))
    return out


def apply_world_packs(world_dir: str, keep: set[str]) -> None:
    lib = {}
    for p in list_packs():
        man = load_pack_manifest(os.path.join(PACKS_DIR, p["uuid"]))
        if man:
            lib[p["uuid"]] = (p, man)
    # a mod's required packs come along automatically (e.g. Twilight Abyss needs Dimension Lib)
    keep = set(keep)
    stack = list(keep)
    while stack:
        u = stack.pop()
        for dep in pack_dependencies(lib[u][1]) if u in lib else []:
            if dep in lib and dep not in keep:
                keep.add(dep)
                stack.append(dep)
    entries = read_world_pack_entries(world_dir)
    new_entries: dict[str, list[dict]] = {"behavior": [], "resource": []}
    existing: set[str] = set()
    for ptype, lst in entries.items():
        for e in lst:
            uuid_ = str(e.get("pack_id", "")).lower()
            existing.add(uuid_)
            if uuid_ in keep:
                new_entries[ptype].append(e)
    for uuid_ in keep - existing:
        if uuid_ in lib:
            p, man = lib[uuid_]
            new_entries[p["type"]].append({"pack_id": uuid_, "version": norm_version(man.get("header", {}).get("version"))})
    # drop embedded folders for unassigned packs, then embed assigned library packs
    for dname in _EMBED_DIRS.values():
        base = os.path.join(world_dir, dname)
        if not os.path.isdir(base):
            continue
        for sub in list(os.listdir(base)):
            man = load_pack_manifest(os.path.join(base, sub))
            if man and str(man.get("header", {}).get("uuid", "")).lower() not in keep:
                shutil.rmtree(os.path.join(base, sub), ignore_errors=True)
    for uuid_ in keep:
        if uuid_ not in lib:
            continue
        p, _man = lib[uuid_]
        base = os.path.join(world_dir, _EMBED_DIRS[p["type"]])
        os.makedirs(base, exist_ok=True)
        dest = os.path.join(base, slugify(p["name"]) + "-" + uuid_[:8])
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(os.path.join(PACKS_DIR, uuid_), dest)
        TWEAKS.apply_all(uuid_, dest)
    for ptype, fname in _WORLD_PACK_FILES.items():
        with open(os.path.join(world_dir, fname), "w") as fh:
            json.dump(new_entries[ptype], fh, indent=1)
    chown_tree(world_dir)


def run_set_packs_active(keep: set[str]) -> None:
    try:
        op_update(step="stopping server")
        err = scale_server(0)
        if err:
            op_finish(err)
            return
        op_update(step="updating add-ons")
        apply_world_packs(ACTIVE_WORLD_DIR, keep)
        op_update(step="starting server")
        err = scale_server(1)
        op_finish(err or "")
    except Exception as exc:  # noqa: BLE001
        op_finish(f"unexpected error: {exc}")
        scale_server(1)


def run_restart() -> None:
    try:
        op_update(step="stopping server")
        err = scale_server(0)
        if err:
            op_finish(err)
            return
        op_update(step="starting server")
        err = scale_server(1, wait_seconds=300)
        op_finish(err or "")
    except Exception as exc:  # noqa: BLE001
        op_finish(f"unexpected error: {exc}")
        scale_server(1)


# ------------------------------------------------------------- file editor ---

_EDITABLE_EXT = (".json", ".js", ".ts", ".lang", ".mcfunction", ".txt", ".material", ".md", ".mcmeta")
_BROWSE_ROOTS = ("behavior_packs", "resource_packs")
MAX_EDIT_BYTES = 1024 * 1024


def safe_world_subpath(world_dir: str, rel: str) -> str | None:
    rel = rel.strip().lstrip("/")
    if not rel or rel.split("/")[0] not in _BROWSE_ROOTS:
        return None
    full = os.path.realpath(os.path.join(world_dir, rel))
    base = os.path.realpath(world_dir)
    if full != base and not full.startswith(base + os.sep):
        return None
    return full


def browse_world_files(world_dir: str, rel: str) -> dict:
    if not rel:
        roots = [r for r in _BROWSE_ROOTS if os.path.isdir(os.path.join(world_dir, r))]
        return {"path": "", "entries": [{"name": r, "dir": True, "size": 0} for r in roots]}
    full = safe_world_subpath(world_dir, rel)
    if not full or not os.path.exists(full):
        return {"error": "path not found"}
    if os.path.isdir(full):
        entries = []
        for name in sorted(os.listdir(full)):
            p = os.path.join(full, name)
            entries.append({
                "name": name,
                "dir": os.path.isdir(p),
                "size": 0 if os.path.isdir(p) else os.path.getsize(p),
                "editable": (not os.path.isdir(p)) and name.lower().endswith(_EDITABLE_EXT) and os.path.getsize(p) <= MAX_EDIT_BYTES,
            })
        entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        return {"path": rel, "entries": entries}
    if not full.lower().endswith(_EDITABLE_EXT):
        return {"error": "not an editable file type"}
    if os.path.getsize(full) > MAX_EDIT_BYTES:
        return {"error": "file too large to edit here (>1 MB)"}
    with open(full, encoding="utf-8", errors="replace") as fh:
        return {"path": rel, "content": fh.read()}


def save_world_file(world_dir: str, rel: str, content: str) -> str | None:
    full = safe_world_subpath(world_dir, rel)
    if not full or not os.path.isfile(full):
        return "file not found"
    if not full.lower().endswith(_EDITABLE_EXT):
        return "not an editable file type"
    if len(content.encode()) > MAX_EDIT_BYTES:
        return "content too large"
    if full.lower().endswith(".json"):
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            return f"invalid JSON: {exc}"
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)
    return None


# -------------------------------------------------------------------- UI ----

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


def page_bytes() -> bytes:
    with open(os.path.join(UI_DIR, "index.html"), encoding="utf-8") as fh:
        return fh.read().replace("{{VERSION}}", __version__).encode()


# ----------------------------------------------------------------- server ---


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        msg = fmt % args
        if "/auth/status" in msg or "/api/state" in msg or "/api/dashboard" in msg:
            return  # health checks and UI polling would drown the log
        sys.stderr.write("%s - %s\n" % (self.address_string(), msg))

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 2 * 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}

    # ---------------------------------------------------------- auth gate ----

    PUBLIC_GET = {"/", "/index.html", "/auth/status", "/favicon.ico"}
    PUBLIC_POST = {"/auth/login", "/auth/setup"}

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        super().end_headers()

    def _session(self) -> str | None:
        return read_cookie(self.headers.get("Cookie"))

    def _authed(self) -> bool:
        return AUTH.check(self._session())

    def _secure(self) -> bool:
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def _client(self) -> str:
        return self.client_address[0]

    def _csrf_ok(self) -> bool:
        # Browsers can't add custom headers cross-site without a CORS preflight, which we never grant.
        return self.headers.get("X-Blockhost") == "1"

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/auth/status":
            self._json(200, {"authed": self._authed(), "needs_setup": AUTH.needs_setup(),
                             "mode": AUTH.mode, "version": __version__})
            return
        if path not in self.PUBLIC_GET and not self._authed():
            self._json(401, {"error": "sign in required"})
            return
        if path == "/api/install":
            self._json(200, install_state())
            return
        self._get()

    def do_PUT(self) -> None:  # noqa: N802
        if not self._authed() or not self._csrf_ok():
            self._json(401, {"error": "sign in required"})
            return
        self._put()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._csrf_ok():
            self._json(403, {"error": "missing X-Blockhost header"})
            return
        data = self._read_json()
        if path == "/auth/setup":
            err = AUTH.set_password(str(data.get("password", "")), setup_token=str(data.get("code", "")).strip())
            if err:
                self._json(400, {"error": err})
                return
            self._login_ok(AUTH.new_session())
            return
        if path == "/auth/login":
            token, err = AUTH.login(str(data.get("password", "")), self._client())
            if err:
                self._json(401, {"error": err})
                return
            self._login_ok(token)
            return
        if not self._authed():
            self._json(401, {"error": "sign in required"})
            return
        if path == "/auth/logout":
            AUTH.logout(self._session())
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Set-Cookie", cookie_header("", secure=self._secure(), max_age=0))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/auth/password":
            err = AUTH.set_password(str(data.get("password", "")), current=str(data.get("current", "")))
            if err:
                self._json(400, {"error": err})
                return
            self._login_ok(AUTH.new_session())
            return
        if path in ("/server/install", "/server/update"):
            if path == "/server/install" and data.get("accept_eula") is not True and not CFG.get("eula_accepted"):
                self._json(400, {"error": "you must accept the Minecraft EULA to download the server"})
                return
            if data.get("accept_eula") is True:
                CFG.set(eula_accepted=True)
            if not op_start("update server" if path.endswith("update") else "install server"):
                self._json(409, {"error": "another operation is in progress"})
                return
            threading.Thread(target=run_install, daemon=True).start()
            self._json(200, {"ok": True})
            return
        if path in ("/server/start", "/server/stop"):
            starting = path.endswith("start")
            if not op_start("start server" if starting else "stop server"):
                self._json(409, {"error": "another operation is in progress"})
                return
            threading.Thread(target=run_power, args=(starting,), daemon=True).start()
            self._json(200, {"ok": True})
            return
        self._post(data)

    def _login_ok(self, token: str) -> None:
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Set-Cookie", cookie_header(token, secure=self._secure()))
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------- GET ----

    def _get(self) -> None:
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            body = page_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/api/state":
            status = server_status_cached()
            if OP["running"]:
                names, roles = [], {}
            else:
                names, roles = _ALLOW_C.get(_allowlist_and_roles)
            with OP_LOCK:
                op = {k: OP[k] for k in ("running", "kind", "step", "error")}
            worlds = list_worlds()
            packs = list_packs()
            have = {p["uuid"] for p in packs}
            for w in worlds:
                w["packs_assigned"], w["packs_missing"] = world_pack_summary(w["id"], have)
            self._json(200, {
                "server": status,
                "op": op,
                "worlds": worlds,
                "packs": packs,
                "tweaks": TWEAKS.counts(),
                "allowlist": names,
                "roles": roles,
            })
            return
        if url.path == "/worlds/packs":
            entry_id = os.path.basename(parse_qs(url.query).get("id", [""])[0])
            if not entry_id or not os.path.isdir(world_dir_for(entry_id)):
                self._json(404, {"error": "world not found"})
                return
            self._json(200, {"packs": world_pack_status(entry_id)})
            return
        if url.path == "/mods/settings":
            q = parse_qs(url.query)
            uuid_ = os.path.basename(q.get("uuid", [""])[0]).lower()
            cat = q.get("cat", [""])[0]
            if not os.path.isfile(os.path.join(PACKS_DIR, uuid_, "manifest.json")):
                self._json(404, {"error": "mod not found"})
                return
            if cat:
                if cat not in ms.CATEGORIES:
                    self._json(400, {"error": "unknown category"})
                    return
                self._json(200, {"entries": TWEAKS.settings(uuid_, cat)})
            else:
                self._json(200, TWEAKS.summary(uuid_))
            return
        if url.path == "/worlds/files":
            q = parse_qs(url.query)
            entry_id = os.path.basename(q.get("id", [""])[0])
            rel = q.get("path", [""])[0]
            if not entry_id or not os.path.isdir(world_dir_for(entry_id)):
                self._json(404, {"error": "world not found"})
                return
            self._json(200, browse_world_files(world_dir_for(entry_id), rel))
            return
        if url.path == "/api/dashboard":
            self._json(200, _DASH_C.get(read_dashboard))
            return
        if url.path == "/api/network":
            self._json(200, network_info())
            return
        if url.path == "/api/world":
            if OP["running"]:
                self._json(409, {"error": "another operation is in progress"})
                return
            if "fresh" in parse_qs(url.query):
                _WORLD_C.bust()
            self._json(200, read_world_state())
            return
        if url.path == "/api/logs":
            try:
                tail = min(1000, max(50, int(parse_qs(url.query).get("tail", ["400"])[0])))
            except ValueError:
                tail = 400
            self._json(200, read_server_logs(tail))
            return
        if url.path == "/worlds/export":
            self._export(parse_qs(url.query).get("id", [""])[0])
            return
        self._json(404, {"error": "not found"})

    def _export(self, entry_id: str) -> None:
        active = get_active_id()
        src = ACTIVE_WORLD_DIR if entry_id == active and os.path.isdir(ACTIVE_WORLD_DIR) else entry_path(entry_id)
        if not entry_id or not os.path.isdir(src):
            self._json(404, {"error": "world not found"})
            return
        meta = read_meta(entry_id)
        os.makedirs(TMP_DIR, exist_ok=True)
        tmp = os.path.join(TMP_DIR, f"export-{int(time.time()*1000)}.mcworld")
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _dirs, files in os.walk(src):
                    for f in files:
                        if f == ".library.json":
                            continue
                        full = os.path.join(root, f)
                        zf.write(full, os.path.relpath(full, src))
            size = os.path.getsize(tmp)
            fname = slugify(meta.get("name", entry_id)) + ".mcworld"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(tmp, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ------------------------------------------------------------- PUT ----

    def _put(self) -> None:
        url = urlparse(self.path)
        if url.path not in ("/worlds/upload", "/packs/upload"):
            self._json(404, {"error": "not found"})
            return
        if OP["running"]:
            self._json(409, {"error": "another operation is in progress"})
            return
        name = parse_qs(url.query).get("name", [""])[0].strip() or "uploaded-world"
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            self._json(400, {"error": "empty upload"})
            return
        if length > MAX_UPLOAD:
            self._json(413, {"error": "file too large"})
            return
        os.makedirs(TMP_DIR, exist_ok=True)
        tmp = os.path.join(TMP_DIR, f"upload-{int(time.time()*1000)}.zip")
        try:
            remaining = length
            with open(tmp, "wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    fh.write(chunk)
                    remaining -= len(chunk)
            if remaining > 0:
                self._json(400, {"error": "upload truncated"})
                return
            if url.path == "/packs/upload":
                imported, err = import_packs(tmp)
                if err:
                    self._json(400, {"error": err})
                    return
                self._json(200, {"imported": imported})
                return
            entry_id, err = import_mcworld(tmp, name)
            if err:
                self._json(400, {"error": err})
                return
            self._json(200, {"id": entry_id})
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ------------------------------------------------------------ POST ----

    def _post(self, data: dict) -> None:
        url = urlparse(self.path)
        # Any write may change what the pollers show; cheap to drop everything.
        for c in (_STATUS_C, _ENV_C, _ALLOW_C, _WORLD_C, _DASH_C):
            c.bust()
        bust_packs_cache()

        if url.path == "/worlds/activate":
            entry_id = os.path.basename(str(data.get("id", "")))
            if not entry_id or not os.path.isdir(entry_path(entry_id)):
                self._json(404, {"error": "world not found"})
                return
            if not op_start("switch world"):
                self._json(409, {"error": "another operation is in progress"})
                return
            threading.Thread(target=run_activate, args=(entry_id,), daemon=True).start()
            self._json(200, {"ok": True})
            return

        if url.path == "/worlds/new-seed":
            name = str(data.get("name", "")).strip()
            seed = str(data.get("seed", "")).strip()
            if not name:
                self._json(400, {"error": "name is required"})
                return
            if seed and not re.fullmatch(r"-?\d{1,20}", seed):
                self._json(400, {"error": "seed must be a number"})
                return
            settings = data.get("settings") or {}
            if not isinstance(settings, dict):
                settings = {}
            _env, verr = validate_settings(settings)
            if verr:
                self._json(400, {"error": verr})
                return
            gamerules, gerr = validate_gamerules(data.get("gamerules") or {})
            if gerr:
                self._json(400, {"error": gerr})
                return
            if gamerules and str(settings.get("cheats", "false")) != "true":
                settings["cheats"] = "true"  # game rules need cheats enabled
            packs = {str(u).lower() for u in (data.get("packs") or []) if isinstance(u, str)}
            have = {p["uuid"] for p in list_packs()}
            unknown = packs - have
            if unknown:
                self._json(400, {"error": f"unknown add-on(s): {', '.join(sorted(unknown))}"})
                return
            if not op_start("new world from seed"):
                self._json(409, {"error": "another operation is in progress"})
                return
            threading.Thread(target=run_new_seed, args=(name, seed, settings, packs, gamerules), daemon=True).start()
            self._json(200, {"ok": True})
            return

        if url.path == "/worlds/delete":
            entry_id = os.path.basename(str(data.get("id", "")))
            if not entry_id or not os.path.isdir(entry_path(entry_id)):
                self._json(404, {"error": "world not found"})
                return
            if entry_id == get_active_id():
                self._json(400, {"error": "cannot delete the active world, switch to another one first"})
                return
            shutil.rmtree(entry_path(entry_id))
            self._json(200, {"ok": True})
            return

        if url.path == "/worlds/set-packs":
            entry_id = os.path.basename(str(data.get("id", "")))
            keep = {str(u).lower() for u in data.get("keep", []) if isinstance(u, str)}
            if not entry_id or not os.path.isdir(world_dir_for(entry_id)):
                self._json(404, {"error": "world not found"})
                return
            if entry_id == get_active_id():
                if not op_start("update add-ons"):
                    self._json(409, {"error": "another operation is in progress"})
                    return
                threading.Thread(target=run_set_packs_active, args=(keep,), daemon=True).start()
                self._json(200, {"ok": True, "restarting": True})
                return
            try:
                apply_world_packs(world_dir_for(entry_id), keep)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": f"failed to update packs: {exc}"})
                return
            self._json(200, {"ok": True, "restarting": False})
            return

        if url.path == "/worlds/files/save":
            entry_id = os.path.basename(str(data.get("id", "")))
            rel = str(data.get("path", ""))
            content = data.get("content")
            if not entry_id or not os.path.isdir(world_dir_for(entry_id)) or not isinstance(content, str):
                self._json(400, {"error": "world, path and content required"})
                return
            err = save_world_file(world_dir_for(entry_id), rel, content)
            self._json(400 if err else 200, {"error": err} if err else {"ok": True, "active": entry_id == get_active_id()})
            return

        if url.path in ("/mods/settings/save", "/mods/settings/reset"):
            uuid_ = os.path.basename(str(data.get("uuid", ""))).lower()
            if url.path.endswith("/reset"):
                old = TWEAKS.load(uuid_)
                if (cat := str(data.get("cat", ""))):
                    old = {f: p for f, p in old.items() if ms.category_of(f) == cat}
                changes = [{"file": f, "key": k, "value": None} for f, p in old.items() for k in p]
                # toggles revert on True, numbers on None
                for c in changes:
                    c["value"] = True if isinstance(old[c["file"]][c["key"]], list) else None
            else:
                changes = data.get("changes") if isinstance(data.get("changes"), list) else []
            touched, err = TWEAKS.update(uuid_, [c for c in changes if isinstance(c, dict)])
            if err:
                self._json(400, {"error": err})
                return
            active = False
            try:
                for dest in ms.world_copies(all_world_dirs(), uuid_):
                    for rel in touched:
                        TWEAKS.render_file(uuid_, rel, dest)
                    active = active or dest.startswith(ACTIVE_WORLD_DIR + os.sep)
            except OSError as exc:
                self._json(500, {"error": f"saved, but failed to update worlds: {exc}"})
                return
            self._json(200, {"ok": True, "changed": len(touched), "active": active})
            return

        if url.path == "/server/restart":
            if not op_start("restart server"):
                self._json(409, {"error": "another operation is in progress"})
                return
            threading.Thread(target=run_restart, daemon=True).start()
            self._json(200, {"ok": True})
            return

        if url.path == "/packs/delete":
            uuid_ = os.path.basename(str(data.get("uuid", "")).lower())
            target = os.path.join(PACKS_DIR, uuid_)
            if not uuid_ or not os.path.isdir(target):
                self._json(404, {"error": "pack not found"})
                return
            shutil.rmtree(target)
            self._json(200, {"ok": True})
            return

        if url.path == "/packs/cleanup-duplicates":
            removed, kept = remove_superseded_packs()
            self._json(200, {"ok": True, "removed": removed, "kept_in_use": kept})
            return

        if url.path == "/allowlist/add":
            err = add_player(str(data.get("name", "")))
            _ALLOW_C.bust()
            self._json(400 if err else 200, {"error": err} if err else {"ok": True})
            return

        if url.path == "/allowlist/remove":
            err = remove_player(str(data.get("name", "")))
            _ALLOW_C.bust()
            self._json(400 if err else 200, {"error": err} if err else {"ok": True})
            return

        if url.path == "/players/role":
            err = set_role(str(data.get("name", "")), str(data.get("role", "")))
            _ALLOW_C.bust()
            self._json(400 if err else 200, {"error": err} if err else {"ok": True})
            return

        if url.path == "/players/gamemode":
            err = set_player_gamemode(str(data.get("name", "")), str(data.get("mode", "")))
            self._json(400 if err else 200, {"error": err} if err else {"ok": True})
            return

        if url.path == "/world/gamerule":
            key = str(data.get("key", "")).lower()
            if key not in _GAMERULES:
                self._json(400, {"error": f"unknown game rule: {key}"})
                return
            kind, _default = _GAMERULES[key]
            val = str(data.get("value", "")).strip().lower()
            if kind == "bool" and val not in ("true", "false"):
                self._json(400, {"error": f"{key} must be true/false"})
                return
            if kind == "int":
                lo, hi = _INT_RANGES.get(key, (0, 4096))
                if not val.isdigit() or not lo <= int(val) <= hi:
                    self._json(400, {"error": f"{key} must be {lo}-{hi}"})
                    return
            err = apply_gamerules({key: val})
            if not err:
                active = get_active_id()
                if active:
                    meta = read_meta(active)
                    saved = dict(meta.get("gamerules") or {})
                    if val == _default:
                        saved.pop(key, None)
                    else:
                        saved[key] = val
                    meta["gamerules"] = saved
                    write_meta(active, meta)
            self._json(400 if err else 200, {"error": err} if err else {"ok": True})
            return

        if url.path == "/world/time":
            err = set_world_time(str(data.get("value", "")))
            self._json(400 if err else 200, {"error": err} if err else {"ok": True})
            return

        if url.path == "/world/weather":
            err = set_weather(str(data.get("value", "")), str(data.get("duration", "")))
            self._json(400 if err else 200, {"error": err} if err else {"ok": True})
            return

        if url.path == "/world/difficulty":
            err = set_difficulty(str(data.get("value", "")))
            self._json(400 if err else 200, {"error": err} if err else {"ok": True})
            return

        if url.path == "/world/gamemode":
            err = set_default_gamemode(str(data.get("value", "")))
            self._json(400 if err else 200, {"error": err} if err else {"ok": True, "restart": True})
            return

        if url.path == "/world/console":
            lines, err = run_console(str(data.get("cmd", "")))
            self._json(400 if err else 200, {"error": err} if err else {"ok": True, "lines": lines})
            return

        if url.path == "/network/upnp":
            on = data.get("enabled") is True
            CFG.set(upnp_enabled=on)
            if on:
                NET.start()
            else:
                NET.stop(remove=True)
            self._json(200, network_info())
            return

        if url.path == "/network/tunnel":
            action = str(data.get("action", ""))
            if action == "start":
                if not TUNNEL.snapshot()["supported"]:
                    self._json(400, {"error": "playit's agent isn't available for this system"})
                    return
                CFG.set(playit_enabled=True)
                TUNNEL.begin_setup()
            elif action in ("stop", "unlink"):
                CFG.set(playit_enabled=False)
                (TUNNEL.unlink if action == "unlink" else TUNNEL.stop)()
            else:
                self._json(400, {"error": "action must be start, stop or unlink"})
                return
            self._json(200, network_info())
            return

        if url.path == "/network/external":
            value, err = validate_external_address(str(data.get("address", "")))
            if err:
                self._json(400, {"error": err})
                return
            CFG.set(external_address=value)
            self._json(200, network_info())
            return

        if url.path == "/network/public-ip":
            ip = network.public_ip()
            if not ip:
                self._json(502, {"error": "couldn't look up your public IP address"})
                return
            self._json(200, {"ip": ip})
            return

        if url.path == "/network/check":
            ext = network_info()["external"]
            if not ext["host"]:
                self._json(400, {"error": "no external address is set up yet"})
                return
            ok, why = network.check_reachable(f"{ext['host']}:{ext['port']}")
            self._json(200, {"reachable": ok, "reason": why, "address": f"{ext['host']}:{ext['port']}"})
            return

        self._json(404, {"error": "not found"})


# ------------------------------------------------------------ install ---

_LATEST = {"at": 0.0, "v": "", "url": "", "err": ""}


def latest_cached() -> dict:
    if time.time() - _LATEST["at"] > 3600:
        try:
            v, url = bds.latest_download()
            _LATEST.update(v=v, url=url, err="")
        except Exception as exc:  # noqa: BLE001 (network errors of every kind)
            _LATEST.update(err=f"could not check for updates: {exc}")
        _LATEST["at"] = time.time()
    return dict(_LATEST)


def install_state() -> dict:
    latest = latest_cached()
    return {
        "installed": bds.is_installed(SERVER_DATA),
        "version": bds.installed_version(SERVER_DATA),
        "latest": latest["v"],
        "latest_error": latest["err"],
        "eula_accepted": bool(CFG.get("eula_accepted")),
        "unsupported": bds.platform_supported(),
        "eula_url": bds.EULA_URL,
        "privacy_url": bds.PRIVACY_URL,
    }


def run_install() -> None:
    try:
        reason = bds.platform_supported()
        if reason:
            op_finish(reason)
            return
        op_update(step="checking the latest version")
        _LATEST["at"] = 0.0
        latest = latest_cached()
        if latest["err"]:
            op_finish(latest["err"])
            return
        was_running = SERVER.running()
        if was_running:
            op_update(step="stopping server")
            SERVER.stop()
        bds.install(SERVER_DATA, latest["url"], latest["v"],
                    progress=lambda pct, msg: op_update(step=f"{msg} ({pct}%)"))
        op_update(step="starting server")
        op_finish(SERVER.start(wait_seconds=300) or "")
    except Exception as exc:  # noqa: BLE001
        op_finish(f"install failed: {exc}")


def run_power(start: bool) -> None:
    try:
        op_finish((SERVER.start() if start else SERVER.stop()) or "")
    except Exception as exc:  # noqa: BLE001
        op_finish(f"unexpected error: {exc}")


def serve(cfg: Config, host: str, port: int, *, auth_mode: str = "password", autostart: bool = True,
          on_ready=None) -> None:
    configure(cfg, auth_mode)
    os.makedirs(os.path.join(LIBRARY, "worlds"), exist_ok=True)
    os.makedirs(PACKS_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    SERVER.reap_orphan()
    if autostart and bds.is_installed(SERVER_DATA):
        threading.Thread(target=SERVER.start, daemon=True).start()
    if cfg.get("upnp_enabled"):
        NET.start()
    if cfg.get("playit_enabled") and TUNNEL.linked():
        TUNNEL.begin_setup()
    if on_ready:
        on_ready(httpd)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print("Stopping the Bedrock server...", flush=True)
        SERVER.stop()
        NET.stop(remove=True)
        TUNNEL.stop()
        httpd.server_close()
