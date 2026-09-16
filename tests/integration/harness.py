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
from aioice.ice import TransportPolicy  # noqa: E402
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection  # noqa: E402
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
               "excluded_entries", "last_data_fields_seen", "last_msg_fields_seen", "room_participants",
               "last_mix_rms", "last_mix_rms_l", "last_mix_rms_r", "vis_row", "vis_listener_generation")

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
    join_timeout: int = 30
    #: S13: the TURN server the relay-only peer uses (e.g. the turn-test profile); empty = S13 is skipped
    turn_uri: str = ""
    turn_user: str = ""
    turn_pwd: str = ""
    #: a mixer started with `docker run` rather than compose (the 0.3 scratch mixer): S17 and S20 read its logs and
    #: restart it by this container name; empty = the compose service `janus`
    container: str = ""
    #: slice 0.4: the mixer's JS_JOIN_CAP_SECRET, so the harness can mint join capabilities as the sim does.
    #: Empty = S22-S24 skip (they cannot mint what the mixer would accept).
    join_cap_secret: str = ""


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

    def __init__(self, cfg: Config, name: str, room: int, display: str,
                 join_cap: str | None = None, session_id: str | None = None):
        super().__init__({"janus_url": cfg.janus_url, "api_secret": cfg.api_secret,
                          "room": room, "display": display},
                         logging.getLogger(f"integration.peer.{name}"))
        self.name = name
        self.room = room
        self.display = display
        #: Phase 0 slice 0.4: what the sim would send on the join; None sends neither key, as a pre-0.4 sim does.
        self.join_cap = join_cap
        self.session_id = session_id
        self.crashed = False
        #: SC-87: every {p, v} this peer received for each source display, in arrival order.
        self.dots: dict[str, list] = {}
        #: Phase 0: every presence notice this peer received, (display, "j" | "l"), in arrival order.
        self.presence: list = []
        self._join_result: asyncio.Future = asyncio.get_running_loop().create_future()
        self._task: asyncio.Task | None = None

    def __repr__(self) -> str:
        sid, hid = self.ids
        return f"{self.name}(display={self.display[:8]}, room={self.room}, session={sid}, handle={hid})"

    def join_extra(self) -> dict:
        """Slice 0.4: the join capability and the viewer-session id the sim sends, when this peer has them."""
        extra = {}
        if self.join_cap is not None:
            extra["join_cap"] = self.join_cap
        if self.session_id is not None:
            extra["session_id"] = self.session_id
        return extra

    def local_track(self):
        return ToneTrack()

    def _on_sldata(self, message) -> None:
        super()._on_sldata(message)
        try:
            obj = json.loads(message)
        except Exception:
            return
        if not isinstance(obj, dict):
            return
        # A power batch is {"<display>": {"p": <int>, "v": <bool>}}; presence is {"<display>": {"j": ...}}.
        for display, entry in obj.items():
            if isinstance(entry, dict) and isinstance(entry.get("p"), int) and not isinstance(entry.get("p"), bool):
                self.dots.setdefault(display, []).append((entry["p"], entry.get("v") is True))
            elif isinstance(entry, dict) and ("j" in entry or "l" in entry):
                self.presence.append((display, "j" if "j" in entry else "l"))

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

