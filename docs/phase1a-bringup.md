# Phase 1A bring-up runbook — hold a WebRTC voice session

Goal: a real **Firestorm 7.2.2** viewer establishes and **holds** a WebRTC voice
session against `janus.plugin.slvoice`. No audio — a stable session with a
visible voice dot, active mic control, and **silence** is the acceptance bar.

The failure this phase fixes: against stock `janus.plugin.audiobridge` the viewer
never holds the session. Its offer contains an `m=application` line (the SLData
DataChannel it creates *before* negotiation, per `docs/webrtc-voice-spec.md` §9);
audiobridge doesn't support data channels, so it skips that line, the answer
comes back without it, and the viewer judges the session unusable and retries —
a join/leave loop several times a second, with the core logging **"Skipping
unsupported application media line"**. `janus.plugin.slvoice` answers **both**
the audio and the data-channel m-lines, so the session holds.

> **"Room already exists" is EXPECTED**, not a bug: the C# side re-creates rooms
> and treats error 486 as success (`docs/voice/current-architecture.md` §3.3).

---

## Prerequisites

1. Container running at **`192.168.1.225`**, HTTP `24223` (`/voice`), admin
   `24225` (`/voiceAdmin`). Confirm it supports data channels — this is
   mandatory, the core must be built with SCTP:

   ```sh
   curl http://192.168.1.225:24223/voice/info | grep -o '"data_channels":true'
   curl http://192.168.1.225:24223/voice/info | grep -o '"janus.plugin.slvoice":{"name":"Legion SLVoice mixer"'
   ```
   Both must print. `"data_channels":true` is the SCTP capability; if it's
   `false`, no plugin can answer the data channel (rebuild the image).

2. **`JS_PUBLIC_IP=192.168.1.225`** in the container `.env` (or `JS_PUBLIC_HOST`),
   and the `JS_RTP_PORT_RANGE` UDP range open on the firewall — required for ICE
   to complete (see `docs/docker-notes.md` → "Windows/Docker Desktop" /
   "External access"). Without it the session negotiates but ICE never connects,
   so the data channel never opens.

3. **`PluginName` requires the `feature/voice-plugin-select` branch.** The stock
   os-webrtc-janus addon hardcodes `janus.plugin.audiobridge`
   (`JanusAudioBridge.cs:41`, `current-architecture.md` §6). Selecting
   `janus.plugin.slvoice` by config needs that branch built and deployed into the
   regionserver assemblies (see `docs/phase1-bringup.md` prerequisites).

---

## Regionserver INI

In `D:\legiongrid\regionserver\config\OpenSim.ini` (the in-process leaf; nothing
on the gridserver). Keys per `current-architecture.md` §5:

```ini
[WebRtcVoice]
    Enabled = true
    ; dll:Class of the spatial LEAF (current-architecture.md §2.3/§5), loaded via
    ; ServerUtils.LoadPlugin. NOT the dispatcher (that is an auto-discovered
    ; region module). Copy the exact value from os-webrtc-janus.ini.example.
    SpatialVoiceService    = "WebRtcJanusService.dll:WebRtcJanusService"
    NonSpatialVoiceService = "WebRtcJanusService.dll:WebRtcJanusService"

[JanusWebRtcVoice]
    ; No Enabled key here — the four fields below being non-blank enable the leaf.
    JanusGatewayURI      = "http://192.168.1.225:24223/voice"
    APIToken             = "<== container JS_API_SECRET>"
    JanusGatewayAdminURI = "http://192.168.1.225:24225/voiceAdmin"
    AdminAPIToken        = "<== container JS_ADMIN_SECRET>"
    ; Phase 1A acceptance: attach to slvoice, not audiobridge.
    ; Requires the feature/voice-plugin-select branch (see Prerequisite 3).
    PluginName           = janus.plugin.slvoice
```

---

## Successful-connect log sequence

The definitive per-stage evidence trail. Every `[janus.plugin.slvoice-0x…]` line
is emitted by this plugin (`docker logs <container>`); ICE/DTLS lines are the
Janus core. Enable `LOG_INFO` (Janus default) — every negotiation step logs at
info.

| Stage | Docker logs (Janus core / slvoice plugin) | Region console (indicative) |
|---|---|---|
| **Provision** | `[…slvoice-0x…] New session created` · `Handling request 'create'` · `Created room <N>` (or `Room <N> already exists (error 486)` — **expected**) · `Handling request 'join'` | `[REGION WEBRTC VOICE]: enabled` · OnRegisterCaps per avatar · `[JanusWebRtcVoice] attached janus.plugin.slvoice` · joined room `<N>` |
| **Offer/Answer** | `[…slvoice-0x…] Offer received: Opus pt=111, m=application present=yes` · **`Answer sent: audio Opus pt=111; m=application answered=YES`** · `Participant <id> (<uuid>) joined room <N>` | answer received; voice dot begins connecting |
| **ICE** | core: `… ICE …  connected` / `PeerConnection … state … connected` | dot connecting |
| **DTLS** | core: `… DTLS … handshake … completed` | — |
| **PC up** | `[…slvoice-0x…] WebRTC media is now available (ICE connected, DTLS complete, PeerConnection up)` | dot goes green |
| **Data channel open** | `[…slvoice-0x…] Data channel open (writable)` — then the ~100ms per-peer p/V batch flows to the viewer | mic control active; dot rendered |
| **RTP ingest** | (silent at default level) `query_session` shows `rtp_in_count` rising once the viewer un-mutes; build with `-DSLV_DEBUG_MEDIA` for per-packet lines. Phase 1A counts, never processes. | — |

