# SLData data-channel payload — parsing and slvoice extensions

The SL WebRTC voice client sends small JSON objects on the peer's **data
channel** carrying spatial/presence state ("SLData"). `janus.plugin.slvoice`
parses each object and stores the latest values per participant. This document
describes what the plugin parses (Phase 1) and the one slvoice-specific
**extension** it adds: the `echo` toggle.

> **Source.** Reconciled against the vendored spec
> `docs/voice/webrtc-voice-spec.md`. §9 fixes the client→mixer field set
> (`j/l/sp/sh/lp/lh/m/ug`, "per the published format") and the Opus fmtp; §6
> confirms `lh` is the listener-heading quaternion; §4.2 defines the echo test.
> Phase 1 **stores** these values but does not act on the geometry (no spatial
> logic until Phase 2). Per `docs/voice/current-architecture.md` §4 the OpenSim
> C# side does **not** interpret SLData at all — it forwards the viewer's SDP
> (including the `m=application` data-channel section) verbatim — so this
> plugin is the first and only thing that answers the data channel and parses
> these messages.

## Recognised fields (Phase 1)

The parser (`src/sldata.c`, unit-tested by `tests/test_sldata.c`) recognises:

| Key | Meaning (spec §9 published format; `lh` per §6) | Accepted encoding | Stored as |
|---|---|---|---|
| `j`  | join / roster marker | any | presence only |
| `l`  | leave marker | any | presence only |
| `sp` | self position (avatar) | `[x,y,z]` or `{x,y,z}` | `slv_vec3` |
| `sh` | self heading/orientation | `[x,y,z,w]` or `{x,y,z,w}` | `slv_quat` |
| `lp` | listener position (camera) | `[x,y,z]` or `{x,y,z}` | `slv_vec3` |
| `lh` | listener heading/orientation (**confirmed §6**) | `[x,y,z,w]` or `{x,y,z,w}` | `slv_quat` |
| `m`  | mute flag — **per-source object** (see below) | `{uuid:bool}` (or bool/int, legacy) | `slv_peer_adj[]` (or `int`) |
| `ug` | user gain — **per-source object** (see below) | `{uuid:number}` (or number, legacy) | `slv_peer_adj[]` (or `double`) |
| `echo` | **slvoice extension** (spec §4.2 echo-test control; see below) | bool or int | `int` (0/1) |

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

## Per-source mute and gain — `m` / `ug` (Phase 2, verified against the viewer)

The published field list names `m` and `ug`, but **does not** describe their
shape. Reading the actual viewer source (`phoenix-firestorm/indra/newview/
llvoicewebrtc.cpp`; Firestorm inherits Linden Lab's WebRTC voice code
unchanged) settles it: both are **objects keyed by the TARGET participant's
agent UUID**, emitted by the *listener*:

```json
{ "m":  { "<target-uuid>": true } }     // LLVoiceWebRTCConnection::setUserMute
{ "ug": { "<target-uuid>": 220 } }      // …::setUserVolume, value = volume * 220
```

i.e. "for me, mute / re-gain participant `<target-uuid>`." So per-source
mute/gain is genuinely per-listener, and *"mute A in B's viewer silences A for
B only"* is exactly what the wire says — B sends `{"m":{"A":true}}`, the mixer
drops A from B's mix, and no one else is affected. This is stronger than the SL
client-side model: the mute is enforced **in the mix** (spec §3.4), so a
modified viewer cannot defeat it.

Mixer handling (`src/janus_slvoice.c`):

- The parser (`src/sldata.c`) accepts the object form into `slv_sldata.peers[]`
  (a `{uuid, has_mute, muted, has_gain, gain}` array). The legacy scalar forms
  (`{"m":true}` / `{"ug":0.5}`) are still accepted into `slv_sldata.m/ug` for
  tolerance, but the real viewer never sends them.
- Messages arrive **separately and incrementally** (a mute message carries no
  gain and vice-versa), so the plugin keeps a **persistent per-listener
  peer-control map** on the session and merges each payload into it — a mute
  update never wipes a prior gain.
- In the mix loop, for listener L and each other source P, L's map is looked up
  by P's agent UUID: muted → P excluded from L's mix; gain → P scaled.

