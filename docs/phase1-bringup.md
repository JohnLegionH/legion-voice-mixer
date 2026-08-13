# Phase 1 bring-up runbook — echo test (GATE 0 → GATE 1)

This runbook brings a **fresh** OpenSimulator voice deployment up against the
Legion SLVoice container and proves the full single-participant media path with
the Phase-1 echo test. The target grid (`D:\legiongrid`) is a **Tranquillity**
split deployment and currently has **no voice configuration at all** — this is a
first-time bring-up, not a migration.

Provisioning uses the **in-process `WebRtcJanusService` leaf on the
regionserver** (the Robust-routed connector topology comes later), so **all
voice config below goes on the regionserver, not the gridserver (Robust).**

Acceptance is performed **by you, in-world**. It has two gates, in order:

- **GATE 0 — known-good baseline.** Configure the OpenSim WebRTC voice addon
  against this container with `PluginName` left at its **audiobridge default**,
  and prove flat voice between **two viewers**. This validates the C# side, ICE,
  DTLS, and your network with a mixer known to work.
- **GATE 1 — the plugin.** Flip `PluginName` to `janus.plugin.slvoice`, restart,
  and confirm the **echo test** passes (you hear yourself, ~500 ms delayed).

Do GATE 0 first. If GATE 0 fails, GATE 1 cannot be diagnosed cleanly, because a
GATE-0 failure is in the C#/ICE/network layer, not the plugin.

---

## Prerequisites

1. **The container is running and reachable** at the test host. This runbook
   assumes a container at **`192.168.1.225`**, HTTP signalling **`24223`**,
   admin **`24225`**, base paths **`/voice`** and **`/voiceAdmin`** (the current
   defaults). Confirm from the regionserver host:

   ```sh
   curl http://192.168.1.225:24223/voice/info
   ```
   You must see `"janus.plugin.slvoice":{"name":"Legion SLVoice mixer"...}` and
   (for GATE 0) `"janus.plugin.audiobridge"` in the `plugins` object.

2. **Media reachability.** Under bridge networking the container must advertise a
   reachable IP for ICE, or media never flows even though signalling works. Set
   **`JS_PUBLIC_IP=192.168.1.225`** in the container `.env` (LAN test) and open
   the **`JS_RTP_PORT_RANGE`** UDP range on the host firewall. See
   `docs/docker-notes.md` → "Windows/Docker Desktop".

3. **Secrets match.** The addon's `APIToken`/`AdminAPIToken` must equal the
   container's `JS_API_SECRET`/`JS_ADMIN_SECRET`.