**Two things that must NOT appear:** the core warning
`Skipping unsupported application media line`, and any join/leave loop
(`Creating new session` / `Last user … just left … going idle` repeating). Their
absence, plus `m=application answered=YES`, is the fix working.

**Live diagnostics** (admin API → `query_session`) — get `session_id`/`handle_id`
from the provision logs:

```sh
curl -s -X POST http://192.168.1.225:24225/voiceAdmin \
  -H 'Content-Type: application/json' \
  -d '{"janus":"handle_info","session_id":<S>,"handle_id":<H>,"admin_secret":"<AdminAPIToken>","transaction":"q"}'
```

The plugin block reports: `ice_state`, `dtls_state`, `datachannel_negotiated`,
`datachannel_answered`, `datachannel_open`, `rtp_in_count`,
`data_msgs_received`, `last_data_fields_seen`, `session_uptime`, `opus_pt`.
A held session shows `datachannel_answered:true`, `datachannel_open:true`, and a
climbing `session_uptime`.

---

## Three most likely failure points

1. **The data channel isn't answered → the join/leave loop returns.**
   Signature — docker logs: core `Skipping unsupported application media line`
   and, from the plugin, `Answer sent: … m=application answered=NO` (plus the
   WARN `Offer had a data channel but the answer did NOT accept it`); the viewer
   loops `Creating new session …` / `… going idle`. Causes: (a) the viewer
   attached to **audiobridge**, not slvoice — check the plugin package in the
   logs and that `PluginName = janus.plugin.slvoice` took effect (needs the
   `feature/voice-plugin-select` branch); (b) the container's Janus lacks SCTP —
   `curl …/voice/info` shows `"data_channels":false` (rebuild the image).

2. **Wrong plugin attached (PluginName not honoured).** The stock addon ignores
   `PluginName` and attaches audiobridge, reproducing failure #1. Signature:
   docker logs show `[janus.plugin.audiobridge-…]` handling the join, **no**
   `[janus.plugin.slvoice-…] New session created`. Fix: build & deploy
   `feature/voice-plugin-select`, set `PluginName = janus.plugin.slvoice`.

3. **ICE/DTLS never completes → session negotiated but never held.** The answer
   is correct but the PeerConnection can't connect. Signature: plugin logs
   `Answer sent: … m=application answered=YES` but **no**
   `WebRTC media is now available` and **no** `Data channel open`; core logs ICE
   `failed` or stuck "gathering"; `query_session` shows
   `datachannel_answered:true` but `datachannel_open:false`, `ice_state:disconnected`.
   Fix: set `JS_PUBLIC_IP` (or `JS_PUBLIC_HOST`) to `192.168.1.225` and open the
   `JS_RTP_PORT_RANGE` UDP range on the firewall (`docs/docker-notes.md`).

---

## The negotiation code (what it was modelled on)

- **Answering the `m=application` line** — `src/janus_slvoice.c`
  `janus_slvoice_negotiate()`:
  ```c
  janus_sdp_generate_answer_mline(offer, answer, m,
      JANUS_SDP_OA_MLINE, JANUS_SDP_APPLICATION,
      JANUS_SDP_OA_DONE);
  ```
  This is the minimal correct option list to accept an offered SCTP data channel.
  `janus_textroom` is the in-tree DataChannel reference, but it is **offer-only**
  (it ships a hardcoded offer template, `janus_textroom.c:866`, and never answers
  an offer), so the *answer* pattern is modelled on **`janus_videoroom.c:13113`**,
  which answers an offer's application m-line exactly this way. There is **no**
  plugin capability flag: the core sets up SCTP purely because the answer's
  application m-line has `port>0` and a `DTLS/SCTP` proto — see
  `vendor/janus-gateway/src/sdp.c:1611-1637` (the `else` branch at `:1625` is the
  "Skipping unsupported application media line" warning we avoid).
- **Data-channel lifecycle** (`incoming_data`, `data_ready`, `relay_data`) is
  modelled on **`janus_textroom.c`**: `data_ready` gates the first send
  (`janus_textroom.c:1491`, "we shouldn't send anything before this happens"),
  and `relay_data` uses `janus_plugin_data{ .label=NULL, .protocol=NULL,
  .binary=FALSE, … }` (`janus_textroom.c:1632`).
- **Mixer→client SLData** — the ~100ms per-peer power/VAD batch
  (`janus_slvoice_sender()`), keyed by the participant's display (agent UUID),
  each entry `{"p":<RMS*128>,"V":<VAD>}` with zeros in Phase 1A (silence), plus
  per-peer `{"<uuid>":{"j"|"l":true}}` join/leave notices — per
  `docs/webrtc-voice-spec.md` §9. This is the well-formed state the viewer needs
  to render the (silent) dot.

## Scope

Phase 1A holds the session. **Out of scope:** audio mixing, echo, Opus
decode/encode, RTP forwarding — `incoming_rtp` ingests and counts only. Audio is
Phase 1B+.
