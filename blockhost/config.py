"""Paths and settings. Everything Blockhost owns lives under one data directory."""
from __future__ import annotations

import json
import os
import sys
import threading


def default_data_dir() -> str:
    if os.environ.get("BLOCKHOST_DATA"):
        return os.environ["BLOCKHOST_DATA"]
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "Blockhost")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "blockhost")


class Config:
    """Small JSON settings file at <data>/config.json (password hash, EULA, options)."""

    def __init__(self, data_dir: str):
        self.data_dir = os.path.abspath(data_dir)
        self.server_dir = os.path.join(self.data_dir, "server")
        self.library_dir = os.path.join(self.data_dir, "library")
        self.logs_dir = os.path.join(self.data_dir, "logs")
        self.path = os.path.join(self.data_dir, "config.json")
        self._lock = threading.Lock()
        for d in (self.data_dir, self.server_dir, self.library_dir, self.logs_dir):
            os.makedirs(d, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, **kw) -> None:
        with self._lock:
            self._data.update(kw)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            os.replace(tmp, self.path)
            if sys.platform != "win32":
                os.chmod(self.path, 0o600)
