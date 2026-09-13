"""Plumbing for the two-peer integration harness (tests/integration/README.md).

Everything Janus-shaped is reused from connectors/common: JanusHttp for the client API and
ConnectorPeer for join + PeerConnection + data channel + teardown. This module adds what a
viewer does that a connector peer does not (a real Opus tone, SLData on the data channel, a
crash without a leave), the admin-API oracle (handle_info, peer_ctl_batch), a control handle
for room-level plugin requests (create / list / listparticipants / destroy), and the polling
helper every expectation goes through.
"""

from __future__ import annotations

import array
import asyncio
import fractions
import json
import logging
import math
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_CONNECTORS = REPO / "connectors"
if str(_CONNECTORS) not in sys.path:
    sys.path.insert(0, str(_CONNECTORS))

import aiohttp  # noqa: E402
import av  # noqa: E402
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack  # noqa: E402

from common.janus import PLUGIN, JanusHttp  # noqa: E402
from common.peer import ConnectorPeer  # noqa: E402

#: test rooms live far above anything CalcRoomNumber hands out for real parcels/estates
ROOM_BASE = 900_000_000
#: every oracle expectation polls up to this long, in these steps
POLL_TIMEOUT = 5.0
POLL_STEP = 0.1

ERR_NO_SUCH_ROOM = 485
ERR_ROOM_EXISTS = 486

#: the handle_info plugin_specific keys a FAIL line prints (the rest is noise)
ORACLE_KEYS = ("room", "display", "id", "ice_state", "webrtc_up", "datachannel_open", "rtp_in_count",
               "last_rms", "peer_ctl_entries", "peer_ctl_full_drops", "mod_muted_entries",
               "excluded_entries", "last_data_fields_seen", "last_msg_fields_seen", "room_participants")

log = logging.getLogger("integration")


class Fail(Exception):
    """An expectation that did not hold; `observed` is printed on the FAIL line."""

    def __init__(self, what: str, observed=None):
        super().__init__(what)
        self.what = what
        self.observed = observed


class Skip(Exception):
    """The scenario was deliberately not run (e.g. S4 with --no-restart)."""


@dataclass
class Config:
    janus_url: str
    admin_url: str
    api_secret: str
    admin_secret: str
    compose_file: Path
    grace: int
    restart: bool


def read_env(path: Path) -> dict:
    """KEY=VALUE lines of a compose .env; comments and blanks skipped, surrounding quotes stripped."""
    env: dict = {}
    if not path.is_file():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def new_display() -> str:
    """A display is an agent UUID string, as the sim sets it (spec §3.2)."""
    return str(uuid.uuid4())


def fields(csv) -> set:
    """handle_info's last_*_fields_seen ("sp,sh,lp,lh,ug") as a set."""
    return {f for f in (csv or "").split(",") if f}


def displays(rows) -> list:
    return [row.get("display") for row in (rows or [])]


def pick(info):
    if not isinstance(info, dict):
        return info
    return {k: info[k] for k in ORACLE_KEYS if k in info}


async def until(probe, pred, what: str, timeout: float = POLL_TIMEOUT, step: float = POLL_STEP,
                show=lambda v: v):
    """Poll `probe()` until `pred(value)` holds; Fail(what, last value) once `timeout` passes."""
    deadline = time.monotonic() + timeout
    while True:
        value = await probe()
        try:
            ok = bool(pred(value))
        except Exception:
            ok = False
        if ok:
            return value
        if time.monotonic() >= deadline:
            raise Fail(what, show(value))
        await asyncio.sleep(step)


# ---- media ------------------------------------------------------------------------------------

class ToneTrack(MediaStreamTrack):
    """A real-time 440 Hz sine in 20 ms mono s16 frames at 48 kHz. aiortc encodes it as Opus, so
    the mixer receives a genuine stream: rtp_in_count climbs and last_rms is non-trivial."""

    kind = "audio"
    RATE = 48000
    SAMPLES = 960

    def __init__(self, freq: float = 440.0, amplitude: float = 0.3):
        super().__init__()
        amp = int(32767 * amplitude)
        # One second holds exactly 440 cycles, so a 48000-sample table loops seamlessly.
        self._table = array.array("h", (int(amp * math.sin(2.0 * math.pi * freq * i / self.RATE))
                                        for i in range(self.RATE)))
        self._pos = 0
        self._pts = 0
        self._start = None

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError
        if self._start is None:
            self._start = time.time()
        else:
            wait = self._start + self._pts / self.RATE - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
        end = self._pos + self.SAMPLES
        if end <= self.RATE:
            chunk = self._table[self._pos:end]
        else:
            chunk = self._table[self._pos:] + self._table[:end - self.RATE]
        self._pos = end % self.RATE
        frame = av.AudioFrame(format="s16", layout="mono", samples=self.SAMPLES)
        frame.planes[0].update(chunk.tobytes())
        frame.pts = self._pts
        frame.sample_rate = self.RATE
        frame.time_base = fractions.Fraction(1, self.RATE)
        self._pts += self.SAMPLES
        return frame