class RelayOnlyPeer(TestPeer):
    """A TestPeer that gathers ONLY relay candidates, through the TURN server in cfg.turn_uri (S13). Its offer carries
    nothing but `typ relay`, so its media reaches the mixer through the relay or not at all.

    aiortc 1.13 exposes no ICE transport policy, but aioice's Connection has one (TransportPolicy.RELAY). The peer
    connection builds one ICE gatherer per DTLS transport in its private __createDtlsTransport. This wraps that
    method on this instance only, so other peers in the process are untouched, and on each new gatherer:
    - sets the RELAY policy, which keeps host candidates out of the offer;
    - once gathering is done, detaches the host sockets from Connection._protocols, which is what pairing, checks and
      sending use. aioice's RELAY policy alone does NOT restrict traffic: the host sockets stay in _protocols and
      send connectivity checks straight to the remote candidates.
    The detached sockets are not closed until the peer closes. Closing one makes aioice's connection_lost push an
    end-of-stream marker into the connection's shared queue, and the DTLS layer then reads "Connection lost".
    Both findings came from S13 runs. S13 checks the offer SDP and this peer's nominated pairs rather than
    trusting any of it."""

    def __init__(self, cfg: Config, name: str, room: int, display: str):
        super().__init__(cfg, name, room, display)
        server = RTCIceServer(urls=cfg.turn_uri, username=cfg.turn_user, credential=cfg.turn_pwd)
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[server]))
        create = pc._RTCPeerConnection__createDtlsTransport
        self._detached = []   # host sockets taken out of ICE; closed only after the peer connection closes

        def relay_only_transport():
            dtls = create()
            connection = dtls.transport.iceGatherer._connection
            connection._transport_policy = TransportPolicy.RELAY
            gather = connection.gather_candidates

            async def gather_relay_only(*args, **kwargs):
                await gather(*args, **kwargs)
                kept = []
                for protocol in connection._protocols:
                    if protocol.local_candidate is not None and protocol.local_candidate.type == "relay":
                        kept.append(protocol)
                    else:
                        self._detached.append(protocol)
                connection._protocols = kept

            connection.gather_candidates = gather_relay_only
            return dtls

        pc._RTCPeerConnection__createDtlsTransport = relay_only_transport
        self._pc = pc

    async def close(self, timeout: float = 10.0) -> None:
        await super().close(timeout)
        for protocol in self._detached:
            if protocol.transport is not None:
                protocol.transport.close()
        self._detached.clear()

    def offered_candidates(self) -> list:
        """(type, "ip:port") for every a=candidate line in this peer's offer."""
        desc = self._pc.localDescription
        found = []
        for line in (desc.sdp.splitlines() if desc else []):
            if line.startswith("a=candidate:") and " typ " in line:
                parts = line.split()
                found.append((line.split(" typ ", 1)[1].split()[0], f"{parts[4]}:{parts[5]}"))
        return found

    def _connections(self) -> list:
        """The aioice Connections behind this peer's ICE transports, once each."""
        transports = [t.receiver.transport for t in self._pc.getTransceivers() if t.receiver.transport is not None]
        if self._pc.sctp is not None:
            transports.append(self._pc.sctp.transport)
        seen, found = set(), []
        for dtls in transports:
            connection = dtls.transport.iceGatherer._connection
            if id(connection) not in seen:
                seen.add(id(connection))
                found.append(connection)
        return found

    def relay_proof(self) -> dict:
        """What this peer's own ICE agent is using: the local candidate type of every nominated pair, and how many
        non-relay sockets remain. It is the relay proof S13 relies on. The mixer's selected pair cannot prove the relay
        on a published-port mixer: the relay's packets reach Janus through the port publish, so Janus sees them
        arrive from the gateway as prflx."""
        nominated, non_relay = [], 0
        for connection in self._connections():
            nominated += [pair.local_candidate.type for pair in connection._nominated.values()]
            non_relay += sum(1 for p in connection._protocols
                             if p.local_candidate is None or p.local_candidate.type != "relay")
        return {"nominated_local_types": sorted(nominated), "non_relay_sockets": non_relay}

    async def packets_received(self) -> int:
        """RTP packets this peer has received from the mixer (aiortc inbound-rtp stats)."""
        report = await self._pc.getStats()
        return sum(getattr(s, "packetsReceived", 0) or 0 for s in report.values()
                   if getattr(s, "type", "") == "inbound-rtp")


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

    async def handle_ice(self, peer: TestPeer):
        """The handle's webrtc.ice block (local and remote candidates, selected pair), or None once it is gone."""
        sid, hid = peer.ids
        if sid is None or hid is None:
            return None
        try:
            data = await self._post(f"/{sid}/{hid}", {"janus": "handle_info"})
        except aiohttp.ClientError:
            return None
        if data.get("janus") != "success":
            return None
        return ((data.get("info") or {}).get("webrtc") or {}).get("ice") or {}

    async def peer_ctl_batch(self, room: int, op: str = "replace", mute: dict | None = None,
                             excl: dict | None = None, epoch: str | None = None, generation: int | None = None,
                             base: dict | None = None) -> dict:
        """The sim's peer_ctl_batch (PeerCtlBatchSerializer.BuildRequest + the sink's room stamp):
        "excl" always present, "mute" always present on a replace (empty = no change), and a
        listener key with an empty array clears that listener. With `epoch`, the 0.2 sim's authority
        stamp (JanusPeerCtlBatchSink.StampAuthority): room_epoch and policy_generation after "room",
        and "base" on an add/remove."""
        request = {"request": "peer_ctl_batch", "op": op, "excl": excl or {}, "room": room}
        if op == "replace" or mute:
            request["mute"] = mute or {}
        if epoch is not None:
            request["room_epoch"] = epoch
            request["policy_generation"] = generation
            if base is not None:
                request["base"] = base
        return await self.plugin_request(request)

    async def plugin_request(self, request: dict) -> dict:
        """One Admin message_plugin to the slvoice plugin; its inner response."""
        data = await self._post("", {"janus": "message_plugin", "plugin": PLUGIN, "request": request})
        if data.get("janus") != "success":
            raise Fail(f"{request.get('request')} refused by the Admin API", data.get("error"))
        return data.get("response") or {}

    async def heartbeat(self, epoch: str, rooms: dict, interval_ms: int = 1000, stopping: bool = False) -> dict:
        """The 0.2 sim's peer_ctl_heartbeat (VisAuthority.BuildHeartbeat): rooms is {room: {"policy_generation": g,
        "listeners": {display: generation}}}."""
        request = {"request": "peer_ctl_heartbeat", "room_epoch": epoch, "interval_ms": interval_ms,
                   "rooms": {str(room): body for room, body in rooms.items()}}
        if stopping:
            request["state"] = "stopping"
        return await self.plugin_request(request)


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


