# Phase 1 bring-up runbook — hold the session, then echo

In-world checks against a real **Firestorm 7.2.2** viewer on
`janus.plugin.slvoice`:

- **CHECK 1 — held session (Phase 1A):** the viewer establishes and *holds* a
  WebRTC voice session — voice dot renders, mic control active, no join/leave
  churn. Silence is correct here.
- **CHECK 2 — echo (Phase 1B):** with `SLV_ECHO_AUTOSTART=true`, you speak and
  hear yourself ~500 ms delayed, and see your own level move on the dot.
- **CHECK 3 — two-party mix (Phase 2):** two viewers in one room hear *each
  other* (not themselves); muting one silences it for the other only; the
  speaker's dot animates on the listener's screen.

Do CHECK 1 first; CHECK 2 and CHECK 3 build on the held session. CHECK 3 is the
Phase-2 acceptance and needs `SLV_ECHO_AUTOSTART=false` (echo OFF).

> **"Room already exists" is EXPECTED**, not a bug: the C# side re-creates rooms
> and treats error 486 as success (`docs/voice/current-architecture.md` §3.3).

---

## Prerequisites

1. Container running at **`192.168.1.225`**, HTTP `24223` (`/voice`), admin
   `24225` (`/voiceAdmin`). Confirm SCTP (data channels) — mandatory:
   ```sh
   curl http://192.168.1.225:24223/voice/info | grep -o '"data_channels":true'
   curl http://192.168.1.225:24223/voice/info | grep -o '"janus.plugin.slvoice":{"name":"Legion SLVoice mixer"'
   ```
   Both must print. If `data_channels` is `false`, no plugin can answer the data
   channel (rebuild the image).

2. **`JS_PUBLIC_IP=192.168.1.225`** in the container `.env` (or `JS_PUBLIC_HOST`),
   and the `JS_RTP_PORT_RANGE` UDP range open on the firewall — required for ICE
   to complete (`docs/docker-notes.md`). Without it the session negotiates but
   ICE never connects. The entrypoint now logs the applied value at startup:
   `[entrypoint] nat_1_1_mapping = 192.168.1.225` (and Janus logs `Using
   nat_1_1_mapping for public IP: 192.168.1.225`). If neither var is set the
   entrypoint prints a loud `WARNING`.

3. **`PluginName` requires the `feature/voice-plugin-select` branch.** The stock
   os-webrtc-janus addon hardcodes `janus.plugin.audiobridge`
   (`JanusAudioBridge.cs:41`, `current-architecture.md` §6); that branch makes it
   config-driven. Build and deploy `WebRtcVoiceServiceModule.dll` (dispatcher) +
   `WebRtcJanusService.dll` (leaf, carries the branch + the OSDType.Long
   session-id fix) into the regionserver.

---

## Regionserver INI

In `D:\legiongrid\regionserver\config\OpenSim.ini` (the in-process leaf; nothing
on the gridserver). Keys per `current-architecture.md` §5:

```ini
[WebRtcVoice]
    Enabled = true
    ; dll:Class of the spatial LEAF (§2.3/§5), loaded via ServerUtils.LoadPlugin —
    ; NOT the dispatcher (an auto-discovered region module).
    SpatialVoiceService    = "WebRtcJanusService.dll:WebRtcJanusService"
    NonSpatialVoiceService = "WebRtcJanusService.dll:WebRtcJanusService"

[JanusWebRtcVoice]
    ; No Enabled key here — the four fields below being non-blank enable the leaf.
    JanusGatewayURI      = "http://192.168.1.225:24223/voice"
    APIToken             = "<== container JS_API_SECRET>"
    JanusGatewayAdminURI = "http://192.168.1.225:24225/voiceAdmin"
    AdminAPIToken        = "<== container JS_ADMIN_SECRET>"
    ; Attach to slvoice, not audiobridge (needs feature/voice-plugin-select).
    PluginName           = janus.plugin.slvoice
```

---

## CHECK 1 — the session holds

Set `SLV_ECHO_AUTOSTART=false` (default). Log a viewer in, voice-enabled, in a
region within voice range.

