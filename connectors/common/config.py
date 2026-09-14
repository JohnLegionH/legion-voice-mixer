"""Shared env parsing for the connector peers."""

from __future__ import annotations

import os
import sys


#: SC-96 (spec §3.5): writing room audio to disk needs this set to exactly "yes" (any case).
RECORDING_OPT_IN_ENV = "RECORDING_OPT_IN"


def require_recording_opt_in(prog: str, environ=os.environ) -> None:
    """Refuse to record without an explicit operator opt-in (SC-96).

    Prints the effective value first, so the start-up log always shows it, then exits
    non-zero unless RECORDING_OPT_IN is "yes". Default is off: an unset, empty or any
    other value refuses. Setting it records the operator's statement that the people in
    the room have been told and have agreed; this gate does not collect per-participant
    consent itself."""
    value = environ.get(RECORDING_OPT_IN_ENV, "")
    print(f"{prog}: {RECORDING_OPT_IN_ENV}={value or '<unset>'} (recording opt-in, default off)",
          file=sys.stderr, flush=True)
    if value.strip().lower() != "yes":
        sys.exit(f"{prog}: FATAL: recording is off. Set {RECORDING_OPT_IN_ENV}=yes only after the "
                 f"people in the room have been told they are being recorded and have agreed "
                 f"(spec section 3.5); refusing to start")


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
