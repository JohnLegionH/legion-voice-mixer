# Design Brief — Phase 3b: Spatial DSP

> **THIS DOCUMENT DESCRIBES A DESIGN THAT HAS SINCE SHIPPED. DO NOT READ IT AS CURRENT
> STATE.**
>
> Phase 3b is **implemented** as of `janus.plugin.slvoice` **v0.9.0**
> (`src/janus_slvoice.c:73`). Distance cull with hysteresis, distance attenuation and
> constant-power azimuth panning all run in `janus_slvoice_room_tick`, driven by
> viewer-supplied SLData geometry with the §7.1 camera leash applied in
> `janus_slvoice_snapshot_geometry_locked`.
>
> The body below is the **pre-implementation** design, verified against
> `legion-voice-mixer @ c03de3a` at the freeze point. Its status table, capability claims,
> constants and line citations describe the code as it stood then, and several are now
> false — notably "Done, **unused**" and "Zero spatialization runs today" under *What
> already exists*; `SLV_MAX_MIX` = 64 at `:231` (it is 110, `janus_slvoice.c:140`); and
> "the repo has **no** vector or quaternion math helpers" at `:236` (`src/mixer/vec3.h`,
> `azimuth.h` and `pan.h` all exist).
>
> Note also that **AMENDMENT 3 is superseded by AMENDMENT 8** — a reader who stops at
> AMENDMENT 3 will conclude the spatial constants are compile-time `#define`s, when they
> are process-wide jcfg keys read at runtime.
>
> The body and its amendments are **frozen and stay frozen**; per this file's own rule,
> corrections are appended as dated amendments and the body is not rewritten. This header
> records staleness only. For current behaviour, read the source.

**Status:** FROZEN. Amendments are appended as dated sections; the body is not
rewritten.
**Frozen:** 2026-08-18.
**Authority:** `docs/voice/webrtc-voice-spec.md` §5 (`:97`), §6 (`:110`), §7.1
(`:125`); `docs/voice/mixer-feed-protocol.md` §6 (`:420`–`:436`).
**Verified against:** `legion-voice-mixer @ c03de3a`, `tranquillity-develop @
f8fcb34653`, `D:\phoenix-firestorm` (read-only reference).

## Purpose

Make voice spatial. This is the original complaint that motivated building a mixer
rather than using AudioBridge, and it is the headline feature. Phase 3a closed the
parcel/estate semantics slice; nothing audible has changed yet.

## What already exists

| Capability | State | Where |
|---|---|---|
| Per-listener mix loop | Done | `janus_slvoice.c:1941`, `:1988`–`:2020` |
| Pipeline order: exclusion → mute → gain → **spatial** | Done to gain; spatial slot empty | `janus_slvoice.c:2005`–`:2008` |
| `sp`/`sh`/`lp`/`lh` parsed, stored per participant | Done, **unused** | `sldata.c:134`–`:147`, `janus_slvoice.c:336` |
| VAD + inbound-level cull, 150 ms release hold | Done, matches §5 | `janus_slvoice.c:115`, `:119`, `:290` |
| Encode-skip on silent mixes | Done | `mix.h:60`–`:62` |
| Tick histogram | Done | `janus_slvoice.c:229`, `:2038` |

Zero spatialization runs today (`janus_slvoice.c:71`).

## Resolved: the geometry unit

**All four geometry fields arrive as scaled integers, ×100.** Authoritative source is
the viewer: `LLWebRTCVoiceClient::sendPositionUpdate`
(`llvoicewebrtc.cpp:1231`), construction at `:1239`–`:1266`. Every component is
`(int)(value * 100)` — truncated toward zero, not rounded, with `100` inlined at each
site rather than a named constant.

`sp`/`lp` are **centimetres**; `sh`/`lh` are quaternion components ×100.

The mixer's parser applies no scaling (`sldata.c:53`–`:72`, `:135`), which is correct
— it stores what arrives. `sp = 1234.0` means 12.34 m. `mixer-feed-protocol.md:426`
and `sldata-extensions.md:194` are **accurate**; the imprecise text is
`sldata-extensions.md:28`–`:30`, whose field table omits the unit.