**Gain conversion — the `220` vs `200` quirk.** `setUserVolume` multiplies the
linear volume by `PEER_GAIN_CONVERSION_FACTOR = 220`, so the mixer recovers
linear gain as **`ug / 220`** (clamped to [0, 4]). Note the viewer is internally
inconsistent: its *inbound* join-time gain restore (`OnDataReceivedImpl`) uses
`* 200` instead. The two paths disagree by ~10%; the mixer standardises on
`/220` (the realtime slider path, which is what fires when a user changes
volume during a session). This is cosmetic for audibility and documented here so
it isn't mistaken for a mixer bug.

## Mixer→client format corrections (Phase 2, from the viewer parser)

Reading the viewer's inbound parser (`OnDataReceivedImpl`) also corrected two
mixer→client mistakes that were silently no-ops before:

- **VAD key is lowercase `v`.** The viewer reads `participant_obj["v"]` into
  `mIsSpeaking`; the plugin previously sent uppercase `"V"`, which the viewer
  ignored, so a talker's *speaking* state never lit (only the `p` level moved).
  The batch now sends `v`.
- **Join `j` must be an object `{"p":<primary>}`.** The viewer requires
  `j.is_object()` and reads `j.p` as the primary-server flag; a bare boolean
  `"j":true` was rejected and the join dropped. Presence now sends
  `{"<uuid>":{"j":{"p":true}}}` (single-region: every participant's primary
  server is this mixer). `l` (leave) remains a plain boolean.

## Echo ↔ mix interaction (Phase 2)

Echo is retained from Phase 1 as a **diagnostic per-participant OUTPUT
override**, toggled exactly as before (`{"echo":true/false}` on SLData, or
`SLV_ECHO_AUTOSTART` for a stock viewer). Its meaning under the real mixer:

- An **echoed** participant hears **their own audio, delayed 500 ms, INSTEAD of
  the N-minus-one mix** — the mixer substitutes the echo output for that one
  listener's mix. This proves the whole server media path (decode→delay→encode
  →relay) for that participant, independent of anyone else talking.
- It is **per-listener only**: an echoed participant still contributes normally
  as a *source* to everyone else's mix, and everyone else still hears the real
  conference mix. Only the echoed participant's own downstream is replaced.
- Turning echo off returns that participant to the normal N-1 mix immediately
  (no reallocation — the delay ring is preallocated at join and just cleared).

Because echo is a bring-up/diagnostic aid, leave `SLV_ECHO_AUTOSTART=false` for
normal conference operation; a whole room with autostart on would have everyone
hear themselves and no one hear each other.

## The `echo` extension

The spec provides for a self-serve echo test (§4.2: "on request — SLData message
or viewer menu — the mixer loops the user's own audio back with ~500 ms delay")
and lists **echo-test control** among the optional SLData extensions ignored by
stock viewers (§9). The spec does not pin a wire field name for it; this plugin
uses the boolean `echo` member. It toggles the echo test on the participant that
sent it:

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

## What's implemented vs deferred

**Mixer→client SLData** (Phase 1A → 2): the plugin pushes the per-peer batch
`{ "<uuid>": {"p":<level*128>,"v":<VAD>} }` every ~100 ms (spec §9). In Phase 2
`p`/`v` are the real level/VAD of **every ACTIVE participant**, computed by the
mix tick from decoded audio — so other avatars' dots animate when they talk, not
just the echoed one. Per-peer `j`/`l` join/leave notices are pushed (`j` now the
correct object form).

**Implemented in Phase 2** (`src/janus_slvoice.c`, math in `src/mixer/mix.c`):
- Cross-participant flat **N-minus-one mixing**, per-listener, on a 20 ms
  per-room tick (replaces the Phase-1B echo-to-self).
- **`m`/`ug` acted on**: per-source mute/gain applied in the mix loop (above).
- DTX/VAD cull (150 ms release hold) and encode-skip on silent mixes.
- Per-connection **diag vector** + per-room tick-duration histogram in
  `query_session` (spec §4.1): `ice_state`, `dtls_state`, `datachannel_open`,
  `rtp_in_rate`, `rtp_out_rate`, `decode_ok`, `active`, `mix_memberships`,
  `frames_mixed`, `last_rms`, `tick_histogram`.

**Deferred to Phase 3:**
- Use of `sp/sh/lp/lh` geometry (distance attenuation, panning, HRTF). The
  parser stores them (each component is an int = value×100 on the wire); the mix
  is flat until then.
- The `diag` state member pushed on the **data channel** (§4.1); diagnostics are
  exposed via `query_session` / the admin API for now (see
  `docs/phase1-bringup.md`).
