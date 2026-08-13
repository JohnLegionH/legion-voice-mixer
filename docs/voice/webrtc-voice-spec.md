# Spatial WebRTC Voice Service — Feature Specification

**Status:** Draft for review
**Scope:** A server-side WebRTC voice service (mixer plugin + region/grid integration) implementing the published Second Life WebRTC voice protocol, for OpenSimulator-derived grids. Designed to run unchanged from a single-region standalone to a large multi-mixer grid.

---

## 1. Goals and non-goals

**Goals**

- Full compatibility with the SL WebRTC viewer protocol (ProvisionVoiceAccountRequest / VoiceSignalingRequest caps, SLData data channel) so stock WebRTC-capable viewers work without modification.
- True per-listener spatial audio (distance, azimuth, listener orientation), which no current OpenSimulator voice service provides.
- Privacy and permission enforcement performed **in the mix**, on the server, not delegated to clients.
- First-class diagnosability: voice failures must be triageable by the user or operator in minutes, without log-emailing rituals.
- Restoration of capabilities lost in the Vivox→WebRTC transition (camera-position listening, voice morphing) and capabilities Vivox never had (mixer-enforced moderation, effects/connector hooks).
- Scale-invariant deployment: identical code path and configuration model from a one-region hobby grid to a large commercial grid. Small deployments must not pay complexity for scale they don't need.

**Non-goals**

- Client-side spatialization via selective forwarding (SFU). Ruled out by the privacy model (§3).
- True peer-to-peer media between clients. Ruled out permanently (§3.1).
- Video.

---

## 2. Requirements provenance

Requirements in this document derive from three sources, referenced by tag:

- **[OPS]** — requirements and constraints raised by grid operators and community developers during design discussions (anonymized).
- **[REC]** — the public complaint and bug record from Second Life's 2024–2026 WebRTC migration (forum threads, feedback portal, release notes, outage postmortems).
- **[ENG]** — engineering analysis of the existing OpenSimulator Janus-based implementation and the published SL protocol.

---

## 3. Tier 0 — Trust and privacy invariants

These are architectural properties. Every other section assumes them.

### 3.1 All media is server-relayed, always **[OPS]**

No client ever receives another client's ICE candidates. Every media connection is client ↔ voice server. "Peer-to-peer" calls are implemented as two-party sessions on the non-spatial pool, exactly as Second Life does, so that client IP addresses are never exposed to other users. This must be structurally impossible to misconfigure — there is no configuration option that enables direct client-to-client ICE.

### 3.2 Voice-server trust domains **[OPS]**

Every voice server instance is classified as **grid-operated** or **region-operated**, and the classification is communicated to the client at provisioning time.

- Hypergrid visitors are provisioned only onto grid-operated servers. Region-local voice is not offered to them. (A visiting avatar must not be required to hand ICE negotiation to infrastructure run by an arbitrary region operator.)
- Local users connecting to region-operated servers receive a disclosure.
- Default topology is grid-operated; region-local is an opt-in for closed estates that want the latency benefit.

### 3.3 Sim-authoritative permission enforcement **[ENG]**

Parcel and estate audibility is always computed from the avatar's authoritative sim-side position and the sim's access lists. Access/visibility state is pushed from the simulator to the mixer with version epochs and **fails closed** on staleness. Client-reported listener position is accepted only as a rendering hint (§6.1), never as a permission input, because client positions are spoofable.

### 3.4 Mixer output is the permission boundary **[ENG]**

A listener's downstream mix contains only audio that listener is entitled to hear. There is no "muted client-side" audio in flight; a modified viewer cannot un-hide a hidden avatar or un-mute a moderator mute. This is the property that makes per-parcel privacy (§7.3) enforceable, and it is the reason the service is an MCU (server mixes) rather than an SFU (server forwards).

### 3.5 Capture requires consent **[OPS]**

The connector layer (§7.5) can tap live audio. Any tap on a spatial or group mix requires an in-channel disclosure to affected participants. No plaintext media at rest without explicit configuration and disclosure. The moderation and legal posture ships with the hook, not after the first incident.

---

## 4. Tier 1 — Reliability and diagnosability

The dominant failure class in the public record **[REC]** is silent partial failure: voice indicators animate, the data channel works, and no audio flows in one or both directions — with neither the user nor support able to see which layer failed. Affected users have reinstalled viewers, replaced antivirus software, and wiped machines before discovering the fault was elsewhere.

### 4.1 Per-connection state vector

The mixer maintains, per connection: ICE state, DTLS state, inbound RTP rate, decode success, active mix memberships, outbound RTP rate, and last client acknowledgment. Exposed three ways:

- **Console:** `voice status <avatar>` on the region/grid console.
- **Admin surface:** per-region and per-mixer web view.
- **Client:** a `diag` member pushed on the SLData channel, so a viewer can render "connected; receiving 0 pkt/s from server" vs. "receiving 340 pkt/s; output device delivered 0 frames" — splitting server-fault from device-fault in one glance.

