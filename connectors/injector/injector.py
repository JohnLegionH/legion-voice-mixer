"""S-CON-6 injector peer (docs/voice/connector-build-plan.md; brief Amendment 2).

A headless aiortc WebRTC peer that joins one janus.plugin.slvoice room exactly
as the S-CON-4 recorder does (display = the sim-registered connector NPC's
UUID, sendrecv) but SENDS audio: 20 ms mono 48 kHz Opus frames fed from a
PCM/WAV pipe or file (SOURCE). When the source is exhausted or idle it sends
silence frames — the track never stops. It also receives the room mixdown so
it stays a normal participant: discarded by default, written to WAV segments
via the recorder's writer when RECORD=1. The data channel is opened (presence
flows; presence_dropped_dc_closed stays quiet) and never written to.

Sim-side prerequisite: the [VoiceConnector.<name>] record must carry
MayInject=true — with MayInject=false the sim pushes a moderation mute at
registration and NOBODY hears this peer (that silence is S-CON-5(e), the
enforcement test, not a defect here). The NPC's own matrix row constrains the
injected audio like any avatar's (the inject-side mirror of the scope gate).

The Janus session/join/poll/keepalive/teardown plumbing is shared with the
recorder in common/peer.py; the framing/resampling lives in source.py
(stdlib-only, unit-tested).
"""

from __future__ import annotations

import asyncio
import fractions
import logging
import os
import signal
import stat
import threading
import time

import av
from aiortc.mediastreams import MediaStreamTrack

from common.config import base_env
from common.peer import ConnectorPeer
from common.receive import consume_audio
from common.segments import WavSegmentWriter
from injector.source import (FRAME_SAMPLES, RATE, SILENCE_FRAME, PcmFramer,
                             read_wav_header)

log = logging.getLogger("injector")


def env_config() -> dict:
    cfg = base_env("injector")
    cfg.update({
        "source": os.environ.get("SOURCE", "/injector/in.wav"),
        "loop": os.environ.get("LOOP", "0") == "1",
        "record": os.environ.get("RECORD", "0") == "1",
        "out_dir": os.environ.get("OUT_DIR", "/recordings"),
        "segment_seconds": int(os.environ.get("SEGMENT_SECONDS", "600")),
    })
    return cfg


class SourceReader:
    """Feeds a PcmFramer from SOURCE on a daemon thread.

    A thread because FIFO open/read BLOCK until a writer appears, and that must
    never stall the event loop (keepalives, long-poll). Behaviour by source
    kind, per the plan's pipe semantics:

    - FIFO: EOF means the writer closed; reopen and wait for the next writer
      (repeated injections through one pipe), regardless of LOOP;
    - regular file, LOOP=1: reopen at EOF and replay;
    - regular file, LOOP=0: play once, then silence (the reader ends; the
      track keeps sending SILENCE_FRAME — it never stops);
    - source absent: poll for it, silence meanwhile.

    Over-run is bounded: at most MAX_BUFFERED_FRAMES are held ahead of real
    time; the reader sleeps rather than ballooning memory when the file feeds
    faster than 48 kHz wall time (any regular file does).
    """

    MAX_BUFFERED_FRAMES = 50     # 1 s ahead of the paced track
    CHUNK = 8192

    def __init__(self, path: str, loop_file: bool):
        self._path = path
        self._loop = loop_file
        self._lock = threading.Lock()
        self._framer: PcmFramer | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="injector-source")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        # daemon thread: a FIFO open blocked waiting for a writer cannot be
        # interrupted, but it dies with the process.
        self._stop.set()

    def get_frame(self) -> bytes | None:
        with self._lock:
            return self._framer.get_frame() if self._framer is not None else None

    # ---- reader thread

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                is_fifo = stat.S_ISFIFO(os.stat(self._path).st_mode)
            except OSError:
                log.info("source %s not present; sending silence until it appears",
                         self._path)
                if self._stop.wait(1.0):
                    return
                continue
            try:
                self._play_once(self._path)
            except ValueError as e:
                log.warning("source rejected: %s", e)
                if not is_fifo and not self._loop:
                    return               # a bad file would reject forever; don't spin
                if self._stop.wait(2.0):
                    return
                continue
            except OSError as e:
                log.warning("source read failed: %s", e)
                if self._stop.wait(1.0):
                    return
                continue
            if self._stop.is_set():
                return
            if is_fifo:
                log.info("source EOF (pipe writer closed); waiting for the next writer")
            elif self._loop:
                log.info("source EOF; LOOP=1 — replaying")
            else:
                log.info("source EOF; LOOP=0 — sending silence from here on")
                return

    def _play_once(self, path: str) -> None:
        with open(path, "rb") as f:
            rate, channels = read_wav_header(f)
            log.info("source open: %s (%d Hz, %d ch)", path, rate, channels)
            with self._lock:
                self._framer = PcmFramer(rate, channels)
            while not self._stop.is_set():
                with self._lock:
                    full = self._framer.buffered_frames >= self.MAX_BUFFERED_FRAMES
                if full:
                    if self._stop.wait(0.02):
                        return
                    continue
                chunk = f.read(self.CHUNK)
                if not chunk:
                    break
                with self._lock:
                    self._framer.push(chunk)
        with self._lock:
            self._framer.flush()


