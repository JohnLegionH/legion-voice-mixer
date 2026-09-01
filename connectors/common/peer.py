"""The connector peer session lifecycle, shared by recorder and injector.

Everything Janus-shaped lives here: create/attach, the join (offer carried in
the join message's jsep — aiortc gathers ICE inside setLocalDescription, so no
client->Janus trickling is needed), the event long-poll loop, Janus->us trickle
candidates, the keepalive, and the leave/detach/destroy teardown. Subclasses
differ only in the media they attach and consume:

  local_track()        -> a MediaStreamTrack to SEND, or None to negotiate the
                          audio m-line sendrecv with no local track (the
                          recorder's silent-participant shape: never a frame,
                          audible=0, one encode slot)
  on_audio_track(t)    -> called once with the received (mixdown) track
  on_teardown()        -> cancel media tasks, before leave/detach/destroy
  on_closed()          -> after pc.close(): flush/close writers, final log

The SLData data channel is always opened (so presence flows and
presence_dropped_dc_closed stays quiet) and NEVER written to; incoming SLData
is DEBUG-logged by field letters only (top-level keys — presence pushes are
keyed by agent UUID with inner j/l maps; no content beyond the UUIDs is
logged; docs/voice/mixer-feed-protocol.md field shapes).
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp

from common.janus import KEEPALIVE_SECONDS, JanusHttp


class ConnectorPeer:
    #: appended to the "shutting down" log line by subclasses (e.g. ", close wav")
    shutdown_note = ""

    def __init__(self, cfg: dict, log: logging.Logger):
        self._cfg = cfg
        self._log = log
        self._pc = RTCPeerConnection()
        self._answered = asyncio.Event()
        self._stopping = asyncio.Event()

    # ---- subclass hooks

    def local_track(self):
        return None

    def on_audio_track(self, track) -> None:
        pass

    def on_teardown(self) -> None:
        pass

    def on_closed(self) -> None:
        pass

    # ---- lifecycle

    async def run(self) -> None:
        cfg = self._cfg
        async with aiohttp.ClientSession() as http:
            janus = JanusHttp(cfg["janus_url"], cfg["api_secret"], http)
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
            self._pc.on("track", self._on_track)

            offer = await self._pc.createOffer()
            await self._pc.setLocalDescription(offer)

            await janus.message(
                {"request": "join", "room": cfg["room"], "display": cfg["display"]},
                jsep={"type": self._pc.localDescription.type,
                      "sdp": self._pc.localDescription.sdp})
            await janus.trickle_completed()

            keepalive = asyncio.create_task(self._keepalive_loop(janus))
            try:
                await self._event_loop(janus)
            finally:
                keepalive.cancel()
                await self._shutdown(janus)

    def stop(self) -> None:
        self._stopping.set()

    # ---- event plumbing

    async def _event_loop(self, janus: JanusHttp) -> None:
        while not self._stopping.is_set():
            poll = asyncio.create_task(janus.poll())
            stop = asyncio.create_task(self._stopping.wait())
            done, pending = await asyncio.wait({poll, stop}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            if stop in done:
                break
            event = poll.result()
            if event is None:
                continue
            kind = event.get("janus")
            if kind == "event":
                data = (event.get("plugindata") or {}).get("data") or {}
                if data.get("audiobridge") == "joined":
                    self._log.info("joined room %s as %s; %d existing participant(s)",
                                   data.get("room"), self._cfg["display"],
                                   len(data.get("participants") or []))
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
                self._log.warning("hangup from Janus (%s); stopping", event.get("reason"))
                self._stopping.set()
            # acks and timeouts are uninteresting

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
                self._log.warning("keepalive failed: %s", e)

    # ---- media and data

    def _on_track(self, track) -> None:
        if track.kind != "audio":
            return
        self.on_audio_track(track)

    def _on_sldata(self, message) -> None:
        try:
            keys = list(json.loads(message).keys())
        except Exception:
            keys = ["<unparsable>"]
        self._log.debug("SLData keys=%s", keys)

    # ---- teardown

    async def _shutdown(self, janus: JanusHttp) -> None:
        self._log.info("shutting down: leave, detach, destroy%s", self.shutdown_note)
        self.on_teardown()
        for step in (lambda: janus.message({"request": "leave"}),
                     janus.detach, janus.destroy):
            try:
                await step()
            except Exception as e:
                self._log.warning("teardown step failed (continuing): %s", e)
        await self._pc.close()
        self.on_closed()
