# Voice configuration register (sim side)

The sim-side counterpart of the mixer's "Configuration compatibility rule" and
"Knob register" (`legion-voice-mixer/docs/docker-notes.md`), for the ini keys the
`os-webrtc-janus` addon reads. Written 2026-09-14 (mixer slice 8b) from the tree at
`feature/ais-v3` `c8bc0a7cfa`; every file:line below was checked by grep. The live
column is `D:\legiongrid\regionserver\config\OpenSim.ini` on that date.
Updated 2026-09-14 for fix V-1 (`3283c939f1`); the RM, JS, Ex and Ini line numbers were re-checked at
that commit.

## Configuration compatibility rule

Upgrading a regionserver onto a new build must not silently change what a running
grid does.

- **A new key's default reproduces the behaviour of the build before the key
  existed.**
- **Narrowing is always opt-in**: refusals, guards, timeouts that end work early,
  allow-lists. The operator sets the narrower value; an upgrade never does.
- **Security- and connectivity-relevant keys log their effective value at module
  start**, so a wrong value is visible in the region log before anyone logs in.
  Secrets (`APIToken`, `AdminAPIToken`) are logged as set/unset only. (Not yet
  audited against the ten keys below; a follow-up slice should check it.)
- **Every deploy record lists "Behaviour changes on upgrade" and "One-time
  migrations"**, even when empty. The mixer's `docs/RELEASES.md` carries the sim
  releases that affect mixer or connector operators.

A default that must break compatibility is allowed only if it is recorded below as a
**pre-rule exception, retained deliberately**, with what changed and why.

## Knob register

File legend (all under `Addons/os-webrtc-janus/`):
- **RM** = `WebRtcVoiceRegionModule/WebRtcVoiceRegionModule.cs`
- **SM** = `WebRtcVoiceServiceModule/WebRtcVoiceServiceModule.cs`
- **JS** = `Janus/WebRtcJanusService.cs`
- **VCM** = `WebRtcVoiceRegionModule/VoiceConnector/VoiceConnectorModule.cs`
- **Ex** = `os-webrtc-janus.ini.example`
- **Ini** = `os-webrtc-janus.ini` (the shipped file)

Since `3283c939f1` (fix V-1) the shipped `os-webrtc-janus.ini` carries four of these keys: `StunServers`,
`VisibilityFeederEnabled`, `VisibilityEmitEnabled` and `PluginName`. It carries none of the others.