**Angular resolution is coarse:** `sh`/`lh` arrive at ~0.01 resolution, truncated.
Adequate for amplitude panning; revisit before anything leans on `lh` for HRTF.

## Position authority

Audibility is sim-authoritative (§3.3) and 3a delivered it. Spatial *rendering* is a
separate question the spec leaves open.

All geometry is viewer-supplied over SLData today. `VoiceStateFeeder` carries
exclusion sets only (`VisibilityBatchSender.cs:252`–`:256`);
`AgentView.AbsolutePosition` is used for parcel resolution and never emitted
(`VisibilityMatrix.cs:71`–`:76`). `mixer-feed-protocol.md:420`–`:436` states that
adding a per-source `pos:[x,y,z]` is an **additive field, not a protocol v2**, and
names child-agent positions as the one identified gap.

**Security bound.** Because audibility is already sim-authoritative, spoofed geometry
cannot make an avatar audible where they were not permitted. It can only alter
rendering *within an already-permitted set*. This holds structurally: exclusion is
applied as a hard drop (`janus_slvoice.c:2009`–`:2010`) and excluded sources never
reach the sum (`mix.h:22`–`:28`), so no spatial gain can attenuate-but-not-silence
them. **3b must keep exclusion a hard drop and never fold it into the spatial gain
vector.**

**Decision:** ship on viewer-supplied geometry. The sim position feed is a later
additive change, driven by whichever bites first — child agents without `sp`, or
estate-level leash configuration.

## Scope

**First slice:**

1. Geometry snapshot pass, tick-owned, including the per-participant `lp` self-clamp
   (§7.1 leash)
2. Distance culling with hysteresis — completes §6's "cull first: distance, VAD,
   inbound level"
3. Distance attenuation
4. Amplitude panning by azimuth — §6's "mid" tier

**Deferred, each because §6 itself conditions it on occupancy or on HRTF existing:**

- **HRTF and ITD** (§6 near tier) — needs an HRIR set, convolution, allocation
  discipline. Its own phase.
- **Azimuth binning and crossfades** — §6 states binning only pays above a
  talker-count threshold and that low-occupancy regions never run that path. Legion
  is low-occupancy; building it now adds artifacts and buys nothing.
- **Far-tier mono ambience sum** — a load optimisation, premature before a crowded
  region exists.
- **Dirty-flagged coefficient recompute** — optimises HRIR selection, which does not
  exist yet.
- **Sim-authoritative position feed** — see Position authority.

## Constraints

- **Pipeline order is frozen:** exclusion → mute → gain → spatial. Spatial gain
  composes with, never replaces, the per-listener viewer gain (`janus_slvoice.c:2001`).
  Exclusion stays a hard drop.
- **Geometry must be snapshotted into tick-owned storage, not read live.**
  `last_data` is written on the data-channel thread under that session's own mutex
  (`janus_slvoice.c:2188`–`:2192`); the mix tick holds only the *listener's* mutex
  (`:1986`) when reading other participants. Reading `sess[j]->last_data.sp` from the
  tick is a torn read. Mirror the existing `decbuf` discipline — tick-owned, written
  in the decode pass, read in the render pass, no lock (`janus_slvoice.c:286`–`:289`).
- **Allocation-free after session setup** (§6). Per-(listener, source) state — including
  the cull hysteresis flag — in flat preallocated arrays sized at setup. This state
  belongs in the existing per-listener inner loop (`:1988`–`:2011`) alongside the
  mute and gain build; the slice adds computation there, not a new loop.
- **20 ms tick budget.** A spatial path that pushes `ge20ms` or `overruns` off zero is
  a failure regardless of how it sounds.
- **No server-side NS or AGC, ever** (§5). **48 kHz float end-to-end, no resampling
  after ingest** (§5).
- Mixer changes deploy by container rebuild; **tags trigger release CI**.

## Rendering pairing

For listener **L** rendering source **S**:

- **Distance** = `|S.sp − L.lp|`
- **Azimuth** = angle of `(S.sp − L.lp)` rotated into L's `lh` frame, horizontal
  projection

**The origin is `L.lp` for both**; `lh` supplies orientation only. Taking the azimuth
origin from L's avatar `sp` while distance comes from `lp` makes camming incoherent —
the source would pan from the avatar but attenuate from the camera. A single origin
keeps §7.1 camming behaviour consistent.