class Heartbeater:
    """The sim's heartbeat (nonspatial-phase0-design.md §3) for one room: one peer_ctl_heartbeat every `period` seconds
    naming `listeners` ({display: generation}, mutable while running). `last_sent` is the monotonic time the latest
    heartbeat's reply arrived, the mixer's latest confirmation."""

    def __init__(self, admin: Admin, epoch: str, room: int, listeners: dict, generation: int = 1, period: float = 1.0):
        self._admin = admin
        self.epoch = epoch
        self._room = room
        self.listeners = dict(listeners)
        self.generation = generation
        self._period = period
        self._running = asyncio.Event()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.sent = 0
        self.last_sent = 0.0
        self.last_reply: dict | None = None
        self.last_error: str | None = None

    def start(self) -> "Heartbeater":
        self._running.set()
        self._task = asyncio.create_task(self._loop(), name="heartbeater")
        return self

    async def _loop(self) -> None:
        while True:
            await self._running.wait()
            async with self._lock:
                try:
                    body = {"policy_generation": self.generation, "listeners": dict(self.listeners)}
                    self.last_reply = await self._admin.heartbeat(self.epoch, {self._room: body})
                    self.last_sent = time.monotonic()
                    self.sent += 1
                except Exception as e:
                    self.last_error = repr(e)
            await asyncio.sleep(self._period)

    async def pause(self) -> float:
        """Stop sending; returns, once any heartbeat in flight has been answered, the time of the last confirmation."""
        self._running.clear()
        async with self._lock:
            return self.last_sent

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

    async def request(self, body: dict, timeout: float = POLL_TIMEOUT, jsep: dict | None = None) -> dict:
        ack = await self._janus.message(body, jsep=jsep)
        tx = ack.get("transaction")

        async def reply():
            return self._events.get(tx)

        data = await until(reply, lambda d: d is not None, f"plugin reply to '{body.get('request')}'",
                           timeout, step=0.02)
        self._events.pop(tx, None)
        return data

    async def create_room(self, room: int, description: str, vis_authority: bool = False) -> None:
        """create; 486 (already there) is fine. vis_authority: the 0.2 sim's declaration (AudioBridgeCreateRoomReq)."""
        body = {"request": "create", "room": room, "description": description}
        if vis_authority:
            body["vis_authority"] = True
        data = await self.request(body)
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