### 4.2 Built-in echo test

Every region offers a self-serve echo test: on request (SLData message or viewer menu), the mixer loops the user's own audio back with ~500 ms delay. A working round trip proves the entire server path, isolating remaining faults to the local device chain. This replaces the single grid-wide test region model with a capability available everywhere at negligible cost.

### 4.3 Fail loud

If the mixer cannot deliver audio a listener should be receiving, it says so on the data channel and the viewer can surface it. The system must never render the appearance of working voice over a dead media path.

### 4.4 Session event log

Structured per-session event log (join, ICE transitions, first media, stalls, teardown cause) retained short-term for postmortems.

### 4.5 Fleet observability (activates at scale)

Per-mixer health, session counts, mix-deadline overrun rates, relay-vs-direct path ratios. The public record includes a load-growth cascade **[REC]** in which overwhelmed voice components blocked new sessions and destabilized existing ones as rerouting rippled across regions; the metrics here exist to see that coming. On a single-mixer grid this is one status page; nothing else changes.

---

## 5. Tier 2 — Media plane quality

The most common quality complaint in the record **[REC]** is "muddy / talking through a radio," which is a processing-chain failure, not a codec limit.

- Internal pipeline at 48 kHz float end-to-end; no resampling after ingest.
- **No server-side noise suppression or AGC, ever.** The client's WebRTC stack already applies NS/AGC/AEC; double-processing is the radio-voice artifact.
- Talker ingest ≥ 32 kbps Opus; listener mix 64–96 kbps stereo Opus, 20 ms frames.
- DTX/VAD-gated decode: silent talkers cost nothing before the RTP layer. A 100–200 ms release hold prevents word-onset chopping from DTX hangover.
- **Encode-skip:** listeners whose entire audible set is silent receive DTX and cost near-zero encode. Encode cost therefore scales with *audible* listeners, not connected listeners — the single largest scaling lever in the design.
- **Degradation ladder** under CPU pressure, in order: frame size up (20→30 ms) → distance tiers widened → far-field talkers shed from mixes → admission refusal (last resort). Per-region tick isolation so one saturated region cannot starve its neighbors.

---

## 6. Tier 3 — Spatial engine