`S.sh` is unused in this slice; basic azimuth panning does not need the talker's
facing.

## Attenuation model and configuration

**Model.** Distance-based falloff reaching approximately zero at the cutoff, with
hysteresis on the distance cull.

Pure inverse-distance with a hard cutoff is **rejected**: gain at cutoff would be
10/60 ≈ 0.17, roughly −15 dB, slammed to zero — an audible click each time a talker
crosses 60 m, and decode-path flapping if distance oscillates about the boundary. §6
mandates crossfades at azimuth-bin boundaries; the same instinct applies in the
distance domain.

Resolve during implementation by either a rolloff-shaped curve decaying to ≈0 at the
cutoff, or inverse-distance with a short fade-to-silence over the final few metres.

**Cull hysteresis is required** either way: drop at the cutoff, re-add shorter, so a
talker hovering at the boundary does not chatter the active set. Because the curve is
≈0 at the cutoff, the hysteresis band is perceptually free — a source at 58–60 m is
either culled or rendered at ≈0 gain, inaudible either way.

**Configuration** — plugin jcfg, per-region, expressed in metres:

| Key | Default | Meaning |
|---|---|---|
| reference distance | 10 m | full volume within this radius |
| audible cutoff | 60 m | falloff reaches ≈0; distance-cull threshold |
| cull re-add distance | 58 m | hysteresis; must be < cutoff |
| camera leash | 50 m | `MAX_AUDIO_DIST`, `lp` self-clamp |

**Unit discipline — the most likely source of a 100× bug.** Config is metres; stored
geometry is centimetres. Convert **once, at config load**: leash 5000, cutoff 6000,
re-add 5800, reference 1000. No metre value reaches the DSP; no stored-unit value
appears in config.

**Cutoff and leash are deliberately different** and measure different things: the
leash is `L.lp` to `L.sp`; the cutoff is `L.lp` to `S.sp`. 50 ≠ 60 is not a smell.

## §7.1 camera leash

The viewer applies a 50 m tether client-side: `enforceTether()`
(`llvoicewebrtc.cpp:1203`–`:1220`, `MAX_AUDIO_DIST = 50.0f` at `:92`) projects the
requested camera position onto a 50 m sphere around the avatar before
`mListenerPosition` is serialised as `lp` (`:1254`–`:1256`).

**This bounds honest viewers, not the threat model.** The clamp is client-side and
therefore spoofable; SL's server re-tethers independently for exactly that reason
(`:1224`–`:1230`). A modified viewer can send unclamped `lp`, and a mixer
spatialising from raw `lp` would follow the camera arbitrarily far — still bounded by
3a, but able to render a permitted-but-distant talker from an arbitrary vantage.

**Included in the first slice**, not deferred: it is a per-participant self-clamp —
for each participant P, clamp `P.lp` to within `MAX_AUDIO_DIST` of `P.sp`, projection
lifted from `:1213`, in the ×100 unit (5000). It lives naturally in the geometry
snapshot pass, applied once per tick before any listener reads it, with no extra
locking. Deferring it would mean revisiting the same lines.

**Known deviation:** §7.1 specifies the leash as *estate*-configurable; this ships it
per-region as an interim simplification. Flagged for reconciliation when estate-level
config lands.

**Composite reach — expected, not a leash failure.** Leash (camera ≤ 50 m from
avatar) plus cutoff (source ≤ 60 m from camera) means a camming listener can render a
talker up to ~110 m from their own avatar. Within §7.1's intent and safe under the
security model: permission derives from the avatar's authoritative position via 3a,
so this affects only rendering of already-permitted talkers.

## Acceptance

Walking test, two avatars, same region: volume falls with distance and reaches
inaudible near the cutoff; voice pans left/right correctly as the listener turns; the
tick histogram shows no `ge20ms` and no `overruns` across the session.

**Plus a structural regression assertion, not a listening test:** an excluded source
must contribute *exactly zero* to the mix — RMS-0, not summed — verified structurally.
A bug that reintroduces an excluded source as a quiet signal rather than silence is
inaudible in casual testing and would silently undo 3a. Re-run the
`parcel-voice-semantics.md` §O procedure to confirm enforcement is unchanged.

