"""Streaming WAV parsing and 20 ms framing for the S-CON-6 injector
(docs/voice/connector-build-plan.md: "audio source = a PCM/WAV pipe first —
deliberately dumb; the TTS engine and the live mic are just different writers
to the same pipe").

Deliberately dependency-free (stdlib only) so the framing/resampling behaviour
is unit-testable without aiortc/av installed — the same split as
common/segments.py on the receive side. The send format is fixed: mono s16 at
48 kHz, 960 samples per 20 ms frame (the mixer's tick; SLV_RATE 48000).
"""

from __future__ import annotations

import sys
from array import array

RATE = 48000
FRAME_SAMPLES = 960                              # 20 ms at 48 kHz
FRAME_BYTES = FRAME_SAMPLES * 2                  # mono s16
SILENCE_FRAME = b"\x00" * FRAME_BYTES


def _read_exact(stream, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            raise ValueError("truncated WAV header")
        buf += chunk
    return buf


def read_wav_header(stream) -> tuple[int, int]:
    """Parse a RIFF/WAVE header from a sequentially-readable stream and return
    (sample_rate, channels), leaving the stream positioned at the first PCM byte.

    Works on unseekable streams (a FIFO), skips non-fmt/data chunks (LIST etc.),
    and IGNORES the declared data-chunk size — a pipe writer streaming WAV may
    declare 0 or a placeholder; the caller reads PCM until EOF. s16 PCM only:
    anything else raises ValueError (never framed as junk audio).
    """
    riff = _read_exact(stream, 12)
    if riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE stream")
    fmt: tuple[int, int] | None = None
    while True:
        hdr = _read_exact(stream, 8)
        cid, size = hdr[:4], int.from_bytes(hdr[4:8], "little")
        if cid == b"fmt ":
            body = _read_exact(stream, size + (size & 1))  # chunks are word-aligned
            audio_format = int.from_bytes(body[0:2], "little")
            channels = int.from_bytes(body[2:4], "little")
            rate = int.from_bytes(body[4:8], "little")
            bits = int.from_bytes(body[14:16], "little")
            if audio_format != 1 or bits != 16:
                raise ValueError(
                    f"only s16 PCM WAV is supported (format={audio_format}, bits={bits})")
            if channels not in (1, 2):
                raise ValueError(f"only mono or stereo WAV is supported (channels={channels})")
            if rate <= 0:
                raise ValueError(f"bad sample rate {rate}")
            fmt = (rate, channels)
        elif cid == b"data":
            if fmt is None:
                raise ValueError("WAV data chunk before fmt chunk")
            return fmt
        else:
            _read_exact(stream, size + (size & 1))


class PcmFramer:
    """Converts pushed s16 PCM (mono or stereo, any sample rate) into exact
    FRAME_SAMPLES-sample mono 48 kHz s16 frames.

    - stereo is downmixed by averaging L/R;
    - other rates are linearly interpolated to 48 kHz, with the fractional read
      position and the boundary samples carried across pushes (no seams — a
      chunked push produces byte-identical output to a single push);
    - get_frame() returns only COMPLETE frames (or None: the caller sends
      SILENCE_FRAME, never a short/junk frame — the plan's empty-pipe rule);
    - flush() zero-pads the buffered tail to a full frame at end-of-source.

    Not thread-safe; the caller serialises access (the injector guards it with
    one lock between the reader thread and the track).
    """

    def __init__(self, sample_rate: int, channels: int):
        if channels not in (1, 2):
            raise ValueError(f"channels must be 1 or 2, got {channels}")
        if sample_rate <= 0:
            raise ValueError(f"bad sample rate {sample_rate}")
        self._rate = sample_rate
        self._channels = channels
        self._pending = b""              # byte tail not yet aligned to a sample unit
        self._carry: list[int] = []      # source-rate mono samples awaiting interpolation
        # Read position into _carry in units of 1/RATE source samples (integer,
        # so resampling is EXACT and chunk-boundary-invariant: pushing the same
        # bytes in any chunking yields byte-identical output).
        self._pos_num = 0
        self._out = bytearray()          # mono 48 kHz s16, frame-extraction buffer

    @property
    def buffered_frames(self) -> int:
        return len(self._out) // FRAME_BYTES

    def push(self, pcm: bytes) -> None:
        unit = 2 * self._channels
        data = self._pending + pcm
        usable = len(data) - (len(data) % unit)
        self._pending = data[usable:]
        if not usable:
            return
        samples = array("h")
        samples.frombytes(data[:usable])
        if sys.byteorder == "big":
            samples.byteswap()           # WAV PCM is little-endian
        if self._channels == 2:
            mono = [(samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples), 2)]
        else:
            mono = samples.tolist()
        if self._rate == RATE:
            self._append(mono)
            return
        # Linear interpolation to 48 kHz, in integer arithmetic: output sample k
        # reads source position k*rate/RATE exactly (pos advances by rate in
        # units of 1/RATE source samples). The boundary sample and position
        # carry to the next push, so chunking produces no seam and no drift.
        src = self._carry + mono
        pos = self._pos_num
        out: list[int] = []
        limit = (len(src) - 1) * RATE
        while pos < limit:
            i, rem = divmod(pos, RATE)
            a = src[i]
            out.append(a + ((src[i + 1] - a) * rem) // RATE)
            pos += self._rate
        consumed = pos // RATE
        self._carry = src[consumed:]
        self._pos_num = pos - consumed * RATE
        self._append(out)

    def get_frame(self) -> bytes | None:
        """One complete 20 ms frame (FRAME_BYTES of mono s16 @48k), or None."""
        if len(self._out) < FRAME_BYTES:
            return None
        frame = bytes(self._out[:FRAME_BYTES])
        del self._out[:FRAME_BYTES]
        return frame

    def flush(self) -> None:
        """End-of-source: zero-pad the buffered tail up to a full frame so the
        last real samples are sent rather than held forever. Sub-sample residue
        (_pending/_carry) is dropped — it cannot form even one output sample."""
        tail = len(self._out) % FRAME_BYTES
        if tail:
            self._out += b"\x00" * (FRAME_BYTES - tail)

    def _append(self, mono: list[int]) -> None:
        if not mono:
            return
        a = array("h", [max(-32768, min(32767, v)) for v in mono])
        if sys.byteorder == "big":
            a.byteswap()
        self._out += a.tobytes()