Design conclusions from earlier analysis **[ENG]**: no global spatial index (shared pre-mix structures don't amortize when every listener is a separate viewpoint with per-listener exceptions); culling and per-listener rendering with load-adaptive aggregation instead.

- Cull first: distance, VAD, inbound level. Typical active-talker counts after culling are single digits even in busy regions.
- **Direct per-source HRTF below a talker-count threshold.** Azimuth binning engages only above it (binning only pays when talkers exceed bin count), with crossfades over several 10 ms frames at bin crossings, pre-staged from position derivatives. At low occupancy — where most regions live — the binning path and its artifacts simply don't run.
- **Distance tiers:** full HRTF with ITD near; amplitude panning + lowpass mid; single mono ambience sum far. Perceptually honest (nobody localizes a voice at 40 m) and cheap at every occupancy level.
- Dirty-flagged coefficient recompute: stationary listener + stable talker set skips HRIR selection/interpolation setup per frame.
- Listener orientation honored from the SLData `lh` quaternion — head-tracked spatialization is where this exceeds Vivox rather than merely replacing it.
- Per-(listener, source) crossfade state kept in flat preallocated arrays sized at session setup; no per-transition allocation.

---

## 7. Tier 4 — Features

### 7.1 Camera-position listening **[REC]**

Restores the Vivox-era "hear from camera" behavior whose loss is an explicit complaint in the record, without reopening the eavesdropping hole:

- Attenuation and HRTF may follow client-reported listener position within an **estate-configurable leash radius** of the avatar.
- Parcel/estate *audibility* is always computed from the avatar's authoritative position (§3.3). Camming never grants audio access the avatar's position wouldn't.

### 7.2 Performer mode **[REC]**

Per-avatar, estate-grantable flag: high-bitrate stereo ingest; client signaled to disable NS/AGC for that stream; exempt from far-tier degradation. Serves live music, which WebRTC's default processing chain audibly damages — a documented pain point that motivated SL's own user-controllable noise-reduction setting.

### 7.3 Parcel and estate voice zones **[OPS]**

Per-listener visibility matrix over a shared estate/region mix: avatars can be hidden from each other based on parcel access, enforced in the mix (§3.4). Because participant lists, voice dots, and power levels are driven by the same SLData visibility set, a hidden avatar is absent from both audio and UI consistently — computed from one source of truth so a dot never lights with no audio behind it. Note this is deliberately *stronger* than the SL model, which implements parcel privacy as connection topology; enforcing it in the mix removes SL's parcel-boundary reconnection stutter **[REC]** as a side effect.

### 7.4 Voice morphing

Per-talker pitch/formant shifting applied at decode, before spatial encode — so cost scales with morphed active talkers (typically 0–3), not listeners.

- **CPU:** ~1–5 % of a core per morphed stream at 48 kHz. Negligible at any scale.
- **Latency:** the real runtime cost — analysis-window algorithms add 20–60 ms to morphed talkers only. Acceptable on a ~100–150 ms voice path.
- **Quality:** cheap shifters sound metallic on exactly this signal (solo close-mic speech). Budget tuning time.
- **Licensing:** for a redistributable service this is the decisive cost. SoundTouch is LGPL (dynamic-link only), Rubber Band is GPL/commercial. Recommendation: implement PSOLA in-house (~few hundred lines, well-documented algorithm) to keep the distribution unencumbered.
- **Moderation interaction:** morphing enables voice-based ban evasion and deception; estates get a `no_morphing` flag, and morph state is visible to moderators.

### 7.5 Connector layer **[OPS]**

A tap-and-inject API on the mixer (plain RTP or WebSocket), generalizing the requested post-processing hooks:

- **Taps:** recording (consent-gated per §3.5), transcription, future translation.
- **Injectors:** NPC/bot TTS voices as first-class positioned sources; external DJ/stream sources entering spatial voice as positioned audio rather than parcel media URLs.
- **Effects sends:** parcel-property environmental processing (reverb, echo zones) — post-processing as *content*, configurable by land settings.

### 7.6 Moderation surface **[OPS]**

Estate-level mute/gain enforced in the mix; "podium" mode granting designated speakers gain priority; per-parcel voice zones (§7.3). All mixer-enforced and therefore not bypassable by modified viewers — a property the previous voice provider never had.

---

## 8. Deployment and scaling model

**Requirement: most deployments will be small. The small case is the default case.** A single-region standalone or small grid runs one mixer process alongside the simulator or grid services, configured by one INI section, with no allocator, no fleet, and no external dependencies beyond the mixer itself. Everything in §§3–7 works identically in this mode. Scale features activate by configuration, not by code path.

### 8.1 Small (1 region – ~20 regions)

One mixer process serves spatial + group + P2P. Diagnostics are the console command and one status page. STUN only; TURN optional. This must install with: build, one INI section, done — the existing ecosystem's Docker/cert/TURN assembly burden is itself a documented adoption barrier **[ENG]**, and "voice works out of the box and tells you why when it doesn't" is a distribution feature.

### 8.2 Medium (grid, tens–hundreds of regions)

Spatial mixers scale horizontally by region — spatial voice is embarrassingly parallel by region, which is the design's structural mercy. The group/adhoc pool scales independently (different load profile). A simple placement map (region → mixer) replaces the single process; adjacent regions preferentially co-located on one mixer so border-dwellers and cross-region listening (multiple neighbor sessions summed client-side per the protocol) stay cheap.

### 8.3 Large (InWorldz/SL scale)

- **Placement service:** bin-packing regions onto mixers by predicted load; sessions carry enough state to migrate.
- **Admission control with backpressure:** saturated mixers shed *new* sessions to other capacity; established sessions are protected. (The load-cascade outage in the record **[REC]** is the failure mode this prevents.)
- **TURN fleet:** at consumer scale a meaningful fraction of users ride TURN relays; this is real bandwidth and its own capacity plan. The diag vector (§4.1) distinguishes relay-path from direct-path sessions because a chunk of "connected but silent" lives there.
- **Encode economics:** encode CPU is the dominant cost line at scale. The mitigation to wire in early (brutal to retrofit): optional **first-order ambisonic delivery** — orientation-independent B-format mixes, rotated and binauralized viewer-side, SDP-negotiated with fallback to server-side binaural stereo for stock viewers. B-format mixes dedupe across co-located listeners, collapsing encode counts precisely in the crowded venues that cost the most.
- **Abuse controls:** rate-limited provisioning, per-account session caps, RTP source validation.

---

## 9. Protocol compatibility summary

- Caps: `ProvisionVoiceAccountRequest` (JSEP offer/answer, `channel_type` local/multiagent, `parcel_local_id`, logout), `VoiceSignalingRequest` (trickled ICE, completion marker).
- SDP: fmtp mangle honored (`minptime=10;useinbandfec=1;stereo=1;sprop-stereo=1;maxplaybackrate=48000`).
- SLData channel: client→mixer `j/l/sp/sh/lp/lh/m/ug` per the published format; mixer→client per-peer `p/V/j/l` batched ~100 ms. **Extensions (all optional, ignored by stock viewers):** `diag` (§4.1), echo-test control (§4.2), morph state (§7.4), trust-domain disclosure (§3.2).
- Cross-region: neighbor connections with primary flag, client-side summing, per the published model. Parcel changes within a region do **not** trigger connection changes (§7.3).

## 10. Open questions

1. Hypergrid group/P2P policy: which grid's pool hosts a call between users of two federated grids, and what does each party's client disclose?
2. FOA viewer-side decode: target viewer(s) and negotiation details; who carries the viewer patch.
3. Recording/consent defaults per jurisdiction for the connector layer.
4. Session migration mechanics for live mixer drain at scale (needed for §8.3; over-engineering for §8.1 — gate behind the placement service).