# ---- peers ------------------------------------------------------------------------------------

class TestPeer(ConnectorPeer):
    """A viewer-shaped peer: ConnectorPeer's join / PeerConnection / data channel lifecycle, plus a
    tone, viewer SLData, and crash() -- the PeerConnection dies and the peer tells Janus nothing more
    (no leave, no detach), as a crashed or unplugged viewer would."""

    def __init__(self, cfg: Config, name: str, room: int, display: str):
        super().__init__({"janus_url": cfg.janus_url, "api_secret": cfg.api_secret,
                          "room": room, "display": display},
                         logging.getLogger(f"integration.peer.{name}"))
        self.name = name
        self.room = room
        self.display = display
        self.crashed = False
        self._join_result: asyncio.Future = asyncio.get_running_loop().create_future()
        self._task: asyncio.Task | None = None

    def __repr__(self) -> str:
        sid, hid = self.ids
        return f"{self.name}(display={self.display[:8]}, room={self.room}, session={sid}, handle={hid})"

    def local_track(self):
        return ToneTrack()

    def on_plugin_event(self, data: dict) -> None:
        if self._join_result.done():
            return
        if data.get("audiobridge") == "joined" or "error_code" in data:
            self._join_result.set_result(data)

    async def start(self, timeout: float = 10.0) -> "TestPeer":
        self._task = asyncio.create_task(self.run(), name=f"peer-{self.name}")
        done, _ = await asyncio.wait({self._task, self._join_result}, timeout=timeout,
                                     return_when=asyncio.FIRST_COMPLETED)
        if self._join_result.done():
            data = self._join_result.result()
            if "error_code" in data:
                raise Fail(f"{self.name} join into room {self.room} refused",
                           {"error_code": data.get("error_code"), "error": data.get("error")})
            return self
        if self._task in done:
            raise Fail(f"{self.name} peer died before joining room {self.room}", repr(self._task.exception()))
        raise Fail(f"{self.name} got no join answer within {timeout:.0f} s", None)

    @property
    def ids(self) -> tuple:
        return (self.janus.session_id, self.janus.handle_id) if self.janus else (None, None)

    async def wait_channel_open(self, timeout: float = POLL_TIMEOUT) -> None:
        """Our side of the SLData channel is writable (the mixer's own view is datachannel_open)."""
        await until(self._channel_state, lambda s: s == "open",
                    f"{self.name} SLData data channel open", timeout)

    async def _channel_state(self):
        return self.channel.readyState if self.channel is not None else None

    def send(self, obj: dict) -> None:
        self.channel.send(json.dumps(obj, separators=(",", ":")))

    def send_join(self) -> None:
        """The viewer's own presence marker once its data interface is attached (llvoicewebrtc.cpp:3477-3494)."""
        self.send({"j": {"p": True}})

    def send_geometry(self, x: float = 128.0, y: float = 128.0, z: float = 25.0) -> None:
        """One viewer spatial message (llvoicewebrtc.cpp:1239-1266): positions and quaternions as int x100."""
        pos = {"x": int(x * 100), "y": int(y * 100), "z": int(z * 100)}
        rot = {"x": 0, "y": 0, "z": 0, "w": 100}
        self.send({"sp": pos, "sh": rot, "lp": pos, "lh": rot})

    async def crash(self) -> None:
        self.crashed = True
        await self._pc.close()

    async def _shutdown(self, janus: JanusHttp) -> None:
        if self.crashed:
            # A crashed viewer sends nothing: no leave, detach or destroy (Ctx.teardown reaps the session).
            await self._pc.close()
            self.on_closed()
            return
        await super()._shutdown(janus)

    async def close(self, timeout: float = 10.0) -> None:
        """leave + detach + destroy (skipped for a crashed peer) and close the PeerConnection."""
        if self._task is None:
            return
        self.stop()
        try:
            await asyncio.wait_for(self._task, timeout)
        except Exception:
            pass   # a peer whose Janus died (S4) or that never joined still counts as closed


