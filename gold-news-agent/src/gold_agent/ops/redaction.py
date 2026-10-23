"""Secret redaction for anything we log, report or send."""

from __future__ import annotations

import re
from collections.abc import Iterable

_SECRET_QUERY = re.compile(r"(?i)\b(api_?key|apikey|token|access_token|key)=([^&\s\"']+)")
_BOT_TOKEN_PATH = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")


class Redactor:
    """Removes secret values (and secret-looking query params) from any text we emit."""

    def __init__(self, secrets: Iterable[str]) -> None:
        self._secrets = sorted((s for s in secrets if len(s) >= 4), key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        text = _BOT_TOKEN_PATH.sub("/bot***", text)
        return _SECRET_QUERY.sub(lambda m: f"{m.group(1)}=***", text)
