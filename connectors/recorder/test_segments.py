"""Unit tests for the WAV segment writer (S-CON-4, build plan: "rollover at
SEGMENT_SECONDS, valid RIFF header on close"). pytest-style; a __main__ fallback
runs them with plain python where pytest is not installed."""

import os
import tempfile
import wave
from datetime import datetime, timezone

from segments import WavSegmentWriter


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _writer(tmp, clock, seconds=600):
    stamps = iter(datetime(2026, 8, 31, 22, 0, i, tzinfo=timezone.utc) for i in range(60))
    return WavSegmentWriter(tmp, "aabbccdd-1234", segment_seconds=seconds,
                            clock=clock, now_utc=lambda: next(stamps))


def test_rollover_at_segment_seconds():
    clock = FakeClock()
    with tempfile.TemporaryDirectory() as tmp:
        w = _writer(tmp, clock, seconds=600)
        w.write(b"\x00\x00" * 480, 48000, 2)
        first = w.current_path
        clock.t += 599
        w.write(b"\x00\x00" * 480, 48000, 2)
        assert w.current_path == first, "no roll before the boundary"
        clock.t += 2
        w.write(b"\x00\x00" * 480, 48000, 2)
        assert w.current_path != first, "rolled at SEGMENT_SECONDS"
        w.close()
        assert len(w.segments_written) == 2
        assert os.path.basename(first).startswith("aabbccdd_"), "display truncated to 8 chars"


def test_format_change_rolls_early():
    clock = FakeClock()
    with tempfile.TemporaryDirectory() as tmp:
        w = _writer(tmp, clock)
        w.write(b"\x00\x00" * 480, 48000, 2)
        first = w.current_path
        w.write(b"\x00\x00" * 480, 48000, 1)  # channel change mid-stream
        assert w.current_path != first
        w.close()
        assert len(w.segments_written) == 2


def test_valid_riff_header_on_close():
    clock = FakeClock()
    with tempfile.TemporaryDirectory() as tmp:
        w = _writer(tmp, clock)
        pcm = b"\x01\x00\x02\x00" * 4800  # 4800 stereo s16 frames
        w.write(pcm, 48000, 2)
        w.close()
        path = w.segments_written[0]
        with open(path, "rb") as f:
            magic = f.read(12)
        assert magic[:4] == b"RIFF" and magic[8:12] == b"WAVE"
        with wave.open(path, "rb") as r:  # the stdlib reader validates chunk sizes
            assert r.getnchannels() == 2
            assert r.getframerate() == 48000
            assert r.getsampwidth() == 2
            assert r.getnframes() == 4800
            assert r.readframes(4800) == pcm


def test_empty_write_opens_nothing():
    clock = FakeClock()
    with tempfile.TemporaryDirectory() as tmp:
        w = _writer(tmp, clock)
        w.write(b"", 48000, 2)
        assert w.current_path is None
        w.close()
        assert w.segments_written == []


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    raise SystemExit(failures)