# ---- oracle and control -----------------------------------------------------------------------

class Admin:
    """The oracle: Janus Admin API over HTTP, admin_secret in the body (the sim's
    JanusAdminClient.BuildEnvelope)."""

    def __init__(self, cfg: Config, http: aiohttp.ClientSession):
        self._url = cfg.admin_url
        self._secret = cfg.admin_secret
        self._http = http

    async def _post(self, path: str, body: dict) -> dict:
        body = {"transaction": uuid.uuid4().hex[:12], "admin_secret": self._secret, **body}
        async with self._http.post(f"{self._url}{path}", json=body) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def ping(self) -> dict:
        return await self._post("", {"janus": "ping"})

    async def handle_info(self, peer: TestPeer):
        """The handle's plugin_specific (query_session), or None once the handle or session is gone."""
        sid, hid = peer.ids
        if sid is None or hid is None:
            return None
        try:
            data = await self._post(f"/{sid}/{hid}", {"janus": "handle_info"})
        except aiohttp.ClientError:
            return None
        if data.get("janus") != "success":
            return None
        return (data.get("info") or {}).get("plugin_specific") or {}

    async def peer_ctl_batch(self, room: int, op: str = "replace", mute: dict | None = None,
                             excl: dict | None = None) -> dict:
        """The sim's peer_ctl_batch (PeerCtlBatchSerializer.BuildRequest + the sink's room stamp):
        "excl" always present, "mute" always present on a replace (empty = no change), and a
        listener key with an empty array clears that listener."""
        request = {"request": "peer_ctl_batch", "op": op, "excl": excl or {}, "room": room}
        if op == "replace" or mute:
            request["mute"] = mute or {}
        data = await self._post("", {"janus": "message_plugin", "plugin": PLUGIN, "request": request})
        if data.get("janus") != "success":
            raise Fail("peer_ctl_batch refused by the Admin API", data.get("error"))
        return data.get("response") or {}


class BatchResender:
    """Re-sends one peer_ctl_batch every `period` seconds in the background, as the sim's feeder does."""

    def __init__(self, admin: Admin, room: int, mute: dict, period: float = 0.25):
        self._admin = admin
        self._room = room
        self._mute = mute
        self._period = period
        self._running = asyncio.Event()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.sent = 0
        self.last_response: dict | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        self._running.set()
        self._task = asyncio.create_task(self._loop(), name="batch-resender")

    async def _loop(self) -> None:
        while True:
            await self._running.wait()
            async with self._lock:
                try:
                    self.last_response = await self._admin.peer_ctl_batch(self._room, "replace", mute=self._mute)
                    self.sent += 1
                except Exception as e:
                    self.last_error = repr(e)
            await asyncio.sleep(self._period)

    async def pause(self) -> None:
        """Stop sending; returns once any send in flight has completed."""
        self._running.clear()
        async with self._lock:
            pass

    def resume(self) -> None:
        self._running.set()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass
            self._task = None


class Control:
    """A client-API handle for room-level plugin requests. The plugin answers asynchronously, so each
    reply is matched to its request by transaction from this handle's own long-poll."""

    def __init__(self, cfg: Config, http: aiohttp.ClientSession):
        self._janus = JanusHttp(cfg.janus_url, cfg.api_secret, http)
        self._events: dict = {}
        self._task: asyncio.Task | None = None

    async def open(self) -> "Control":
        await self._janus.create()
        await self._janus.attach()
        self._task = asyncio.create_task(self._poll(), name="control-poll")
        return self

    async def _poll(self) -> None:
        while True:
            try:
                event = await self._janus.poll()
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(0.2)
                continue
            if event and event.get("janus") == "event" and event.get("transaction"):
                self._events[event["transaction"]] = (event.get("plugindata") or {}).get("data") or {}

    async def request(self, body: dict, timeout: float = POLL_TIMEOUT) -> dict:
        ack = await self._janus.message(body)
        tx = ack.get("transaction")

        async def reply():
            return self._events.get(tx)

        data = await until(reply, lambda d: d is not None, f"plugin reply to '{body.get('request')}'",
                           timeout, step=0.02)
        self._events.pop(tx, None)
        return data

    async def create_room(self, room: int, description: str) -> None:
        data = await self.request({"request": "create", "room": room, "description": description})
        if data.get("audiobridge") != "created" and data.get("error_code") != ERR_ROOM_EXISTS:
            raise Fail(f"create room {room}", data)

    async def participants(self, room: int):
        """listparticipants rows, or None when the room does not exist (485)."""
        data = await self.request({"request": "listparticipants", "room": room})
        if data.get("error_code") == ERR_NO_SUCH_ROOM:
            return None
        if "participants" not in data:
            raise Fail(f"listparticipants {room}", data)
        return data["participants"]

    async def room_ids(self) -> set:
        data = await self.request({"request": "list"})
        return {r.get("room") for r in data.get("list") or []}

    async def destroy_room(self, room: int) -> None:
        await self.request({"request": "destroy", "room": room})

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass
        for step in (self._janus.detach, self._janus.destroy):
            try:
                await step()
            except Exception:
                pass


