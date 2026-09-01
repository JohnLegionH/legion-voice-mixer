"""Unit tests for the WAV segment writer (S-CON-4, build plan: "rollover at
SEGMENT_SECONDS, valid RIFF header on close"). pytest-style; a __main__ fallback
runs them with plain python where pytest is not installed."""

import fractions
import os
import tempfile
import wave
from datetime import datetime, timezone

from segments import ContiguousFrameFeed, WavSegmentWriter, frame_pcm


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


def _feed_50_frames(tmp, time_base_den, pts_step):
    """50 synthetic 960-sample stereo frames through the contiguous feed, each with
    a decoder-style OVERSIZED plane (6x: 960 real samples + 4800 samples of zeroed
    slack — the 2026-09-01 defect shape). Returns (writer, warn list)."""
    clock = FakeClock()
    w = _writer(tmp, clock)
    warns = []
    feed = ContiguousFrameFeed(w, warn=warns.append)
    real = b"\x01\x00\x02\x00" * (960 * 2 // 2)      # 960 stereo samples, all non-zero
    plane = real + b"\x00" * (4800 * 4)              # + the allocation slack
    tb = fractions.Fraction(1, time_base_den)
    for i in range(50):
        pcm = frame_pcm(plane, 960, 2)
        feed.push(pcm, 48000, 2, pts=i * pts_step, time_base=tb)
    w.close()
    return w, warns


def _assert_contiguous_48000(path):
    with wave.open(path, "rb") as r:
        assert r.getnframes() == 48000, f"expected 48000 samples, got {r.getnframes()}"
        data = r.readframes(48000)
    values = [int.from_bytes(data[i:i + 2], "little", signed=True)
              for i in range(0, len(data), 2)]
    assert all(v != 0 for v in values), "zero run found — padding leaked into the output"


def test_contiguous_50_frames_tb_1_48000():
    # pts advancing 960 in time_base 1/48000 (the correct timeline).
    with tempfile.TemporaryDirectory() as tmp:
        w, warns = _feed_50_frames(tmp, 48000, 960)
        _assert_contiguous_48000(w.segments_written[0])
        assert warns == [], "a contiguous timeline must not warn"


def test_contiguous_50_frames_tb_1_8000():
    # The same 20 ms cadence expressed in time_base 1/8000 (pts advancing 160):
    # placement must not trust pts units — output is identical.
    with tempfile.TemporaryDirectory() as tmp:
        w, warns = _feed_50_frames(tmp, 8000, 160)
        _assert_contiguous_48000(w.segments_written[0])
        assert warns == [], "correct wall-clock cadence in odd units must not warn"


def test_frame_pcm_strips_plane_slack():
    real = b"\x07\x00" * (960 * 2)
    plane = real + b"\x00" * (4800 * 4)
    assert frame_pcm(plane, 960, 2) == real


def test_timestamp_jump_warns_but_never_pads():
    clock = FakeClock()
    with tempfile.TemporaryDirectory() as tmp:
        w = _writer(tmp, clock)
        warns = []
        feed = ContiguousFrameFeed(w, warn=warns.append)
        pcm = b"\x01\x00" * (960 * 2)
        tb = fractions.Fraction(1, 48000)
        feed.push(pcm, 48000, 2, pts=0, time_base=tb)
        feed.push(pcm, 48000, 2, pts=48000 * 90, time_base=tb)  # +90 s jump
        w.close()
        assert len(warns) == 1 and "jumped" in warns[0]
        with wave.open(w.segments_written[0], "rb") as r:
            assert r.getnframes() == 1920, "exactly the two frames' samples — no padding"


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
