"""WAV segment writer for the S-CON-4 recorder (docs/voice/connector-build-plan.md).

Deliberately dependency-free (stdlib only) so the rollover and header behaviour is
unit-testable without aiortc/av installed. One segment file per SEGMENT_SECONDS of
wall time, named <DISPLAY first 8>_<UTC yyyymmdd-HHMMSS>.wav; sample rate and
channel count follow the received stream, and a mid-stream format change rolls the
segment early (a WAV file has one fixed format).
"""

from __future__ import annotations

import os
import time
import wave
from datetime import datetime, timezone


class WavSegmentWriter:
    """Writes s16 PCM into rolling WAV segments.

    ``clock`` is injectable for tests (defaults to ``time.time``); ``now_utc`` names
    the segment files and is injectable for the same reason.
    """

    def __init__(self, out_dir: str, display: str, segment_seconds: int = 600,
                 clock=time.time, now_utc=None):
        self._out_dir = out_dir
        self._prefix = (display or "recorder")[:8]
        self._segment_seconds = segment_seconds
        self._clock = clock
        self._now_utc = now_utc or (lambda: datetime.now(timezone.utc))
        self._wav: wave.Wave_write | None = None
        self._path: str | None = None
        self._opened_at: float = 0.0
        self._rate: int | None = None
        self._channels: int | None = None
        self.segments_written: list[str] = []

    @property
    def current_path(self) -> str | None:
        return self._path

    def write(self, pcm: bytes, sample_rate: int, channels: int) -> None:
        """Append interleaved s16 PCM. Opens the first segment lazily, rolls on the
        time boundary, and rolls early on a sample-rate/channel change."""
        if not pcm:
            return
        format_changed = (self._wav is not None
                          and (sample_rate != self._rate or channels != self._channels))
        expired = (self._wav is not None
                   and self._clock() - self._opened_at >= self._segment_seconds)
        if format_changed or expired:
            self._close_current()
        if self._wav is None:
            self._open(sample_rate, channels)
        self._wav.writeframes(pcm)

    def close(self) -> None:
        """Close the current segment properly (the wave module finalises the RIFF
        header — sizes in RIFF/data chunks — on close)."""
        self._close_current()

    # ---- internals

    def _open(self, sample_rate: int, channels: int) -> None:
        os.makedirs(self._out_dir, exist_ok=True)
        stamp = self._now_utc().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(self._out_dir, f"{self._prefix}_{stamp}.wav")
        # A same-second rollover (format change) must not clobber the previous file.
        n = 1
        while os.path.exists(path):
            path = os.path.join(self._out_dir, f"{self._prefix}_{stamp}_{n}.wav")
            n += 1
        w = wave.open(path, "wb")
        w.setnchannels(channels)
        w.setsampwidth(2)  # s16
        w.setframerate(sample_rate)
        self._wav = w
        self._path = path
        self._opened_at = self._clock()
        self._rate = sample_rate
        self._channels = channels

    def _close_current(self) -> None:
        if self._wav is not None:
            self._wav.close()
            self.segments_written.append(self._path)
            self._wav = None
            self._path = None