**Successful-connect log trail.** Every `[janus.plugin.slvoice-0x…]` line is this
plugin (`docker logs <container>`); ICE/DTLS lines are the Janus core. All
negotiation steps log at `LOG_INFO` (Janus default).

| Stage | Docker logs (Janus core / slvoice plugin) |
|---|---|
| **Provision** | `New session created` · `Handling request 'create'` · `Created room <N>` (or `Room <N> already exists (error 486)` — **expected**) · `Handling request 'join'` |
| **Offer/Answer** | `Offer received: Opus pt=111, m=application present=yes` · **`Answer sent: audio Opus pt=111; m=application answered=YES`** · `Participant <id> (<uuid>) joined room <N>` (full `==== OFFER SDP ====` / `==== ANSWER SDP ====` dumps also print) |
| **ICE / DTLS** | core: `… ICE … connected` · `… DTLS … completed` |
| **PC up** | `WebRTC media is now available (ICE connected, DTLS complete, PeerConnection up)` |
| **Data channel** | `Data channel open (writable)` — then the ~100 ms per-peer p/V batch flows; the viewer renders the dot and activates the mic |

**Pass:** dot visible, mic active, session stays up. **Must NOT appear:** the core
warning `Skipping unsupported application media line`, or any join/leave loop.

The answer's audio m-line must carry `a=extmap:...sdes:mid` — a multi-line BUNDLE
without the MID extension is silently rejected by libwebrtc (this was the v0.4.1
fix). It's in the `==== ANSWER SDP ====` dump.

**Live diagnostics** (admin API → `query_session`); get `session_id`/`handle_id`
from the provision logs:
```sh
curl -s -X POST http://192.168.1.225:24225/voiceAdmin -H 'Content-Type: application/json' \
  -d '{"janus":"handle_info","session_id":<S>,"handle_id":<H>,"admin_secret":"<AdminAPIToken>","transaction":"q"}'
```
A held session shows `datachannel_answered:true`, `datachannel_open:true`, and a
climbing `session_uptime`; `rtp_in_count` rises once you speak.

### CHECK 1 failure points
1. **Data channel not answered → churn returns.** core `Skipping unsupported
   application media line` + plugin `m=application answered=NO`. Cause: attached
   to **audiobridge** (PluginName not honoured — needs the branch), or Janus
   lacks SCTP (`data_channels:false`).
2. **Wrong plugin attached.** Logs show `[janus.plugin.audiobridge-…]`, no
   `[janus.plugin.slvoice-…] New session created`. Deploy `feature/voice-plugin-select`.
3. **ICE never completes.** `m=application answered=YES` but no `WebRTC media is
   now available`; `datachannel_open:false`, `ice_state:disconnected`. Set
   `JS_PUBLIC_IP=192.168.1.225` and open the RTP UDP range.

---

## CHECK 2 — echo (hear yourself, ~500 ms delayed)

Stock viewers can't send the `{"echo":true}` SLData toggle, so use the env knob:

1. Set **`SLV_ECHO_AUTOSTART=true`** in the container `.env`, `docker compose up -d`.
2. Log a viewer in, enable voice, **speak**.
3. You should hear yourself back ~500 ms later, and your own level should move on
   the dot (the p/V batch now carries your real RMS/VAD).
4. Set `SLV_ECHO_AUTOSTART=false` afterward — echo is a bring-up knob, not normal
   operation.