| Key | Section | Default (read at) | Example / shipped ini | Live | Since | Before the key existed |
|---|---|---|---|---|---|---|
| `RequestTimeoutMs` | `[JanusWebRtcVoice]` | 5000 = `JanusSession.DefaultRequestTimeoutMs` (JS:130; ≤0 falls back to 5000) | Ex:62, commented `5000`; Ini absent | unset → 5000 | `3ca74633df` 2026-09-13 (O-50/O-51) | No timeout: after the ack a request waited on its completion source forever. **Pre-rule exception, retained deliberately** (below). |
| `RefusalCacheSeconds` | `[WebRtcVoice]` | 5 = `ProvisionRefusalCache.DefaultSeconds` (RM:125, SM:89; negative → 0 = off) | absent | unset → 5 | `1b88989e9f` 2026-09-13 (O-72) | No cache: every viewer retry re-ran the estate/parcel checks and the provision. **Pre-rule exception, retained deliberately** (below). |
| `VisibilityTickMs` | `[WebRtcVoice]` | 250 (RM:128) | absent | 250 | `e044ee1670` 2026-08-16 (Phase 3a feeder) | Feature did not exist; no periodic visibility work. **Pre-rule exception, retained deliberately** (below). |
| `VisibilityEmitEnabled` | `[WebRtcVoice]` | **true** since `3283c939f1` = `DefaultVisibilityEmitEnabled` (RM:84; read RM:90, called RM:129). **Before V-1:** false. | Ex:33 `true`; Ini:27 `true` | true | `fc1454ea3e` 2026-08-16 (peer_ctl_batch sender); default flipped in `3283c939f1` 2026-09-14 (V-1, O-78) | The feeder computed the matrix but nothing was sent to the mixer; the `false` default reproduced that until V-1. **Pre-rule exception, retained deliberately** (below). |
| `VisibilityFeederEnabled` | `[WebRtcVoice]` | **true** since `3283c939f1` = `DefaultVisibilityFeederEnabled` (RM:83; read RM:87, called RM:127). **Before V-1:** false. | Ex:32 `true`; Ini:26 `true` | true | `e044ee1670` 2026-08-16; default flipped in `3283c939f1` 2026-09-14 (V-1, O-78) | No feeder thread; the `false` default reproduced that until V-1. **Pre-rule exception, retained deliberately** (below). |
| `AllowNpcVoice` | `[WebRtcVoice]` | false (SM:86 enforced; VCM:90 read for disclosure) | Ex:41, commented `false`; Ini absent | unset → false | `7240797fc8` 2026-08-31 (S-CON-1) | No NPC guard: NPC presences provisioned voice like avatars. **Pre-rule exception, retained deliberately** (below). |
| `VoiceRangeMetres` | `[WebRtcVoice]` | 20 = `DefaultVoiceRangeMetres` (VCM:91) | Ex:44, commented `20`; Ini absent | unset → 20 | `d95754509d` 2026-08-31 (S-CON-3) | Feature did not exist; the value only sizes the new connector proximity notice. |
| `StunServers` | `[WebRtcVoice]` | empty (RM:126); empty = the addon adds no `stun-servers` feature | Ex:26 and Ini:20, active `stun:stun.l.google.com:19302` (Ini since `3283c939f1`) | `stun:stun.l.google.com:19302` | `5e0f289fc1` 2026-08-15 | The addon advertised nothing. The core's own `StunServers` (`GridInfo.cs:519`, since `fc607035c8`) could already advertise `stun-servers`. The empty code default still reproduces the addon's prior behaviour; only the shipped files changed. |
| `PluginName` | `[JanusWebRtcVoice]` | **`janus.plugin.slvoice`** since `3283c939f1` = `DefaultPluginName` (JS:55; read JS:59, called JS:120). **Before V-1:** `janus.plugin.audiobridge`. | Ex:58 and Ini:41, `janus.plugin.slvoice` (since `3283c939f1`) | `janus.plugin.slvoice` | `ade2b29f6b` 2026-08-13; default changed in `3283c939f1` 2026-09-14 (V-1, SC-108/SC-115) | Hard-coded `janus.plugin.audiobridge` (`JanusAudioBridge.cs:42` at the parent); the audiobridge default reproduced that until V-1. **Pre-rule exception, retained deliberately** (below). |
| `CapabilitySecret` | `[VoiceConnector.<name>]` | unset = no join-capability endpoint for the record (`VoiceConnector/VoiceConnectorRegistry.cs:163`); under 32 characters = WARN and treated as unset (`:166-171`); VCM:209-213 registers the handler only when some record has one | absent | unset | slice 0.7b, 2026-09-16 (O-88, design §11.10) | No endpoint existed. Unset reproduces that exactly: no handler registered, payloads and logs unchanged. Setting it serves `POST /voice/connector/<name>/join-cap` on the region HTTP server with `Authorization: Bearer <secret>`. When the peer and the region are on different hosts the bearer crosses the network, so use TLS or a private network. **A connector that must work on an empty region needs this set** (slice 0.8c2, ruling on O-93/O-92): fetching the capability is what makes the sim ensure the connector's room exists, and the sim creates a room for nobody else — without a secret the connector can only join a room some viewer has already provisioned, and its arming waits out the `unknown_room` backoff until one does. |
| `AdminTimeoutMs` | `[JanusWebRtcVoice]` | 5000 (RM:141) | absent | unset → 5000 | `fc1454ea3e` 2026-08-16 (first read in SM, moved to RM in `de7d4ad801`) | No admin sends existed. It bounds the peer_ctl_batch emission, which is on by default since `3283c939f1`. |

### Pre-rule exceptions, retained deliberately

- **`RequestTimeoutMs` (5000).**
  - *What changed:* a Janus request whose completion event arrives after 5 s now
    fails with a synthetic `"timeout"` instead of waiting forever.
  - *Why accepted:* the unbounded wait pinned a caps thread permanently whenever an
    event was lost (O-50). An answer later than 5 s is already a failed join to the
    viewer, so no working install relied on the old behaviour.
- **`RefusalCacheSeconds` (5).**
  - *What changed:* a refused or failed provision is replayed from cache for 5 s
    per agent instead of re-running the checks and the provision.
  - *Why accepted:* the viewer retries every non-2xx immediately with no backoff.
    Without the cache, a single refusal became a ~2 Hz provision storm against the
    sim and the mixer (O-72, seen live 2026-09-13). 5 s only delays a refusal that
    has just been lifted, and `0` restores the old behaviour.
