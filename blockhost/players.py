"""Player history, blocking and the grief log.

Bedrock keeps no record of who has joined, so Blockhost reads the server's output as it
happens and saves what it sees in the library folder:

- players-seen.json: everyone who has connected, with first/last login and login count
- blocked-players.json: gamertags that get kicked whenever they join
- grief-log.json: lava, fire and TNT placements reported by the Grief Logger add-on
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone

GAMERTAG_RE = re.compile(r"[A-Za-z0-9 _\-]{1,32}")
KICK_MESSAGE = "You are blocked from this server."
GRIEF_LIMIT = 5000

_CONNECT_RE = re.compile(r"Player (connected|disconnected): (.+?), xuid: (\d*)")
_GRIEF_RE = re.compile(r"\[GRIEF\] (\w+) \| (.+?) \| (\w+) (-?\d+) (-?\d+) (-?\d+)")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _load(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def _save(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


class PlayerTracker:
    def __init__(self, library_dir: str, kick=None, *, kick_delays: tuple[float, ...] = (2, 5, 10)):
        self.seen_path = os.path.join(library_dir, "players-seen.json")
        self.blocked_path = os.path.join(library_dir, "blocked-players.json")
        self.grief_path = os.path.join(library_dir, "grief-log.json")
        self.kick = kick  # callable(command: str); sends a console command to the server
        self.kick_delays = kick_delays
        self._lock = threading.Lock()

    # -- reading the server log -------------------------------------------

    def on_line(self, line: str) -> None:
        if "Player " in line:
            m = _CONNECT_RE.search(line)
            if m:
                self._record_player(m.group(1), m.group(2).strip(), m.group(3))
        if "[GRIEF]" in line:
            m = _GRIEF_RE.search(line)
            if m:
                self._record_grief(m)

    def _record_player(self, kind: str, name: str, xuid: str) -> None:
        when = _now()
        with self._lock:
            seen = _load(self.seen_path, {})
            rec = seen.setdefault(name.lower(), {"name": name, "xuid": "", "first_seen": when,
                                                 "last_seen": when, "joins": 0})
            rec["name"] = name
            if xuid:
                rec["xuid"] = xuid
            rec["last_seen"] = when
            if kind == "connected":
                rec["last_join"] = when
                rec["joins"] = rec.get("joins", 0) + 1
            _save(self.seen_path, seen)
        if kind == "connected" and self.is_blocked(name, xuid):
            threading.Thread(target=self._kick_soon, args=(name,), daemon=True).start()

    def _record_grief(self, m: re.Match) -> None:
        entry = {"time": _now(), "action": m.group(1), "player": m.group(2).strip(), "dim": m.group(3),
                 "pos": [int(m.group(4)), int(m.group(5)), int(m.group(6))]}
        with self._lock:
            log = _load(self.grief_path, [])
            log.append(entry)
            _save(self.grief_path, log[-GRIEF_LIMIT:])

    # -- views --------------------------------------------------------------

    def seen(self) -> list[dict]:
        return sorted(_load(self.seen_path, {}).values(), key=lambda r: r.get("last_seen", ""), reverse=True)

    def xuid_for(self, name: str) -> str:
        return str(_load(self.seen_path, {}).get(name.lower(), {}).get("xuid", ""))

    def grief(self, limit: int = 200) -> list[dict]:
        return _load(self.grief_path, [])[-limit:][::-1]

    def blocked(self) -> list[str]:
        return sorted((r["name"] for r in _load(self.blocked_path, {}).values()), key=str.lower)

    # -- blocking -----------------------------------------------------------

    def is_blocked(self, name: str, xuid: str = "") -> bool:
        for key, rec in _load(self.blocked_path, {}).items():
            if key == name.lower() or (xuid and rec.get("xuid") == xuid):
                return True
        return False

    def block(self, name: str) -> str | None:
        name = name.strip()
        if not GAMERTAG_RE.fullmatch(name):
            return "invalid gamertag"
        with self._lock:
            data = _load(self.blocked_path, {})
            data[name.lower()] = {"name": name, "xuid": self.xuid_for(name), "since": _now()}
            _save(self.blocked_path, data)
        self._kick_now(name)  # harmless if they're offline
        return None

    def unblock(self, name: str) -> str | None:
        with self._lock:
            data = _load(self.blocked_path, {})
            if data.pop(name.strip().lower(), None) is None:
                return f"{name.strip()!r} is not blocked"
            _save(self.blocked_path, data)
        return None

    def _kick_now(self, name: str) -> None:
        if self.kick and GAMERTAG_RE.fullmatch(name):
            self.kick(f'kick "{name}" {KICK_MESSAGE}')

    def _kick_soon(self, name: str) -> None:
        # The server reports the connection before the player is in the world, so an
        # immediate kick can miss; try a few times.
        for delay in self.kick_delays:
            time.sleep(delay)
            self._kick_now(name)
