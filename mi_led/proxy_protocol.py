"""Shared constants and helpers for the BLE WebSocket proxy."""

from __future__ import annotations

import json
from typing import Any

DEFAULT_PROXY_HOST = "0.0.0.0"
DEFAULT_PROXY_PORT = 8765
DEFAULT_PROXY_URL = f"ws://127.0.0.1:{DEFAULT_PROXY_PORT}"

PROTOCOL_VERSION = 1


def dumps(message: dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":"))


def loads(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Proxy message must be a JSON object")
    return data