- **`AllowNpcVoice` (false).**
  - *What changed:* NPC presences are refused voice unless they are a registered
    connector identity.
  - *Why accepted:* an unregistered NPC in voice bypasses the connector policy
    record, its moderation mute and its disclosure (S-CON-1, O-46 class). No live
    install used NPC voice before connectors, and registered connectors are
    unaffected.
- **`VisibilityTickMs` (250).**
  - *What changed:* enabling the visibility feeder brought a 250 ms periodic matrix
    pass that did not exist before.
  - *Why accepted:* 250 ms is the rate the visibility design was measured at. When the
    key was added the tick was inert, because `VisibilityFeederEnabled` defaulted to
    false. Since `3283c939f1` the feeder runs by default, so the tick runs too (see the
    V-1 exception below).
- **`PluginName` (`janus.plugin.slvoice`, since `3283c939f1`).**
  - *What changed:* an install that never set the key attached voice handles to the
    stock `janus.plugin.audiobridge`. It now attaches to the Legion mixer. The id is
    `JANUS_SLVOICE_PACKAGE` in legion-voice-mixer `src/janus_slvoice.c`.
  - *Why accepted:* under the old default, a by-the-book install attached to
    AudioBridge and appeared to work, with none of the mixer's spatial mix, visibility
    or moderation (SC-108, SC-115). `PluginName = janus.plugin.audiobridge` restores
    the old behaviour.
- **`VisibilityFeederEnabled` and `VisibilityEmitEnabled` (true, since `3283c939f1`).**
  - *What changed:* an install that never set them ran no feeder and sent the mixer no
    permission state. It now runs the 250 ms feeder in each region and sends
    `peer_ctl_batch` over the Janus admin API. When `JanusGatewayAdminURI` or
    `AdminAPIToken` is absent it runs matrix-only and logs a WARN.
  - *Why accepted:* with both false, a fresh deployment enforced none of the
    mix-level permissions: bans, SeeAVs hiding and moderation mutes (O-78). Setting both
    false restores the old behaviour.
  - *Limit:* the mixer still passes audio before a listener's first batch (SC-31,
    SC-33), so this narrows the fail-open window rather than closing it.

## Other voice keys read (not yet registered)

- `[WebRtcVoice]`
  - `Enabled`: SM:82, JS:110, RM:119, `WebRtcVoiceServiceConnector.cs:60`, `WebRtcVoiceServerConnector.cs:62`
  - `MessageDetails`: RM:122
  - `SpatialVoiceService`: SM:94
  - `NonSpatialVoiceService`: SM:95
  - `WebRtcVoiceServerURI`: `WebRtcVoiceServiceConnector.cs:63`
  - `LocalServiceModule`: `WebRtcVoiceServerConnector.cs:71`
  - `VisibilityRoomSendConcurrency`: RM:132, default 4
  - `NpcNameToken`: VCM:89, default "NPC"
- `[JanusWebRtcVoice]`
  - `JanusGatewayURI`: JS:114
  - `APIToken`: JS:115
  - `JanusGatewayAdminURI`: JS:116, RM:139
  - `AdminAPIToken`: JS:117, RM:140
  - `MessageDetails`: JS:128
- Grid id (`Janus/JanusAudioBridge.cs:75-80`): `GatekeeperURI`, `[GatekeeperService] ExternalName`, `[GridService] Gatekeeper`.
- `[VoiceConnector.<name>]` (`VoiceConnector/VoiceConnectorRegistry.cs:98-153`): `Enabled`, `NpcFirstName`, `NpcLastName`, `Scope`, `Position`, `MayInject`, `AuthorisedBy`, `InjectSourceUrl`, `Region`. `CapabilitySecret` is registered in the knob table above (slice 0.7b).
- Slice 0.7b: VCM also reads `[WebRtcVoice] JoinCapabilityEnabled` (VCM:111) and `[JanusWebRtcVoice] JoinCapabilitySecret` (VCM:112), the same keys JS mints avatar capabilities with, to mint connector capabilities.

## Notes

- **`StunServers` has two readers.** Both the addon's `[WebRtcVoice] StunServers`
  and the core's `StunServers` (`[Startup]` etc., `Scene.cs:1328-1331`) add
  `stun-servers` to SimulatorFeatures. Which one wins when both are set is not
  established. Live sets only the addon key.
- **The shipped ini lacked most of these keys before V-1** (O-21 / O-57). Since
  `3283c939f1`, both `os-webrtc-janus.ini` and the example carry `StunServers`,
  `VisibilityFeederEnabled`, `VisibilityEmitEnabled` and `PluginName`. Neither carries
  `VisibilityTickMs` or `AdminTimeoutMs`.
