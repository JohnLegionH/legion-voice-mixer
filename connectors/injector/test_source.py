"""Unit tests for the S-CON-6 source framer (build plan: "the pipe source
under- and over-run behaviour; silence on an empty pipe (no junk frames)").
pytest-style; a __main__ fallback runs them with plain python where pytest is
not installed. Run from connectors/ (so the package imports resolve):
`python -m pytest injector/` or `python -m injector.test_source`."""

import io
import struct
import wave

from injector.source import (FRAME_BYTES, FRAME_SAMPLES, SILENCE_FRAME,
                             PcmFramer, read_wav_header)


def _s16(values):
    return struct.pack(f"<{len(values)}h", *values)


def _frames(framer):
    out = []
    while (f := framer.get_frame()) is not None:
        out.append(f)
    return out


def test_mono_48k_exact_frames():
    # 5 x 960 samples in -> exactly 5 frames out, byte-identical, none left over.
    framer = PcmFramer(48000, 1)
    pcm = _s16([(i % 200) + 1 for i in range(FRAME_SAMPLES * 5)])
    framer.push(pcm)
    frames = _frames(framer)
    assert [len(f) for f in frames] == [FRAME_BYTES] * 5
    assert b"".join(frames) == pcm
    assert framer.get_frame() is None


def test_stereo_downmix_averages():
    framer = PcmFramer(48000, 2)
    framer.push(_s16([100, 200] * FRAME_SAMPLES))  # L=100 R=200 interleaved
    (frame,) = _frames(framer)
    assert frame == _s16([150] * FRAME_SAMPLES)


def test_resample_16k_mono():
    # 1 s of constant-value 16 kHz mono -> 47997 output samples ((16000-1)*3:
    # the last source sample has no successor to interpolate toward), flushed
    # to 48000 in exact 960-sample frames. Interpolation of a constant is exact.
    framer = PcmFramer(16000, 1)
    framer.push(_s16([1000] * 16000))
    framer.flush()
    frames = _frames(framer)
    total = sum(len(f) for f in frames) // 2
    assert total == 48000, f"expected exactly 50 frames after flush, got {total} samples"
    for f in frames[:-1]:
        assert len(f) == FRAME_BYTES
        assert f == _s16([1000] * FRAME_SAMPLES)
    assert frames[-1] == _s16([1000] * 957 + [0] * 3), "tail: 47997 real + 3 pad"


def test_resample_44100_stereo():
    framer = PcmFramer(44100, 2)
    framer.push(_s16([300, 500] * 44100))          # 1 s stereo; mono mix = 400
    framer.flush()
    frames = _frames(framer)
    total = sum(len(f) for f in frames) // 2
    assert abs(total - 48000) < FRAME_SAMPLES, f"~1 s out, got {total} samples"
    for f in frames[:-1]:
        assert f == _s16([400] * FRAME_SAMPLES)
    tail = struct.unpack(f"<{FRAME_SAMPLES}h", frames[-1])
    assert set(tail) <= {400, 0}, "last frame is real samples then zero pad only"


def test_chunked_push_matches_single_push():
    # Odd-sized chunks (splitting samples and L/R pairs) must produce
    # byte-identical output to one push — the carry/pending seams hold.
    pcm = _s16([((i * 37) % 4001) - 2000 for i in range(44100 * 2)])  # 1 s stereo
    one = PcmFramer(44100, 2)
    one.push(pcm)
    one.flush()
    many = PcmFramer(44100, 2)
    pos, sizes = 0, [1, 2, 3, 5, 7, 4093, 8192]
    while pos < len(pcm):
        size = sizes[pos % len(sizes)]
        many.push(pcm[pos:pos + size])
        pos += size
    many.flush()
    assert b"".join(_frames(one)) == b"".join(_frames(many))


def test_silence_when_idle_no_junk_frames():
    # Empty framer: no frame at all (the track substitutes SILENCE_FRAME) —
    # never a short or partial frame.
    framer = PcmFramer(48000, 1)
    assert framer.get_frame() is None
    framer.push(_s16([7] * 100))                   # under-run: less than a frame
    assert framer.get_frame() is None, "a partial buffer must not frame"
    assert SILENCE_FRAME == b"\x00" * FRAME_BYTES


def test_flush_pads_tail_to_one_frame():
    framer = PcmFramer(48000, 1)
    framer.push(_s16([9] * 1000))                  # 1 full frame + 40 tail
    framer.flush()
    frames = _frames(framer)
    assert len(frames) == 2
    assert frames[1] == _s16([9] * 40 + [0] * (FRAME_SAMPLES - 40))


def test_overrun_bound_is_observable():
    # buffered_frames is the reader thread's back-pressure signal: it grows
    # with pushed audio and shrinks as frames are taken.
    framer = PcmFramer(48000, 1)
    framer.push(b"\x01\x00" * (FRAME_SAMPLES * 10))
    assert framer.buffered_frames == 10
    framer.get_frame()
    assert framer.buffered_frames == 9


def test_wav_header_from_wave_module():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(_s16([1, 2, 3, 4]))
    buf.seek(0)
    assert read_wav_header(buf) == (44100, 2)
    assert buf.read() == _s16([1, 2, 3, 4]), "stream left at the first PCM byte"


class _Unseekable:
    """A forward-only reader (the FIFO shape): read() only, no seek/tell."""

    def __init__(self, data):
        self._data = data
        self._pos = 0

    def read(self, n):
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


def test_wav_header_streaming_pipe_shape():
    # A pipe writer's header: an extra LIST chunk, data size declared 0 (the
    # writer cannot know it), read through an unseekable stream.
    header = (b"RIFF" + (0).to_bytes(4, "little") + b"WAVE"
              + b"LIST" + (4).to_bytes(4, "little") + b"INFO"
              + b"fmt " + (16).to_bytes(4, "little")
              + struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)
              + b"data" + (0).to_bytes(4, "little"))
    pcm = _s16([5, 6, 7, 8])
    stream = _Unseekable(header + pcm)
    assert read_wav_header(stream) == (16000, 1)
    assert stream.read(8) == pcm, "declared data size ignored; PCM streams until EOF"


def test_wav_header_rejects_non_s16():
    header = (b"RIFF" + (0).to_bytes(4, "little") + b"WAVE"
              + b"fmt " + (16).to_bytes(4, "little")
              + struct.pack("<HHIIHH", 3, 1, 48000, 192000, 4, 32)  # float32
              + b"data" + (0).to_bytes(4, "little"))
    try:
        read_wav_header(_Unseekable(header))
    except ValueError as e:
        assert "s16" in str(e)
    else:
        raise AssertionError("float32 WAV must be rejected, not framed as junk")


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
