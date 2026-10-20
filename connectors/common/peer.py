# Copyright (c) 2026 Legion Builds
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GNU General Public License, version 3 only; see the LICENSE file in the repository root.
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

A peer never gives up on its room. An attempt is one
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

The region path (RegionOfferPeer, near the end of this file) sends the offer through a region's
ProvisionVoiceAccountRequest instead; the direct path is unchanged.

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
import base64
import dataclasses
import json
import logging
import time
import xml.etree.ElementTree as ET

import aiohttp
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp

from common.janus import KEEPALIVE_SECONDS, JanusHttp
from common.joincap import DEFAULT_BACKOFF, fetch_until_granted

#: The identity quaternion a viewer sends as x100 integers (the harness's send_geometry sends the same)
IDENTITY_ROT = {"x": 0, "y": 0, "z": 0, "w": 100}


def geometry_message(position_cm) -> str:
    """One SLData spatial message putting this peer at `position_cm` - GLOBAL centimetres as
    integers, the viewer's own frame (Firestorm llvoicewebrtc.cpp:1241-1262: sp, sh, lp, lh, each (int)(v * 100)).
    sp and lp are the same point (a connector has no camera), sh and lh the identity, as the harness's send_geometry
    sends them; byte for byte the same encoding. The mixer parses it at sldata.c:134-146."""
    pos = {"x": int(position_cm[0]), "y": int(position_cm[1]), "z": int(position_cm[2])}
    return json.dumps({"sp": pos, "sh": IDENTITY_ROT, "lp": pos, "lh": IDENTITY_ROT}, separators=(",", ":"))

#: (first delay, ceiling) in seconds between attempts; doubles between them.
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


def _new_peer_connection(cfg: dict) -> RTCPeerConnection:
    """A PeerConnection with this peer's ICE policy.

    `ice_servers` absent keeps aiortc's own default, which is a STUN lookup. `ice_servers: []` gathers
    HOST CANDIDATES ONLY, and for a peer whose mixer is on this host or this Docker network that is all
    it can ever use: the server-reflexive pair is never selected. It matters because gathering is
    synchronous with the join. MEASURED on this host, one peer, same code path otherwise:

        aiortc default   setLocalDescription  5026 ms   8 candidates (6 host + 2 srflx)
        ice_servers=[]   setLocalDescription    45 ms   6 candidates (host only)

    Five seconds, on every join, waiting on a public STUN server for a candidate the peer discards.
    """
    servers = cfg.get("ice_servers")
    if servers is None:
        return RTCPeerConnection()
    return RTCPeerConnection(RTCConfiguration(iceServers=list(servers)))