class PipeAudioTrack(MediaStreamTrack):
    """Real-time-paced 20 ms mono 48 kHz s16 frames from the SourceReader;
    SILENCE_FRAME when the source is idle or exhausted. Pacing follows aiortc's
    own AudioStreamTrack shape: frame n is released no earlier than
    start + n*20 ms, so a whole file is injected at listening speed, not at
    read speed."""

    kind = "audio"

    def __init__(self, reader: SourceReader):
        super().__init__()
        self._reader = reader
        self._start: float | None = None
        self._samples = 0

    async def recv(self) -> av.AudioFrame:
        if self._start is None:
            self._start = time.time()
            self._samples = 0
        else:
            self._samples += FRAME_SAMPLES
            wait = self._start + self._samples / RATE - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
        pcm = self._reader.get_frame() or SILENCE_FRAME
        frame = av.AudioFrame(format="s16", layout="mono", samples=FRAME_SAMPLES)
        plane = frame.planes[0]
        # av may align the plane past samples*2 bytes; pad the slack — the
        # encoder reads frame.samples, never the slack (the receive-side
        # mistake, inverted harmlessly; see common/segments.frame_pcm).
        plane.update(pcm.ljust(plane.buffer_size, b"\x00"))
        frame.sample_rate = RATE
        frame.pts = self._samples
        frame.time_base = fractions.Fraction(1, RATE)
        return frame


class Injector(ConnectorPeer):
    def __init__(self, cfg: dict):
        super().__init__(cfg, log)
        self._reader = SourceReader(cfg["source"], cfg["loop"])
        self._track = PipeAudioTrack(self._reader)
        self._writer = (WavSegmentWriter(cfg["out_dir"], cfg["display"],
                                         cfg["segment_seconds"])
                        if cfg["record"] else None)
        self._rx_task: asyncio.Task | None = None
        self.shutdown_note = ", close wav" if cfg["record"] else ""

    def local_track(self):
        self._reader.start()
        log.info("sending from %s (LOOP=%d); silence when idle",
                 self._cfg["source"], int(self._cfg["loop"]))
        return self._track

    def on_audio_track(self, track) -> None:
        if self._writer is not None:
            log.info("audio track received; RECORD=1 — writing segments to %s",
                     self._cfg["out_dir"])
            self._rx_task = asyncio.ensure_future(
                consume_audio(track, self._writer, self._stopping, log))
        else:
            log.info("audio track received; discarding the mixdown (RECORD=0)")
            self._rx_task = asyncio.ensure_future(self._discard(track))

    async def _discard(self, track) -> None:
        # The peer must keep consuming to stay a normal participant; the frames
        # are dropped on the floor.
        try:
            while not self._stopping.is_set():
                await track.recv()
        except Exception as e:
            if not self._stopping.is_set():
                log.warning("audio track ended: %s", e)

    def on_teardown(self) -> None:
        self._reader.stop()
        self._track.stop()
        if self._rx_task:
            self._rx_task.cancel()

    def on_closed(self) -> None:
        if self._writer is not None:
            self._writer.close()
            log.info("closed; %d segment(s) written", len(self._writer.segments_written))


def main() -> None:
    cfg = env_config()
    logging.basicConfig(level=cfg["log_level"],
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    injector = Injector(cfg)
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, injector.stop)
        except NotImplementedError:
            pass  # Windows dev shell; the container is Linux
    try:
        loop.run_until_complete(injector.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
