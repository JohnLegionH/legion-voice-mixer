"""Slice 0.7b (ledger O-88, design §11.10): fetch a sim-minted join capability before every join.

Connector peers are NOT exempted from JS_JOIN_CAP_REQUIRED. The viewer never reaches the Janus API, so the connector
peers are the mixer's only non-sim clients, which is exactly the party the capability checks. A peer configured with
CONNECTOR_CAP_URL and CONNECTOR_CAP_SECRET therefore asks the sim's region HTTP server for one before EVERY join:

    POST <CONNECTOR_CAP_URL>          Authorization: Bearer <CONNECTOR_CAP_SECRET>
    200 {"display", "room", "session_id", "join_cap", "expires"}

Any other answer, or no answer, is retried with backoff for as long as the peer runs. A configured peer NEVER joins
without a capability. The bearer and the capability are never logged.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

CAP_URL_ENV = "CONNECTOR_CAP_URL"
CAP_SECRET_ENV = "CONNECTOR_CAP_SECRET"

#: (first retry delay, ceiling) in seconds; doubles between them.
DEFAULT_BACKOFF = (1.0, 30.0)
REQUEST_TIMEOUT_S = 10.0


class CapabilityUnavailable(Exception):
    """One fetch attempt failed. The message names the status or the error, never the bearer."""


async def fetch_once(http: aiohttp.ClientSession, url: str, secret: str) -> dict:
    headers = {"Authorization": f"Bearer {secret}"}
    try:
        async with http.post(url, headers=headers, data=b"",
                             timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S)) as resp:
            if resp.status != 200:
                raise CapabilityUnavailable(f"HTTP {resp.status}")
            body = await resp.json(content_type=None)
    except CapabilityUnavailable:
        raise
    except Exception as e:  # transport, timeout, unparsable body
        raise CapabilityUnavailable(type(e).__name__) from None
    try:
        grant = {"display": str(body["display"]), "room": int(body["room"]), "session_id": str(body["session_id"]),
                 "join_cap": str(body["join_cap"]), "expires": int(body["expires"])}
    except (KeyError, TypeError, ValueError):
        raise CapabilityUnavailable("malformed 200 body") from None
    if not grant["join_cap"] or not grant["session_id"] or not grant["display"]:
        raise CapabilityUnavailable("malformed 200 body")
    # Slice 0.8h (O-62): the sim's optional position, GLOBAL centimetres as integers - the viewer's own SLData frame.
    # Absent is normal (a pre-0.8h sim, or a record the sim cannot place): the peer is then mixed non-spatially. A
    # malformed one is a malformed grant, like every other field here: a sim sending a broken position is a bug.
    pos = body.get("position")
    if pos is not None:
        try:
            grant["position"] = {"x": int(pos["x"]), "y": int(pos["y"]), "z": int(pos["z"])}
        except (KeyError, TypeError, ValueError):
            raise CapabilityUnavailable("malformed position in a 200 body") from None
    return grant


async def fetch_until_granted(http: aiohttp.ClientSession, url: str, secret: str, log: logging.Logger,
                              stopping: asyncio.Event, backoff: tuple = DEFAULT_BACKOFF) -> dict | None:
    """Fetch until the sim grants a capability. Returns None only when the peer is stopping first."""
    delay, ceiling = backoff
    attempt = 0
    while not stopping.is_set():
        attempt += 1
        try:
            grant = await fetch_once(http, url, secret)
            log.info("join capability granted for room %s, expires %s (attempt %d)",
                     grant["room"], grant["expires"], attempt)
            return grant
        except CapabilityUnavailable as e:
            log.warning("join capability fetch failed (%s); not joining, retrying in %.1f s (attempt %d)",
                        e, delay, attempt)
        try:
            await asyncio.wait_for(stopping.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
        delay = min(delay * 2, ceiling)
    return None