**Echo log trail** (on top of CHECK 1's):
| Stage | Docker logs (slvoice plugin) |
|---|---|
| **Autostart armed** | at container start: `[janus.plugin.slvoice] SLV_ECHO_AUTOSTART enabled — echo starts automatically on connect` |
| **Echo on** | at PC-up: `Echo auto-started (SLV_ECHO_AUTOSTART)` (or `Echo ENABLED (500ms delay) (SLData toggle)` if a client sends the toggle) |
| **First audio** | when you speak: `First audio frame decoded (echo active, 960 samples/ch)` |

**Live diagnostics** (`query_session`) while speaking: `echo_active:true`,
`frames_decoded` and `frames_encoded` climbing, `rtp_out_count` climbing (echo
packets sent back), and `last_rms` non-zero (your speaking level).

### CHECK 2 failure points
1. **Echo never starts.** No `Echo auto-started` / `First audio frame decoded`;
   `query_session` `echo_active:false`. Cause: `SLV_ECHO_AUTOSTART` not set/false,
   or the container wasn't re-`up`ed after editing `.env`. Confirm the
   `SLV_ECHO_AUTOSTART enabled` line at startup.
2. **Echo on but no audio back.** `echo_active:true` and `rtp_in_count` rising,
   but `frames_decoded`/`rtp_out_count` flat. Cause: no Opus payload (check
   `opus_pt` in `query_session`) or repeated `Opus decode error` in the logs.
   Confirm the viewer is actually sending audio (un-muted, `rtp_in_count` rising).
3. **Choppy / robotic echo.** Expected floor is clean 500 ms echo. Persistent
   choppiness points at packet loss (PLC engaging) or CPU — check for
   `Opus decode error`/`encode error` and `slow_link` lines. The fixed-lag jitter
   buffer (`SLV_JB_LAG` frames) tolerates minor reordering only.

---

## CHECK 3 — two-party mix (Phase 2 acceptance)

Set **`SLV_ECHO_AUTOSTART=false`** (echo OFF — echo would make each person hear
themselves instead of the other). Bring **two** voice-enabled viewers into the
**same region/parcel**, in voice range: e.g. *Legion Hienrichs* (A) and *Aleric
Fenwood* (B), on one machine (two viewer instances) or two.

Both provision and join the same room number (the C# `CalcRoomNumber` is
deterministic per parcel, so both land in one room; "Room already exists (486)"
on the second is **expected**). Each join logs `Participant <id> (<uuid>) joined
room <N>`.

**Pass criteria (the acceptance):**
1. **A speaks → B hears A; B speaks → A hears B.**
2. **Neither hears themselves** (N-minus-one: a listener's own audio is excluded
   from its mix).
3. **Mute A in B's viewer → A goes silent for B only** (right-click A → mute, or
   the volume slider to 0). A still hears B; if a third party C were present, C
   would still hear A. Un-mute restores A for B.
4. **The speaker's dot animates on the listener's screen** — when A talks, B's
   viewer shows A's voice dot/level moving (and vice-versa).

