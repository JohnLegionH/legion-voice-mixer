"""The connector peer session lifecycle, shared by recorder and injector.

Everything Janus-shaped lives here: create/attach, the join (offer carried in
the join message's jsep — aiortc gathers ICE inside setLocalDescription, so no
client->Janus trickling is needed), the event long-poll loop, Janus->us trickle
candidates, the keepalive, and the leave/detach/destroy teardown. Subclasses
differ only in the media they attach and consume:

  local_track()        -> a MediaStreamTrack to SEND, or None to negotiate the
                          audio m-line sendrecv with no local track (the
                          recorder's silent-participant shape: never a frame,
                          audible=0, one encode slot). Called once per attempt.
  on_audio_track(t)    -> called once per attempt with the received (mixdown) track
  on_plugin_event(d)   -> every plugin event's data ("joined", or an error_code)
  on_teardown()        -> end of each attempt: cancel media tasks, before leave/detach/destroy
  on_closed()          -> once, when the peer stops for good: flush/close writers, final log

Slice 0.8f (ledger O-99): a peer never gives up on its room. An attempt is one
capability fetch (when CONNECTOR_CAP_URL is configured), one Janus session and
one join. When the join fails (485 no such room, a capability refusal, no
answer, a transport error) or the room or session is lost later (a hangup, the
session gone, or the room destroyed under the peer, which the mixer does
silently and a periodic listparticipants probe detects), the attempt tears its
session down and the peer tries again after a backoff: 2 s doubling to 60 s,
back to 2 s after a join that stayed up 60 s. Every attempt fetches a fresh
capability; none is reused, and a configured peer never joins bare. One INFO
line per attempt names the reason and the next delay, never a secret, bearer or
capability. The integration harness's TestPeer turns this off (`rejoin`): its
scenarios read a single join's outcome.

The SLData data channel is always opened (so presence flows and
presence_dropped_dc_closed stays quiet). The connector peers NEVER write to it;
incoming SLData is DEBUG-logged by field letters only (top-level keys — presence
pushes are keyed by agent UUID with inner j/l maps; no content beyond the UUIDs is
logged; docs/voice/mixer-feed-protocol.md field shapes). `janus` and `channel` are
exposed once an attempt has created them, for the integration harness
(tests/integration/), which sends viewer-shaped SLData and reads the handle ids.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import aiohttp
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp

from common.janus import KEEPALIVE_SECONDS, JanusHttp
from common.joincap import DEFAULT_BACKOFF, fetch_until_granted

#: Slice 0.8f: (first delay, ceiling) in seconds between attempts; doubles between them.
REJOIN_BACKOFF = (2.0, 60.0)
#: a join that stayed up this long resets the backoff to its first delay
REJOIN_STABLE_S = 60.0
#: how often a joined peer asks the mixer whether it is still in its room
ROOM_PROBE_S = 5.0
#: a join with no answer at all by then is a failed attempt
JOIN_ANSWER_TIMEOUT_S = 30.0


class RejoinBackoff:
    """The delay before the next attempt, given how long the last one stayed joined."""

    def __init__(self, first: float = REJOIN_BACKOFF[0], ceiling: float = REJOIN_BACKOFF[1],
                 stable_s: float = REJOIN_STABLE_S):
        self._first, self._ceiling, self._stable_s = first, ceiling, stable_s
        self._next = first

    def next(self, up_for: float) -> float:
        if up_for >= self._stable_s:
            self._next = self._first
        delay = self._next
        self._next = min(self._next * 2, self._ceiling)
        return delay


def describe(e: Exception) -> str:
    """An exception as a log-safe reason. aiohttp's own text carries the request URL, and a long-poll URL carries the
    API secret as a query parameter, so an HTTP failure is reported by status alone and anything else by type."""
    if isinstance(e, aiohttp.ClientResponseError):
        return f"HTTP {e.status} from Janus"
    if isinstance(e, RuntimeError) and str(e).startswith("janus error"):
        return str(e)   # common/janus.py: the request verb and Janus's {code, reason}
    return type(e).__name__


class ConnectorPeer:
    #: appended to the "shutting down" log line by subclasses (e.g. ", close wav")
    shutdown_note = ""
    #: slice 0.8f: rejoin after a failed join or a lost room/session. The harness's TestPeer sets it False.
    rejoin = True

    def __init__(self, cfg: dict, log: logging.Logger):
        self._cfg = cfg
        self._log = log
        self._pc = RTCPeerConnection()
        self._answered = asyncio.Event()
        self._stopping = asyncio.Event()
        #: the Janus client (session/handle ids) once an attempt has attached
        self.janus: JanusHttp | None = None
        #: the SLData data channel once an attempt has created it
        self.channel = None
        self._attempts = 0
        self._joined_at: float | None = None
        self._my_id = None
        self._probe_tx: set = set()

    # ---- subclass hooks

    def local_track(self):
        return None

    def join_extra(self) -> dict:
        """Extra members merged into the join request (SC-96: a recording peer adds "recorder": true)."""
        return {}

    def on_identity(self, display: str, room: int) -> None:
        """Slice 0.7b: the display and room this join uses, once a capability grant has supplied them."""
        pass

    def on_audio_track(self, track) -> None:
        pass

    def on_plugin_event(self, data: dict) -> None:
        pass

    def on_teardown(self) -> None:
        pass

    def on_closed(self) -> None:
        pass

    # ---- lifecycle

    async def run(self) -> None:
        cfg = self._cfg
        first, ceiling = cfg.get("rejoin_backoff", REJOIN_BACKOFF)
        backoff = RejoinBackoff(first, ceiling, cfg.get("rejoin_stable_s", REJOIN_STABLE_S))
        attempt = 0
        try:
            async with aiohttp.ClientSession() as http:
                while not self._stopping.is_set():
                    attempt += 1
                    # Slice 0.7b: a peer configured for capabilities fetches one before EVERY join and never joins
                    # without it. Slice 0.8f: that includes every rejoin, so a capability is never reused.
                    grant = None
                    if cfg.get("cap_url"):
                        grant = await fetch_until_granted(http, cfg["cap_url"], cfg["cap_secret"], self._log,
                                                          self._stopping, cfg.get("cap_backoff", DEFAULT_BACKOFF))
                        if grant is None:
                            break   # stopped while waiting for a capability: nothing was created, nothing to tear down
                        self._adopt_grant(grant)
                    reason, up_for = await self._session(http, grant)
                    if self._stopping.is_set() or not self.rejoin:
                        break
                    delay = backoff.next(up_for)
                    self._log.info("join attempt %d ended: %s; rejoining in %.1f s%s", attempt, reason, delay,
                                   " with a fresh capability" if cfg.get("cap_url") else "")
                    if await self._wait(delay):
                        break
        finally:
            self.on_closed()

    async def _wait(self, delay: float) -> bool:
        """Sleep the backoff; True when the peer was stopped meanwhile."""
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
        return self._stopping.is_set()

    async def _session(self, http: aiohttp.ClientSession, grant: dict | None) -> tuple:
        """One attempt: a Janus session, one join, events until it ends. Returns (why it ended, seconds joined).
        Always leaves its session torn down."""
        cfg = self._cfg
        if self._attempts:
            self._pc = RTCPeerConnection()   # a closed PeerConnection cannot be reused; the first is __init__'s
        self._attempts += 1
        self._answered = asyncio.Event()
        self._joined_at = None
        self._my_id = None
        self._probe_tx = set()
        self.channel = None
        janus = JanusHttp(cfg["janus_url"], cfg["api_secret"], http)
        self.janus = janus
        keepalive = None
        try:
            await janus.create()
            await janus.attach()
            self._log.info("janus session=%s handle=%s", janus.session_id, janus.handle_id)

            track = self.local_track()
            if track is None:
                # One audio transceiver, sendrecv with NO local track: we negotiate
                # the m-line but never emit a frame (assessment §2).
                self._pc.addTransceiver("audio", direction="sendrecv")
            else:
                # addTrack negotiates the same sendrecv m-line, carrying the track.
                self._pc.addTrack(track)
            channel = self._pc.createDataChannel("SLData")
            channel.on("message", self._on_sldata)
            self.channel = channel
            self._pc.on("track", self._on_track)
            # The mixer's SLData (presence, power batches) does not come back on the channel created
            # above: the plugin sends with no label, so Janus opens its own "JanusDataChannel" toward
            # us (vendor/janus-gateway/src/sctp.c janus_sctp_send_data). Listen on whatever it opens.
            self._pc.on("datachannel", self._on_remote_channel)

            offer = await self._pc.createOffer()
            await self._pc.setLocalDescription(offer)

            join = {"request": "join", "room": cfg["room"], "display": cfg["display"], **self.join_extra()}
            if grant is not None:
                join["join_cap"] = grant["join_cap"]
                join["session_id"] = grant["session_id"]
            await janus.message(
                join,
                jsep={"type": self._pc.localDescription.type,
                      "sdp": self._pc.localDescription.sdp})
            await janus.trickle_completed()

            keepalive = asyncio.create_task(self._keepalive_loop(janus))
            reason = await self._event_loop(janus)
        except Exception as e:
            if not self.rejoin:
                raise
            reason = describe(e)
        finally:
            if keepalive is not None:
                keepalive.cancel()
            up_for = time.monotonic() - self._joined_at if self._joined_at is not None else 0.0
            await self._shutdown(janus)
        return reason, up_for

    def stop(self) -> None:
        self._stopping.set()

    def _adopt_grant(self, grant: dict) -> None:
        """The sim's grant is authoritative for display and room; an env value that disagrees loses, loudly."""
        cfg = self._cfg
        for key in ("display", "room"):
            if cfg.get(key) not in (None, "") and cfg[key] != grant[key]:
                self._log.warning("%s from the environment (%s) differs from the sim's capability grant (%s); "
                                  "using the grant", key.upper(), cfg[key], grant[key])
            cfg[key] = grant[key]
        self.on_identity(cfg["display"], cfg["room"])

    # ---- event plumbing

    async def _event_loop(self, janus: JanusHttp) -> str:
        """Events until the attempt ends; returns why. With rejoin off only a stop (or a hangup, which stops the
        peer) ends it, exactly as before slice 0.8f."""
        join_deadline = time.monotonic() + self._cfg.get("join_answer_timeout_s", JOIN_ANSWER_TIMEOUT_S)
        probe = None
        try:
            while not self._stopping.is_set():
                poll = asyncio.create_task(janus.poll())
                stop = asyncio.create_task(self._stopping.wait())
                timeout = None
                if self.rejoin and self._joined_at is None:
                    timeout = max(0.0, join_deadline - time.monotonic())
                done, pending = await asyncio.wait({poll, stop}, timeout=timeout,
                                                   return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                if stop in done:
                    break
                if not done:
                    return "no answer to the join"
                event = poll.result()
                if event is None:
                    continue
                kind = event.get("janus")
                if kind == "event":
                    data = (event.get("plugindata") or {}).get("data") or {}
                    if event.get("transaction") in self._probe_tx:
                        self._probe_tx.discard(event.get("transaction"))
                        lost = self._room_lost(data)
                        if lost:
                            return lost
                        continue
                    self.on_plugin_event(data)
                    if data.get("audiobridge") == "joined":
                        self._log.info("joined room %s as %s; %d existing participant(s)",
                                       data.get("room"), self._cfg["display"],
                                       len(data.get("participants") or []))
                        self._joined_at = time.monotonic()
                        self._my_id = data.get("id")
                        if self.rejoin and probe is None:
                            probe = asyncio.create_task(self._probe_loop(janus))
                    elif "error_code" in data and self.rejoin and self._joined_at is None:
                        why = f" ({data['reason']})" if data.get("reason") else ""
                        return f"join refused: {data.get('error_code')} {data.get('error')}{why}"
                    jsep = event.get("jsep")
                    if jsep and not self._answered.is_set():
                        await self._pc.setRemoteDescription(
                            RTCSessionDescription(sdp=jsep["sdp"], type=jsep["type"]))
                        self._answered.set()
                        self._log.info("answer applied; waiting for media")
                elif kind == "trickle":
                    await self._apply_trickle(event.get("candidate") or {})
                elif kind == "webrtcup":
                    self._log.info("webrtcup: PeerConnection is up")
                elif kind == "media":
                    self._log.info("media event: type=%s receiving=%s",
                                   event.get("type"), event.get("receiving"))
                elif kind == "hangup":
                    if self.rejoin:
                        return f"hangup from Janus ({event.get('reason')})"
                    self._log.warning("hangup from Janus (%s); stopping", event.get("reason"))
                    self._stopping.set()
                elif kind == "error" and self.rejoin:
                    err = event.get("error") or {}
                    return f"Janus session lost ({err.get('code')} {err.get('reason')})"
                # acks and timeouts are uninteresting
            return "stopped"
        finally:
            if probe is not None:
                probe.cancel()

    async def _probe_loop(self, janus: JanusHttp) -> None:
        """Slice 0.8f: a room destroyed under a joined peer evicts it silently (no event reaches it), so a joined
        peer asks for its room's participants every ROOM_PROBE_S; the answer is judged in _room_lost."""
        every = self._cfg.get("room_probe_s", ROOM_PROBE_S)
        while True:
            await asyncio.sleep(every)
            try:
                ack = await janus.message({"request": "listparticipants", "room": self._cfg["room"]})
            except Exception as e:
                self._log.warning("room probe failed: %s", describe(e))
                continue
            if len(self._probe_tx) > 16:
                self._probe_tx.clear()   # answers that never came; they are not coming now
            self._probe_tx.add(ack.get("transaction"))

    def _room_lost(self, data: dict) -> str | None:
        if "error_code" in data:
            return f"room lost: {data.get('error_code')} {data.get('error')}"
        ids = [p.get("id") for p in data.get("participants") or []]
        if self._my_id is not None and self._my_id not in ids:
            return f"room lost: no longer a participant of room {self._cfg['room']}"
        return None

    async def _apply_trickle(self, cand: dict) -> None:
        if cand.get("completed"):
            await self._pc.addIceCandidate(None)
            return
        try:
            ice = candidate_from_sdp(cand["candidate"])
            ice.sdpMid = cand.get("sdpMid")
            ice.sdpMLineIndex = cand.get("sdpMLineIndex")
            await self._pc.addIceCandidate(ice)
        except Exception as e:  # a malformed candidate must not kill the peer
            self._log.warning("ignoring unparsable trickle candidate: %s", e)

    async def _keepalive_loop(self, janus: JanusHttp) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            try:
                await janus.keepalive()
            except Exception as e:
                self._log.warning("keepalive failed: %s", describe(e))

    # ---- media and data

    def _on_track(self, track) -> None:
        if track.kind != "audio":
            return
        self.on_audio_track(track)

    def _on_remote_channel(self, channel) -> None:
        self._log.debug("remote data channel opened: %s", channel.label)
        channel.on("message", self._on_sldata)

    def _on_sldata(self, message) -> None:
        try:
            keys = list(json.loads(message).keys())
        except Exception:
            keys = ["<unparsable>"]
        self._log.debug("SLData keys=%s", keys)

    # ---- teardown

    async def _shutdown(self, janus: JanusHttp) -> None:
        """One attempt's teardown: media hooks, then leave/detach/destroy for whatever the attempt created. A join
        that never succeeded sends no leave when rejoining (the mixer would only answer 487 not joined)."""
        self._log.info("shutting down: leave, detach, destroy%s", self.shutdown_note)
        self.on_teardown()
        steps = []
        if janus.handle_id is not None:
            if self._joined_at is not None or not self.rejoin:
                steps.append(lambda: janus.message({"request": "leave"}))
            steps.append(janus.detach)
        if janus.session_id is not None:
            steps.append(janus.destroy)
        for step in steps:
            try:
                await step()
            except Exception as e:
                self._log.warning("teardown step failed (continuing): %s", describe(e))
        await self._pc.close()