## Open question — non-blocking

**Do neighbour-room handles carry `sp`?** `query_session` exposes
`last_data_fields_seen` per handle, so this is one read on a Transylvania handle with
avatars online. **The answer does not change this slice:** even if neighbour handles
carry `sp`, it is in the *neighbour region's* coordinate frame, so cross-region
spatialisation needs a region-offset transform regardless. This is a second
independent reason cross-region and child-agent spatial belongs with the sim-position
feed, and it confirms same-region-two-avatars as the right first cut — it sidesteps
both the frame transform and the child-position gap.

---

## AMENDMENT 1 — per-pair state must be UUID-keyed (2026-08-18)

Recon for slice one item 1 surfaced a constraint the body does not pin.

The Constraints section requires per-(listener, source) state in "flat preallocated arrays sized at setup." That wording permits, and a naive reading suggests, an array indexed by source position in the tick's `sess[]` array. **That would be wrong.** `sess[]` is populated from `room->participants` hash iteration (`janus_slvoice.c:1946`-`:1960`), whose order changes across ticks as participants join and leave, so a position-indexed slot refers to a different pair each tick.

**Per-(listener, source) state must be keyed by source UUID**, as `peer_ctl` is (`janus_slvoice.c:309`, keyed at `:252`, scanned at `:1996`-`:2004`).

It also cannot reuse `peer_ctl`: that array is `SLV_MAX_PEER_ADJ` = 32 (`sldata.h:39`) against a participant cap of `SLV_MAX_MIX` = 64 (`janus_slvoice.c:125`), and is sparse by design — only viewer-adjusted pairs exist. Cull hysteresis needs a slot for every audible pair. It is therefore a parallel per-listener UUID-keyed array sized to `SLV_MAX_MIX`.

**Also recorded from the same recon:**

- The geometry snapshot is inline struct fields on the session, not a heap buffer. This is stronger than the `decbuf` pattern the body names — zero allocation ever, rather than allocation at join — and adds nothing to `media_alloc_locked`.
- The repo has **no** vector or quaternion math helpers (`sldata.h:60`, `:63` define the types as plain aggregates; no `vec3_`/`quat_`/dot/cross/normalize exists anywhere). Slice one writes the first ones: subtract, magnitude, and scaled-add for the leash projection.

---

## AMENDMENT 2 — per-pair state requires LRU eviction (2026-08-18)

Recon for slice one item 2 surfaced a gap Amendment 1 did not address.

Amendment 1 pinned per-(listener, source) state as a flat, UUID-keyed array sized `SLV_MAX_MIX` (64). It did not specify behaviour when the number of *distinct* UUIDs seen over a session exceeds the slot count, which join/leave/rejoin churn guarantees.

**`peer_ctl`'s approach is wrong here.** That array drops new entries when full (`janus_slvoice.c:2155`-`:2156`), which is tolerable for an adjustment cap — a 33rd explicit mute simply falls back to default gain. For hysteresis it is not: a present source denied a slot has no persistent state, re-decides its cull from scratch every tick, and chatters at the boundary — defeating the purpose of the hysteresis entirely.

**Per-pair state must therefore evict**, by least-recently-seen tick stamp.

**Eviction is provably safe.** The room is hard-capped at `SLV_MAX_MIX` = 64 concurrent participants, so at most 63 live *other* sources exist for any listener — strictly fewer than the 64 slots. Any slot displaced on a full append is therefore necessarily a departed UUID. No cross-check against the live participant set is needed; a stale stamp is sufficient evidence.

**Also recorded from the same recon:**

- **Scan cost is accepted as specified.** Worst case at 64 participants is 64 listeners × 64 sources × up to 64 UUID compares = ~262,000 strcmp per tick, roughly doubling the existing `peer_ctl` load. At the occupancy §6 describes as typical — single-digit active talkers — it is ~512 per tick. UUIDs diverge in their first bytes, so compares are cheap. The flat linear scan stands; a hash table would be a deviation from Amendment 1 and is not warranted. The tick histogram catches an overrun if reality disagrees.
- **The cull writes `mutes[j]` only.** `s->active` (`janus_slvoice.c:301`, set at `:1842`-`:1846`) and `audible[]` (`:2012`) are global and distance-agnostic; a per-listener cull must never touch them, the decode path, or the power/VAD reporting. Consequence: sources are decoded in pass 1 regardless of distance, so a per-listener cull saves no decode CPU. Shedding far-field talkers from decode is §5's separate global degradation lever, not part of this.
- **First tick a pair is seen:** plain threshold at the cutoff, no hysteresis band. Hysteresis governs transitions, and there is no prior state to hold.