**Mix log trail** (on top of CHECK 1's; `docker logs <container>`):

| Stage | Docker logs (slvoice plugin) |
|---|---|
| **Both joined** | two `Participant <id> (<uuid>) joined room <N>` lines; each viewer gets a `{"<other-uuid>":{"j":{"p":true}}}` presence push |
| **Someone speaks** | `First audio frame decoded (960 samples/ch)` for the *speaking* participant's handle (decode happens once per source in the tick, not per listener) |
| **Active-set churn** (debug log level) | `participant <id> entered the active set` when talk starts; `… left the active set` ~150 ms after it stops |
| **Tick health** | no `room <N> tick <k> took …ms (>15ms deadline)` warnings under a 2-party load |

**Live diagnostics** (`query_session` on each handle, admin API as in CHECK 1).
For a **listener that is currently hearing a talker**:
- `active:true` on the **talking** participant; `rtp_in_rate` ~50 pkt/s on the talker;
- `frames_mixed` climbing and `rtp_out_rate` ~50 pkt/s on the **listener**
  (encode-skip means a listener hearing silence has `rtp_out_rate` ~0 — that is
  correct, not a fault);
- `mix_sources` ≥ 1 on a listener while someone else talks;
- `mix_memberships` ≥ 1 on a talker (its audio is folded into the other's mix);
- `tick_histogram.overruns` stays 0; `tick_histogram.max_ms` well under 15.

### CHECK 3 failure signatures
1. **Each person hears themselves, not the other.** `echo_active:true` in
   `query_session`. Cause: `SLV_ECHO_AUTOSTART=true` still set — echo overrides
   the mix per participant. Set it `false` and re-`up`.
2. **A talks, B hears nothing.** On B (listener): `rtp_out_rate` ~0 and
   `frames_mixed` flat while A's `active:true` and A's `rtp_in_rate` > 0. Check
   A is actually in the **same room** as B (`room` matches in both
   `query_session`), A is un-muted in B (`peer_ctl_entries`/mute), and A's audio
   is decoding (A `decode_ok:true`, `frames_decoded` climbing). If A's
   `rtp_in_rate` is 0, A isn't sending (viewer mic muted / device) — not a mixer
   fault.
3. **Mute doesn't silence.** Muting A in B has no effect. Confirm B's viewer sent
   it: B's `query_session` `data_msgs_received` rises on the mute click and
   `peer_ctl_entries` ≥ 1; with `SLV_DEBUG_MEDIA` the log shows
   `SLData fields=[m]`. If `peer_ctl_entries` stays 0, the viewer isn't emitting
   `{"m":{"<A>":true}}` on the data channel (check the data channel is open,
   `datachannel_open:true`).
4. **Dot doesn't animate for the other avatar.** The other participant must exist
   in the viewer's roster and the batch must reach it. Confirm `datachannel_open:
   true` on the listener and that the talker's `power_p`/VAD are non-zero
   server-side (talker `active:true`, `last_rms` > 0). The batch uses lowercase
   `v` for VAD and `{"j":{"p":true}}` for joins (both corrected in Phase 2 from
   the viewer source); an old build sending `"V"`/bare `"j"` would show a level
   but no speaking state / no data-channel-driven join.
5. **Choppy mix / robotic.** As CHECK 2: packet loss (PLC) or CPU. Watch for
   `room <N> tick … took …ms (>15ms deadline)` warnings and `Opus decode/encode
   error` lines; `tick_histogram` buckets show where ticks land.

## The media path (Phase 2)

`incoming_rtp` now only feeds each participant's fixed-lag **jitter buffer** and
stamps liveness. A dedicated **per-room 20 ms tick thread** then, each tick:
(1) decodes every ACTIVE participant once (DTX/VAD cull: no RTP within 150 ms →
inactive → decode skipped), computing real level/VAD; (2) for each listener,
sums the OTHER active participants (flat, equal gain, minus that listener's
per-source mutes, scaled by per-source `ug/220`) — the **N-minus-one** mix —
clamps, Opus-encodes (stereo, spec §9 fmtp), and `relay_rtp`s it; a listener
whose mix is silent is **encode-skipped** (no packet, timestamp still advances)
so encode cost scales with *audible* listeners. An **echoed** participant hears
its own 500 ms-delayed audio instead of the mix (diagnostic override). The pure
summing math is in `src/mixer/mix.c` (unit-tested, `tests/test_mix.c`).

## The media path (Phase 1B, superseded by the tick above)

`incoming_rtp` → fixed-lag **jitter buffer** (reorder + Opus PLC for gaps) →
`opus_decode_float` (48 kHz stereo) → **500 ms delay ring** → `opus_encode_float`
(stereo, spec §9 fmtp) → `relay_rtp` back to the **same** participant
(`janus_slvoice.c` `janus_slvoice_echo_frame` / `…_echo_ingest`). Every buffer is
preallocated at echo-enable — no per-packet allocation. RMS of each decoded frame
becomes `p = RMS*128` and a simple energy VAD in the mixer→client batch.

## The negotiation path (Phase 1A, field-proven — do not restructure)

- **Answering `m=application`** (`janus_slvoice_negotiate`): `janus_sdp_generate_answer_mline(…, JANUS_SDP_OA_MLINE, JANUS_SDP_APPLICATION, JANUS_SDP_OA_ACCEPT_EXTMAP, JANUS_RTP_EXTMAP_MID, JANUS_SDP_OA_DONE)` — modelled on `janus_videoroom.c:13113`. The core sets up SCTP because the answer's app m-line has `port>0` + `DTLS/SCTP` proto (`vendor/janus-gateway/src/sdp.c:1611`). The audio line accepts the MID + audio-level extensions (`janus_audiobridge.c:8244`) — required for the multi-line BUNDLE.
- **Data-channel lifecycle** (`incoming_data`/`data_ready`/`relay_data`) modelled on `janus_textroom.c` (`data_ready` gates the first send, `:1491`).

## Scope

Phase 1B echoes each participant to **itself** only. Cross-participant **mixing**
and **spatialization** are Phase 2 (`src/mixer/mixer.h`).
