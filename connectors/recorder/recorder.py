"""S-CON-4 recorder peer (docs/voice/connector-build-plan.md; connector-assessment §2).

A headless aiortc WebRTC peer that joins one janus.plugin.slvoice room as the
sim-registered connector NPC identity (DISPLAY = the NPC's agent UUID from the
sim's "[CONNECTOR] registered ... npc=... room=..." line) and writes the received
mixdown to rolling WAV segments. It never sends audio (the sendrecv transceiver
carries no track) and never sends SLData; with the connector registered sim-side,
its exclusion column is applied by the mixer (fan-out by display), so the
recording is subject to parcel/estate semantics by construction.

The Janus session/join/poll/keepalive/teardown plumbing lives in
common/peer.py (shared with the S-CON-6 injector); this module contributes
only the receive-and-record media behaviour.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from common.config import base_env
from common.peer import ConnectorPeer
from common.receive import consume_audio
from common.segments import WavSegmentWriter

log = logging.getLogger("recorder")


def env_config() -> dict:
    cfg = base_env("recorder")
    cfg["out_dir"] = os.environ.get("OUT_DIR", "/recordings")
    cfg["segment_seconds"] = int(os.environ.get("SEGMENT_SECONDS", "600"))
    return cfg


class Recorder(ConnectorPeer):
    shutdown_note = ", close wav"

    def __init__(self, cfg: dict):
        super().__init__(cfg, log)
        self._writer = WavSegmentWriter(cfg["out_dir"], cfg["display"], cfg["segment_seconds"])
        self._track_task: asyncio.Task | None = None

    # local_track() stays None: the base peer negotiates the sendrecv m-line
    # with no track — the silent-participant shape (never active, audible=0).

    def on_audio_track(self, track) -> None:
        log.info("audio track received; writing segments to %s", self._cfg["out_dir"])
        self._track_task = asyncio.ensure_future(
            consume_audio(track, self._writer, self._stopping, log))

    def on_teardown(self) -> None:
        if self._track_task:
            self._track_task.cancel()

    def on_closed(self) -> None:
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