---

## AMENDMENT 3 — the config surface does not exist as the body assumes (2026-08-18)

Recon for slice one item 3 surfaced a conflict between the body and the mixer's architecture.

The body's attenuation section specifies configuration as "plugin jcfg, per-region, expressed in metres." **There is no per-region config channel in the mixer.** `janus_slvoice_init` reads a single process-wide `janus.plugin.slvoice.jcfg`, extracts exactly two things — static rooms and `general.echo_autostart` — and then frees the config without retaining a settings struct. Rooms are created dynamically, so nothing associates configuration with a region.

Adding `general.*` items would therefore ship **process-wide** configuration while the body claims per-region, which is worse than shipping neither.

**Decision:** spatial constants remain compile-time `#define`s through slice one, matching the leash, cutoff, and re-add constants already there. The per-region config surface is deferred to a dedicated item, which must first reconcile per-region intent against a one-jcfg, dynamically-created-room mixer. The body's "per-region" wording stands as intent, not as a description of what slice one ships.

**Also recorded from the same recon:**

- **The attenuation curve is settled:** a single monotonic falloff, `gain *= (t)^p` where `t = (cutoff − d) / (cutoff − reference)`, with reference distance 10 m (1000 stored) and shaping exponent `p` starting at 2.0. Equals 1 at the reference, exactly 0 at the cutoff, no fade window and no seam.
- **Attenuation composes into `gains[j]`, never replaces it.** The array defaults to 1.0 and carries the viewer gain (`ug/220`, clamped to 4.0) when set. The final mix is clamped by `slv_mix_clamp`, so the product cannot overshoot.
- **Cost is accepted.** The distance is reused from item 2's cull, so attenuation adds only one `pow` per pair in the falloff band plus the per-sample multiply — worst case ~4096 `pow` per tick, roughly 0.8% of the 20 ms budget. Inside the reference and beyond the cutoff there is no `pow` at all.
- **Items 2 and 3 reinforce each other as predicted:** because the curve reaches ~0 at the cutoff, a source inside the hysteresis band is either culled or rendered at near-zero gain — inaudible either way, so the cull boundary has no audible seam.

---

## AMENDMENT 4 — panning conventions, pinned (2026-08-18)

Recon for slice one item 4 found that none of the conventions panning depends on are established anywhere in the repo. They are pinned here so the implementation cannot settle them by accident.

**Stereo pan sign — DECIDED:** a source to the listener's **right** is louder in the **right** output channel. Nothing in the repo, the viewer, or the docs fixed this; it is chosen here explicitly.

**Frame:** `sp` and `lp` are region-local (`sldata.h:59`), ×100. Axis semantics are SL/OpenSim convention, external to this repo: X east, Y north, Z up, right-handed. Azimuth uses `S.sp − L.lp`, so the origin is immaterial and only axis directions matter.

**Orientation:** `lh` is the listener/camera orientation, set from `setListenerPosition`'s rot (`llvoicewebrtc.cpp:1156`, `:1168`); `sh` is the avatar's (`:1173`, `:1193`). `slv_quat{x,y,z,w}` maps to `LLQuaternion (x,y,z,w)`, confirmed by the viewer's serialization order (`:1260`-`:1263`). Forward is local +X rotated by the orientation — at identity, forward is +X (east). Positive rotation about +Z is counter-clockwise viewed from above. These are external SL conventions, not repo-established.

**`lh` IS NOT A UNIT QUATERNION.** It arrives ×100 and int-truncated (`llvoicewebrtc.cpp:1260`-`:1263`), so a 90° rotation about Z arrives as `{0,0,70,70}`, magnitude ≈ 99. **The azimuth function must normalize `lh` before rotating.** Failing to do so does not crash — it produces a plausible-looking but wrong azimuth, which is the hardest class of bug to notice. A unit test using the wire-realistic truncated form is mandatory.

