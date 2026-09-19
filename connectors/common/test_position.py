"""Unit tests for slice 0.8h (ledger O-62): a connector has a position. pytest-style; a __main__ fallback runs them
with plain python. Run from connectors/: `python -m pytest common/` or `python -m common.test_position`.

Before 0.8h a connector peer opened its SLData channel and sent nothing on it, so the mixer held no geometry for it and
mixed it flat - the same level to everyone in its room (janus_slvoice.c, the flat-gain rule for a pair missing
geometry). Now the peer sends its position once the channel opens, in EXACTLY the viewer's frame: global centimetres
as integers (Firestorm llvoicewebrtc.cpp:1241-1262), with sp and lp the same point and identity headings. The position
comes from the sim's capability grant (re-computed at every fetch), else from CONNECTOR_POSITION_GLOBAL_CM, else there
is none and the peer says so once. The integration harness's S38 drives the real peer against a mixer."""

import asyncio
import json
import logging
import sys
from pathlib import Path

from common.config import POSITION_ENV, position_env
from common.peer import ConnectorPeer, geometry_message

REPO = Path(__file__).resolve().parents[2]


class FakeChannel:
    """Just enough of an RTCDataChannel: event handlers, and what was sent."""

    def __init__(self):
        self.handlers = {}
        self.sent = []

    def on(self, event, fn=None):
        if fn is None:
            def deco(f):
                self.handlers.setdefault(event, []).append(f)
                return f
            return deco
        self.handlers.setdefault(event, []).append(fn)
        return fn

    def emit(self, event, *args):
        for f in self.handlers.get(event, []):
            f(*args)

    def send(self, data):
        self.sent.append(data)


class _Log(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelname, record.getMessage()))


def _peer(cfg, cls=ConnectorPeer):
    handler = _Log()
    log = logging.getLogger(f"test_position.{id(handler)}")
    log.setLevel(logging.DEBUG)
    log.addHandler(handler)
    peer = cls(cfg, log)
    peer.log_handler = handler
    return peer


def _run(coro):
    return asyncio.run(coro)


BASE = {"janus_url": "http://janus.test/voice", "api_secret": "", "room": 1234, "display": "npc", "log_level": "INFO"}
P1 = (25612800, 25612800, 2200)   # (256000 + 128, 256000 + 128, 22) m, a standard region at grid (1000, 1000)
P2 = (25759800, 25876700, 2500)   # Elm-like: a var region at grid (1004, 1008), Position (574, 719, 25)


def test_the_encoder_matches_the_harness_encoder_byte_for_byte():
    """The harness's own send_geometry (tests/integration/harness.py), run unmodified, and the peer's encoder produce
    the same bytes for the same point - one encoding, the viewer's."""
    sys.path.insert(0, str(REPO))
    import tests.integration.harness as h

    for metres, cm in (((128.0, 256.5, 25.0), (12800, 25650, 2500)),
                       ((256128.0, 256128.0, 22.0), P1)):
        fake = type("F", (), {})()
        fake.channel = FakeChannel()
        fake.send = lambda obj, f=fake: h.TestPeer.send(f, obj)
        h.TestPeer.send_geometry(fake, *metres)
        assert geometry_message(cm) == fake.channel.sent[0], (geometry_message(cm), fake.channel.sent[0])


def test_the_message_is_the_viewer_shape_with_integers():
    msg = json.loads(geometry_message((25612800.9, 25612800.1, 2200.0)))
    assert list(msg) == ["sp", "sh", "lp", "lh"]
    assert msg["sp"] == msg["lp"] == {"x": 25612800, "y": 25612800, "z": 2200}
    assert msg["sh"] == msg["lh"] == {"x": 0, "y": 0, "z": 0, "w": 100}
    assert all(type(v) is int for v in msg["sp"].values())


def test_it_sends_its_position_once_the_channel_opens():
    async def go():
        peer = _peer(dict(BASE, position_cm=P1))
        ch = FakeChannel()
        peer._wire_channel(ch)
        assert ch.sent == [], "nothing before the channel is open"
        ch.emit("open")
        return ch.sent

    sent = _run(go())
    assert sent == [geometry_message(P1)], sent


