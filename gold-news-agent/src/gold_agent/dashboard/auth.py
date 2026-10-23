"""Dashboard login (C2.4): one password from DASHBOARD_PASSWORD, an HMAC-signed session cookie,
and a per-client limit on failed attempts. Changing the password invalidates every session."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from datetime import datetime, timedelta

COOKIE = "gold_session"
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def is_loopback(host: str) -> bool:
    return host in _LOOPBACK or host.startswith("127.")


class SessionSigner:
    def __init__(self, password: str) -> None:
        self._password = password
        self._key = hashlib.sha256(b"gold-dashboard-session|" + password.encode()).digest()

    def check_password(self, attempt: str) -> bool:
        digest = hashlib.sha256  # equal-length digests: not even the length leaks
        return hmac.compare_digest(
            digest(attempt.encode()).digest(), digest(self._password.encode()).digest()
        )

    def _mac(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(self, now: datetime, lifetime: timedelta) -> str:
        payload = f"{int((now + lifetime).timestamp())}.{secrets.token_hex(8)}"
        return f"{payload}.{self._mac(payload)}"

    def valid(self, token: str | None, now: datetime) -> bool:
        if not token or token.count(".") != 2:
            return False
        expires, nonce, mac = token.split(".")
        if not hmac.compare_digest(mac, self._mac(f"{expires}.{nonce}")):
            return False
        return expires.isdigit() and int(expires) > now.timestamp()


class LoginLimiter:
    """Sliding window of failed attempts per client address."""

    def __init__(self, attempts: int, window: timedelta) -> None:
        self._attempts = attempts
        self._window = window
        self._failures: dict[str, deque[datetime]] = defaultdict(deque)

    def blocked(self, client: str, now: datetime) -> bool:
        failures = self._failures[client]
        while failures and now - failures[0] >= self._window:
            failures.popleft()
        return len(failures) >= self._attempts

    def failed(self, client: str, now: datetime) -> None:
        self._failures[client].append(now)

    def succeeded(self, client: str) -> None:
        self._failures.pop(client, None)
