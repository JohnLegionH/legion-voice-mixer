"""Unit tests for the slice 0.8f rejoin loop (common/peer.py, ledger O-99). pytest-style; a __main__ fallback runs them
with plain python. Run from connectors/: `python -m pytest common/` or `python -m common.test_rejoin`.

The Janus/WebRTC half of an attempt (ConnectorPeer._session) is replaced by a script here; the integration harness's
S37 drives the real one against a mixer."""

import asyncio
import logging

import common.peer as peer_mod
from common.peer import REJOIN_BACKOFF, REJOIN_STABLE_S, ConnectorPeer, RejoinBackoff

BEARER = "unit-bearer-secret-0123456789abcdef"


def test_backoff_doubles_from_2_s_to_a_60_s_cap():
    b = RejoinBackoff()
    assert REJOIN_BACKOFF == (2.0, 60.0) and REJOIN_STABLE_S == 60.0
    assert [b.next(0.0) for _ in range(8)] == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]


def test_backoff_resets_after_a_join_that_stayed_up_60_s():
    b = RejoinBackoff()
    assert [b.next(0.0) for _ in range(4)] == [2.0, 4.0, 8.0, 16.0]
    assert b.next(59.9) == 32.0          # up, but not for long enough: keep backing off
    assert b.next(60.0) == 2.0           # stayed up 60 s: start again from the bottom
    assert b.next(0.0) == 4.0


class _Log(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelname, record.getMessage()))


class _Scripted(ConnectorPeer):
    """A peer whose attempts follow a script of (reason, seconds joined) outcomes; it stops when the script ends."""

    def __init__(self, cfg, outcomes):
        self.log_handler = _Log()
        log = logging.getLogger(f"test_rejoin.{id(self)}")
        log.setLevel(logging.DEBUG)
        log.addHandler(self.log_handler)
        super().__init__(cfg, log)
        self._outcomes = list(outcomes)
        self.sessions = []   # the grant each attempt joined with
        self.delays = []     # every backoff the loop waited
        self.closed = 0

    async def _session(self, http, grant):
        self.sessions.append(grant)
        reason, up_for = self._outcomes.pop(0)
        if not self._outcomes:
            self.stop()
        return reason, up_for

    async def _wait(self, delay):
        self.delays.append(delay)
        return self._stopping.is_set()

    def on_closed(self):
        self.closed += 1


def _configured():
    return {"janus_url": "http://janus.invalid/voice", "api_secret": "", "room": 7, "display": "npc",
            "cap_url": "http://sim.invalid/voice/connector/x/join-cap", "cap_secret": BEARER}


def _run(peer, fetches):
    """Run the peer with fetch_until_granted replaced by one that mints a fresh grant per call."""
    real = peer_mod.fetch_until_granted

    async def fake_fetch(http, url, secret, log, stopping, backoff):
        assert secret == BEARER
        fetches.append(None)
        if stopping.is_set():
            return None
        n = len(fetches)
        return {"display": "npc", "room": 7, "session_id": f"s{n}", "join_cap": f"cap-{n}", "expires": 0}

    peer_mod.fetch_until_granted = fake_fetch
    try:
        asyncio.run(peer.run())
    finally:
        peer_mod.fetch_until_granted = real


def test_each_attempt_fetches_its_own_capability_and_backs_off():
    fetches = []
    peer = _Scripted(_configured(), [("join refused: 485 No such room", 0.0)] * 4 + [("room lost", 75.0),
                                                                                      ("room lost", 0.0)])
    _run(peer, fetches)
    assert len(fetches) == 6 and len(peer.sessions) == 6
    caps = [g["join_cap"] for g in peer.sessions]
    assert caps == [f"cap-{n}" for n in range(1, 7)]          # a fresh one per attempt, never reused
    assert peer.delays == [2.0, 4.0, 8.0, 16.0, 2.0]            # reset after the attempt that stayed up 75 s
    assert peer.closed == 1                                     # on_closed once, when the peer finally stops


def test_one_info_line_per_attempt_with_reason_and_delay_and_no_secret():
    peer = _Scripted(_configured(), [("join refused: 485 No such room", 0.0)] * 3)
    _run(peer, [])
    lines = [m for lvl, m in peer.log_handler.lines if lvl == "INFO" and "rejoining in" in m]
    assert len(lines) == 2                                      # the third attempt ended because the peer stopped
    assert "485 No such room" in lines[0] and "2.0 s" in lines[0] and "4.0 s" in lines[1]
    everything = " ".join(m for _, m in peer.log_handler.lines)
    assert BEARER not in everything and "cap-1" not in everything and "cap-2" not in everything


def test_a_configured_peer_never_joins_without_a_capability():
    """The fetch comes back empty only when the peer is stopping; then no session is started at all."""
    peer = _Scripted(_configured(), [("join refused: 485 No such room", 0.0), ("unused", 0.0)])
    calls = []

    async def fetch_then_stop(http, url, secret, log, stopping, backoff):
        calls.append(None)
        if len(calls) == 1:
            return {"display": "npc", "room": 7, "session_id": "s1", "join_cap": "cap-1", "expires": 0}
        peer.stop()                                              # stopping lands while the second fetch is retrying
        return None

    real = peer_mod.fetch_until_granted
    peer_mod.fetch_until_granted = fetch_then_stop
    try:
        asyncio.run(peer.run())
    finally:
        peer_mod.fetch_until_granted = real
    assert len(calls) == 2 and len(peer.sessions) == 1
    assert all(g is not None and g["join_cap"] for g in peer.sessions)


def test_an_unconfigured_peer_joins_bare_and_never_fetches():
    fetches = []
    cfg = {k: v for k, v in _configured().items() if k not in ("cap_url", "cap_secret")}
    peer = _Scripted(cfg, [("join refused: 485 No such room", 0.0), ("room lost", 0.0)])
    _run(peer, fetches)
    assert fetches == [] and peer.sessions == [None, None]


def test_a_peer_with_rejoin_off_makes_one_attempt():
    """The integration harness's TestPeer turns rejoin off: one attempt, whatever its outcome."""
    peer = _Scripted(_configured(), [("join refused: 485 No such room", 0.0), ("unused", 0.0)])
    peer.rejoin = False
    _run(peer, [])
    assert len(peer.sessions) == 1 and peer.delays == [] and peer.closed == 1


if __name__ == "__main__":
    import sys
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                failed += 1
                print(f"FAIL {name}: {e!r}")
    sys.exit(1 if failed else 0)