class ConnectorPeer:
    #: appended to the "shutting down" log line by subclasses (e.g. ", close wav")
    shutdown_note = ""
    #: Rejoin after a failed join or a lost room/session. The harness's TestPeer sets it False.
    rejoin = True
    #: Warn once at start when this peer has no position. The harness's TestPeer sets it False: it sends
    #: its own geometry (send_geometry).
    position_warning = True

    def __init__(self, cfg: dict, log: logging.Logger):
        self._cfg = cfg
        self._log = log
        self._pc = _new_peer_connection(cfg)
        self._answered = asyncio.Event()
        self._stopping = asyncio.Event()
        #: the Janus client (session/handle ids) once an attempt has attached
        self.janus: JanusHttp | None = None
        #: the SLData data channel once an attempt has created it
        self.channel = None
        #: This peer's position, GLOBAL centimetres (x, y, z) as integers, or None - from
        #: CONNECTOR_POSITION_GLOBAL_CM at start, replaced by the sim's grant at every join that carries one
        self.position_cm = cfg.get("position_cm")
        self._attempts = 0
        self._joined_at: float | None = None
        self._my_id = None
        self._probe_tx: set = set()

    # ---- subclass hooks

    def local_track(self):
        return None

    def join_extra(self) -> dict:
        """Extra members merged into the join request (a recording peer adds "recorder": true)."""
        return {}

    def on_identity(self, display: str, room: int) -> None:
        """The display and room this join uses, once a capability grant has supplied them."""
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
        if self.position_warning and self.position_cm is None and not cfg.get("cap_url"):
            # Exactly today's behaviour, said once.
            self._log.warning("no position: neither a capability grant (CONNECTOR_CAP_URL) nor "
                              "CONNECTOR_POSITION_GLOBAL_CM is configured, so the mixer has no geometry for this "
                              "connector and mixes it non-spatially - the same level to everyone in its room")
        try:
            async with aiohttp.ClientSession() as http:
                while not self._stopping.is_set():
                    attempt += 1
                    # A peer configured for capabilities fetches one before EVERY join and never joins
                    # without it. That includes every rejoin, so a capability is never reused.
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
            self._pc = _new_peer_connection(cfg)   # a closed PeerConnection cannot be reused; the first is __init__'s
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
                # the m-line but never emit a frame.
                self._pc.addTransceiver("audio", direction="sendrecv")
            else:
                # addTrack negotiates the same sendrecv m-line, carrying the track.
                self._pc.addTrack(track)
            channel = self._pc.createDataChannel("SLData")
            self._wire_channel(channel)
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

    def _wire_channel(self, channel) -> None:
        """The attempt's SLData channel. Once it opens, send this peer's position, so the mixer
        places it instead of mixing it flat to the whole room. Every attempt (so every rejoin) builds a new channel and
        sends again. The channel is ordered and reliable (aiortc's defaults: no maxRetransmits or maxPacketLifeTime),
        so one send per channel is enough, and the mixer keeps a session's last SLData until the next."""
        channel.on("message", self._on_sldata)
        self.channel = channel

        def on_open() -> None:
            if self.position_cm is None:
                return
            channel.send(geometry_message(self.position_cm))
            self._log.info("position sent on the data channel: global cm %s", ",".join(str(v) for v in self.position_cm))

        channel.on("open", on_open)

    def _adopt_grant(self, grant: dict) -> None:
        """The sim's grant is authoritative for display and room; an env value that disagrees loses, loudly."""
        cfg = self._cfg
        for key in ("display", "room"):
            if cfg.get(key) not in (None, "") and cfg[key] != grant[key]:
                self._log.warning("%s from the environment (%s) differs from the sim's capability grant (%s); "
                                  "using the grant", key.upper(), cfg[key], grant[key])
            cfg[key] = grant[key]
        # The sim computes the position at every fetch; it replaces whatever this peer held.
        pos = grant.get("position")
        if isinstance(pos, dict) and all(k in pos for k in ("x", "y", "z")):
            self.position_cm = (int(pos["x"]), int(pos["y"]), int(pos["z"]))
        self.on_identity(cfg["display"], cfg["room"])

    # ---- event plumbing

    async def _event_loop(self, janus: JanusHttp) -> str:
        """Events until the attempt ends; returns why. With rejoin off only a stop (or a hangup, which stops the
        peer) ends it, exactly as before rejoin existed."""
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
        """A room destroyed under a joined peer evicts it silently (no event reaches it), so a joined
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


# ---------------------------------------------------------------------------------------------------------------------
# The offer through a region: ProvisionVoiceAccountRequest
#
# A viewer never talks to Janus. It POSTs its WebRTC offer to its region's ProvisionVoiceAccountRequest capability as
# LLSD XML, and the region runs the admission checks (voice enabled, parcel, estate, channel), picks the mixer room and
# joins the mixer. This path sends the same request, so a test exercises the region's half of a join as well as the
# mixer's. The direct-to-Janus path above (ConnectorPeer) is unchanged.
#
# The body is the one the region's voice service reads (Addons/os-webrtc-janus/Janus/WebRtcJanusService.cs,
# ProvisionVoiceAccountRequestBAD): jsep {type: offer, sdp}, voice_server_type "webrtc", channel_type ("local" for the
# parcel or region room, "multiagent" for a call), channel (the call's session id, under `channel`, not `channel_id`),
# credentials (the call's credentials) and parcel_local_id (local only). The answer is {jsep, viewer_session, room}; a
# refusal is {response: "failed", error}, or an HTTP status other than 200 (409 room full). Teardown is
# {logout: true, viewer_session, voice_server_type} with no channel_type. The viewer's candidates go to the
# VoiceSignalingRequest capability, {candidate: {completed: true}, viewer_session, voice_server_type}.
# ---------------------------------------------------------------------------------------------------------------------

VOICE_SERVER_TYPE = "webrtc"
#: how long one capability POST may take
REGION_POST_TIMEOUT_S = 30.0
#: how long an answered offer may take to reach ICE connected
ICE_CONNECT_TIMEOUT_S = 20.0


def llsd_dumps(value) -> bytes:
    """LLSD XML for a Python value: dict (map), list (array), str, bool, int, float, bytes (binary), None (undef)."""
    root = ET.Element("llsd")
    root.append(_llsd_element(value))
    # An XML parser turns a raw CR (and CRLF) into LF; an SDP's CRLF line ends must arrive as sent, so escape them.
    return b'<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="utf-8").replace(b"\r", b"&#13;")


def _llsd_element(value) -> ET.Element:
    if value is None:
        return ET.Element("undef")
    if isinstance(value, bool):          # before int: bool is an int
        e = ET.Element("boolean")
        e.text = "true" if value else "false"
        return e
    if isinstance(value, int):
        e = ET.Element("integer")
        e.text = str(value)
        return e
    if isinstance(value, float):
        e = ET.Element("real")
        e.text = repr(value)
        return e
    if isinstance(value, str):
        e = ET.Element("string")
        e.text = value
        return e
    if isinstance(value, (bytes, bytearray)):
        e = ET.Element("binary")
        e.text = base64.b64encode(bytes(value)).decode("ascii")
        return e
    if isinstance(value, dict):
        e = ET.Element("map")
        for k, v in value.items():
            key = ET.SubElement(e, "key")
            key.text = str(k)
            e.append(_llsd_element(v))
        return e
    if isinstance(value, (list, tuple)):
        e = ET.Element("array")
        for v in value:
            e.append(_llsd_element(v))
        return e
    raise TypeError(f"no LLSD form for {type(value).__name__}")


def llsd_loads(data: bytes | str):
    """A Python value for an LLSD XML document: map -> dict, array -> list, integer -> int, real -> float,
    boolean -> bool, binary -> bytes, undef -> None; string, uuid, uri and date stay strings."""
    root = ET.fromstring(data)
    if root.tag != "llsd" or len(root) != 1:
        raise ValueError("not an LLSD XML document")
    return _llsd_value(root[0])


def _llsd_value(e: ET.Element):
    tag, text = e.tag, e.text or ""
    if tag == "undef":
        return None
    if tag == "boolean":
        return text.strip().lower() in ("true", "1")
    if tag == "integer":
        return int(text or 0)
    if tag == "real":
        return float(text or 0)
    if tag in ("string", "uuid", "uri", "date"):
        return text
    if tag == "binary":
        return base64.b64decode(text)
    if tag == "array":
        return [_llsd_value(c) for c in e]
    if tag == "map":
        out = {}
        children = list(e)
        for i in range(0, len(children) - 1, 2):
            if children[i].tag != "key":
                raise ValueError("a map entry must start with a key")
            out[children[i].text or ""] = _llsd_value(children[i + 1])
        return out
    raise ValueError(f"unknown LLSD element <{tag}>")


def provision_body(jsep: dict, *, channel_type: str = "local", channel: str | None = None,
                   credentials: str | None = None, parcel_local_id: int | None = None) -> dict:
    """The ProvisionVoiceAccountRequest body for an offer. `channel` and `credentials` are a call's session id and
    credentials (channel_type "multiagent"); `parcel_local_id` is for "local". Each is left out when not given, as the
    viewer leaves it out."""
    body = {"jsep": {"type": jsep["type"], "sdp": jsep["sdp"]}, "voice_server_type": VOICE_SERVER_TYPE,
            "channel_type": channel_type}
    if channel:
        body["channel"] = channel
    if credentials:
        body["credentials"] = credentials
    if parcel_local_id is not None:
        body["parcel_local_id"] = int(parcel_local_id)
    return body


def logout_body(viewer_session: str) -> dict:
    """The teardown body: no channel_type, routed by viewer_session."""
    return {"logout": True, "viewer_session": viewer_session, "voice_server_type": VOICE_SERVER_TYPE}


@dataclasses.dataclass
class RegionAnswer:
    """What the region answered a provision request with. `ok` is true only for a 200 that carries a jsep answer and a
    viewer_session; anything else keeps the status and the reply so a caller can judge it."""
    status: int
    reply: dict | None
    text: str | None
    jsep: dict | None = None
    viewer_session: str | None = None
    room: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.jsep is not None and self.viewer_session is not None

    @property
    def error(self) -> str | None:
        if self.ok:
            return None
        reply = self.reply or {}
        return str(reply.get("error") or self.text or f"HTTP {self.status}")


async def post_llsd(http: aiohttp.ClientSession, url: str, body: dict, timeout: float = REGION_POST_TIMEOUT_S):
    """POST a body as LLSD XML. Returns (status, reply as a dict or None, text when the reply was not LLSD).
    The URL is a capability: it is never put in an exception or a log line here."""
    headers = {"Content-Type": "application/llsd+xml", "Accept": "application/llsd+xml"}
    async with http.post(url, data=llsd_dumps(body), headers=headers,
                         timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        raw = await resp.read()
        status = resp.status
    if not raw.strip():
        return status, None, None
    try:
        reply = llsd_loads(raw)
    except Exception:
        return status, None, raw.decode("utf-8", "replace")[:2000]
    return status, reply if isinstance(reply, dict) else None, None


async def provision_via_region(http: aiohttp.ClientSession, url: str, jsep: dict, *, channel_type: str = "local",
                               channel: str | None = None, credentials: str | None = None,
                               parcel_local_id: int | None = None,
                               timeout: float = REGION_POST_TIMEOUT_S) -> RegionAnswer:
    """Send an offer through a region's ProvisionVoiceAccountRequest and read the jsep answer, the viewer_session and
    the mixer room back."""
    body = provision_body(jsep, channel_type=channel_type, channel=channel, credentials=credentials,
                          parcel_local_id=parcel_local_id)
    status, reply, text = await post_llsd(http, url, body, timeout)
    answer = RegionAnswer(status, reply, text)
    if reply:
        jsep_in = reply.get("jsep")
        if isinstance(jsep_in, dict) and jsep_in.get("sdp"):
            answer.jsep = {"type": jsep_in.get("type", "answer"), "sdp": jsep_in["sdp"]}
        vs = reply.get("viewer_session")
        answer.viewer_session = str(vs) if vs else None
        room = reply.get("room")
        answer.room = room if isinstance(room, int) else None
    return answer


async def logout_via_region(http: aiohttp.ClientSession, url: str, viewer_session: str,
                            timeout: float = REGION_POST_TIMEOUT_S) -> int:
    """End a provisioned voice session through the same capability; returns the HTTP status."""
    status, _, _ = await post_llsd(http, url, logout_body(viewer_session), timeout)
    return status


async def signal_via_region(http: aiohttp.ClientSession, url: str, viewer_session: str, candidate: dict,
                            timeout: float = REGION_POST_TIMEOUT_S) -> int:
    """Send the viewer's candidate (or {"completed": True}) through VoiceSignalingRequest; returns the HTTP status."""
    body = {"candidate": candidate, "viewer_session": viewer_session, "voice_server_type": VOICE_SERVER_TYPE}
    status, _, _ = await post_llsd(http, url, body, timeout)
    return status


