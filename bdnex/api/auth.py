"""Who may use the server: the token, the user name and password (HTTP Basic or a session cookie), and a limit on
wrong guesses.

A session is a random value kept in memory: restarting the server logs everybody out, and logging out ends it at once.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import secrets
import time
from typing import Optional

COOKIE = "bdnex_session"
MAX_FAILURES = 5          # wrong guesses from one place ...
WINDOW = 300              # ... within this many seconds lock it out ...
LOCK = 300                # ... for this many seconds


def same(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


class Throttle:
    """Counts wrong guesses per client; after :data:`MAX_FAILURES` the client waits. A right guess resets it."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}

    def wait(self, who: str) -> int:
        """Seconds this client still has to wait (0: it may try)."""
        until = self._locked.get(who, 0.0)
        left = until - self._clock()
        if left <= 0:
            self._locked.pop(who, None)
            return 0
        return int(left) + 1

    def failed(self, who: str) -> None:
        now = self._clock()
        recent = [t for t in self._failures.get(who, []) if now - t < WINDOW] + [now]
        self._failures[who] = recent
        if len(recent) >= MAX_FAILURES:
            self._locked[who] = now + LOCK
            self._failures.pop(who, None)
        if len(self._failures) > 5000:  # a flood of different addresses must not fill the memory
            self._failures.clear()

    def succeeded(self, who: str) -> None:
        self._failures.pop(who, None)


class Sessions:
    def __init__(self, hours: float, clock=time.time):
        self.ttl = max(0.01, hours) * 3600
        self._clock = clock
        self._until: dict[str, float] = {}

    def start(self) -> str:
        now = self._clock()
        self._until = {k: v for k, v in self._until.items() if v > now}
        token = secrets.token_urlsafe(32)
        self._until[token] = now + self.ttl
        return token

    def valid(self, token: Optional[str]) -> bool:
        return bool(token) and self._until.get(token, 0) > self._clock()

    def end(self, token: Optional[str]) -> None:
        self._until.pop(token or "", None)


def basic_credentials(value: str) -> tuple[str, str]:
    """``(user, password)`` from the base64 part of ``Authorization: Basic ...``; ``("", "")`` when it is not valid."""
    try:
        name, _, secret = base64.b64decode(value, validate=True).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return "", ""
    return name, secret


def login_ok(name: str, secret: str, username: str, password: str) -> bool:
    return same(name, username) & same(secret, password)  # both always compared