def test_after_a_rejoin_the_new_channel_sends_the_new_grants_position():
    """Every attempt adopts a fresh grant (the sim re-computes the position at every fetch) and builds a new channel;
    the new channel sends the new position once it opens."""
    async def go():
        peer = _peer(dict(BASE, cap_url="http://sim.test/voice/connector/x/join-cap", cap_secret="unit"))
        peer._adopt_grant({"display": "npc", "room": 1234, "session_id": "s", "join_cap": "v1.a.b", "expires": 1,
                           "position": {"x": P1[0], "y": P1[1], "z": P1[2]}})
        ch1 = FakeChannel()
        peer._wire_channel(ch1)
        ch1.emit("open")
        peer._adopt_grant({"display": "npc", "room": 1234, "session_id": "s", "join_cap": "v1.c.d", "expires": 2,
                           "position": {"x": P2[0], "y": P2[1], "z": P2[2]}})
        ch2 = FakeChannel()
        peer._wire_channel(ch2)
        ch2.emit("open")
        return ch1.sent, ch2.sent

    first, second = _run(go())
    assert first == [geometry_message(P1)] and second == [geometry_message(P2)], (first, second)


def test_a_grant_position_replaces_the_env_position():
    async def go():
        peer = _peer(dict(BASE, position_cm=(1, 2, 3), cap_url="http://sim.test/x", cap_secret="unit"))
        peer._adopt_grant({"display": "npc", "room": 1234, "position": {"x": P1[0], "y": P1[1], "z": P1[2]}})
        return peer.position_cm

    assert _run(go()) == P1


class _OneAttempt(ConnectorPeer):
    async def _session(self, http, grant):
        self.stop()
        return "stopped", 0.0


def test_no_position_warns_once_and_sends_nothing():
    """R3: neither a grant nor CONNECTOR_POSITION_GLOBAL_CM - exactly today's behaviour, plus ONE warning at start."""
    async def go():
        peer = _peer(dict(BASE), _OneAttempt)
        await peer.run()
        ch = FakeChannel()
        peer._wire_channel(ch)
        ch.emit("open")
        return peer.log_handler.lines, ch.sent

    lines, sent = _run(go())
    warns = [m for lvl, m in lines if lvl == "WARNING" and "no position" in m]
    assert len(warns) == 1, lines
    assert sent == []


def test_the_fetched_grant_carries_the_position():
    """The seam the sim's field has to cross: fetch_once rebuilds the grant from known keys, so it has to know this
    one. A malformed position is a malformed grant - a sim that sends a broken one is a bug, said loudly."""
    from aiohttp import web

    import common.joincap as joincap

    async def go():
        cases = []

        async def handle(request):
            return web.json_response(cases[-1])

        app = web.Application()
        app.router.add_post("/cap", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        base = {"display": "npc", "room": 7, "session_id": "s", "join_cap": "v1.a.b", "expires": 1}
        try:
            import aiohttp
            async with aiohttp.ClientSession() as http:
                url = f"http://127.0.0.1:{port}/cap"
                cases.append(dict(base, position={"x": P1[0], "y": P1[1], "z": P1[2]}))
                with_pos = await joincap.fetch_once(http, url, "sec")
                cases.append(dict(base))
                without = await joincap.fetch_once(http, url, "sec")
                cases.append(dict(base, position={"x": "east", "y": 2, "z": 3}))
                try:
                    await joincap.fetch_once(http, url, "sec")
                    broken = "accepted"
                except joincap.CapabilityUnavailable:
                    broken = "refused"
                return with_pos, without, broken
        finally:
            await runner.cleanup()

    with_pos, without, broken = _run(go())
    assert with_pos.get("position") == {"x": P1[0], "y": P1[1], "z": P1[2]}, with_pos
    assert "position" not in without, without
    assert broken == "refused"


def test_the_env_key():
    assert position_env("t", {}) is None
    assert position_env("t", {POSITION_ENV: "25612800, 25612800,2200"}) == P1
    for bad in ("1,2", "1,2,3,4", "a,b,c", "1.5,2,3"):
        try:
            position_env("t", {POSITION_ENV: bad})
        except SystemExit:
            continue
        raise AssertionError(f"{bad!r} should be refused")


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)):
        try:
            fn()
            print("PASS", name)
        except Exception as e:   # noqa: BLE001 - report every failure, not just the first
            failed += 1
            print("FAIL", name, "-", type(e).__name__, e)
    sys.exit(1 if failed else 0)