**Frame layout:** interleaved float PCM is firmly established (`mix.h:12`-`:14`, `janus_slvoice.c:100`, `:103`). Even index = channel 0, odd = channel 1. That index 0 is "left" is convention, not documented — panning is the first code to distinguish the channels.

**Verification is numeric, not aural.** Listening tests are not available for this work. The azimuth unit tests are the acceptance criteria for item 4, not a supplement to them. Per-channel output level is to be exposed as a diagnostic alongside `last_mix_rms`, which sums both channels and therefore cannot detect a left/right swap.

---

## AMENDMENT 5 — pan law and the stereo mix path (2026-08-18)

Recon for the item 4 wiring settled two decisions the body did not anticipate.

**The mix core cannot pan as written.** `slv_mix_nminus1` takes one scalar gain per source (`mix.c:33`) applied uniformly to every sample (`:40`-`:42`), so it cannot produce different left and right levels. Pre-scaling a source's `decbuf` is not an option — that buffer is tick-owned and shared read-only across every listener's mix (`janus_slvoice.c:324`-`:325`), so scaling it for one listener corrupts the others. Panning the summed frame is not an option either: by then the per-source direction is gone.

**Decision:** add a per-channel stereo core and make the existing `slv_mix_nminus1` a thin wrapper that calls it with equal left and right gains. This keeps the 26 existing `test_mix` checks green and makes the equivalence self-evident, rather than changing the signature and updating 8 test call sites.

**Pan law — `sin(azimuth)` as the pan signal.** Constant power, `p = (sin(az) + 1) / 2`, `gainL = cos(p·π/2)`, `gainR = sin(p·π/2)`, so `gainL² + gainR² = 1`.

Mapping azimuth linearly to pan position would be **discontinuous at ±π**: a source drifting across directly-behind would snap between pan extremes, and `+π` and `−π` — the same direction — would map to opposite sides. `sin(az)` is continuous everywhere and needs no clamping.

**A source directly behind renders centred, and this is correct.** `sin(±π) = 0`, so behind maps to centre — present at full level, not lateralised. Amplitude panning fundamentally cannot express front versus back; that is what the deferred HRTF and ITD are for. This is the honest limit of the technique, not a defect, and should not be "fixed" by anyone later reading it as one.

**No geometry, no pan.** When either snapshot is invalid the pan is skipped entirely and both channel gains equal the scalar gain — the same guard the cull and attenuation already use.

**Per-channel diagnostic.** `slv_mix_rms` operates on contiguous samples (`mix.h:55`-`:58`) and cannot address a strided channel, so left and right RMS are computed inline in the tick rather than by extending the unit-tested core. `last_mix_rms` sums both channels and cannot detect a left/right swap; `last_mix_rms_l` and `last_mix_rms_r` are the swap detector Amendment 4 requires.

---

## AMENDMENT 6 — slice one complete; the constant-power level change (2026-08-18)

Slice one is built, deployed, and verified: geometry snapshot with the §7.1 camera leash, distance culling with hysteresis, distance attenuation, and constant-power amplitude panning.

**Verification was numeric throughout, per Amendment 4.** Attenuation was confirmed by `last_mix_rms` falling from 0.19 to 0.027 as the listener moved to roughly 40 m from a fixed talker, against a predicted gain of 0.16. Panning was confirmed by rotating a stationary listener through a full circle beside a fixed talker and observing `last_mix_rms_l` / `last_mix_rms_r` sweep from a ratio of ~200:1 (hard left) through parity to ~1:9000 (hard right) and back. No listening test was used at any point.

**A centred source is approximately 3 dB quieter than before slice one.** Previously both channels received the full scalar gain, so a source contributed `2g²` of power. Under constant-power panning a centred source contributes `2(0.707g)² = g²`. This is inherent to any constant-power pan law — the previous behaviour was double-mono, which is 3 dB hot relative to a correct pan — and must **not** be "corrected" by scaling the pan gains, which would break the `L² + R² = 1` property `tests/test_pan.c` asserts. If overall level needs raising, that is a separate output-gain decision, not a pan-law change.

