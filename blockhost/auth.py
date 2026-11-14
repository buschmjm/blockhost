"""Built-in login: one admin password, session cookies, first-run setup token.

- The password is stored as a PBKDF2-SHA256 hash in config.json.
- Until a password exists, the only way in is a one-time setup token printed to
  the console/log (so someone else on the network can't claim the server first).
- POST requests must carry the X-Blockhost header, which browsers won't send
  cross-site without a CORS preflight we never approve (CSRF protection).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from .config import Config

COOKIE = "blockhost_session"
SESSION_TTL = 30 * 24 * 3600
PBKDF2_ITERATIONS = 600_000
MIN_PASSWORD = 8


def hash_password(password: str, *, salt: bytes | None = None, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
    return hmac.compare_digest(calc.hex(), digest_hex)


class Auth:
    def __init__(self, cfg: Config, *, mode: str = "password"):
        self.cfg = cfg
        self.mode = mode  # "password" or "none" (only behind your own SSO proxy)
        self._sessions: dict[str, float] = {}
        self._fails: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()
        self.setup_token = "" if self.has_password() else secrets.token_urlsafe(18)

    # -- state -----------------------------------------------------------

    def has_password(self) -> bool:
        return bool(self.cfg.get("password_hash"))

    def needs_setup(self) -> bool:
        return self.mode == "password" and not self.has_password()

    # -- password --------------------------------------------------------

    def set_password(self, password: str, *, setup_token: str | None = None, current: str | None = None) -> str | None:
        if len(password) < MIN_PASSWORD:
            return f"password must be at least {MIN_PASSWORD} characters"
        if self.has_password():
            if current is None or not verify_password(current, self.cfg.get("password_hash")):
                return "current password is wrong"
        elif not setup_token or not hmac.compare_digest(setup_token, self.setup_token):
            return "setup code is wrong. It is printed in the Blockhost console or log"
        self.cfg.set(password_hash=hash_password(password))
        self.setup_token = ""
        with self._lock:
            self._sessions.clear()
        return None

    def login(self, password: str, client: str) -> tuple[str | None, str | None]:
        with self._lock:
            n, until = self._fails.get(client, (0, 0.0))
            if until > time.time():
                return None, f"too many attempts, wait {int(until - time.time()) + 1}s"
        stored = self.cfg.get("password_hash") or ""
        if not stored or not verify_password(password, stored):
            with self._lock:
                n += 1
                self._fails[client] = (n, time.time() + min(2 ** max(0, n - 3), 300) if n >= 3 else 0.0)
            return None, "wrong password"
        with self._lock:
            self._fails.pop(client, None)
        return self.new_session(), None

    # -- sessions --------------------------------------------------------

    def new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            self._sessions = {t: exp for t, exp in self._sessions.items() if exp > now}
            self._sessions[token] = now + SESSION_TTL
        return token

    def check(self, token: str | None) -> bool:
        if self.mode == "none":
            return True
        if not token:
            return False
        with self._lock:
            exp = self._sessions.get(token)
            return bool(exp and exp > time.time())

    def logout(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(token, None)


def cookie_header(token: str, *, secure: bool, max_age: int = SESSION_TTL) -> str:
    parts = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={max_age}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def read_cookie(header: str | None) -> str | None:
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return v
    return None
