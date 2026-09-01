"""Receive-side consumption of the room mixdown, shared by the recorder and the
injector's RECORD=1 path. Moved from the S-CON-4 recorder unchanged."""

from __future__ import annotations

import asyncio
import logging

from common.segments import ContiguousFrameFeed, WavSegmentWriter, frame_pcm


async def consume_audio(track, writer: WavSegmentWriter,
                        stopping: asyncio.Event, log: logging.Logger) -> None:
    import av  # aiortc dependency; imported here so segments.py stays stdlib-only
    resampler = None
    feed = ContiguousFrameFeed(writer, warn=log.warning)
    try:
        while not stopping.is_set():
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
        if not stopping.is_set():
            log.warning("audio track ended: %s", e)