4. **GATE 1 only — the `PluginName` key requires your
   `feature/voice-plugin-select` branch.** The stock os-webrtc-janus addon
   hard-attaches to `janus.plugin.audiobridge` and has **no `PluginName`
   setting**. Selecting `janus.plugin.slvoice` by config requires the
   `feature/voice-plugin-select` branch to be **built and deployed into the
   regionserver's addon assemblies** before GATE 1. Until that branch is
   deployed, GATE 1 is blocked (see GATE 1 failure #1).

---

## Container recap (values used below)

| Setting | Value |
|---|---|
| Host | `192.168.1.225` |
| HTTP signalling | `24223`, base path `/voice` |
| Admin API | `24225`, base path `/voiceAdmin` |
| `JS_API_SECRET` (== addon `APIToken`) | `testsecret` (use your real value) |
| `JS_ADMIN_SECRET` (== addon `AdminAPIToken`) | `testadmin` (use your real value) |
| `JS_PUBLIC_IP` | `192.168.1.225` |
| Echo auto-start (GATE 1 convenience) | `SLV_ECHO_AUTOSTART=true` in `.env` |

---

## Regionserver INI configuration

Add the two sections below to the regionserver's OpenSim config. In the split
layout that is:

```
D:\legiongrid\regionserver\config\OpenSim.ini
```

(or a dedicated `D:\legiongrid\regionserver\config\WebRtcVoice.ini` that
`OpenSim.ini` includes via its `[Includes]` — either works, as long as it is on
the **regionserver**). **Nothing** goes in `D:\legiongrid\gridserver\config\Robust.ini`
for this in-process bring-up.

> **Key-name caveat.** The authoritative C# config contract lives in
> `docs/current-architecture.md`, which is not yet vendored into this repo. The
> keys below follow the os-webrtc-janus convention and this repo's
> `docs/docker-notes.md`. If your deployed addon build spells a key differently,
> its own `.example`/`.ini` is the final authority — reconcile against it.

```ini
[WebRtcVoice]
    Enabled = true
    ; In-process leaf on the region for both spatial and non-spatial voice.
    SpatialVoiceService    = "WebRtcJanusService"
    NonSpatialVoiceService = "WebRtcJanusService"

[JanusWebRtcVoice]
    Enabled = true
    ; Signalling + admin endpoints of the container (note /voice, /voiceAdmin).
    JanusGatewayURI      = "http://192.168.1.225:24223/voice"
    JanusGatewayAdminURI = "http://192.168.1.225:24225/voiceAdmin"
    ; Must equal the container's JS_API_SECRET / JS_ADMIN_SECRET.
    APIToken      = "testsecret"
    AdminAPIToken = "testadmin"

    ; ---- Plugin selection --------------------------------------------------
    ; GATE 0: leave PluginName unset (or commented) -> defaults to audiobridge.
    ; GATE 1: set PluginName = janus.plugin.slvoice   (needs feature/voice-plugin-select)
    ; PluginName = janus.plugin.slvoice
```

### GATE 0 config
Leave `PluginName` **commented/unset** (default `janus.plugin.audiobridge`).

### GATE 1 config change
Two edits, then restart the regionserver:
1. Uncomment `PluginName = janus.plugin.slvoice` in `[JanusWebRtcVoice]`.
2. Add `SLV_ECHO_AUTOSTART=true` to the container `.env` and
   `docker compose up -d` the container (so a stock viewer hears itself without
   sending an `{"echo":true}` data-channel message). Alternatively leave it off
   and drive the toggle from your test client — see `docs/sldata-extensions.md`.

---

## GATE 0 — flat voice between two viewers (audiobridge baseline)

**Procedure**
1. Apply the GATE 0 config; start/restart the regionserver.
2. Log in **two** viewers to the same region, within voice range.
3. Speak on each. Each viewer should hear the other (flat, non-spatial mix).

**What a successful connect shows**

*Region console (`regionserver`)* — indicative content to look for (exact text
depends on the addon build):
- `[WebRtcVoice] enabled; SpatialVoiceService = WebRtcJanusService`
- `[JanusWebRtcVoice] Janus session created` / `attached janus.plugin.audiobridge`
- `[JanusWebRtcVoice] joined room <N>` per avatar

*Docker logs (`docker logs <container>`)* — Janus core, audiobridge plugin:
- `Creating new session` / `Creating new handle in session`
- `[janus.plugin.audiobridge-0x…] … joined room <N>`
- ICE: `… ICE … connected` / `PeerConnection … state … connected`
- DTLS: `… DTLS … handshake … completed`
- Media: audiobridge mixing both participants (both hear each other)

**Three most likely GATE-0 failures & their signatures**

1. **Wrong API secret.** Signature — docker logs:
   `[WARN] … Unauthorized request (wrong API secret)`; region console: Janus
   provisioning returns **HTTP 403**. Fix: `APIToken` must equal
   `JS_API_SECRET`.
2. **Wrong URI / base path.** Signature — region console: "could not reach
   Janus" / **HTTP 404**; docker logs show **no** `Creating new session`. Verify
   `curl http://192.168.1.225:24223/voice/info` works from the regionserver host
   (a stock `/janus` path is now **404** — the default is `/voice`).
3. **ICE/media never establishes** (public IP or firewall). Signature — docker
   logs: `[WARN] … ICE failed` or ICE stuck "gathering", and **no**
   `PeerConnection … connected`; viewers show connected but silent. Fix: set
   `JS_PUBLIC_IP=192.168.1.225` and open the `JS_RTP_PORT_RANGE` UDP range.

Do not proceed to GATE 1 until two viewers reliably hear each other.

---

## GATE 1 — echo test on `janus.plugin.slvoice`

**Procedure**
1. Apply the GATE 1 config change (PluginName + `SLV_ECHO_AUTOSTART=true`);
   restart the regionserver and re-`up` the container.
2. Log **one** viewer into the region, in voice range.
3. Speak. You should hear **yourself, ~500 ms delayed**. That is the echo test
   passing: it proves JSEP → ICE → DTLS → data channel → Opus RTP in → decode →
   delay → encode → RTP back, end to end, with no mixer involved.

**What a successful connect shows, stage by stage**

Every `[janus.plugin.slvoice-0x…]` line below is emitted by this plugin
(`docker logs <container>`); the ICE/DTLS lines are Janus core.

| Stage | Docker logs (Janus / slvoice plugin) | Region console (indicative) |
|---|---|---|
| **Provisioning** | `[…slvoice-0x…] New session created` · `Handling request 'create'` · `Created room <N>` · `Handling request 'join'` · `Negotiated Opus pt=111, datachannel=yes` · `Participant <id> (<name>) joined room <N>` | `[JanusWebRtcVoice] attached janus.plugin.slvoice` · `joined room <N>` |
| **ICE** | core: `… Trickle candidate …` · `… ICE … connected` / `PeerConnection … state … connected` | viewer voice dot connecting |
| **DTLS** | core: `… DTLS … handshake … completed` (SRTP keys set) | — |
| **PC up** | `[…slvoice-0x…] WebRTC media is now available (PeerConnection up)` (+ `Echo auto-started (echo_autostart)` if enabled) | voice dot goes green |
| **Data channel open** | `[…slvoice-0x…] Data channel open` | position updates begin |
| **First RTP in** | (default build is silent per-packet) confirm via admin `query_session`: `rtp_in_count` rising, `last_decode_ok: true`. Build with `-DSLV_DEBUG_MEDIA` to log `echo: in X bytes -> N samples -> out Y bytes (pt=111)` per packet | — |
| **Echo on** | `[…slvoice-0x…] Echo mode ENABLED (500ms delay)` (via SLData) or `Echo auto-started (echo_autostart)` (via env) | you hear yourself, delayed |

**Live diagnostics (admin API → `query_session`).** At any point, query the
plugin's per-session state through the Janus **admin** API. Get the `session_id`
and `handle_id` from the provisioning logs, then:

```sh
curl -s -X POST http://192.168.1.225:24225/voiceAdmin \
  -H 'Content-Type: application/json' \
  -d '{"janus":"handle_info","session_id":<S>,"handle_id":<H>,
       "admin_secret":"testadmin","transaction":"q1"}'
```

The plugin block in the response reports:
`webrtc_up`, `ice_state`, `dtls_state`, `datachannel_open`, `opus_pt`,
`echo_active`, `rtp_in_count`, `rtp_in_rate`, `last_decode_ok`,
`data_msgs_received`, `last_data_fields_seen`. This is the fastest way to see
exactly how far a connect got.

**Three most likely GATE-1 failures & their signatures**

1. **`PluginName` not honoured (branch not deployed).** The stock addon ignores
   `PluginName` and attaches audiobridge anyway. Signature — docker logs show
   `[janus.plugin.audiobridge-…] … joined` (or `No such plugin
   'janus.plugin.slvoice'` if the value reached Janus but the region build can't
   request it), and **no** `[janus.plugin.slvoice-…] New session created`.
   Fix: build & deploy `feature/voice-plugin-select` on the regionserver (see
   Prerequisite 4).
2. **Echo never turns on.** PeerConnection comes up and RTP flows, but there is
   no loopback because neither `{"echo":true}` nor `echo_autostart` was set.
   Signature — docker logs show `WebRTC media is now available` and
   (admin) `rtp_in_count` rising, but **no** `Echo mode ENABLED` /
   `auto-started`, and `query_session` shows `echo_active: false`. Fix: set
   `SLV_ECHO_AUTOSTART=true` (and re-`up` the container), or have your client
   send `{"echo":true}` on the data channel.
3. **Opus not negotiated, or decode errors.** If the offer carries no Opus, join
   fails: docker logs `[…slvoice-…] Request error 420: Offer does not include
   Opus`, and the region console reports a provisioning error. If negotiation
   succeeds but audio is garbled/silent, watch for repeated
   `[…slvoice-…] Opus decode error: …` (→ `last_decode_ok:false`). Fix: confirm
   the viewer offers Opus (it does by default) and that the negotiated
   `opus_pt` in `query_session` is sane (typically 111).

**Pass criterion:** with one viewer, speaking produces your own voice back,
~500 ms later, cleanly. That completes Phase 1.

---

## Notes / scope

- Phase 1 is **single-participant echo only** — no mixing, no spatial logic, no
  per-listener processing (that is Phase 2, `src/mixer/mixer.h`). Two viewers on
  `janus.plugin.slvoice` will each independently echo themselves; they will
  **not** hear each other yet. Hearing each other is validated at GATE 0 on
  audiobridge precisely because slvoice cannot mix yet.
- The audiobridge compatibility constraint (`docs/protocol-compat.md`) is why
  GATE 0 ⇄ GATE 1 is a pure config flip with no C# change.
- SLData / the `echo` toggle: `docs/sldata-extensions.md`.
- Container networking / `JS_PUBLIC_IP` / ports: `docs/docker-notes.md`.