class NoMediaPeer(Control):
    """A participant whose PeerConnection never comes up (O-75, S10). It creates its Janus session,
    attaches, and joins with a real aiortc offer whose ICE candidates are stripped. It never trickles a
    candidate or end-of-candidates, and never applies the answer. Afterwards it only long-polls (which
    keeps the Janus session alive, as the sim's long-poll does for a viewer). Janus never gets a
    connectivity check, so setup_media never fires and there is no PeerConnection to hang up."""

    def __init__(self, cfg: Config, http: aiohttp.ClientSession, name: str, room: int, display: str,
                 join_cap: str | None = None, session_id: str | None = None):
        super().__init__(cfg, http)
        self.name = name
        self.room = room
        self.display = display
        #: Phase 0 slice 0.4: what the sim would send on the join; both None is a pre-0.4 join.
        self.join_cap = join_cap
        self.session_id = session_id
        self.crashed = False

    @property
    def ids(self) -> tuple:
        return (self._janus.session_id, self._janus.handle_id)

    async def join(self) -> dict:
        """The join reply, whatever it says: this peer is how a scenario reads a REFUSAL (error_code and the
        slice 0.4 `reason`), where TestPeer.start would raise."""
        pc = RTCPeerConnection()
        try:
            pc.addTransceiver("audio", direction="sendrecv")
            pc.createDataChannel("SLData")
            await pc.setLocalDescription(await pc.createOffer())
            sdp = "\r\n".join(line for line in pc.localDescription.sdp.splitlines()
                              if not line.startswith(("a=candidate:", "a=end-of-candidates"))) + "\r\n"
        finally:
            await pc.close()
        body = {"request": "join", "room": self.room, "display": self.display}
        if self.join_cap is not None:
            body["join_cap"] = self.join_cap
        if self.session_id is not None:
            body["session_id"] = self.session_id
        return await self.request(body, jsep={"type": "offer", "sdp": sdp})

    async def stop(self) -> None:
        await self.close()


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

    async def join(self, name: str, room: int, display: str | None = None, vis_authority: bool = False,
                   join_cap: str | None = None, session_id: str | None = None) -> TestPeer:
        """create (486 = already there) then join, the sim's order. Slice 0.4: join_cap / session_id are what the
        sim would send; both None is a pre-0.4 join."""
        await self.control.create_room(room, f"integration {self.name}", vis_authority=vis_authority)
        peer = TestPeer(self.cfg, name, room, display or new_display(), join_cap=join_cap, session_id=session_id)
        self.peers.append(peer)
        return await peer.start()

    async def join_relay_only(self, name: str, room: int, display: str | None = None) -> RelayOnlyPeer:
        """create (486 = already there) then join with a peer that offers ONLY relay candidates (cfg.turn_uri)."""
        await self.control.create_room(room, f"integration {self.name}")
        peer = RelayOnlyPeer(self.cfg, name, room, display or new_display())
        self.peers.append(peer)
        return await peer.start(timeout=20.0)

    async def join_without_media(self, name: str, room: int, display: str | None = None, vis_authority: bool = False,
                                 join_cap: str | None = None, session_id: str | None = None,
                                 expect_join: bool = True) -> NoMediaPeer:
        """create (486 = already there) then a join whose PeerConnection never comes up (NoMediaPeer). Slice 0.4:
        with expect_join False the reply is kept on the peer as `.join_reply` instead of raising, which is how a
        scenario reads a refusal's reason."""
        await self.control.create_room(room, f"integration {self.name}", vis_authority=vis_authority)
        peer = await NoMediaPeer(self.cfg, self.http, name, room, display or new_display(),
                                 join_cap=join_cap, session_id=session_id).open()
        self.background.append(peer)   # teardown stops it: detach + destroy its Janus session
        data = await peer.join()
        peer.join_reply = data
        if expect_join and data.get("audiobridge") != "joined":
            raise Fail(f"{name} join (no media) into room {room}", data)
        return peer

    async def info(self, peer):
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


async def mixer_restart(cfg: Config) -> subprocess.CompletedProcess:
    """Restart the mixer under test: the named container (cfg.container) or the compose service."""
    if cfg.container:
        return await asyncio.to_thread(subprocess.run, ["docker", "restart", cfg.container], capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", timeout=180)
    return await compose(cfg, "restart", "janus")


async def mixer_logs(cfg: Config, since: str) -> str:
    """The mixer's log since an RFC 3339 time, stdout and stderr together."""
    if cfg.container:
        res = await asyncio.to_thread(subprocess.run, ["docker", "logs", "--since", since, cfg.container],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    else:
        res = await compose(cfg, "logs", "--no-log-prefix", "--since", since, "janus")
    return (res.stdout or "") + (res.stderr or "")