**Mutation testing found a decorative test.** `test_pitch_independent` as originally written could not fail: its source was level and on the pitch axis, so a dropped horizontal projection left it passing. Review by hand had not caught this. Where unit tests are the acceptance criteria, verifying that they can fail is part of writing them.

---

## AMENDMENT 7 — the exclusion feed names avatars, not handles (2026-08-18)

Recon for the §M collision fix established the boundary that decides its design.

**Agent UUID is the only identifier either feed carries.** The visibility batch is keyed purely by agent UUID (`visbatch.h:16`-`:17`, `:58`-`:60`), and the viewer's peer_ctl feed likewise (`sldata.h:74`-`:80`). Neither carries a handle, session, or connection id.

**Therefore an exclusion entry means "this avatar," never "this handle."** Applying an entry to every session matching the display is not a workaround for missing information — it is the faithful reading of what the sim sent. Resolving to a single session, as the current single-value `by_display` map does, is the incorrect behaviour, and which session survives depends on undefined GLib iteration order.

**Lifetime constraint:** each session owns its own `excluded` table, destroyed at teardown (`janus_slvoice.c:570`) and destroyed-and-replaced wholesale in the REPLACE branch (`:1078`-`:1079`). A fan-out fix must replicate the merge into each owner's set. Sharing one table pointer across sessions would double-free.

**The root cause is separate.** The duplicate arises because the join path performs no display-uniqueness check (`janus_slvoice.c:1406`, `:1434`, `:1449`-`:1451`) and the plugin assigns a fresh `user_id` per join, so an orphaned handle coexists with its replacement. Evicting a stale same-display handle at join would collapse every display-keyed hazard at once, including the independent dot-batch collapse at `:1671`. That is a more invasive change — it tears down a live session on a heuristic — and is deferred to its own recon rather than folded in here.

The exclusion fan-out treats the symptom correctly. The join-time uniqueness gate treats the disease.

---

## AMENDMENT 8 — the config surface exists after all; process-wide jcfg decided (2026-08-21)

Recon for the spatial config item revisited Amendment 3 and found it overstated the obstacle. This corrects it and records the decision. **It supersedes Amendment 3's decision** that the spatial constants "remain compile-time `#define`s… deferred."

**Amendment 3 was wrong that "nothing associates configuration with a region."** The room struct already retains per-room settings — `room_id`, `description`, `is_private`, `sampling_rate`, `spatial_audio`, `permanent` (`room_create`, `janus_slvoice.c:474`-`:479`) — and both the static jcfg loader (`:642`-`:654`) and the dynamic create request (`:1402`-`:1407`) populate it. The sim controls room identity: it derives the room number and sends `create` with it (`:1400`-`:1401`, and the "every region that hashes to the same room number" contract at `:1412`-`:1414`). **So a per-region config channel does exist.** What is missing is only that the five DSP distances are not among the retained fields — a much smaller gap than "the config surface does not exist."

**Decision: the spatial constants become process-wide jcfg keys retained in a settings struct**, following the pattern `echo_autostart` already uses (`:260`, parsed at `:698`-`:703`). They are parsed in `janus_slvoice_init` **before** `janus_config_destroy` (`:716`), converted metres→stored units **exactly once at parse**, with the current `#define` values as defaults so an absent key leaves behaviour unchanged. `SLV_FALLOFF_EXP` is dimensionless and takes no conversion.

**Per-region is deferred, not rejected.** It is real machinery — room-struct fields plus create-request keys on **both** the mixer and the C# sim — justified only when regions genuinely need *different* distances. Nothing in the current deployment shows that need. Because the room struct and the create channel already exist (above), per-region can be layered on later as global defaults with an optional per-room override, converting once in `room_create`, **without redoing the process-wide work**.

**`peer_ctl_batch` is the wrong carrier, structurally.** It is a delta feed applied repeatedly per batch (`apply_visbatch`, `:1206`), so configuration riding it would be re-converted on every application — breaking the convert-once discipline by construction, not by carelessness. `mixer-feed-protocol.md`'s additive-field claim (§6, `:420`-`:440`) is about optional **per-source** fields flowing at movement cadence, not room-level config; it does not license config on this channel.