class RegionOfferError(Exception):
    """The region refused the offer, or the media did not come up. The message never carries a capability URL."""


class RegionOfferPeer:
    """A peer that joins through a region instead of directly to Janus: one PeerConnection, one offer sent through
    the region's ProvisionVoiceAccountRequest, the answer applied, then a wait for ICE to connect.

    cfg keys: provision_url (required, the capability URL), signaling_url (VoiceSignalingRequest; optional, when set
    the end of candidates is signalled as a viewer does), channel_type ("local"), channel, credentials,
    parcel_local_id, ice_servers (as ConnectorPeer: absent keeps aiortc's STUN default, [] is host candidates only),
    position_cm (sent on the SLData channel once it opens, as ConnectorPeer does).

    local_track() returns the media to send, or None for a silent sendrecv m-line, as ConnectorPeer.local_track does.
    """

    def __init__(self, cfg: dict, log: logging.Logger):
        self._cfg = cfg
        self._log = log
        self._pc = _new_peer_connection(cfg)
        self.viewer_session: str | None = None
        self.room: int | None = None
        self.answer: RegionAnswer | None = None
        self.channel = None
        self.ice_states: list = []
        self._http: aiohttp.ClientSession | None = None
        self._connected = asyncio.Event()
        self._failed = asyncio.Event()

    def local_track(self):
        return None

    async def connect(self, timeout: float = ICE_CONNECT_TIMEOUT_S) -> RegionAnswer:
        """Offer, answer, ICE. Returns the region's answer once ICE is connected. Raises RegionOfferError when the
        region refused (the answer is on `.answer`) or ICE did not connect in `timeout`."""
        cfg = self._cfg
        self._http = aiohttp.ClientSession()
        pc = self._pc
        track = self.local_track()
        if track is None:
            pc.addTransceiver("audio", direction="sendrecv")
        else:
            pc.addTrack(track)
        channel = pc.createDataChannel("SLData")
        self.channel = channel
        position = cfg.get("position_cm")
        if position is not None:
            channel.on("open", lambda: channel.send(geometry_message(position)))

        @pc.on("iceconnectionstatechange")
        def _on_ice() -> None:
            state = pc.iceConnectionState
            self.ice_states.append(state)
            self._log.info("ice connection state: %s", state)
            if state in ("connected", "completed"):
                self._connected.set()
            elif state in ("failed", "closed"):
                self._failed.set()

        await pc.setLocalDescription(await pc.createOffer())
        local = pc.localDescription
        answer = await provision_via_region(
            self._http, cfg["provision_url"], {"type": local.type, "sdp": local.sdp},
            channel_type=cfg.get("channel_type", "local"), channel=cfg.get("channel"),
            credentials=cfg.get("credentials"), parcel_local_id=cfg.get("parcel_local_id"))
        self.answer = answer
        if not answer.ok:
            raise RegionOfferError(f"the region refused the offer: HTTP {answer.status}, {answer.error}")
        self.viewer_session, self.room = answer.viewer_session, answer.room
        self._log.info("answer from the region: viewer_session=%s room=%s", self.viewer_session, self.room)
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer.jsep["sdp"], type=answer.jsep["type"]))
        if cfg.get("signaling_url"):
            status = await signal_via_region(self._http, cfg["signaling_url"], self.viewer_session, {"completed": True})
            self._log.info("end of candidates signalled: HTTP %d", status)
        if pc.iceConnectionState in ("connected", "completed"):
            self._connected.set()
        waiters = [asyncio.ensure_future(self._connected.wait()), asyncio.ensure_future(self._failed.wait())]
        try:
            await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()
        if not self._connected.is_set():
            why = "ICE failed" if self._failed.is_set() else f"ICE did not connect within {timeout:.0f} s"
            raise RegionOfferError(f"{why} (states: {','.join(self.ice_states) or 'none'})")
        return answer

    async def hangup(self) -> int | None:
        """Logout through the region (when provisioned), then close. Returns the logout's HTTP status, or None."""
        status = None
        try:
            if self.viewer_session and self._http is not None:
                status = await logout_via_region(self._http, self._cfg["provision_url"], self.viewer_session)
                self._log.info("logout through the region: HTTP %d", status)
        finally:
            await self._pc.close()
            if self._http is not None:
                await self._http.close()
        return status