# ---- per-scenario context ---------------------------------------------------------------------

class Ctx:
    """One scenario's world: fresh control handle and rooms, and everything it must tear down."""

    def __init__(self, cfg: Config, http: aiohttp.ClientSession, admin: Admin, name: str, alloc_room):
        self.cfg = cfg
        self.http = http
        self.admin = admin
        self.name = name
        self.control: Control | None = None
        self.peers: list = []
        self.rooms: set = set()
        self.background: list = []
        self._alloc_room = alloc_room

    async def open(self) -> "Ctx":
        self.control = await Control(self.cfg, self.http).open()
        return self

    async def reopen_control(self) -> None:
        if self.control is not None:
            await self.control.close()
        self.control = await Control(self.cfg, self.http).open()

    def new_room(self) -> int:
        room = self._alloc_room()
        self.rooms.add(room)
        return room

    async def join(self, name: str, room: int, display: str | None = None) -> TestPeer:
        """create (486 = already there) then join, the sim's order."""
        await self.control.create_room(room, f"integration {self.name}")
        peer = TestPeer(self.cfg, name, room, display or new_display())
        self.peers.append(peer)
        return await peer.start()

    async def info(self, peer: TestPeer):
        return await self.admin.handle_info(peer)

    async def until_info(self, peer: TestPeer, pred, what: str, timeout: float = POLL_TIMEOUT) -> dict:
        return await until(lambda: self.admin.handle_info(peer), lambda i: i is not None and pred(i),
                           what, timeout, show=pick)

    async def until_participants(self, room: int, pred, what: str, timeout: float = POLL_TIMEOUT):
        return await until(lambda: self.control.participants(room), pred, what, timeout,
                           show=lambda rows: rows if rows is None else
                           [{"id": r.get("id"), "display": r.get("display"), "setup": r.get("setup")} for r in rows])

    async def ready(self, *peers: TestPeer) -> None:
        """Each peer's channel is open on both sides and its tone is reaching the mixer."""
        for p in peers:
            await p.wait_channel_open()
            await self.until_info(p, lambda i, p=p: i.get("room") == p.room and i.get("datachannel_open") is True
                                  and (i.get("rtp_in_count") or 0) > 0,
                                  f"{p.name} in room {p.room} with datachannel_open and rtp_in_count > 0")

    async def teardown(self) -> None:
        for task in self.background:
            try:
                await task.stop()
            except Exception:
                pass
        for peer in reversed(self.peers):
            await peer.close()
        # A crashed peer never told Janus anything; destroy its session so nothing leaks onward.
        for peer in self.peers:
            if peer.crashed and peer.janus is not None and peer.janus.session_id is not None:
                reaper = JanusHttp(self.cfg.janus_url, self.cfg.api_secret, self.http)
                reaper.session_id = peer.janus.session_id
                try:
                    await reaper.destroy()
                except Exception:
                    pass
        if self.control is not None:
            for room in sorted(self.rooms):
                try:
                    await self.control.destroy_room(room)
                except Exception:
                    pass
            await self.control.close()


# ---- docker -----------------------------------------------------------------------------------

async def compose(cfg: Config, *args: str, timeout: float = 180.0) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-f", str(cfg.compose_file), *args]
    return await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, cwd=str(cfg.compose_file.parent))


async def wait_mixer_up(cfg: Config, http: aiohttp.ClientSession, timeout: float = 90.0) -> float:
    """Poll the client API's /info until it answers 200; returns the seconds it took."""
    start = time.monotonic()

    async def probe():
        try:
            async with http.get(f"{cfg.janus_url}/info", timeout=aiohttp.ClientTimeout(total=2)) as resp:
                return resp.status
        except Exception as e:
            return repr(e)

    await until(probe, lambda s: s == 200, "mixer /info answering after the restart", timeout, step=0.5)
    return time.monotonic() - start
