"""Shared env parsing for the connector peers."""

from __future__ import annotations

import os
import sys


def base_env(prog: str) -> dict:
    """The env keys every peer needs. ROOM and DISPLAY come from the sim's
    registration line; JANUS_URL is compose-derived (see docker-compose.yml)."""
    cfg = {
        "janus_url": os.environ.get("JANUS_URL", "http://janus:14223/voice").rstrip("/"),
        "api_secret": os.environ.get("JANUS_API_SECRET", ""),
        "room": os.environ.get("ROOM"),
        "display": os.environ.get("DISPLAY"),
        "log_level": os.environ.get("LOG_LEVEL", "INFO").upper(),
    }
    missing = [k for k in ("room", "display") if not cfg[k]]
    if missing:
        sys.exit(f"{prog}: missing required env {', '.join(m.upper() for m in missing)} "
                 f"— copy them from the sim's '[CONNECTOR] registered ... npc=<DISPLAY> room=<ROOM>' line")
    try:
        cfg["room"] = int(cfg["room"])
    except ValueError:
        sys.exit(f"{prog}: ROOM must be an integer room number, got {cfg['room']!r}")
    return cfg
