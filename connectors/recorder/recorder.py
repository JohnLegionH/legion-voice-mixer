"""S-CON-4 recorder peer (docs/voice/connector-build-plan.md; connector-assessment §2).

A headless aiortc WebRTC peer that joins one janus.plugin.slvoice room as the
sim-registered connector NPC identity (DISPLAY = the NPC's agent UUID from the
sim's "[CONNECTOR] registered ... npc=... room=..." line) and writes the received
mixdown to rolling WAV segments. It never sends audio (the sendrecv transceiver
carries no track) and never sends SLData; with the connector registered sim-side,
its exclusion column is applied by the mixer (fan-out by display), so the
recording is subject to parcel/estate semantics by construction.

Janus HTTP API shapes used (mixer plugin verbs cited from src/janus_slvoice.c):
  create   POST {base}                       {"janus":"create", ...}
  attach   POST {base}/{session}             {"janus":"attach","plugin":"janus.plugin.slvoice"}
  join     POST {base}/{session}/{handle}    {"janus":"message","body":{"request":"join",
             "room":ROOM,"display":DISPLAY},"jsep":<offer>}     (join arm :1974)
  events   GET  {base}/{session}?maxev=1     long-poll; "joined" event carries the answer
  trickle  POST {"janus":"trickle","candidate":...}            (Janus->us applied; we send
             only {"completed":true} — aiortc embeds its candidates in the offer SDP)
  leave    POST {"janus":"message","body":{"request":"leave"}}  (leave arm :2129)
  keepalive POST {"janus":"keepalive"} every 30 s
  detach/destroy on shutdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import uuid

import aiohttp
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp

from segments import ContiguousFrameFeed, WavSegmentWriter, frame_pcm

log = logging.getLogger("recorder")

KEEPALIVE_SECONDS = 30
PLUGIN = "janus.plugin.slvoice"


def env_config() -> dict:
    cfg = {
        "janus_url": os.environ.get("JANUS_URL", "http://janus:14223/voice").rstrip("/"),
        "api_secret": os.environ.get("JANUS_API_SECRET", ""),
        "room": os.environ.get("ROOM"),
        "display": os.environ.get("DISPLAY"),
        "out_dir": os.environ.get("OUT_DIR", "/recordings"),
        "segment_seconds": int(os.environ.get("SEGMENT_SECONDS", "600")),
        "log_level": os.environ.get("LOG_LEVEL", "INFO").upper(),
    }
    missing = [k for k in ("room", "display") if not cfg[k]]
    if missing:
        sys.exit(f"recorder: missing required env {', '.join(m.upper() for m in missing)} "
                 f"— copy them from the sim's '[CONNECTOR] registered ... npc=<DISPLAY> room=<ROOM>' line")
    try:
        cfg["room"] = int(cfg["room"])
    except ValueError:
        sys.exit(f"recorder: ROOM must be an integer room number, got {cfg['room']!r}")
    return cfg


class JanusHttp:
    """Minimal Janus HTTP transport client (deliberately small — build plan D5: a
    later port should move little)."""

    def __init__(self, base: str, api_secret: str, http: aiohttp.ClientSession):
        self._base = base
        self._secret = api_secret
        self._http = http
        self.session_id: int | None = None
        self.handle_id: int | None = None

    def _body(self, janus: str, **extra) -> dict:
        body = {"janus": janus, "transaction": uuid.uuid4().hex[:12], **extra}
        if self._secret:
            body["apisecret"] = self._secret
        return body

    async def _post(self, path: str, body: dict) -> dict:
        async with self._http.post(f"{self._base}{path}", json=body) as resp:
            resp.raise_for_status()
            data = await resp.json()
        if data.get("janus") == "error":
            raise RuntimeError(f"janus error on {body['janus']}: {data.get('error')}")
        return data

    async def create(self) -> None:
        data = await self._post("", self._body("create"))
        self.session_id = data["data"]["id"]

    async def attach(self) -> None:
        data = await self._post(f"/{self.session_id}", self._body("attach", plugin=PLUGIN))
        self.handle_id = data["data"]["id"]

    async def message(self, body: dict, jsep: dict | None = None) -> dict:
        msg = self._body("message", body=body)
        if jsep is not None:
            msg["jsep"] = jsep
        return await self._post(f"/{self.session_id}/{self.handle_id}", msg)

    async def trickle_completed(self) -> None:
        await self._post(f"/{self.session_id}/{self.handle_id}",
                         self._body("trickle", candidate={"completed": True}))

    async def keepalive(self) -> None:
        await self._post(f"/{self.session_id}", self._body("keepalive"))

    async def detach(self) -> None:
        await self._post(f"/{self.session_id}/{self.handle_id}", self._body("detach"))

    async def destroy(self) -> None:
        await self._post(f"/{self.session_id}", self._body("destroy"))

    async def poll(self) -> dict | None:
        """One long-poll turn. Returns the event, or None on the transport's own
        keepalive timeout answer."""
        params = {"maxev": "1"}
        if self._secret:
            params["apisecret"] = self._secret
        async with self._http.get(f"{self._base}/{self.session_id}", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return None if data.get("janus") == "keepalive" else data


class Recorder:
    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._pc = RTCPeerConnection()
        self._writer = WavSegmentWriter(cfg["out_dir"], cfg["display"], cfg["segment_seconds"])
        self._answered = asyncio.Event()
        self._stopping = asyncio.Event()
        self._track_task: asyncio.Task | None = None

    async def run(self) -> None:
        cfg = self._cfg
        async with aiohttp.ClientSession() as http:
            janus = JanusHttp(cfg["janus_url"], cfg["api_secret"], http)
            await janus.create()
            await janus.attach()
            log.info("janus session=%s handle=%s", janus.session_id, janus.handle_id)

            # One audio transceiver, sendrecv with NO local track: we negotiate the
            # m-line but never emit a frame — the silent-participant shape the
            # assessment §2 describes (never active, audible=0, one encode slot).
            self._pc.addTransceiver("audio", direction="sendrecv")
            channel = self._pc.createDataChannel("SLData")
            channel.on("message", self._on_sldata)
            self._pc.on("track", self._on_track)

            offer = await self._pc.createOffer()
            # aiortc gathers ICE inside setLocalDescription, so the offer SDP the
            # join carries is complete — no client->Janus candidate trickling is
            # needed; the explicit completed-trickle below tells Janus so.
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
                    log.info("joined room %s as %s; %d existing participant(s)",
                             data.get("room"), self._cfg["display"],
                             len(data.get("participants") or []))
                jsep = event.get("jsep")
                if jsep and not self._answered.is_set():
                    await self._pc.setRemoteDescription(
                        RTCSessionDescription(sdp=jsep["sdp"], type=jsep["type"]))
                    self._answered.set()
                    log.info("answer applied; waiting for media")
            elif kind == "trickle":
                await self._apply_trickle(event.get("candidate") or {})
            elif kind == "webrtcup":
                log.info("webrtcup: PeerConnection is up")
            elif kind == "media":
                log.info("media event: type=%s receiving=%s",
                         event.get("type"), event.get("receiving"))
            elif kind == "hangup":
                log.warning("hangup from Janus (%s); stopping", event.get("reason"))
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
        except Exception as e:  # a malformed candidate must not kill the recorder
            log.warning("ignoring unparsable trickle candidate: %s", e)

    async def _keepalive_loop(self, janus: JanusHttp) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            try:
                await janus.keepalive()
            except Exception as e:
                log.warning("keepalive failed: %s", e)

    # ---- media and data

    def _on_track(self, track) -> None:
        if track.kind != "audio":
            return
        log.info("audio track received; writing segments to %s", self._cfg["out_dir"])
        self._track_task = asyncio.ensure_future(self._consume(track))

    async def _consume(self, track) -> None:
        import av  # aiortc dependency; imported here so segments.py stays stdlib-only
        resampler = None
        feed = ContiguousFrameFeed(self._writer, warn=log.warning)
        try:
            while not self._stopping.is_set():
                frame = await track.recv()
                # aiortc's Opus decoder yields packed s16 @48k already — the common
                # path needs no resampler at all. The defensive conversion for any
                # other format is kept; measured (2026-09-01), the resampler passes
                # sample counts through 1:1 and introduces no zero frames under any
                # pts/time_base regime — the live zero-fill was the PLANE SLACK, not
                # the resampler (see segments.frame_pcm).
                if frame.format.name == "s16" and not frame.format.is_planar:
                    outs = [frame]
                else:
                    if resampler is None:
                        resampler = av.AudioResampler(format="s16", layout=frame.layout.name,
                                                      rate=frame.sample_rate)
                    outs = resampler.resample(frame)
                for out in outs:
                    channels = len(out.layout.channels)
                    # frame_pcm slices to out.samples — NEVER the whole plane buffer:
                    # libopus allocates planes for its 120 ms max frame, so a 20 ms
                    # decode carries 5x its length in zeroed slack (the 2026-09-01
                    # first-recording defect: [960 real][4800 zeros] per frame).
                    feed.push(frame_pcm(bytes(out.planes[0]), out.samples, channels),
                              out.sample_rate, channels, out.pts, out.time_base)
        except Exception as e:
            if not self._stopping.is_set():
                log.warning("audio track ended: %s", e)

    def _on_sldata(self, message) -> None:
        # DEBUG-log the field letters only (top-level keys). Presence pushes are keyed
        # by agent UUID with inner j/l maps; power batches likewise — no content beyond
        # the UUIDs is logged (docs/voice/mixer-feed-protocol.md field shapes).
        try:
            keys = list(json.loads(message).keys())
        except Exception:
            keys = ["<unparsable>"]
        log.debug("SLData keys=%s", keys)

    # ---- teardown

    async def _shutdown(self, janus: JanusHttp) -> None:
        log.info("shutting down: leave, detach, destroy, close wav")
        if self._track_task:
            self._track_task.cancel()
        for step in (lambda: janus.message({"request": "leave"}),
                     janus.detach, janus.destroy):
            try:
                await step()
            except Exception as e:
                log.warning("teardown step failed (continuing): %s", e)
        await self._pc.close()
        self._writer.close()
        log.info("closed; %d segment(s) written", len(self._writer.segments_written))


def main() -> None:
    cfg = env_config()
    logging.basicConfig(level=cfg["log_level"],
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    recorder = Recorder(cfg)
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, recorder.stop)
        except NotImplementedError:
            pass  # Windows dev shell; the container is Linux
    try:
        loop.run_until_complete(recorder.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
