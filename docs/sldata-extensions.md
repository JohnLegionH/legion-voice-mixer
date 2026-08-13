# SLData data-channel payload — parsing and slvoice extensions

The SL WebRTC voice client sends small JSON objects on the peer's **data
channel** carrying spatial/presence state ("SLData"). `janus.plugin.slvoice`
parses each object and stores the latest values per participant. This document
describes what the plugin parses (Phase 1) and the one slvoice-specific
**extension** it adds: the `echo` toggle.

> **Sourcing note.** The canonical field list is `docs/webrtc-voice-spec.md` §9.
> That spec document is not yet vendored into this repo, so the field set below
> is taken from the Phase-1 task brief (`j, l, sp, sh, lp, lh, m, ug`). The
> *names* are authoritative; the precise geometric **meaning** of `sp/sh/lp/lh`
> is treated as **provisional** here and is not acted upon in Phase 1 (no
> spatial logic until Phase 2). When the spec is vendored, reconcile the
> "meaning" column against §9 — the parser (`src/sldata.c`) will not need to
> change to store them, only Phase-2 code that interprets them.

## Recognised fields (Phase 1)

The parser (`src/sldata.c`, unit-tested by `tests/test_sldata.c`) recognises:

| Key | Assumed meaning (provisional) | Accepted encoding | Stored as |
|---|---|---|---|
| `j`  | join / roster marker | any | presence only |
| `l`  | leave marker | any | presence only |
| `sp` | self position (avatar) | `[x,y,z]` or `{x,y,z}` | `slv_vec3` |
| `sh` | self heading/orientation | `[x,y,z,w]` or `{x,y,z,w}` | `slv_quat` |
| `lp` | listener position (camera) | `[x,y,z]` or `{x,y,z}` | `slv_vec3` |
| `lh` | listener heading/orientation | `[x,y,z,w]` or `{x,y,z,w}` | `slv_quat` |
| `m`  | mute flag | bool or int | `int` (0/1) |
| `ug` | user gain | number | `double` |
| `echo` | **slvoice extension** (see below) | bool or int | `int` (0/1) |

Parsing policy (all enforced and unit-tested):

- **Unknown fields are ignored silently** — an audiobridge/SL-shaped payload
  with extra keys parses fine.
- **Wrong-typed known fields are ignored, not fatal** (e.g. `"sp":"nope"` is
  skipped; the message still parses).
- **Malformed JSON / non-object top level** → reported as malformed; the plugin
  logs it at `LOG_WARN` and never crashes.
- **Oversized payloads** (> `SLV_SLDATA_MAX_BYTES` = 8192) are rejected before
  parsing.
- The parser honours the exact **byte length** of the data-channel buffer
  (data-channel buffers are not NUL-terminated).

Diagnostics: `query_session` reports `data_msgs_received` and
`last_data_fields_seen` (a comma-separated list such as `sp,sh,echo`), derived
from the last parsed payload.

## The `echo` extension

`echo` is a **slvoice-specific extension**, not part of the base SL protocol. It
toggles the Phase-1 echo test on the participant that sent it:

```json
{ "echo": true }     // enable: your audio is looped back to you, delayed 500ms
{ "echo": false }    // disable: stop the loopback
```

Semantics:

- `{"echo":true}` allocates the echo resources (Opus 48 kHz stereo
  decoder/encoder, a 500 ms delay ring buffer, and the outbound RTP buffer —
  all allocated once, here, never per packet) and starts looping the
  participant's own incoming Opus RTP back to them with a 500 ms delay.
- `{"echo":false}` stops the loopback and frees those resources.
- The toggle is **per participant** and affects only the sender.
- Integer forms (`1`/`0`) are accepted as `true`/`false`.
- It can be combined with other fields in the same message; only `echo` changes
  echo state, the rest are stored as usual.

Because it is an extension, adding it does not violate the audiobridge
compatibility constraint (`docs/protocol-compat.md`): it rides on the data
channel, not on a plugin request, and it is a **new** field, never a
repurposing of an existing one.

### `echo_autostart` (bring-up convenience)

A stock SL viewer does **not** send `{"echo":true}`. For Phase-1 bring-up the
plugin can auto-enable echo the moment a participant's PeerConnection is up, so
a normal viewer hears itself without any special client:

- Env var (easiest under Docker — add to `.env`): `SLV_ECHO_AUTOSTART=true`
- or plugin jcfg: `general.echo_autostart = true`

Default is **off**. The runtime `{"echo":true}`/`{"echo":false}` toggle still
works regardless of this setting. See `docs/phase1-bringup.md`.

## What Phase 1 does NOT do

- No use of `sp/sh/lp/lh` geometry (no distance attenuation, no panning).
- No per-listener anything, no mixing across participants.
- No mutation of another participant's state from `m`/`ug`.

Those are Phase 2 (`src/mixer/mixer.h`). Phase 1 only **stores** the latest
values so the storage/telemetry surface is in place from the start.
