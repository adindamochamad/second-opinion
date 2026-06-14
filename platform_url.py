"""Resolve Thenvoi platform URLs from the THENVOI_PLATFORM_URL env var."""
from __future__ import annotations

import os

DEFAULT_PLATFORM_URL = "https://app.thenvoi.com"


def get_platform_url() -> str:
    """Return the base platform URL (no trailing slash)."""
    return os.environ.get("THENVOI_PLATFORM_URL", DEFAULT_PLATFORM_URL).rstrip("/")


def get_ws_url() -> str:
    """Derive the WebSocket URL from the platform URL."""
    url = get_platform_url()
    ws_scheme = "wss" if url.startswith("https") else "ws"
    host = url.split("://", 1)[1]
    return f"{ws_scheme}://{host}/api/v1/socket/websocket"
