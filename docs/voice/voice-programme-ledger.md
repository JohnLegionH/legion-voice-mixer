# Ledger — WebRTC Voice Programme

**Artifact type:** Ledger — **LIVING**. Never frozen. Amend in place; date every change.
**Last reconciled:** 2026-08-31 (evening), against `tranquillity-develop` at *fix(voice): O-41 —
logout provision now disconnects the viewer session* (`c972136380`, branch
`feature/voice-visibility-matrix`) and `legion-voice-mixer` at `5e0d637` (M-A2A-3 attach-window
backlog re-send, branch `main`) [SRC: `git log`; the 19:01 deploy verification].
*(The 2026-08-27 evening reconciliation narrative below is retained as history.)*
This evening five commits shipped, deployed, and were **verified live in-world and via the admin API**:
sim `02ce1b9b10` (moderation mute channel) + `b80efffa36` (empty-column pending guard); mixer
`354e9fe` (join backlog) + `0d6d0d0` (mute parse / keep+grey) + `27977c8` (join-window deferral). The
mixer is **no longer at `872f0d9`** (this morning's basis). New/amended entries carry 2026-08-27 dates
in §3, §4.1 (O-8 resolved; O-39/O-40 added), §5.0. *(Prior basis, this afternoon: `935bd5b6d2`
console commands / `872f0d9` mixer.)*
*Previous basis 2026-08-26 at `3c95ddea0e`; six commits folded in below, five of them code —
S1b, S2, S3a, S3b and the moderation console commands — plus this ledger's own first commit.
Note the drift was six commits, not seven: `53e560fdc4` (this file) is one of them.*
*Amended 2026-08-30 against `tranquillity-develop` at *fix(voice): S-A2A-5 — bind viewer_session
lookups to the requesting agent* (`116dc8e3aa`, same branch) [SRC: `git log`]: the avatar-to-avatar
programme is **built and committed (S-A2A-1..5), NOT deployed** (§3); the S-A2A-3 item-0 finding —
every voice logout provision refused 403 since `d9fa72c351` — is recorded with its blast radius (§3,
O-41); §5.0 carries the deployed-vs-committed amendment, the next-deploy watch list and the deploy-
lineage note. The running build is unchanged since the 2026-08-28 amendment.*
*Amended 2026-08-30 (evening) against `tranquillity-develop` at *fix(voice): S-A2A-3.1 — non-spatial
service null when both roles name the same DLL; graceful no-session logout* (`d23e41c762`) [SRC:
`git log`; deploy verifications; live region log]: **three regionserver deploys today** landed the
counter fix, the logout fix, S-A2A-1..5 and the three live-found follow-up fixes (§5.0), and the
**FIRST LIVE A2A CALL COMPLETED end to end, audio both ways** (§3, 17:50). Four defects were found
live and fixed same-day (§3); the watch list is answered (§5.0); O-42/O-43/O-44 filed. Mixer
unchanged (`27977c8` / `a5a6c7b7189a`).*
*Amended 2026-08-31 against `tranquillity-develop` at `2ff8bf57af` (regionserver unchanged) [SRC:
live log 05:27; mixer `git log`; container verification]: **O-42 is CLOSED, all three parts** (§3) —
S-A2A-6 live, the caller's End Call restored, and the callee auto-hangs-up on the caller's logout —
via three mixer slices (`6ee39be`, `3f4780d`, `5e0d637`, image `1bf042e373dc` deployed, §5.0). The
**stock-viewer compatibility constraint** is recorded as standing (§4, above the O-list). S-A2A-7
recorded as RESERVE, not needed. O-28's mixer ledger copy is now due.*
*Amended 2026-08-31 (later) — docs only, no code basis change [SRC: blob-hash comparison,
`git rev-parse HEAD:<path>` both repos]: **O-28 is CLOSED** (§4.1) — the two `Docs/voice`
directories are now a full **13-file mirror**: six documents copied sim→mixer (this ledger among
them, new there), six mixer→sim (`webrtc-voice-spec.md` now present in this tree, §1;
`mixer-feed-protocol.md` brought forward to the mixer's `5e0d637` strict superset),
`phase3a-feeder-acceptance.md` already equal. §4.4's Repo column reads "both" for every
`Docs/voice` document; §4.3 (k) resynced; a CRLF testing gotcha is recorded at the end of §4.3.*
*Amended 2026-08-31 (evening) against `tranquillity-develop` at `c972136380` [SRC: live region log
19:03–19:07; deploy verification; test runs]: **O-41 and O-43 are CLOSED** (§4.1) — the
caps-wrapper exception logging (`b72afc9fb8`) and the logout-arm disconnect (`c972136380`) built,
tested and deployed 19:01 (§5.0, rollback `20260831-190101`; the deploy records the standing
**core-Release / voice-Debug convention**). O-41 verified live at 19:04:52 — both mixer handles
removed on a voice toggle-off; O-43 not yet observed firing (watch item stands). The A2A
regression check after O-41 PASSED (§3, 19:06–19:07, auto-hangup in 226 ms, behaviour unchanged).
`backlog_resent=1` observed on both spatial handles at the 19:03 join — M-A2A-3 confirmed on the
join path; the A2A during-call read remains owed (§3). O-45 filed (CreateRoom "inconclusive"
ERROR noise, §4.1).*
*Amended 2026-08-31 (night) against `tranquillity-develop` at `31ee058d04` plus this commit
[SRC: the assessment; brief Amendment 2; the build plan]: **O-40 is IN PLANNING** — the
ground-truth assessment (`connector-assessment-20260831.md`, committed `31ee058d04`), brief
**Amendment 2** (Q2–Q6 all DECIDED: policy-record authorisation, injection IN SCOPE with the
mute-channel default-deny, three-layer stock-viewer disclosure, the HG provisioning rule, the
aiortc runtime) and the **build plan** (`connector-build-plan.md`, S-CON-1..7, no mixer change
anywhere) are in the tree. **O-46 filed** (the mixer join is ungated and `display` is trusted —
pre-existing, not connector-specific; remedy = a join secret, a small mixer change) and **O-47
filed** (untrusted-HG voice provision refusal, owed to the trusted-HG rollout). §4.4 gains both
connector rows; the `Docs/voice` mirror is **15 files after this commit** (14 + the build plan;
the tasking said 16 — the verified count is 15), mixer copies due per the Maintenance rule.*
*Amended 2026-08-31 (night, later) against `tranquillity-develop` at `d95754509d` [SRC: deploy
verification 21:59; live region log 22:01–22:06]: **O-40 is IN PROGRESS** — S-CON-1..3 built,
deployed (§5.0 row 5, rollback `20260831-215949`) and live-verified with a Recorder record on
Ebony (§4.1: NPC + registration + mute-at-registration surviving to a late joiner; entry notice
on stock Firestorm). O-46 gains the api_secret nuance (§4.1); a per-region "loaded" log
duplication is recorded as cosmetic in §5.0, not filed. The `Docs/voice` mirror stays 15 files.*
*Amended 2026-09-01 against `legion-voice-mixer` at `1c37b4c` (regionserver unchanged at
`d95754509d`) [SRC: mixer `git log`; the two recordings; live in-world observation]: **O-40
S-CON-4 is DONE** — the recorder peer (mixer `6300edc`) plus the same-day fix `1c37b4c` for the
first recording's defect (the recorder wrote the libopus decoder's whole 120 ms plane per 20 ms
frame; §4.1). The second recording (2026-09-01 10:38) verified: 15.3 s, non-zero fraction
0.9997, the operator confirms it is his voice; the peer joined room 226001844 as the NPC
identity, ICE completed, webrtcup, Janus container untouched throughout. **S-CON-5 is
part-observed** — roster (a), entry notice (b) and playback (f) OBSERVED; the handle-walk (c)
owed; the exclusion test (d) DEFERRED; mute-enforced silence (e) DEFERRED to S-CON-6 (§4.1; the
build plan carries the dated notes). Two observations recorded as not-defects (§4.1). No new
O-items. Next slice: S-CON-6.*
**Scope:** the whole voice programme — the `os-webrtc-janus` addon in this tree, the
`janus.plugin.slvoice` mixer, and the documents about both. Adjacent parcel/estate enforcement
defects are listed only where a voice document depends on them.
**Convention note:** neither repository has a `ledger/`, `adr/` or `rfc/` directory or any written
artifact-type convention; the Discovery Note / Recon Report / Design Brief / Build Plan / Ledger /
ADR / RFC vocabulary exists only in conversation. This file lives in `Docs/voice/` because every
voice artifact does and because that directory is the cross-repo-synced one
(`Docs/voice/.gitattributes`: synced verbatim with `legion-voice-mixer:docs/voice/`, LF-pinned).
**Copied to the mixer repo 2026-08-31** under the same sync convention; keep in sync per the
Maintenance rule.

## How to read the evidence marks

Every status claim carries one of:

- **[SRC]** — verified against source, a commit in the log, a live log line, a live config
  value, or container metadata, read during this reconciliation. The citation follows.
- **[DOC]** — stated in a document. A document's own status header is a *claim*, not evidence;
  several were found stale this week and are listed in §4.3.
- **[INF]** — inferred by the reconciler from [SRC] facts, with the inference stated.
- **UNKNOWN** — could not be established. §6 says what would settle each.

Commits are cited by subject (this branch is rebased; SHAs go stale — `Docs/KnownDefects.md`
preamble). Where a short SHA is given it is paired with the subject.

---

## 1. The authority

**Path:** `legion-voice-mixer/docs/voice/webrtc-voice-spec.md`, 198 lines. **A copy is now
present in this tree** at `Docs/voice/webrtc-voice-spec.md` as of 2026-08-31 (198 lines,
verbatim — the O-28 close, §4.1). *(Until then there was no copy here; `Docs/voice/` held five
files, none of them the spec.)* Every phase
plan's `§3.1 / §7.2 / §7.4 / §10` references resolve to this file [SRC: the 3b brief's
`**Authority:**` line cites it by path; `sldata-extensions.md` and `protocol-compat.md` call it
"the vendored spec"].

**Freeze state: `Status: Draft for review`** [SRC: line 3]. It has **never been frozen**, carries
no date, no basis commit, and no amendment log. Its section map: §1 goals, §2 provenance, §3
Tier 0 trust/privacy (3.1 server-relayed, 3.2 trust domains, 3.3 sim-authoritative enforcement
with "version epochs" that "fail closed", 3.4 mixer output is the permission boundary, 3.5
capture consent), §4 Tier 1 diagnosability (4.1 per-connection state vector + `diag` SLData
member + `voice status <avatar>` console, 4.2 echo test, 4.3 fail loud, 4.4 session event log,
4.5 fleet observability), §5 Tier 2 media quality, §6 Tier 3 spatial engine, §7 Tier 4 features
(7.1 camera leash, 7.2 performer mode, 7.3 parcel/estate zones, 7.4 voice morphing/PSOLA, 7.5
connector layer, 7.6 moderation surface incl. estate mute/gain and podium), §8 deployment
tiers, §9 protocol compatibility, §10 four open questions. There is no `§10.1` heading — §10 is
a numbered list, so "§10.1" in conversation means its item 1 (hypergrid group/P2P policy).

**Does it still match the code?** Section by section, against `src/janus_slvoice.c` and the
addon at the basis commits:

| Spec | Code | Mark |
|---|---|---|
| §3.1 all media server-relayed | Mixer is an MCU; no client-to-client ICE path exists in either repo | [SRC] structural: only `relay_rtp` to the owning handle |
| §3.2 trust domains, HG-only-on-grid-servers, disclosure | Nothing. `grep -ci trust src/` = 0; no provisioning-time classification | [SRC] absent |
| §3.3 sim-authoritative enforcement | Yes for audibility: the 3a matrix is built from sim state only (`Visibility/FeederWorld.cs`, `VisibilityRules.cs`) | [SRC] |
| §3.3 "version epochs … fails closed on staleness" | **No.** The `peer_ctl_batch` wire carries `op`/`room`/`excl` only (`src/visbatch.c:76`–`:107`); the mixer keeps its last state indefinitely — fail-**open**. The `vis_epoch` counter (`janus_slvoice.c`) is a diagnostic tally, not a protocol epoch | [SRC] contradicts spec |
| §3.4 mixer output is the boundary | Yes: exclusion is a hard drop before the sum (`janus_slvoice.c`, pass 2 `mutes[j]=1` on `slv_roster_excludes`; `mix.c` skips muted) | [SRC] |
| §3.5 capture consent | No capture exists (§7.5 unbuilt), so nothing to gate | [SRC] absent |
| §4.1 per-connection state vector / `diag` / `voice status` | `query_session` exposes rtp in/out rates, decode, mix memberships, RMS, tick histogram, visibility counters [SRC]. **No** `diag` SLData member (`grep '"diag"'` = 0) and **no** `voice status` console command — the sim registers only `janus info`, `janus list rooms`, `show voice closing` [SRC: `AddCommand` sites] | partial |
| §4.2 echo test | Yes: `{"echo":true}` SLData toggle + `SLV_ECHO_AUTOSTART` [SRC: `SLV_FIELD_ECHO`, `echo_start_locked`] | [SRC] |
| §4.3 fail loud | No data-channel failure signalling exists | [SRC] absent |
| §4.4 session event log | None (`grep 'event log'` = 0) | [SRC] absent |
| §4.5 fleet observability | None beyond per-handle `query_session` | [SRC] absent |
| §5 48 kHz float, no NS/AGC, ≥32k ingest / 64–96k stereo mix, DTX/VAD 100–200 ms release, encode-skip | Yes: `SLV_RATE 48000`, `SLV_CHANNELS 2`, `SLV_OPUS_BITRATE 64000`, `SLV_VAD_RELEASE_MS 150`, encode skipped on silent mix | [SRC] |
| §5 degradation ladder | None (`grep degrad` = 0) | [SRC] absent |
| §6 cull first (distance/VAD/level) | Yes | [SRC] |
| §6 HRTF, ITD, distance tiers, azimuth binning, dirty-flag recompute | None — the plugin's own description says "no HRTF, ITD, distance tiers or azimuth binning yet" [SRC: `JANUS_SLVOICE_DESCRIPTION`]; what ships is cull + attenuation + constant-power azimuth pan | partial |
| §6 listener orientation from `lh` | Yes: `slv_azimuth(s->snap_lp, s->snap_lh, …)` | [SRC] |
| §7.1 camera leash | Yes, but **per-process jcfg**, not estate-configurable — recorded as a known deviation in the 3b brief | [SRC] partial |
| §7.2 performer mode | None (`grep performer src/` = 0; no commit) | [SRC] absent |
| §7.3 parcel/estate zones in the mix | Estate channel: yes. Per-parcel channels: **no** — the delivery gap (§3, item 3a-D) | [SRC] partial |
| §7.4 voice morphing / PSOLA | None (`grep morph src/` = 0; the four `morph` hits in this tree's history are unrelated BulletSim/HTTP commits) | [SRC] absent |
| §7.5 connector layer | None in code; a DRAFT brief exists (§3 item C) | [SRC] absent |
| §7.6 moderation: estate mute/gain, podium, parcel zones | **Parcel** voice moderation slice 1 exists (sim CAP + store + matrix rule) [SRC]. Estate-level mute/gain and podium: none (`grep podium` = 0; `grep estate src/` = 1, a comment) | partial |
| §8.1 small deployment: one INI section, docker compose | Yes for the shipped shape [SRC: `docker-compose.yml`, `[WebRtcVoice]`/`[JanusWebRtcVoice]`] | [SRC] |
| §8.2/§8.3 placement, admission backpressure, TURN fleet, ambisonics, migration | None (`grep ambisonic` = 0; one mixer per grid) | [SRC] absent |
| §9 caps, fmtp mangle, SLData fields | Yes: both caps; fmtp `minptime=10;useinbandfec=1;stereo=1;sprop-stereo=1;maxplaybackrate=48000` at `janus_slvoice.c:1457`; `j/l/sp/sh/lp/lh/m/ug` parsed | [SRC] |
| §9 "parcel changes within a region do not trigger connection changes" | True in the sense that nothing pushes one — and that is now a recorded gap, not a feature (§4.1 item O-11) | [SRC] |

**Net:** the spec is an aspirational Draft. Tiers 0/1 are partially built, Tier 2 built, Tier 3
built to the "mid tier" only, Tier 4 has one of six features (7.3, and only on the estate
channel) plus half of a seventh (7.6 parcel moderation). Nothing in either repo claims otherwise
in code; the only document that overstates is the mixer README (§4.3).

---

## 2. Phase structure as the repositories record it

The mixer's git log is the cleanest phase record in either repo [SRC: 66 commits, subjects
below]. This tree's log records the sim halves of 3a onward and the moderation/teardown/ban
work; it does not use phase numbers for its own commits except "Phase 3a".

**As recorded:**

| # | Phase (recorded name) | Recording commit(s) | Version |
|---|---|---|---|
| 0 | *Phase 0 scaffold: janus.plugin.slvoice spatial voice mixer* (2026-08-12) | mixer `e1abecf` | — |
| 1 | *Phase 1: single-participant echo test (JSEP + Opus + data channel)*; *Phase 1A: hold a WebRTC voice session (answer the SLData data channel)*; *Phase 1B: echo audio on the held session* | mixer `e483799`, `cc39f2b`, `2d474f9` | 0.3.0 → 0.5.0 |
| 2 | *Phase 2: flat N-minus-one conference mixing* | mixer `1381077` | 0.6.0 |
| 3a | *Phase 3a: consume the per-listener visibility matrix (peer_ctl_batch)* (mixer); *VoiceStateFeeder — per-listener visibility matrix producer (Phase 3a)*, *FeederWorldFromScene adapter + region-module wiring (Phase 3a)*, *JanusAdminClient … (Phase 3a prereq)*, *peer_ctl_batch sender — wire the visibility matrix to the mixer (option C)* (this tree) | mixer `fbb0b7b`; tree `3e29a7c6f7`, `e044ee1670`, `caf5bc7b49`, `fc1454ea3e` | 0.7.0 |
| 3b | *Phase 3b slice 1 — geometry snapshot pass with §7.1 camera leash*; *slice 2 — distance cull with hysteresis*; *slice 3 — distance attenuation*; *item 4 — horizontal azimuth* + *stereo mix core and constant-power pan law* + *wire azimuth pan into the mix*; brief Amendments 1–8 | mixer `ac6a12a`, `ccddc93`, `8e40959`, `25787a3`, `6411c16`, `3b7cd94` | 0.8.0 → 0.9.0 |

**Not phases, but recorded work streams** (no phase number in any commit or document):
voice moderation slice 1 (this tree, 2026-08-21/22); presence-close teardown (this tree,
2026-08-22); capacity cap / HTTP 409 (both repos, 2026-08-20); scaling assessment (mixer doc,
2026-08-18/20); duplicate-display detection + fan-out (mixer, 2026-08-19/22); runtime spatial
config (mixer, 2026-08-21); connector design brief DRAFT (mixer doc, 2026-08-21/22); estate-channel
ban fix and the per-room emission plan S1–S5 (this tree, 2026-08-25/26).

**Correcting the chat-history claim.** The list "recon, echo-test bring-up, mixing, 3a, 3b,
ACL/position push with epoch fail-closed, trust domains and HG policy, features (performer,
estate mute/gain, podium, echo console trigger), connector with PSOLA morphing" is **not how
either repository records the programme**, and it is nine items, not seven:

- "recon" is not a phase; it is a set of documents (`current-architecture.md`,
  `parcel-voice-semantics.md` baseline, `Docs/audit/webrtc-upstream-audit.md`) [SRC].
- "echo-test bring-up", "mixing", "3a", "3b" map to Phases 1, 2, 3a, 3b [SRC].
- "ACL/position push with epoch fail-closed" exists **only as spec §3.3 language**. No commit,
  document, or code names it as a phase; no epoch is on the wire; the sim position feed was
  explicitly deferred by the 3b brief ("ship on viewer-supplied geometry") [SRC].
- "trust domains and HG policy" exists only as spec §3.2 and §10 item 1, plus connector-brief
  Q5 [SRC]. No code, no phase.
- "features (performer mode, estate mute/gain, podium, echo console trigger)" exist only as
  spec §7.2/§7.6. The echo test exists as an SLData toggle and an env knob, **not** a console
  trigger [SRC: no console command]. None of the four is a phase anywhere.
- "connector layer with PSOLA morphing" conflates spec §7.5 (connector; DRAFT brief exists)
  with §7.4 (morphing; PSOLA is a licensing recommendation in the spec, nothing more). No code.

So the repositories record **six phases (0, 1A/1B, 2, 3a, 3b)** plus unnumbered work streams,
and treat everything from spec §3.2/§3.3-epochs/§7.2/§7.4/§7.5/§7.6-estate onward as
**unscheduled**.

---

## 3. Status by phase and work item

Statuses: **done** / **partial** / **not started** / UNKNOWN. "Done" means the code is in the
basis commit and exercised; live verification is stated separately because it is separately
evidenced.

### Phase 0 — scaffold — done
[SRC] mixer `e1abecf`; `docs/protocol-compat.md` records the audiobridge-superset constraint.
Nothing remains. See §4.3 for that document's stale status.

### Phase 1A/1B — hold a session, echo — done
[SRC] `janus_slvoice_negotiate` answers both m-lines; `incoming_data` parses SLData; echo ring
+ `build_echo`; `SLV_ECHO_AUTOSTART` in `docker-entrypoint.sh`. Live verification: [DOC]
`phase1-bringup.md` CHECK 1/2 described against Firestorm 7.2.2; no dated pass is recorded in
that file. Remaining: none as a phase. Echo ring is now allocated lazily (*slvoice: allocate the
240KB echo delay ring lazily on first enable*) [SRC: `janus_slvoice.c:2148`].

### Phase 2 — flat N-minus-one mix — done
[SRC] `janus_slvoice_room_tick` pass 1 decode / pass 2 per-listener sum via
`slv_mix_nminus1_stereo`; per-source `m`/`ug` honoured; encode-skip; tick histogram. Live
verification: **no dated CHECK 3 pass is recorded anywhere** [SRC: grep across both docs trees].
[INF] the Phase 3a acceptance runs of 2026-08-17/18 (which require a working mix to observe
exclusion) exercised it live. `docs/sldata-extensions.md` says `m`/`ug` were "verified against
the viewer" [DOC].

### Phase 3a — sim-authoritative per-listener visibility — done for the estate channel; partial overall
Sim: feeder (`Visibility/VoiceStateFeeder.cs`), matrix (`VisibilityMatrix.cs`), rules
(`VisibilityRules.cs`: voice-enable, estate ban, moderation, parcel ban/restrict symmetric,
SeeAVs symmetric), Scene adapter (`FeederWorldFromScene.cs`) with the TaxFree-ignoring ban
delegate (`LandBan.cs`), sender (`VisibilityBatchSender.cs`), Admin-API transport
(`JanusAdminClient.cs`), sink (`JanusPeerCtlBatchSink.cs`), Watchdog-registered tick thread
(`VoiceVisibilityService.cs:112`) [SRC]. Mixer: `visbatch.c` parser, `apply_visbatch` with
display fan-out, `slv_roster_excludes` used by mix, dot batch, presence and join backlog, drop
counters [SRC]. Live verification: [DOC] `parcel-voice-semantics.md` ADDENDUM 2/3 — acceptance
run §O on 2026-08-18 against this tree `8e52212b0f` and **mixer plugin 0.7.0**; [SRC] the live
config has `VisibilityFeederEnabled = true`, `VisibilityEmitEnabled = true`; the region log of
2026-08-25 20:29 shows all three sinks constructed and feeders started `(emit=True)`, and zero
emission errors, latches or give-ups through the 2026-08-26 04:54 shutdown.

**What remains (recorded):** 3a-D the per-parcel delivery gap (§4.1 O-1); 3a-E the estate-room
fallback interplay with the missing channel-change push (O-11); §G neighbour-region rooms
(O-12); §M duplicate-display residue (O-13); the 64 KB dense-batch rejection (O-2); the
pending-join give-up noise (O-8); two sender unit tests failing since the ILogger migration
(O-20).

### Phase 3b — spatial DSP — done to the spec's "mid tier"; the near/far tiers not started
[SRC] slice 1 geometry snapshot + leash (`snapshot_geometry_locked`), slice 2 cull with
hysteresis (`distance_cull_locked`, `cull_hyst[]`), slice 3 attenuation (`pow(t, falloff_exp)`),
item 4 azimuth + constant-power pan (`azimuth.h`, `pan.h`, `mix.c` stereo), runtime jcfg for the
five constants (`load_spatial_settings`), per-channel RMS diagnostics. Live verification:
UNKNOWN — no dated in-world listening record exists in either repo; the 3b brief states
"verification is numeric (no listening tests available)" and the unit tests are the acceptance
criteria [DOC]. **Remaining, all recorded as deferred in the 3b brief:** HRTF+ITD (near tier),
azimuth binning + crossfades, far-tier mono ambience, dirty-flag coefficient recompute,
sim-authoritative position feed (child agents; cross-region frame transform), estate-level
leash configuration (ships per-process), per-region spatial config (Amendment 8: "deferred, not
rejected"). Non-blocking open question: do neighbour-room handles carry `sp`?

### Voice moderation — slice 1 done; **mute channel SHIPPED 2026-08-27** (roster parity); reporting-parity gap narrowed
[SRC] `SpatialVoiceModerationRequest` CAP registered (`WebRtcVoiceRegionModule.cs:271`),
`VoiceModerationStore.cs`, `VoiceModerationAuth.cs`, matrix rule 2b (`VisibilityRules.cs:37`).
Live verification: [DOC] brief `Status: … VERIFIED end to end (2026-08-22)`, re-verified on
net10 2026-08-24, requiring a Firestorm master-tracking build. **Open:** moderation state is
reported to no client — SL parity gap, worked around **viewer-side** on `phoenix-firestorm`
branch `fix/voice-webrtc-fixes` [DOC]; OQ1 (`mute_all` scope confirmation in SL) and OQ2
(exemption set) unanswered [DOC]. The moderation store is in-memory, non-persistent [SRC].

*Amended 2026-08-27 (evening) — moderation mute is now SHIPPED end-to-end, matching Linden Lab's own
model (Option A): a moderation mute travels on a **separate additive `mute` channel** in the
`peer_ctl_batch` wire, distinct from the exclusion (`excl`) set. The mixer **keeps the muted source's
roster row, greyed**, and silences it in the mix (mix gates on `mod_muted` OR the viewer's own
`muted`) rather than removing it; a ban still removes the row. Routing is **disjoint (ban wins)** — the
matrix never places a source in both channels for one listener — and **skew-safe both directions**: a
`replace` **always carries the `mute` key** (empty `{}` when there are no mutes) so a new mixer can tell
a new sim from an old one, an old mixer ignores the unknown key, and a new mixer reads an absent key as
"no mutes." **Proven in-world this evening**: a moderation mute keeps the target's row greyed (not
vanished) and unmuting from the moderator UI restores it. Sim `02ce1b9b10`; mixer parse/keep+grey
`0d6d0d0`. What remains open is **reporting** parity (O-15): the mute state is now visible to the admin
API (`mod_muted_entries`, below) but is still not reported to non-moderator clients. OQ1/OQ2 (O-16)
remain unanswered; the store is still in-memory (persistence = slice 2).*

### Presence-close teardown — done
[SRC] *webrtc-voice: presence-close teardown with generation-token capture* +
*teardown diagnostics*; `VoiceViewerSession.CaptureSessionsForClose`, `ClosingSessions`,
`show voice closing`. KnownDefects entry status "implemented 2026-08-22" agrees [DOC].

### Capacity cap and HTTP 409 — done
[SRC] mixer join-time `ROOM_FULL` at `SLV_MAX_MIX` = 110 (`janus_slvoice.c:1681`); sim maps
495 → 409 (`WebRtcVoiceRegionModule.cs:540`). Remaining: the all-audible load case is guarded
by nothing (mixer comment at the cap) [SRC]; scaling-assessment open items (O-16).

### Duplicate-display handling — partial
[SRC] *slvoice: fan visibility exclusions out to every session matching the listener display*;
*join-time duplicate-display detection + deterministic dot-batch merge*. Remaining [DOC §M
addendum]: orphan capacity burn, leave-dot ghosting; eviction deliberately not done
(cannot notify an evicted viewer). **This tree's KnownDefects entry still says "not started"**
(§4.3-a).

### Estate-channel ban/restrict at provisioning — done
[SRC] *fix(voice): enforce parcel ban/restrict on the estate voice channel*
(`WebRtcVoiceRegionModule.cs:513`). Deployed 2026-08-25 (§5). Closes one of three parts of
OPEN item #13 (§4.1 O-3).

### Per-room visibility emission (build plan S1–S5, M1) — **S1–S5 COMPLETE (2026-08-27)**; deployed through S3b, S4 built and pending one deploy
*Amended 2026-08-27.* [SRC] **S1** *feat(voice): return the joined room in the provision success
response* (`3c95ddea0e`); **S1b** *refactor(voice): extract the provision response builder, pin
its shape* (`7b08786d19`) — all three response maps moved to `Janus/ProvisionResponseBuilder.cs`
with `ProvisionResponseShapeTests` pinning key order and per-key type byte-for-byte on both the
LLSD-XML and JSON-connector paths; **S2** *feat(voice): record the room each agent joined, per
region* (`98465dc662`) — `AgentRoomTable.cs`, newest-provision-wins, resolver handed to the sink
in `VoiceVisibilityService`'s constructor; **S3a** *feat(voice): add the per-room batch
partitioner, unwired* (`ef119f2a90`) — `Visibility/PeerCtlBatchPartitioner.cs`; **S3b**
*feat(voice): emit one visibility batch per room* (`e35463a088`) — the sink partitions and sends
bounded-parallel, `VisibilityRoomSendConcurrency` config key added. **All four are deployed**
(§5, deploy of 2026-08-26 16:23).

**S4 — inner-reply reading — DONE 2026-08-27** *feat(voice): read the mixer's inner slvoice reply
and surface its counts* (`33fc3b412e`). The sim now parses the mixer's `peer_ctl_batch` reply
(`{janus:success, response:{slvoice, entries, mute_entries, skipped, deferred_listeners}}`) in the
SINK — the client stays a generic Janus admin transport and only returns the raw body. Severity
policy (deliberate): `deferred_listeners>0` → **INFO** (the join-window deferral self-heal working as
designed, NOT a fault — warning on it would rebuild the alarm fatigue `b80efffa36` just removed);
`skipped>0`, a non-applied inner status, or a malformed inner reply → **WARN**; an absent reply (old
mixer) or an applied all-zero reply → **silent**. Counts are surfaced to the sender via a read-only
`LastSendStats` property — plumbing only; the sender acts on nothing. **Skew guarantee:** an old /
pre-mute mixer reply, carrying none of these fields, parses to a default "no info" that logs nothing,
leaves stats zero, and returns the same `PeerCtlSendResult` — byte-for-byte today's behaviour.

*S4 diverged from the brief's plan, recorded for honesty.* §8's S4 specified a new
`PeerCtlSendResult.NotApplied` enum value that `VisibilityBatchSender` would count, with classification
in `JanusAdminClientTests`. The shipped S4 took the **least-invasive** shape instead: the 3-value
`PeerCtlSendResult` is unchanged, no caller's control flow changed, classification lives in the sink
(`JanusPeerCtlBatchSink.ParseInnerReply`/`ClassifyReply`), and the counts ride a stats property. Same
observability, smaller blast radius. The brief's S4 wording is the plan; this is what landed.

**S5 — documentation — DONE 2026-08-27 (this entry).** The per-room emission programme is written up
here (S1→S4 above); the brief's status is flipped to IMPLEMENTED; the mixer wire doc's mute/deferral
section (`mixer-feed-protocol.md` §3.4) was added mixer-side (`03418f7`). **The S1–S5 build plan is
COMPLETE.** M1 (mixer version-string bump, O-27) remains optional and not started. Decisions OQ1–OQ7
recorded in the brief [DOC §7].

S3b was named in the brief as the first in-world-testable step. **No dated in-world run of the
per-room emission path exists** — the region has not been started since the deploy [SRC: §5].
That is U-11 (§6).

### Voice moderation console surface — done, deployed, untested in-world
*Added 2026-08-27.* [SRC] *feat(voice): add console commands to see and clear parcel voice
moderation* (`935bd5b6d2`): `show voice moderation` and `voice moderation unmute
<agent-uuid-or-name>`, registered under the `"Voice"` help category in
`WebRtcVoiceRegionModule.Initialise` via `VoiceModerationCommands.cs`; `VoiceModerationTargets.cs`
holds the pure UUID-or-name resolution (ambiguity and absence both reported, never guessed);
`VoiceModerationStore` gains an ordered detached `Snapshot()` and `UnmuteAgent` now returns
whether it cleared anything. Store remains in-memory and non-persistent — persistence is still
slice 2. 18 new unit tests; the two known stall-guard failures (O-20) are unrelated and
unchanged. Deployed 2026-08-26 18:48 (§5).

**Why it exists:** a parcel mute removed the muted avatar's roster row at the mixer, and that row
was the only way to reach the unmute — so the mute removed its own undo. This is the server-side
escape hatch. The viewer-side half is the separate `fix/voice-webrtc-fixes` work (§7.5). Not yet
exercised on a live region [SRC: region stopped since the deploy].

### Mixer join backlog — SHIPPED 2026-08-27 (`354e9fe`)
*Added 2026-08-27 (evening).* [SRC] mixer *fix(voice): send existing-room participants to a joining
listener* (`354e9fe`). Before this the mixer emitted a `"j"` presence **only on a transition**
(speech/movement/visibility), so a listener joining a room that already had occupants saw an **empty
roster** until someone moved — `push_presence` told the room about a newcomer but never told the
newcomer about the room. The fix synthesises the roster backlog at `data_ready` (the first writable
moment on the listener's data channel), filtered by the listener's own exclusion set, wire-identical to
a live `"j"`. **Proven in-world**: a late joiner now sees existing occupants immediately, and the `"j"`
backlog was observed at `data_ready`. This **supersedes the viewer-repo `voice-participant-row-
suppression.md` framing** (which chased sim/viewer suppression theories); root cause was mixer-side, see
`legion-voice-mixer/docs/join-backlog-defect.md`.

### GIVING-UP warning — diagnosed and resolved 2026-08-27 (`b80efffa36`)
*Added 2026-08-27 (evening).* [SRC] The `[VISIBILITY SENDER]` pending-join path had **no confirmation
predicate at all**: the mixer exposes no room-membership query and its `Ok` never means "applied," so
the sender blind-re-sent a joining listener's full column `PendingJoinMaxAttempts` (6) times and
**always** logged the GIVING-UP warning — for *every* login, including listeners with zero exclusions
and zero mutes. The instrument was worthless: a real dropped ban looked identical to the constant false
alarms (this is O-8). **Fix** *fix(voice): skip the pending-join re-send for a listener with empty
columns* (`b80efffa36`): an empty-excl **and** empty-mute listener is now vacuously confirmed and never
enters the pending machinery — no re-sends, a DEBUG line instead of the WARN; a listener carrying an
exclusion or mute still gets the full bounded re-send. Guarded at first drain, not enqueue, because the
feed columns are only safely readable on the drain thread. The warning is meaningful again. (Deployed
this evening, §5.0.)

### Join-window silent-drop — defect and mixer-side fix, SHIPPED 2026-08-27 (`27977c8`)
*Added 2026-08-27 (evening).* [SRC] mixer *fix(voice): defer a visibility entry for a not-yet-joined
listener, replay on join* (`27977c8`). **Defect**: an exclusion or mute arriving between provision and
room-join was dropped by `apply_visbatch`/`apply_mutebatch` (`nmatch==0`) and stayed lost until a full
snapshot — which in steady state may never come, so a parcel-banned agent logging straight into the
parcel could go **unenforced indefinitely**. **Fix**: a per-room **deferred store** keyed by listener
holds that listener's latest `excl` and `mute` columns under **op-faithful merge** (`replace` sets,
`add` unions, `remove` subtracts, an emptied column clears) and **replays them into the session inside
the locked join, before the join roster is built and before any presence is emitted** — deferred
exclusions are in force before anything derived from them is revealed. **Why op-fidelity over wholesale
last-write**: a post-window un-ban (`remove`) must never be resurrected into an exclusion on join;
wholesale replace would do exactly that. Capped per room (`SLV_VIS_MAX_DEFERRED`, oldest evicted and
counted), freed on room teardown, **no TTL** (a proven join arrived at +8 min — a timer would recreate
the loss). New dependency-free module `src/deferred.{c,h}` with a 35-check unit test; all mixer tests
green; `.so` 306,600 B. **Verified in-world and via the admin API this evening.**

### New observability — added 2026-08-27
*Added 2026-08-27 (evening).* [SRC] The `peer_ctl_batch` **reply now carries an additive
`deferred_listeners`** count (entries deferred this batch, excl + mute) — the first time a sim can see
from the response that its entry was retained, not silently dropped. `query_session` / `handle_info`
expose the store's counters under `visibility`: `deferred_current`, `deferred_adds`,
`deferred_replaced`, `deferred_replayed`, `deferred_evicted`; and each session gains
`mod_muted_entries` (moderation flag, previously invisible to the admin API). All additive; old sim
ignores the reply key, old mixer never emits it. Sim-side reading of `deferred_listeners` (warn on
drops) is **S4, still open** (O-1).

### NEW EXPECTED BEHAVIOUR — `have_batch:false` / `epoch:0` on a clean listener is HEALTHY
*Added 2026-08-27 (evening).* Post-`b80efffa36`, a zero-exclusion / zero-mute listener causes **no
batch to be sent at all** (there is nothing to enforce), so `query_session` shows `visibility.epoch:0`
and `have_batch:false` for that room until the first real exclusion/mute arrives — at which point a
batch appears carrying actual content. **This is correct, not a fault.** Recorded so a future session
reading the admin API does not misread the absence of a batch as a broken feed.

### Sink counters read only the exclusion channel — investigated and RESOLVED 2026-08-28 (`0190d864ef`)
*Added 2026-08-28.* Opened 2026-08-27 evening as a possible enforcement regression; **resolved as a
counter/logging bug — enforcement never broke.**

- **Symptom.** Every post-deploy moderation event logged `[JANUS PEERCTL SINK] … addressed 0 room(s)`
  and `[VOICE VISIBILITY] +0/-0 listeners` while avatars were in voice.
- **Decisive captures** (Janus Admin API `handle_info`, two avatars, target unblocked): during an
  active menu mute the listener's handle read `mod_muted_entries=1`; after unmute, `0`. **Enforcement
  is proven end-to-end, both directions, on the deployed build** (sim `18640868dc`; mixer image
  `a5a6c7b7189a` / `27977c8` content).
- **Root cause (static trace, verified).** The three sim-side instruments — `[VOICE VISIBILITY]`
  `+N/-N`, the sink's `addressed N room(s)`, and the `_lastSend*` properties — read **only the
  exclusion channel** (the `part` partition). The mute channel rides the parallel `muteRooms` union
  that only the wire consumes. **Blind by construction since the mute channel was introduced — NOT a
  regression.** `b80efffa36` is exonerated: it guards the join path, leaves the `AgentRoomTable`
  registry untouched, and the observed events were steady-state deltas, not join-path sends.
- **The 2026-08-27 ~05:24 "regression evidence" is re-read accordingly:** the counters were lying AND
  the ear was confounded — the target sat on the moderator's **Block Voice** list (a personal mute).
  The ~21:50 three-way audio pass stands unchanged.
- **Fix.** `0190d864ef` *fix(voice): count the mute channel in the visibility/sink instruments* —
  counters/logging only, **wire byte-identical**, unit-tested against the exact capture scenario.
  Committed, **not yet deployed**; rides the next sim staging batch. **Live confirmation owed then:**
  one mute should log `addressed 1 room(s)`.

### Reading the instruments — two operational notes (added 2026-08-28)
*Added 2026-08-28.* Recorded so a future session does not misread the admin API.

- **Mixer `handle_info`: `data_msgs_received` / `last_data_fields_seen` count the viewer's OWN SLData
  channel** (e.g. `"m"` = that viewer's *personal* mute of someone), incremented only in
  `janus_slvoice_incoming_data`. **Sim moderation** arrives via `apply_mutebatch` on the Admin API
  feed and moves `mod_muted_entries` **without** moving those two fields. One changing without the
  other is **expected**, not an anomaly — they instrument two independent inputs.
- **`AgentRoomTable` never removes entries** (deliberate, documented in-source). A stale entry is
  unreachable via matrix membership gating and is overwritten on the agent's next provision. **Not a
  leak; do not "fix" it** — close-time removal without the teardown generation token would let a late
  close of an OLD login erase the NEW login's record.

### Avatar-to-avatar voice (A2A) — S-A2A-1..5 BUILT and COMMITTED 2026-08-30; DEPLOYED and LIVE-VERIFIED the same day
*Added 2026-08-30.* [SRC: `git log` on `feature/voice-visibility-matrix`; viewer repo `phoenix-firestorm`.]
*Updated 2026-08-30 (evening): deployed (three stagings, §5.0), extended by the live-found fixes
S-A2A-2.1 (`7bd7e7fec3`), S-A2A-2.2 (`182660547b`), S-A2A-3.1 (`d23e41c762`), and **verified by the
first live call** (subsection below). The "NOT deployed; no in-world run" statements in this
subsection's body are the morning's state, retained as written.*
The O-30 feature ("has never worked", §7.3), built as five slices against the plan's DECIDED items
(`Docs/voice/a2a-build-plan.md` §1: no P2P transport, room model (a), registry-primary authorization,
server-minted channel + token, single-instance scope). One line each:

- **Assessment** `e39723a850` *docs(voice): add the A2A ground-truth assessment against b7fbc717fa* —
  `Docs/voice/a2a-assessment-20260830.md`, with the 2026-08-30 addendum (U-13 resolved: the viewer
  CAN drive `multiagent`; body keys `channel` / `credentials`, no `parcel_local_id`).
- **Wire trace (viewer)** `ba70cba746` in `phoenix-firestorm` —
  `docs/voice-a2a-wire-trace-20260830.md` against Firestorm `a9a34638a3`: P2P-as-ad-hoc, `"call"`
  expects `voice_credentials` in the HTTP body, invitation = `voice` map with `invitation_type:2` and no
  `instantmessage`, hangup = provision `{logout:true, viewer_session}` with **no `channel_type`**.
- **Plan** `a59e71e827` *docs(voice): add the A2A build plan; record the U-13 resolution*.
- **S-A2A-1** `636842d8ee` — `A2ASessionRegistry` (XOR session id, per-session 32-byte token, invite
  TTL 2 min), `"start p2p voice"` records the pair (`params` absent → 400), `"call"` returns
  `voice_credentials`, the permanent `[A2A CHATSESSION]` / `[A2A PROVISION]` DEBUG instruments.
- **S-A2A-2** `f0fe15ab03` — the `ChatterBoxInvitation` to the callee via the generic event queue
  (`A2AInviteDelivery`), root-presence-preferred scene resolution; callee not on this instance → no
  invite, record left to TTL (plan §1.7).
- **S-A2A-3** `f73fa4584b` — `A2AProvisionAdmission` replaces the O-29 deny **for `multiagent` only**
  (live session + named party + constant-time token compare, else 403); **logout recognised by its own
  field FIRST** (the item-0 fix, next subsection); room model (a) enforced — only a `local` provision
  reaches `OnListenerProvisioned`; callee's admitted provision = accept (Invited→Active); Active
  suppresses re-invite; decline / both-logout / client-close remove; Active idle backstop 8 h; the
  service reads `channel` (U-13). `"call"`: non-party 403, unknown 404.
- **S-A2A-4** `90d74cfc69` — grid id (normalised `GatekeeperURI`) in the `multiagent` room hash; O-35
  closed for `multiagent`; `local` arm byte-for-byte unchanged (fold fixture pins it).
- **S-A2A-5** `116dc8e3aa` — both `viewer_session` lookup sites bound to the requesting agent; a
  mismatch is not-found plus one WARN naming both agents (plan §1.6).

**Tests** [SRC: `dotnet test` at each slice]: region module 85 → **133/133**; Janus service 146/148 →
**170/172**, the two failures still the O-20 pair. **Status: built and committed, NOT deployed** —
every A2A binary is past the running build (§5.0). **No in-world run exists**; the acceptance is the
plan's §4 two-party protocol, to be run on the next sim deploy (§5.0 watch list). Live-test watch
items carried from the slice Q&As are recorded in the plan (`a2a-build-plan.md` §5).

### Voice logout provisions refused 403 since `d9fa72c351` — the S-A2A-3 item-0 finding; FIXED in `f73fa4584b`, deployed and CONFIRMED LIVE 2026-08-30
*Added 2026-08-30.* [SRC: source at `116dc8e3aa`; live log `OpenSim.Server.RegionServer20260829.log`.]
*Updated 2026-08-30 (evening): the owed live confirmation is delivered — `decision=logout` on every
teardown, no `refusing provision with channel_type ""` (§5.0 watch-list results).*

- **Defect.** The O-29 fail-closed guard (`d9fa72c351`, deployed 2026-08-27 20:45–20:49 staging,
  §5.0) ran **before any logout recognition**. The viewer's teardown body is
  `{logout:true, viewer_session, voice_server_type}` and carries **no `channel_type`** (wire trace §4,
  `llvoicewebrtc.cpp:2809-2811`), so **every** voice logout provision — spatial included — was refused
  `403` with `refusing provision with channel_type ""`. Live: **12 such lines since the deploy** — the
  first at 2026-08-27 21:30:01 (41 minutes after the staging), none on 08-28, **11 on 2026-08-29**
  from 11:42:12 through 14:46:49 — one per teardown [SRC: `grep -c` over the three daily logs].
- **What failed to run.** The service's logout arm, `WebRtcJanusService.cs:236-245`: the mixer
  `leave` for that participant (`Room.LeaveRoom`), `Room = null`, and the `BuildClosed()` reply.
- **Blast radius.** One **ghost participant in the mixer room per voice toggle or parcel crossing
  within a login**: after a (refused) logout the viewer's next provision carries no `viewer_session`,
  so the sim creates a fresh session (`WebRtcVoiceServiceModule.cs:399` / `:406`) and the old one's
  participant stays in its room with no media — **counting toward `ROOM_FULL` and the exclusion
  column** until the login ends. **Bounded per login**: `Event_OnClientClosed`
  (`WebRtcVoiceServiceModule.cs:201-243`) captures every session of that agent in the region by login
  generation (`VoiceViewerSession.CaptureSessionsForClose`, `VoiceViewerSession.cs:146`) and
  `JanusViewerSession.Shutdown` (`JanusViewerSession.cs:95`) does `LeaveRoom` → `Detach` →
  `DestroySession` on each, with failed teardowns retried from the next provision. Unbounded only
  within a single login.
- **Pre-existing and separate — O-41.** Even when the logout arm runs, it does **not** remove the
  session from `VoiceViewerSession.ViewerSessions` (`VoiceViewerSession.cs:59`); only the hangup path
  does (`DisconnectViewerSession`, `WebRtcJanusService.cs:199-205`). The table entry is retained until
  the client-close capture. Should-fix, filed below; not touched by S-A2A-3.
- **Fix** (`f73fa4584b`): `A2AProvisionAdmission.Decide` (`WebRtcVoiceRegionModule.cs:527`)
  classifies a logout by its own field **first**, ahead of any `channel_type` check, and the handler
  routes it straight to the voice service by `viewer_session` (S-A2A-5 binds that to the agent). The
  `[A2A PROVISION]` line shows `decision=logout`. **Live confirmation owed on the next deploy**:
  `decision=logout` lines and **no** `refusing provision with channel_type ""` on relog; mixer
  participant counts stable across voice toggles within a login (§5.0 watch list).

### A2A FIRST LIVE CALL — PASSED 2026-08-30 17:50 (Legion → Aleric, Ebony)
*Added 2026-08-30 (evening).* [SRC: live region log; operator confirmation in-world.] The build
plan's §4 protocol, run on the day's third deploy (sim through `d23e41c762`, §5.0). The chain,
verified line by line:

`start p2p voice` recorded → `call` `credentials-issued` with **one** invitation (`invite=pending`,
`[A2A INVITE] … decision=sent`) → **callee floater shown** → accept = the callee's `multiagent`
provision → **both parties `multiagent-admitted`** → the service reached (the S-A2A-3.1 seam) →
`SelectRoom` **room 1592022445** (grid-derived, non-spatial) **for both parties** →
`decision=provisioned` Invited then **Active** → mixer media receiving both ways → **audio both
ways confirmed by the operator** → the caller's re-`call` answered `invite=suppressed-active`
(S-A2A-2.1/3 guards holding live) → both `decision=logout` teardowns → `removed-both-logout`.
Spatial voice sessions were torn down around the call and restored after it, as the P2P-as-ad-hoc
model predicts. **O-30 ("avatar-to-avatar voice has never worked") is CLOSED.**

**Test-protocol lesson, recorded so it is not relearned:** the confounded observations that preceded
the clean run — the target sat on the tester's **Block Voice** list, which was mistaken for a
moderation-mute effect (the same confound as the 2026-08-27 "regression evidence", §3 sink-counter
item). Before any voice acceptance run: clear `volume_settings.xml` **and** both testers' Block
lists, then verify with `mod_muted_entries` via the admin API, not by ear.

### Four defects found live and fixed same-day — 2026-08-30
*Added 2026-08-30 (evening).* [SRC: live region log for each symptom; the named commits.] Each was
found by running the §4 protocol against the deployed build, root-caused to a cited line, fixed,
redeployed, and re-verified the same day:

- **(a) Every voice logout provision 403'd since `d9fa72c351`** (deployed 2026-08-27). Symptom:
  `refusing provision with channel_type ""` on every teardown, spatial included. Root cause: the
  O-29 guard ran before any logout recognition and the teardown body carries no `channel_type`.
  Fix: **`f73fa4584b`** (S-A2A-3, logout classified by its own field first). Confirmed live:
  `decision=logout`, no teardown 403s.
- **(b) Unbounded invitation feedback loop** (~90 ms/cycle). Symptom: alternating bare `call`s on
  one XOR id, an invitation and a "started a voice call" chat line per cycle, no provision ever.
  Root cause: the sim invited "the other party" on every `call`, whoever made it, and a viewer
  holding a `callStarted()` channel auto-accepts a reflected invite with no floater and no
  `voice_channel_info` (`llimview.cpp:4204-4210`, `:4177-4185`), rebuilding its channel into a
  bare re-`call`. Fix: **`7bd7e7fec3`** (S-A2A-2.1 — invite only from the record's Caller, once
  per Invited record).
- **(c) A2A bodies carried no `voice_server_type`.** Symptom: even a clean accept never attempted
  a `multiagent` provision. Root cause: the viewer routes channel info by `voice_server_type` and
  defaults an absent value to **Vivox** (`llvoiceclient.cpp:126-132`, `:514-528`), and
  `compareChannels` could never compare equal (`llvoicewebrtc.cpp:1682-1687`), driving the
  channel teardown-rebuild in (b). Fix: **`182660547b`** (S-A2A-2.2 — `voice_server_type:"webrtc"`
  in the invitation `voice` map and the `call` response's `voice_credentials`; `sip_uri`
  deliberately absent).
- **(d) Admitted multiagent provisions died silently before the service.** Symptom:
  `multiagent-admitted` then nothing — no service line, no exception, viewer in
  `VOICE_STATE_SESSION_RETRY` (1/2/4/8 s). Root cause: `WebRtcVoiceServiceModule.Initialise`
  loaded the non-spatial service **only when the two configured DLL names differed**; Legion's
  config names the same DLL for both roles, so `m_nonSpatialVoiceService` was null since the
  module was written and the `CreateViewerSession` dispatch threw `NullReferenceException` —
  swallowed unlogged by the caps wrapper's bare catch (`SimpleStreamHandler.cs:91-101`, → O-43).
  Fix: **`d23e41c762`** (S-A2A-3.1 — identical role config shares the one instance; a logout
  naming no live session answers `closed` instead of erroring).

### O-42 CLOSED, all three parts — 2026-08-31
*Added 2026-08-31.* [SRC: live region log and in-world verification 2026-08-31 ~05:27; mixer
`git log`; the cited viewer lines read at `895f65ab43`.] The caller-floater / callee-stranded
defects from the first live call are closed by one sim slice and three mixer slices:

- **(a) IM-panel participant/moderation surface — S-A2A-6 (`2ff8bf57af`), live**: `[A2A AGENTLIST]`
  ENTER pair on Active and LEAVE on removal observed on the wire; both panels populated.
- **(b) Caller's connected/End-Call state — M-A2A-1 (mixer `6ee39be`)**: the `data_ready` /
  `push_presence` fence closed the one-shot presence race; live, the caller's handle showed
  `presence_pushed >= 1` and **End Call present**.
- **(c) Callee auto-hangup on the caller's exit — M-A2A-2 (mixer `3f4780d`) + M-A2A-3 (mixer
  `5e0d637`)**: M-A2A-2 announces the leave on EVERY removal path (the silent `destroy_session`
  removal was one root cause; `presence_leave_pushed=1` proved delivery), and M-A2A-3 re-sends the
  join backlog on the participant's own `"j"` SLData — proof its data channel is attached — closing
  the **stock viewer's discard-before-observer window** (`llwebrtc.cpp:1763-1771`: frames received
  with no observer are dropped, unbuffered; the observer attaches a main-thread hop after
  `OnDataChannelReady`, `llvoicewebrtc.cpp:3450-3468`). Live: **the callee auto-hung-up at
  05:27:53, 186 ms after the caller's logout**, with "your call has ended" shown.

**Root causes, for the record**: the one-shot presence race (M-A2A-1), the silent `destroy_session`
removal (M-A2A-2), and the stock viewer's attach window — the last is **documented stock-viewer
behaviour the server must accommodate** (per the standing constraint, §4), not a bug we can file.
**Owed**: a `backlog_resent` counter read on a future call (the behavioural proof — the auto-hangup —
is banked; the counter confirms the mechanism attribution). *Update 2026-08-31 (evening):
`backlog_resent=1` observed on both spatial handles at the 19:03 join [SRC: admin API] — M-A2A-3
is confirmed live on the JOIN path; the during-call read on the two A2A handles REMAINS OWED
(not taken on the 19:06 call).*

**S-A2A-7 (sim force-teardown of the remaining party) — RESERVE, not needed.** It would guarantee
call-end through the viewer's error path regardless of presence delivery
(`OnConnectionFailure` → `handleError` → `deactivate`), at the UX cost of an error-path hangup
(retry/backoff, a generic error notification instead of a clean end). Kept on the shelf against a
future presence-delivery regression; no code exists.

### A2A regression check after O-41 — PASSED 2026-08-31 19:06
*Added 2026-08-31 (evening).* [SRC: live region log; in-world observation.] One call run on the
19:01 deploy (§5.0) to prove the O-41 logout disconnect changed nothing in the A2A teardown
chain: `[A2A AGENTLIST]` ENTER at 19:06:43 → caller logout 19:07:00.400 → callee auto-logout
19:07:00.626 (**226 ms**) → `removed-both-logout` → LEAVE; "your call has ended" shown on the
callee's stock Firestorm and **remained displayed**. Behaviour unchanged by O-41.

### Connector layer — not started (design DRAFT)
[DOC] `connector-design-brief.md` `Status: DRAFT. Not frozen.`; Q1 (identity) resolved by
Amendment 1 2026-08-22 as "NPC-backed presence plus policy record"; Q2–Q6 open (§4.1 O-17).
[SRC] no connector code; the brief itself establishes the plain-RTP participant does not exist
in this mixer.

### Unscheduled — spec sections with no phase, no brief, no code
Trust domains and HG policy (§3.2, §10 item 1); wire epochs / fail-closed (§3.3); capture
consent (§3.5); `diag` member, `voice status` console, fail-loud signalling, session event log,
fleet observability (§4.1/4.3/4.4/4.5); degradation ladder (§5); HRTF/ITD/tiers/binning (§6 —
recorded as deferred by 3b, so "deferred" not merely unscheduled); performer mode (§7.2);
voice morphing / PSOLA (§7.4); estate-level mute/gain and podium (§7.6); §8.2/§8.3 scaling
machinery; FOA (§10 item 2); recording consent defaults (§10 item 3); session migration (§10
item 4). All [SRC] absent by grep of both source trees and both git logs.

---

## 4. Consolidated open items

**STANDING CONSTRAINT (recorded 2026-08-31) — stock-viewer compatibility.** The compatibility
target for the whole voice programme is the **stock LL/Firestorm viewer**: every fix is sim- or
mixer-side, producing exactly what LL-upstream viewer code expects. Viewer-side changes are
last-resort, fork-only, and when one is ever made it is recorded here as a **stock-viewer
compatibility gap**, not as the fix. Rationale: the WebRTC voice stack ships to grids whose users
will never build or install a custom viewer. (Applied throughout O-42: the viewer's
discard-before-observer window was accommodated server-side, M-A2A-3, not patched viewer-side.)

### 4.1 One list, deduplicated

| ID | Item | Status | Recorded in |
|---|---|---|---|
| O-1 | Visibility feed addressed only to the estate room; per-parcel agents get no exclusions | filed; **S1–S5 COMPLETE 2026-08-27** (deployed through S3b; S4 `33fc3b412e` built, pending one deploy) | `KnownDefects.md` (this tree); per-room brief; `mixer-feed-protocol.md` §3.4 correction; `parcel-voice-semantics.md` §P Part 2 |
| O-2 | Dense exclusion batch > 64 KB rejected whole, read as applied; sender marks synced | filed 2026-08-26; chunking deferred (OQ6); visibility via S4 | `KnownDefects.md`; per-room brief §3 |
| O-3 | #13 estate-channel ban — three parts: provisioning bypass **closed**; mixer-side closed for estate room only (= O-1); TaxFree void **open** | split | `parcel-voice-semantics.md` OPEN #13 + §E + §P |
| O-4 | TaxFree short-circuit voids parcel ban/restrict at provisioning on both channels; matrix overrides it — the two layers disagree under TaxFree | open, undecided | `parcel-voice-semantics.md` §E, §P Part 3 |
| O-5 | Parcel local IDs hashed as `float` (`Add(int)` → `Add(float)`) | filed; deliberately not fixed (grid-wide renumbering) | `KnownDefects.md`; per-room brief §2c |
| O-6 | OnListenerProvisioned queues a doomed re-send on failed provisions | not started | `KnownDefects.md` (cites stale lines `:408`–`:411`; now `:546`–`:553`) |
| O-7 | Region crossing leaves a live voice handle in the previous region's room | not started, observed 2026-08-24 | `KnownDefects.md` |
| O-8 | Pending-join confirmation gives up for every listener even when the batch lands | **RESOLVED 2026-08-27** (`b80efffa36`): empty-column listeners skip pending, so the GIVING-UP warning is meaningful again (§3) | `KnownDefects.md`; this ledger §3 |
| O-9 | Feeder thread *death* undetected (blocked/wedged now caught) | partial | `KnownDefects.md` — status says "(uncommitted)"; **stale**, see §4.3-b |
| O-10 | Parcel ban does not eject an already-present avatar | not started (core) | `KnownDefects.md` |
| O-11 | No channel-change push on intra-region parcel crossing (no `ParcelVoiceInfoRequest` CAP); agent stays in old room until viewer re-provisions | open, **unfiled** — appears only in a commit message and the per-room brief §6 | commit *fix(voice): enforce parcel ban/restrict…*; per-room brief |
| O-12 | Neighbour-region voice rooms: child agents' room semantics | open | `parcel-voice-semantics.md` §G, §L "REMAINS OPEN" |
| O-13 | Duplicate-display residue: orphan capacity burn, leave-dot ghosting; eviction policy | partial | `parcel-voice-semantics.md` §M addendum; `KnownDefects.md` "Mixer applies…" (stale status, §4.3-a) |
| O-14 | Coarse-location (map dot) vs voice hiding policy divergence | open/undecided | `parcel-voice-semantics.md` OPEN #14; `mixer-feed-protocol.md` §1 |
| O-15 | Moderation state reported to no client (SL parity gap); viewer-side workaround only | open | `voice-moderation-design-brief.md` 2026-08-24 section |
| O-16 | Moderation OQ1 (`mute_all` scope vs SL) and OQ2 (exemption set) | unanswered | `voice-moderation-design-brief.md` |
| O-17 | Connector brief Q2 disclosure, Q3 authorisation, Q4 injection identity, Q5 hypergrid interaction, Q6 disclosure sufficiency | open; Q1 resolved | `connector-design-brief.md` |
| O-18 | 3b deferred DSP: HRTF+ITD, binning, far-tier, dirty-flag; sim position feed; estate-level leash; per-region spatial config | deferred | `phase3b-design-brief.md` + Amendments 3/8 |
| O-19 | Scaling: pass-2 parallelism (open for all-audible case); tick composition at N≈110 decomposed; "exactly one inbound track?" unverified inference | open | `scaling-assessment.md` §Open questions + Amendment 1 |
| O-20 | Two `VisibilityBatchSenderTests` fail (stall-log assertions count a log4net appender; the sender logs via ILogger since 2026-08-23), so `ForceClearStalledSend` has **no live coverage** — pre-existing at `6586838e43` | **FILED 2026-08-26**, not fixed (was "unfiled"; corrected 2026-08-27) | `KnownDefects.md` [SRC: entry added by `7b08786d19`]; this ledger |
| O-21 | **Both** `os-webrtc-janus.ini` **and** its `.example` carry none of `VisibilityFeederEnabled` / `VisibilityEmitEnabled` / `VisibilityTickMs`; the live config uses all three, so a region built from either ships with the feeder off and no log line saying so | **FILED 2026-08-26**, not fixed (was "unfiled, .example only"; corrected 2026-08-27) | `KnownDefects.md` [SRC: entry added by `7b08786d19`]; [SRC: grep = 0 in both files, re-verified 2026-08-27] |
| O-22 | `mixer-feed-protocol.md` "room-level flag in v1.1" for voice-denied vs no-exclusions | idea, unscheduled | `mixer-feed-protocol.md` §3.2 |
| O-23 | `protocol-compat.md` constraint status "ACTIVE (Phase 0)" with expiry at flat-mix parity — parity reached in code (Phase 2); whether the audiobridge-superset constraint still binds is undecided | UNKNOWN | `protocol-compat.md` |
| O-24 | Spec §10 questions 1–4 (HG pool policy, FOA viewer decode, consent defaults, session migration) | untouched | `webrtc-voice-spec.md` |
| O-25 | Parcel access-list persistence non-atomic (delete-then-reinsert) | not started (core; adjacent) | `KnownDefects.md` |
| O-26 | Five estate toggles packed into region flags with no write path | not started (estate; adjacent) | `KnownDefects.md` |
| O-27 | Mixer version string not bumped by the `unknown_room` commit, so a deployed plugin cannot self-identify as carrying it (M1) | optional | per-room brief §8 |
| O-28 | `Docs/voice` cross-repo sync drift: `parcel-voice-semantics.md` differs by 56 lines (this tree's §P not in the mixer); this tree lacks the spec, 3b brief, connector brief, scaling assessment, current-architecture; the mixer lacks the moderation brief and the per-room brief | **CLOSED 2026-08-31** — the two `Docs/voice` directories are a full **13-file mirror**, verified by blob hash (`git rev-parse HEAD:<path>`) on both sides. Sim→mixer: `a2a-assessment-20260830`, `a2a-build-plan`, `per-room-visibility-emission-design-brief`, `voice-moderation-design-brief`, `voice-programme-ledger` (new in the mixer), `parcel-voice-semantics` (mixer copy was at `6c9659f`, lacking this tree's §P additions — three diff hunks). Mixer→sim: `connector-design-brief`, `current-architecture`, `phase3b-design-brief`, `scaling-assessment`, `webrtc-voice-spec` (new in this tree, §1), `mixer-feed-protocol` (sim copy was at `58ae158be5`; the mixer's `5e0d637` copy carried the 2026-08-27 deferral amendment in §3.2 and the M-A2A §3.4 notes (d)/(e)/(f) — a strict superset, copied, not merged). `phase3a-feeder-acceptance`: blobs already equal, untouched | this ledger [SRC: blob-hash comparison 2026-08-31; prior basis: diff 2026-08-26] |

*Rows O-29 – O-38 added 2026-08-27 from the reviewer-condition assessment (§7). All [SRC].*

| ID | Item | Status | Recorded in |
|---|---|---|---|
| O-29 | **`multiagent` provisioning bypasses every access check.** All estate-voice / parcel / ban / restrict enforcement sat inside `if (channelType == "local")` (`WebRtcVoiceRegionModule.cs:472`–`:547`); a `channel_type="multiagent"` request skipped all of it | **RESOLVED 2026-08-27** (`d9fa72c351`): a non-"local" (or missing) channel_type now FAILS CLOSED before any auth-bypassing work; the estate channel is unaffected (it is expressed as "local", not a channel_type). *Amended 2026-08-30:* the deny is **REPLACED for `multiagent` only** by S-A2A-3 (`f73fa4584b`, `A2AProvisionAdmission`) — admitted iff registry session + named party + token; every other type still fails closed; a logout is now recognised ahead of the guard (§3 item-0 finding) | this ledger §7.2, §3 |
| O-30 | **Avatar-to-avatar voice has never worked**: `ChatterBoxInvitation` has no callers anywhere; `voice_enabled` sent `false`; session name is the caller's own; `credentials` read and discarded; other ChatSession methods are stubs | **CLOSED 2026-08-30** — S-A2A-1..5 (`636842d8ee` … `116dc8e3aa`) plus live-found 2.1/2.2/3.1 (`7bd7e7fec3`, `182660547b`, `d23e41c762`), deployed, and **verified by the first live call 2026-08-30 17:50, audio both ways** (§3); refinements tracked as O-42/O-44 | this ledger §7.3, §3; `a2a-build-plan.md` |
| O-31 | Methods named `ProvisionVoiceAccountRequestBAD` / `VoiceSignalingRequestBAD` on production paths (`WebRtcJanusService.cs:211`, `:334`) | open, **unfiled** — should-fix (§8) | this ledger §7.6 |
| O-32 | Sync-over-async: six `.Result` calls in `WebRtcJanusService.cs` (`:137`, `:208`, `:331`, `:437`, `:449`, `:466`), two of them on the provisioning and signalling hot paths | open, **unfiled** | this ledger §7.6 |
| O-33 | `Math.Abs(hashed.GetHashCode())` (`JanusAudioBridge.cs:219`) threw `OverflowException` on `int.MinValue` — room-number derivation failed hard for one stable input | **RESOLVED 2026-08-27** (`2b58c74f9a`): `int.MinValue` folds to `int.MaxValue`, `Math.Abs` kept verbatim for every other value so no existing room renumbers | this ledger §7.6 |
| O-34 | Stale comment `WebRtcJanusService.cs:239` — "channel_type has already been checked to be 'local'" — was **false** (`multiagent` reached that line) | **RESOLVED 2026-08-27**: made TRUE again by `d9fa72c351` — the fail-closed guard upstream means only "local" reaches `:239`, so the comment is accurate and no longer hides O-29 | this ledger §7.6 |
| O-35 | `CalcRoomNumber`'s `"multiagent"` branch hashes only `channelID` + `channelType`, with the in-source comment "should add a GridId here" (`JanusAudioBridge.cs:207`–`:211`) — two grids sharing a mixer can collide on room numbers | **RESOLVED for `multiagent` 2026-08-30** (`90d74cfc69`, S-A2A-4): the arm hashes the normalised `GatekeeperURI` + channel + type; `local` untouched; **open for any future channel_type** (in-source note at `JanusAudioBridge.cs:243`). NOT deployed | this ledger §7.6, §3 |
| O-36 | Unfinished TODO "check for errors and package the response" (`WebRtcVoiceRegionModule.cs:632`) sitting directly above the line that discards the signalling response | open, **unfiled** — cosmetic; see §7.1, where the discard is load-bearing for the no-P2P finding | this ledger §7.6 |
| O-37 | **Viewer:** a stored per-avatar volume in `volume_settings.xml` can permanently suppress that avatar's participant row; audio unaffected; survives grid restart, viewer restart, relog and teleport. Mechanism **UNKNOWN**; workaround documented | filed **viewer-side** 2026-08-26 — deferred (§8) | `phoenix-firestorm:docs/voice-participant-row-suppression.md` (do not duplicate here) |
| O-38 | Hypergrid visitors are provisioned **identically** to local users — the voice addon contains no HG-aware code at all (zero references to `Hypergrid` / `IsLocalGridUser` / `UserAgentService` / `scopeID`). Bears on spec §3.2 and §10 item 1 | open, **policy undecided** — deferred (§8) | this ledger §7.4; spec §3.2, §10 item 1 |
| O-39 | **Grid-mode (Robust-side) voice connector exists in-tree but is DEAD as shipped.** `WebRtcVoiceServerConnector : IServiceConnector` (`WebRtcVoice/WebRtcVoiceServerConnector.cs:47`) hosts the same `WebRtcVoiceServiceModule` — `WebRtcJanusService` chain as the region path, but it is registered in **no** shipped/example ini (`[ServiceList]` carries only the stock Freeswitch line); the only setup is a manual README edit (`README.md:167`). Complete but unreachable out of the box, **unverified**. Recorded for Iain's grid-mode-fails-to-connect report | open, **unverified** — inventory only (added 2026-08-27) | this ledger; `WebRtcVoice/WebRtcVoiceServerConnector.cs`; `Addons/os-webrtc-janus/README.md:157`—`:187` |
| O-40 | **Mixer audio tap (Balpien) — first post-RC pull-in.** Confirmed as the first item to pull in after the reviewer-condition RC; not started, no code or brief in either tree yet | **IN PROGRESS 2026-08-31** — S-CON-1 `7240797fc8`, S-CON-2 `db858f4cff`, S-CON-3 `d95754509d` committed AND deployed (21:59, §5.0), **live-verified 22:01–22:06** with a `[VoiceConnector.Recorder]` record on Ebony (`MayInject=false`): NPC created and registered, room 226001844, the moderation mute pushed at registration with NO listener present and read as `mod_muted_entries=1` on a listener who joined minutes later — the late-joiner path carries the mute channel; the entry notice shown on stock Firestorm at login. Suites: Janus 179/181 (the O-20 pair), region-module 167/167. Next slice: S-CON-4 (recorder peer, mixer repo). *Amended 2026-09-01:* **S-CON-4 DONE** — mixer `6300edc` (recorder peer) + `1c37b4c` (the recorder wrote the libopus decoder's whole 120 ms plane per 20 ms frame: the first recording, 2026-09-01 03:40, was [960 audio][4800 zero] samples repeated 871 times, non-zero fraction exactly 1/6; fixed by slicing each frame to its own sample count and writing contiguously; compose now derives `JANUS_URL` from `JS_HTTP_PORT` after the first run went to the 14223 fallback). Second recording 2026-09-01 10:38: 15.3 s, non-zero fraction 0.9997, no zero run over 34 samples, the operator confirms it is his voice. The peer joined room 226001844 as the NPC identity with one existing participant, ICE completed, webrtcup; the Janus container untouched throughout. **S-CON-5**: (a) roster — "Recorder NPC" shown in stock Firestorm's Nearby Voice while the peer was joined, OBSERVED 2026-09-01; (b) entry notice OBSERVED 2026-08-31; (f) playback OBSERVED 2026-09-01; (c) handle-walk with the peer joined not captured this run, owed; (d) exclusion test DEFERRED (needs a second avatar banned from the NPC's parcel); (e) mute-enforced silence DEFERRED to S-CON-6 (nothing sends yet). Observations, not defects: the received stream is mono duplicated to L/R with the talker at the NPC's position (pan centred); peak reached full scale on 6 samples during deliberately loud speech — watch for audible clipping, no action. No new O-items. Next slice: S-CON-6 (injector peer). *(Planning basis, 2026-08-31 earlier: assessment `31ee058d04`; brief Amendment 2 Q2–Q6 DECIDED; plan S-CON-1..7; no mixer change anywhere.)* | `connector-assessment-20260831.md`; `connector-design-brief.md` Amendment 2; `connector-build-plan.md`; this ledger §5.0 |

*Row O-41 added 2026-08-30 from the S-A2A-3 item-0 finding (§3). [SRC].*

| ID | Item | Status | Recorded in |
|---|---|---|---|
| O-42 | **Caller's IM floater shows no End Call and no participant state during a live A2A call** (the callee's does). *Traced 2026-08-30 (viewer + mixer, read-only) and SPLIT:* **(a)** the IM-panel participant/moderation surface is the missing `ChatterBoxSessionAgentListUpdates` — **built as S-A2A-6** (this tree; ENTER pair on Active, LEAVE to the remaining party on Active-record removal, `can_voice_chat:true` by construction — false hangs up the call, `llimview.cpp:4366-4382`); **(b)** the caller's connected/End-Call state waits on `STATUS_JOINED`, which for the outgoing side fires only on the peer's data-channel `"j"` (`llvoicewebrtc.cpp:1042-1049`, `:1339-1352`) — mixer presence, **fixed as M-A2A-1** (mixer `6ee39be`, fence + counters). Both halves live-verification owed on the next call | **CLOSED 2026-08-31**, all three parts, live-verified: (a) S-A2A-6 `2ff8bf57af`; (b) M-A2A-1 `6ee39be` (End Call present, `presence_pushed>=1`); (c) M-A2A-2 `3f4780d` + M-A2A-3 `5e0d637` (callee auto-hangup 05:27:53, 186 ms after the caller's logout). The stock viewer's attach window is documented behaviour accommodated server-side (§3, §4 standing constraint). `backlog_resent` read 2026-08-31 19:03: 1 on both spatial handles at join (M-A2A-3 live on the join path); the A2A during-call read remains owed | this ledger §3 (O-42 CLOSED); `a2a-build-plan.md` §5 |
| O-43 | **The caps wrapper's bare `catch { 500 }` swallows handler exceptions unlogged** (`SimpleStreamHandler.cs:91-101`). Cost a full diagnostic round today: the S-A2A-3.1 `NullReferenceException` surfaced as "admitted, then silence" with no log line anywhere. Should-fix: log the exception at ERROR (path + handler + exception) before setting 500 | **CLOSED 2026-08-31** — `b72afc9fb8` logs the exception at ERROR (`[SIMPLE STREAM HANDLER]`, method + path) before the 500; deployed 19:01 (§5.0, core **Release**). Not yet observed firing; watch item: the ERROR line on the next handler exception | this ledger §3 (four-defects, d), §5.0 |
| O-44 | **Conference (multi-party) voice — ROADMAP, required by the operator.** Not in A2A scope. The substrate is now proven live (non-spatial mixer rooms, invitations, registry admission); what it needs: an n-party registry, server-minted session ids + membership authorization (the XOR trick is 2-party-only), the `"start conference"` ChatSession arm (today a stub), and likely room model (b) — the session-keyed room table — for session-scoped moderation. Tracked-not-blocking alongside the connector tap (O-40) | open, **roadmap** (recorded 2026-08-30) | this ledger §3; `a2a-build-plan.md` §1.2 (room-model fork) |
| O-41 | **A successful voice logout retains the `ViewerSessions` entry.** The service's logout arm (`WebRtcJanusService.cs:236-245`) leaves the mixer room and replies `BuildClosed()` but never calls `VoiceViewerSession.RemoveViewerSession` (`VoiceViewerSession.cs:264`); only the Janus-disconnect hangup path does (`DisconnectViewerSession`, `WebRtcJanusService.cs:199-205`). The entry — and its `AgentMembershipByRegion` row — persists until the client-close capture (`WebRtcVoiceServiceModule.cs:201-243`), so a voice toggle within a login accumulates one table entry per toggle. Pre-existing (predates `d9fa72c351`); the 403'd-logout defect hid it because the arm never ran | **CLOSED 2026-08-31** — `c972136380`: the logout arm calls `DisconnectViewerSession` after `LeaveRoom` (remove AND shut down; bare `RemoveViewerSession` would have orphaned the live Janus session with nothing left to destroy it — trace 2026-08-31). Deployed 19:01 (§5.0) and **live-verified 19:04:52**: on a voice toggle-off both of the agent's mixer handles (spatial 226001844 and the neighbour-region child session 1578726032) removed within seconds — previously retained until client close. Test added (`RemoveViewerSession_DropsRegistryEntryAndMembership`); Janus suite 175/177 (total was 172; the O-20 pair the only failures, unchanged); region-module 146/146 | this ledger §3 (item-0 finding), §5.0 |
| O-45 | **`[JANUS AUDIO BRIDGE]` logs "CreateRoom. YY Room creation inconclusive" at ERROR on a plain `janus` ack, before the creation event arrives.** Log-level defect: the ack is not a failure — the room comes up and the event lands normally. Benign, noise only | **FIXED as a side effect of `3ca74633df`** (requests now await their own completion); **VERIFIED LIVE 2026-09-13**: fresh CreateRoom after a mixer restart returned 'created' with no 'inconclusive' ERROR. *(Was: open, log-level, filed 2026-08-31, observed at the 19:0x runs.)* | this ledger [SRC: live region log 2026-08-31] |
| O-46 | **The mixer join is ungated and `display` is trusted.** Any process with network reach to the Janus client API can attach, join any room (`janus_slvoice.c:1974-2002` — no secret, token, or authorization on the join path), and claim any avatar's UUID as `display` — inheriting that avatar's exclusion column (`apply_visbatch` fan-out by display, `:1354-1375`) and its roster identity. **Pre-existing, not connector-specific** (the viewer path has always worked this way; network isolation of the Janus port is the only gate in the shipped topology); made pointed by the connector programme, whose policy record gates the sim, not the mixer. Remedy: a **join secret** (sim-minted, carried through provision) — a small mixer change. *Nuance (2026-08-31, live check): the Janus gateway's client API requires the `[JanusWebRtcVoice]` `APIToken` (`api_secret`) to CREATE a session, so "ungated" applies to the plugin JOIN once a Janus session exists — not to reaching the API itself. An attacker needs network reach AND the api_secret before the ungated join is exposed. Remedy unchanged.* | **NARROWED, not closed (slice 0.4, 2026-09-15).** The remedy is built on both sides and shipped OFF: the sim mints a capability per join bound to agent + viewer session + room + (epoch, generation) + expiry + one-shot nonce (`[WebRtcVoice] JoinCapabilityEnabled`, default false), and the mixer verifies it in rooms created with `vis_authority` (`JS_JOIN_CAP_REQUIRED`, default 0; with it off every capability is still verified and counted). Design §11 was written for this slice — the feature had no design section before it. *What it narrows:* a holder of `JS_API_SECRET` can still create a session and attach, but can no longer join a declared room as an avatar the sim did not just admit, nor replay a capability, nor use one minted under a superseded authority. *Why it is not closed:* (1) the capability attests what the sim decided, and for a child agent the sim still picks the room from the viewer's own `parcel_local_id` (**O-77**), so a capability can be minted for a room the viewer nominated; (2) undeclared rooms (A2A, static, harness, connector) are never gated; (3) **connector peers and recorder taps mint no capability**, so the requirement cannot be enabled without silencing them — the same peers as **O-88**, which must be designed in one pass with this (design §11.10). *Closing this row* needs the proximity test, connector capabilities, and the requirement actually enabled. | `connector-assessment-20260831.md` §2, §7(c); `nonspatial-phase0-design.md` §11; this ledger |
| O-47 | **Untrusted-HG voice provision refusal — owed to the trusted-HG rollout.** Brief Amendment 2 D4 (DECIDED): trusted-HG visitors get region voice and are recordable/addressable under connector disclosure; untrusted-HG visitors get NO region voice — their provision is refused. Today the voice path has no HG-aware code at all (O-38), so nothing enforces this; the rule is recorded here so the trusted-HG regionserver rollout picks it up. No code in the connector plan | open, **owed to the trusted-HG rollout** (filed 2026-08-31) | `connector-design-brief.md` Amendment 2 D4; this ledger (O-38) |
| O-48 | **The `"local"` provision trusted the viewer's `parcel_local_id`.** `ProvisionVoiceAccountRequest` looked the parcel up by the client-supplied id (`scene.LandChannel.GetLandObject(parcelID)`), ran AllowVoiceChat / ban / restrict against that parcel, and forwarded the same id to the service, which hashes it into the mixer room (`CalcRoomNumber`). `sp.AbsolutePosition` was never used: a client could name another parcel and join its room, and omitting the id skipped the whole block into the `-999` estate room. Vivox/FreeSwitch derive the parcel from avatar position. The visibility matrix has no same-parcel rule by design, so it does not close this | **DEPLOYED + VERIFIED LIVE 2026-09-13** (root login: source=ServerPosition server=2; O-48a child-agent regression fixed `0f19e4e584`, verified: Transylvania child provision source=None, no retry storm). Regionserver build `1.1.388-alpha+7aaab38a03` (WebRtcVoiceRegionModule), deployed ~10:00 CDT, rollback `D:\legiongrid\_backup\regionserver-20260913-0958`. Fix `7191e9b6a1` (2026-09-12): parcel = `GetLandObject(sp.AbsolutePosition.X, .Y)`; every existing check runs against it; with `UseEstateVoiceChan` clear the forwarded `parcel_local_id` is overwritten with the server `LocalID` (an honest viewer sends the same number — no live room renumbers). Client id is a hint: DEBUG `[PARCEL RESOLVE]` always, WARN on mismatch, never refused (O-11). Decision in `ProvisionParcelResolver.cs`; region-module suite 170/170 (167 + 3). O-48a child-agent regression (neighbour-region provisions failed silently, 2 Hz viewer retry) fixed `0f19e4e584`; stock viewer omits parcel_local_id for the estate channel (live 2026-09-13) | audit W-1 (`webrtc-audit-20260909.md` §1); `parcel-voice-semantics.md` §3.1 amendment |
| O-49 | **Moderation mute shares the 32-slot `peer_ctl` table** (never cleared on leave) and is silently dropped when full; a long-lived listener becomes un-moderatable (W-2) | **FIXED IN CODE `f54a116`, deployed 2026-09-12 19:09 CDT** (container start 2026-09-13T00:09:32Z): per-session `GHashTable *mod_muted` set, created in `create_session`, destroyed in `session_free`, **cleared in `leave_room`** next to `excluded`. Every reader and writer is rewired: `is_mod_muted`, `set_mod_muted_locked` (still TRUE iff changed), `apply_mutebatch` REPLACE/ADD/REMOVE, the deferred replay, the `query_session` `mod_muted_entries` (now the set size), and the mix term (checked outside the `peer_ctl` loop). New `peer_ctl_full_drops` counts viewer entries dropped by the full `peer_ctl`. The set is only reachable from inside the plugin, so no unit test covers it; exercised by the live check. **VERIFIED by S3/S6 2026-09-13** (O-73 harness: the re-sent mute lands on a rejoined session within 1 s and an empty-array replace clears it; with `peer_ctl` full at 32 entries + 1 drop, a mute for a 34th source still applies). **VERIFIED LIVE 2026-09-13 16:45 by console mute**: Legion's 226001844 handle mod_muted_entries 1, source's own handles 0, neighbour room untouched | mixer `docs/voice-mute-wiring.md` (Mixer side section); this ledger |
| O-50 | **Ack'd Janus requests have no timeout**; a lost event + `.Result` pins a caps thread permanently; `RequestTime` unused; pending TCSs never faulted on disconnect (W-3) | **DEPLOYED 2026-09-13; O-50 verified live: 0 'timed out after' lines across a mixer stop/start** (shipped in the 08:54 voice deploy, `1.1.386-alpha+30ef218e22`) — slice 3 `3ca74633df`: ack'd requests wait at most `[JanusWebRtcVoice] RequestTimeoutMs` (default 5000) then return a synthetic error (`error.reason` `"timeout"`, WARN `[JANUS SESSION]: request … timed out after … ms`); `DestroySession` and both long-poll exit arms complete pending requests (`"session destroyed"` / `"long poll exited"`). `.Result` call sites unchanged (O-32 stays open, now bounded). Janus suite 183/185 (4 new tests; the O-20 pair the only failures, unchanged) | audit §1 |
| O-51 | `_OutstandingRequests` locked in one method, unlocked in four (W-4) | **DEPLOYED 2026-09-13; O-50 verified live: 0 'timed out after' lines across a mixer stop/start** (shipped in the 08:54 voice deploy, `1.1.386-alpha+30ef218e22`) — slice 3 `3ca74633df`: every access (Add, removal, snapshot, count) under `lock(_OutstandingRequests)`, no await inside it | audit §2 |
| O-52 | A2A `OnClientClosed` handler lacks the `IsChildAgent` guard the service module has; child closes mark a live party gone (W-5) | **DEPLOYED 2026-09-13 (unit-tested; no live step — child closes are silent by design)** (shipped in the 08:54 voice deploy, `1.1.386-alpha+30ef218e22`; carried into the ~10:00 `1.1.388-alpha+7aaab38a03` region module) — slice 4 `afe2df635d`: the region module's `OnClientClosed` delegate looks the presence up in the scene the close fired for and returns on no presence or a child agent (`A2ASessionRegistry.ShouldMarkGone`, the service module's guard); tests `A2AClientCloseGuardTests` | audit §2 |
| O-53 | Failed provisions leak registry entry + Janus session + handle per viewer retry; `IsAgentInRegion` true for an agent in no room; `:232` connect failure NREs (W-6) | **DEPLOYED + VERIFIED LIVE 2026-09-13 (mixer stopped: 'janus unavailable' ERROR + 'removing it' per retry, no NRE)** (shipped in the 08:54 voice deploy, `1.1.386-alpha+30ef218e22`) — slice 4 `afe2df635d`: (a) `WebRtcVoiceServiceModule.ProvisionVoiceAccountRequest` removes a session it created when the provision fails (no map, or no `viewer_session`) and shuts it down; a pre-existing session is left alone. (b) `WebRtcJanusService` checks the connect result and answers `{response:"failed", error:"janus unavailable"}`, destroys a half-built `JanusSession`, and nulls `Room` on both `JoinRoom` failure arms. Tests `ProvisionFailedSessionCleanupTests`, `ProvisionConnectFailureTests`; `ProvisionDispatchTests` first case updated (it asserted the leak). Janus suite 188/190 (O-20 pair), region-module 175/175 | audit §2 |
| O-54 | Rooms and 50 Hz tick threads never destroyed; unbounded since A2A (one per pair) (W-7) | **FIXED IN CODE in the mixer slice-5 commit (`fix(mixer): O-54 empty-room grace destroy…`), deployed 2026-09-13 11:57 CDT**: a non-permanent room empty for `JS_EMPTY_ROOM_GRACE_S` seconds (default 60; 0 disables) is destroyed by the sender thread's once-a-second sweep through the shared room teardown (tick thread joined, stragglers reset, removed from `rooms`), logging INFO `[slvoice] room <id> destroyed after <n>s empty`. The clock starts at creation or on the last leave; a join clears it; a join racing the destroy answers 485, which the sim self-heals (ForgetRoom, re-create). Static-config rooms are exempt. `test_room_lifecycle` 37/37 in the image build. Container start logged `Empty-room grace destroy: … 60 s`; **VERIFIED by S5 2026-09-13** (O-73 harness: R1 emptied by a leave-and-join to R2 left `list` no earlier than the 60 s grace, the log carried `[slvoice] room <R1> destroyed after <n>s empty`, and the next join re-created R1) | audit §2; per-room brief §7 |
| O-55 | Compose publishes admin (live 24225) and WS (8188) on 0.0.0.0; WS unused by the sim (W-8) | **opt-in narrowing; defaults unchanged on upgrade — compatibility rule in docker-notes.** Knobs added in the slice-6m commit (`b96e7b3`, deployed 2026-09-13 18:28 CDT). Its defaults `JS_ADMIN_BIND=127.0.0.1` and `JS_WS_ENABLED=false` changed a live install on rebuild: the regionserver lost `192.168.1.225:24225` and WS disappeared. Hotfix 6m-1 (`fix(mixer): JS_ADMIN_BIND/JS_WS_ENABLED defaults reproduce pre-6m behaviour…`, deployed 2026-09-13 18:55 CDT, container start 23:55:35Z, image `b13f834e`) restores the pre-6m defaults. Unset `JS_ADMIN_BIND` publishes admin on all interfaces (compose omits the host IP), and the entrypoint prints `INFO: admin API bind=0.0.0.0` plus the WARNING "reachable on all interfaces; protected by JS_ADMIN_SECRET only …". `JS_WS_ENABLED` defaults to `true` (loaded and published; `false` unloads the transport). The `docker-compose.ws.yml` overlay was removed. To narrow, an operator sets `JS_ADMIN_BIND=<the address in JanusGatewayAdminURI>` and/or `JS_WS_ENABLED=false`. Live after deploy with the `.env` unchanged (neither knob set): `docker port` shows 24225 and 8188 on `0.0.0.0` and `[::]`, and the Admin API answered `ping` at `http://192.168.1.225:24225/voiceAdmin`. The rule, knob register, "Behaviour changes on upgrade" and "One-time migrations" are in `docs/docker-notes.md`. `entrypoint_test.sh` 41/41 | audit §2 (W-8) |
| O-56 | `hangup_media` does not leave the room (diverges from audiobridge); dead PC holds roster row + capacity slot until session destroy (W-9) | **FIXED IN CODE in the mixer slice-5 commit (`fix(mixer): O-54 empty-room grace destroy…`), deployed 2026-09-13 11:57 CDT**: `hangup_media` now calls `leave_room` (announces "l", drops the roster row and the `SLV_MAX_MIX` slot, resets room-scoped state, starts the grace clock if last) and frees media with jitter-buffer priming and the RTP liveness stamp reset; supersedes the M-A2A-2 "hangup never leaves" decision. The sim's later leave gets NOT_JOINED and `destroy_session` is a no-op. Unit-tested; **VERIFIED by S2/S8 2026-09-13** (O-73 harness: a PeerConnection closed with no leave loses its row and its slot within 5 s; with a same-display rejoin overlapping the live handle (the duplicate-display WARN logged), the old PeerConnection's death leaves one row, the new handle, within 5 s) | audit §2 |
| O-57 | Shipped `os-webrtc-janus.ini` lacks `StunServers` (stock viewers cannot `CreatePeerConnection`), `AllowNpcVoice`, connector records — beyond O-21's five keys (W-15) | open — slice 6, folds into O-21 | audit §2 |
| O-58 | `VoiceViewerSession.VoiceServiceSessionId` throws; `TryGetViewerSessionByVSSessionId` enumerates it (W-10) | open, low — live on the O-39 path | audit §3 |
| O-59 | Long-poll exit asymmetry: GETERROR default arm tears down all sessions on a Janus blip; exception arm exits without `OnDisconnect` (W-11) | open, low | audit §3 |
| O-60 | Failed join leaves `Room` set; `LeaveRoom` always false; singular `candidate` dropped; signalling NRE on null `Session`; `ChatSessionRequest` no child-agent check; service `_ViewerSession` never reconnects; `_roomCreateLocks` unbounded (W-12/13/14) | **DEPLOYED + VERIFIED LIVE 2026-09-13** (janus list rooms answers after a mixer restart without a region restart; build `1.1.392-alpha+d347102272` (voice + NPC module, core stays 1.1.378), deployed 2026-09-13 ~15:45 CDT, rollback `D:\legiongrid\_backup\regionserver-20260913-1541`). Fixed in `1b88989e9f` (items a–e + service-session reconnect): (a) JoinRoom failure already nulls `Room` on both arms (slice 4, `ProvisionConnectFailureTests`); no region-side equivalent exists. (b) `JanusRoom.LeaveRoom` returns true on "left"/success and on NOT_JOINED 487 (the normal answer after an O-56 hangup), false + WARN otherwise. (c) singular `candidate` passed through `TrickleCandidates` as a one-element list; null `Session` answers `{response:error}` instead of an NRE. (d) `ChatSessionRequest` refuses a child agent (403 + WARN). (e) `_roomCreateLocks`/`_knownRooms` evict room numbers idle 10 min (swept at most once a minute). Service session: `OnDisconnect` marks it disconnected; console commands go through `EnsureServiceSessionAsync` and retry once on a forced reconnect, INFO `[JANUS WEBRTC SERVICE] service session reconnected`. Tests `JanusHygieneTests`. Vivox probe line (`voice_server_type is not 'webrtc'`): downgraded to DEBUG, verified 2026-09-13 | audit §3 |
| O-61 | Connector peers have no reconnect; rely on compose restart (W-16) | recorded, **not a defect** | audit §3 |
| O-62 | **Injected (connector) audio is not spatialised** — no distance fade, the NPC's stream is position-less in the mixer [DOC: operator, S-CON-6 live run 2026-09-01] | open — ground-truth pass owed | S-CON-6 notes; audit §5 |
| O-63 | **Connector NPC UUID changes on every regionserver restart**, forcing a peer `DISPLAY` re-edit [DOC: operator, 2026-09-01]. Consequence (noted 2026-09-12, again 2026-09-13): a connector peer still joined under a pre-restart NPC UUID is not the NPC the sim moderation-muted at registration, so a stale injector plays unmuted and undisclosed until its container is restarted. The "stop injector/recorder before a restart" advice is obsolete from `feat(voice): O-63 deterministic connector-NPC identity (UUIDv5) + fixed-id CreateNPC; console 'voice moderation mute'` — DISPLAY is stable | **DEPLOYED 2026-09-13** (derived id logged at registration; stability confirmed on the next region restart; build `1.1.392-alpha+d347102272` (voice + NPC module, core stays 1.1.378), deployed 2026-09-13 ~15:45 CDT, rollback `D:\legiongrid\_backup\regionserver-20260913-1541`). Fixed in `d347102272` (UUIDv5 identity + fixed-id CreateNPC; console mute added): `ConnectorIdentity.DeriveAgentId` = RFC 4122 v5 under the Legion namespace of `"{gridId}/{regionName}/{recordName}"` (grid id = normalised GatekeeperURI; the tree has no grid UUID); `VoiceConnectorModule` creates the NPC through the existing fixed-id `CreateNPC(..., agentID, ...)` overload, which now refuses (UUID.Zero + ERROR) when a presence with that id is already in the scene; the registration line prints the id with `identity=derived`. Console `voice moderation mute <agent-uuid-or-name> [parcel-local-id]` writes the same store entry as the viewer's moderation mute (logged "moderated by console"). Tests `ConnectorIdentityTests`, `VoiceModerationMuteConsoleTests` |  S-CON-6 notes; audit §5, §8 |
| O-64 | **SLData overwrites geometry**: `incoming_data` assigns `last_data` as a whole struct (`:3078`); a message without `sp`/`lp` flattens the mix until the next geometry update. Live read 2026-09-12: stock viewer sends SLData only on change and always with `sp,sh,lp,lh`, so the trigger is a separately-sent `m`/`ug` map (volume slider or mute while stationary) and it heals on the next camera/avatar move | **FIXED IN CODE `f54a116`, deployed 2026-09-12 19:09 CDT**: `slv_sldata_merge` (`src/sldata.c`) copies only the members whose bit the message carried and ORs the bits into the persistent mask. `last_data_fields_seen` is now the persistent union; the new `last_msg_fields_seen` is the latest message only. `test_sldata` 86/86 (3 merge cases, red first: 12 failures against the old replace). **VERIFIED LIVE 2026-09-13** (last_msg_fields_seen "ug" with sp/lp retained); **VERIFIED by S7 2026-09-13** (O-73 harness) | mixer `docs/sldata-extensions.md` (Persistent state across messages); this ledger |
| O-65 | `docker-entrypoint.sh:33,103` starts with blank `JS_API_SECRET`/`JS_ADMIN_SECRET` | **FIXED IN CODE in the mixer slice-6m commit (`fix(mixer): O-55 admin/WS binds, O-65 fail-closed secrets…`), deployed 2026-09-13 18:28 CDT**: before writing any config the entrypoint exits 1 with `[entrypoint] FATAL: <JS_API_SECRET and/or JS_ADMIN_SECRET> empty; refusing to start` when either is empty or whitespace. (An empty `JS_ADMIN_SECRET` previously left the stock template's `admin_secret = "janusoverlord"` in force.) `ALLOW_INSECURE_DEV=true` starts anyway with a WARNING, documented dev-only in `docs/docker-notes.md`. The live `.env` has both secrets and the container started. `tests/entrypoint_test.sh` 34/34 in Git Bash and in the image build (dash/mawk), which also covers the nat_1_1 RFC 1918 guard and `JS_NAT_EXTRA_IPS` | audit §8 |
| O-66 | Full offer/answer SDP logged at INFO per join (`:1756`, `:1822`) | **FIXED IN CODE in the mixer slice-6m commit (`fix(mixer): O-55 admin/WS binds, O-65 fail-closed secrets, O-66 SDP at VERB…`), deployed 2026-09-13 18:28 CDT**: `janus_slvoice_negotiate` logs the offer and answer dumps at `LOG_VERB` (now `:1952`, `:2027`), building them only when `janus_log_level >= LOG_VERB`. Both pass through `slv_sdp_redact` (`src/sdp_redact.h`), which replaces the `a=ice-ufrag` / `a=ice-pwd` values and the `a=fingerprint` value (hash name kept) with `<redacted>`. The one per-join INFO line is `Answer sent: audio Opus pt=<pt> ptime=20 maxptime=20; m=application answered=<YES|NO>` (`:2021`). `test_sdp_redact` 18/18 in `make test`. Finding: the 111 offer dumps in the pre-deploy container log held no `a=ice-ufrag`, `a=ice-pwd`, `a=fingerprint` or `a=candidate` line, because the Janus core anonymises the offer (`janus_sdp_anonymize`, `janus.c:1754`) before the plugin sees it. The live exposure was SDP volume at INFO; the redaction guards any later path that logs an un-anonymised SDP | audit §8 |
| O-67 | `room_start` result ignored (`:541`); init thread failure leaves `initialized=0` so destroy refuses cleanup (`:981-1003`) | **FIXED IN CODE in the mixer slice-5 commit (`fix(mixer): O-54 empty-room grace destroy…`), deployed 2026-09-13 11:57 CDT**: `room_create` frees the room and returns NULL when `room_start` fails (the create request answers 499, a static room is skipped with ERROR); `janus_slvoice_init` runs the shared `janus_slvoice_teardown()` (also `destroy()`'s body) on either thread-creation failure, so nothing stays live with `initialized=0`. The teardown is unit-tested; the failure branches have no test seam (review only) | audit §8 |
| O-68 | Destroy arm nulls `room` without clearing room-scoped state (`:1955` vs `leave_room:600`) | **FIXED IN CODE in the mixer slice-5 commit (`fix(mixer): O-54 empty-room grace destroy…`), deployed 2026-09-13 11:57 CDT**: `janus_slvoice_reset_room_state_locked` clears `excluded`, `mod_muted`, the cull hysteresis, the geometry snapshot, `sp/sh/lp/lh` and the `j/l` bits, `last_msg_fields` and `backlog_confirmed`; `peer_ctl`, echo and the viewer's own `m/ug/echo` are kept. Called from `leave_room` and from the shared room teardown used by the destroy arm (was `:1955`, which only nulled `room`) and the grace sweep. `test_room_lifecycle`. **VERIFIED by S3 2026-09-13** (O-73 harness: a rejoined session reads 0 mod-mute and 0 excluded before any batch) | audit §8 |
| O-69 | 20 ms packetisation assumed (`:2590-2604`); answer advertises `minptime=10`, no `ptime`/`maxptime` | **FIXED IN CODE `f54a116`, deployed 2026-09-12 19:09 CDT**: `negotiate` adds `a=ptime:20` and `a=maxptime:20` to the answer's audio m-line next to the Opus fmtp; nothing else in negotiate changed. **VERIFIED LIVE 2026-09-12** (a=ptime:20 / a=maxptime:20 per handle) | this ledger |
| O-70 | `deferred.c` REPLACE frees before alloc; OOM empties instead of "unchanged" (`:134-139`) | **FIXED IN CODE `f54a116`, deployed 2026-09-12 19:09 CDT**: allocate and fill `fresh` first, free the old column only after; OOM returns with the channel unchanged. `test_deferred` 35/35 (no OOM injection in the suite) | this ledger |
| O-71 | Source header/README say Phase 1B / mixing out of scope; version 0.9.0 | **FIXED IN CODE in the mixer slice-8b commit (`docs(mixer): O-71 resync header/README/docs to the shipped plugin, version 1.0.0; ci: …`), deployed 2026-09-14 07:03 CDT** (container start 12:03:50Z, image `bccfd7ef`; log `Legion SLVoice mixer initialized! (API v106, 1.0.0)`). The plugin header comment and `JANUS_SLVOICE_DESCRIPTION` now describe what ships: per-listener N-minus-one spatial mixing on a per-room tick, SLData geometry and per-source mute/gain, `peer_ctl_batch` visibility and moderation, grace destroy. `JANUS_SLVOICE_VERSION` 100 / `"1.0.0"`. Stale inline comments fixed (room struct, sender batch, RTCP, `incoming_data`). Also resynced: README (status, CI/release, layout, docs list), `docs/sldata-extensions.md` (implemented vs not), `docs/phase1-bringup.md` (historical banner, scope), `docs/protocol-compat.md` (status), both plugin jcfg templates (removed the unread `name`/`events` keys). `docs/voice/*` mirrors left alone: their 0.9.0 mentions are dated history. Full local harness 8/8 against 1.0.0 | audit §8 |
| O-72 | **Any refused `"local"` provision causes a viewer retry storm**: LL/Firestorm treats every non-2xx except 401/409 as `SESSION_EXIT` and restarts immediately with no backoff (`llvoicewebrtc.cpp` ~`:2913-2927`, `:3182-3201`); seen live 2026-09-13 at ~2 Hz for both the O-48a regression and a stopped mixer. Sim-side throttle per agent, or answer refusals with a code the viewer backs off on | **DEPLOYED 2026-09-13** (RefusalCacheSeconds=5; live check pending — next mixer outage should show one 'refused again (cached, N retries suppressed)' line per window instead of a 2 Hz stream; build `1.1.392-alpha+d347102272` (voice + NPC module, core stays 1.1.378), deployed 2026-09-13 ~15:45 CDT, rollback `D:\legiongrid\_backup\regionserver-20260913-1541`). Fixed in `1b88989e9f` (negative cache): `ProvisionRefusalCache` per (agent, region), `[WebRtcVoice] RefusalCacheSeconds` (default 5, 0 disables). Region module: a 403/404/501 refusal of an admitted "local" provision is replayed (same status and body) before the estate/parcel checks; service module: a failed new-session provision's failure map is replayed without creating a session or calling Janus. A success or a logout clears the entry; one INFO line per (agent, region) per window: `… refused again (cached, N retries suppressed)`. Answering 409 would NOT make the viewer back off: `HTTP_CONFLICT` only sets `mCurrentStatus = ERROR_CHANNEL_FULL` (`llvoicewebrtc.cpp:2917`) and takes the same `SESSION_EXIT` —> `START_SESSION` path; the status reaches `OnConnectionFailure` only from `VOICE_STATE_SESSION_RETRY` (`:3151`), which an HTTP error never enters. Tests `ProvisionRefusalCacheTests`, `ProvisionRefusalCacheServiceTests` | audit addendum |
| O-73 | Two-peer integration harness (`tests/integration/`, S1–S8) is the verification gate for mixer slices | **DONE** in the mixer commit `test(mixer): O-73 two-peer integration harness S1–S8 …` (2026-09-13): aiortc peers with a real Opus tone and viewer-shaped SLData, oracle = Admin API `handle_info` + `list`/`listparticipants`; first full run against the live mixer **8/8 PASS**, grace 60 s, 201.6 s wall-clock (S4 restarted the mixer). S8 was then reordered to join before the crash, because crash-then-rejoin never overlapped on a local mixer (no duplicate-display WARN); rerun PASS with the WARN logged | `tests/integration/README.md`; this ledger || O-46 (amend) | Add: sim-issued join capability (avatar, room, generation, expiry) as defence-in-depth against a leaked `JS_API_SECRET`; the viewer never reaches the Janus API, so not critical | open — status unchanged | audit §8 |
| O-74 | **Root client close leaves neighbour-region voice sessions alive** (viewer sends no per-region logout on Quit); ghost participant holds a mixer slot and blocks grace-destroy. Live 2026-09-13 12:06 (build 1.1.388): Aleric quit; the root close in Ebony captured only Ebony's session, the Transylvania close was a child close (ignored by the guard), and the Transylvania handle stayed in room 1578726032 (`datachannel_open` false) with the sim's long-poll keeping it alive | **DEPLOYED 2026-09-13 13:10 + VERIFIED LIVE** (Aleric Quit: no ghost handle, both rooms grace-destroyed; build `1.1.390-alpha+e0f36ea6db`, rollback `D:\legiongrid\_backup\regionserver-20260913-1309`). Fixed in `e0f36ea6db`: `Event_OnClientClosed` keeps its child guard; a root close captures the dying login's sessions in every region (`VoiceViewerSession.CaptureSessionsForCloseAllRegions`, token-strict), skipping regions where the agent is root right now; DEBUG per region plus `root close of {agent} in {region} captured {n} voice session(s) across {k} region(s)`. Teleport safe by ordering: the source is flagged child (`EntityTransferModule` :1194/:1855) before the destination becomes root and closed only after `MakeChildAgent` (:1232 —> :1260). Tests `RootCloseAllRegionsTests` (5) | this ledger |
| O-75 | **Mixer: a participant whose PC never reaches `webrtc_up` within N s of join is never reaped** (the sim's long-poll keeps the Janus session alive) — belt-and-braces reap in the room tick | **FIXED IN CODE 3618e9a, released 1.1.0 ce26de4, VERIFIED by S10 + live reap line.** *Deploys:* 3618e9a went live 2026-09-14 07:39 CDT (image `0a3624b9`, still reporting 1.0.0); release 1.1.0 went live 08:41 CDT (image `7936e030`), and `/info` reports `janus.plugin.slvoice version=110 version_string=1.1.0`. *Local:* a full harness run against 1.1.0 passed 9/9 in 244.2 s, S4 and S10 included; the live container log carries `[slvoice] 5bc7b9ae-0bdc-4afa-a7a8-3e179293905b reaped from room 909332609: no media 30s after join` (S10's test peer). *CI:* [34851327802](https://github.com/JohnLegionH/legion-voice-mixer/actions/runs/34851327802) ran S10 green with a 10 s timeout. *Session state:* the join arm stamps `join_ts` and re-derives `media_seen`; `setup_media` sets `media_seen`. *Reap:* about once a second, the room tick collects participants still at `media_seen == 0` `JS_JOIN_MEDIA_TIMEOUT_S` (default 30, 0 disables; exported by the entrypoint) after `join_ts`. After releasing `room->mutex` it re-checks each under the session lock and takes it down through `janus_slvoice_media_gone`, the body `hangup_media` now shares: `leave_room` with its "l", slot, `reset_room_state` and grace clock, then echo stop and `media_free`. It logs INFO `[slvoice] <display> reaped from room <id>: no media <n>s after join`. *Exemptions:* a participant that had media and lost it keeps `media_seen == 1` (the O-56 path); the handle survives, so a rejoin works. *Local run:* harness 9/9, including S10. G joined with a candidate-less offer and no PeerConnection, was listed, was gone after the timeout (log `no media 30s after join`), and G's display rejoined with media. *Compatibility:* the default changes behaviour on upgrade by design (docker-notes knob register and "Behaviour changes on upgrade", `docs/RELEASES.md`). *History:* filed 2026-09-13 from O-74 live evidence; an earlier report that this was committed (2026-09-14 06:19) was not borne out, so the work was redone here. | this ledger |
| O-76 | CI runs unit suites + integration harness on every push/PR | **DONE: first green run 2026-09-14,** [ci 34851327802](https://github.com/JohnLegionH/legion-voice-mixer/actions/runs/34851327802) (push, `ce26de4`). The build step ran the unit suites and `entrypoint_test` 43/43; the harness got `8 passed, 0 failed, 1 skipped in 25.4 s (grace 10 s, join timeout 10 s)`, with S4 skipped and S10 passing. The workflow was added in `bed52d5` (slice 8b). Its first run, [34841769396](https://github.com/JohnLegionH/legion-voice-mixer/actions/runs/34841769396), failed installing the harness requirements because pip tried to build `av` 14.4.0 from source; `3618e9a` fixed that with `pip install --only-binary=:all:` and added S10. `.github/workflows/ci.yml` runs on push to `main` and on every pull request. It builds the image (the Dockerfile runs the C unit suites and `tests/entrypoint_test.sh`), starts it through `docker-compose.yml` with a job-written `.env` (explicit knobs, random secrets; never a developer `.env`), and runs `python -m tests.integration.run --grace 10 --join-timeout 10 --no-restart` (S1–S3, S5–S8, S10; `JS_EMPTY_ROOM_GRACE_S=10` and `JS_JOIN_MEDIA_TIMEOUT_S=10` set in the job's `.env`). S4 restarts the service under test and stays a local pre-deploy check. Any FAIL fails the job. `release.yml` (tags) unchanged | this ledger |
| O-77 | **Child-agent provision trusts the viewer's `parcel_local_id`**: for a child agent, the viewer-supplied `parcel_local_id` is used as the parcel for the `AllowVoiceChat` and ban/restrict checks. It is also forwarded into the room hash. Nothing tests it for proximity against the agent's server-relayed position (`WebRtcVoiceRegionModule.cs`, `ParcelSource.ClientHint` path: `:675`, `:676`, `:724` comment; set at `ProvisionParcelResolver.cs:53`). Deliberate, not a regression; narrows the O-48 class but does not close it | open | spec coverage 2026-09-14 (SC-29, SC-32) |
| O-78 | **Mix-enforced permissions fail open on a fresh deployment**: `VisibilityFeederEnabled` and `VisibilityEmitEnabled` both default false (`WebRtcVoiceRegionModule.cs:116`, `:118`). Nothing in the mixer acts on visibility-batch staleness: the last-batch age is only reported (mixer `src/janus_slvoice.c:1512` at mixer `ce792a7`; corrected 2026-09-15, this row read `:1475`, a comment in the `room_participants` block) | **FIXED IN CODE `3283c939f1` (sim defaults); not deployed.** *Slice 0.2, sim half, 2026-09-15:* arming for every listener (empty columns included), room epochs, generations, 1 s heartbeats and acting on the mixer's reply are built behind `[WebRtcVoice] VisibilityArmingEnabled`, default **false**, so nothing changes on the wire until it is turned on (golden-file tested). Connector/recorder arming is deferred as O-88. *Slice 0.3, mixer half, 2026-09-15 (mixer `4492dbc`):* keying found per session (sets on each session, fanned out by display); now also one arming record per room per avatar, epoch adoption with takeover, `policy_generation` ordering, `base` checks, `peer_ctl_heartbeat`, `vis_authority` rooms, the four-row decision table and pair rule behind `JS_VIS_FAIL_CLOSED` (default **0**, shadow mode), `JS_VIS_STALE_MS` 8000 (clamped to 7250), `would_silence_*` counters, `vis_protocol` 2 and `mixer_instance` in every reply (design amendment "slice 0.3, mixer half"). *Mixer deployed 2026-09-15 10:06 CDT, image `115663b3`, fail-closed off (rollback `legion-voice-mixer:rollback-pre-03` = `c7b57f56`):* generated config byte-identical to the pre-0.3 image (golden), board unchanged (exit 2), live rooms re-created `vis_authority=false`, harness S1–S14 13/13 + S15 PASS live; knob-on only on scratch containers: S1–S3, S6–S8, S11, S12, S16–S20 PASS, S21 PASS against the old image, and the real 0.2 sender/sink/authority armed, heartbeated and detected a restart's `mixer_instance` change (`VisibilityArmingLiveMixerTests`, explicit). *Deployed 2026-09-15 08:47 CDT (voice-only, `1.1.412-alpha+b0da9f64c9`), knob off:* all four post-start checks PASS on the 08:54 start (loaded versions; the same fallback-room and feeder-started lines as the 09-14 16:17 start; zero arming, authority, heartbeat or `vis_authority` lines; John's voice join logged the knob-off `skipping pending re-send` line). Knob-on arming and heartbeats were unit-tested only at 0.2; slice 0.3 proved them on the wire against a scratch mixer (above), never on live audio, which stays for the 0.6 shadow soak. *Fixed:* both keys now default true (`WebRtcVoiceRegionModule.DefaultVisibilityFeederEnabled` / `DefaultVisibilityEmitEnabled`, read through `ReadVisibilityFeederEnabled` / `ReadVisibilityEmitEnabled`), and the shipped `os-webrtc-janus.ini` and the example set both to true. *Upgrade consequence:* an install that never set them now runs the feeder and emits `peer_ctl_batch` to the Janus admin API; with `JanusGatewayAdminURI`/`AdminAPIToken` absent it runs matrix-only and WARNs. Set both false to keep the old behaviour. *Still open in effect:* both halves are built but both knobs are off on Legion Grid, so a listener still hears every source until its first batch arrives and staleness is only counted (SC-31, SC-33). Closing it is enabling `VisibilityArmingEnabled`, then `JS_VIS_FAIL_CLOSED`, gated on O-88 and the 0.6 soak (design §6.4). *Tests:* `VisibilityDefaultsTests` 4/4 | spec coverage 2026-09-14 (SC-31); fix V-1 |
| O-79 | **`JanusSession.TrickleCandidates` drops the viewer's candidates**: it ignores its `pCandidates` argument and sends `new TrickleReq(pVSession)` (`JanusSession.cs:200`, `:204`), the end-of-candidates constructor (`JanusMessages.cs:329`–`:333`, `"completed", true`). Only the candidates in the initial SDP reach Janus. Check this against the parked off-LAN ICE item before spending effort on `JS_NAT_EXTRA_IPS` | **FIXED IN CODE tranq-ais `975d2ff5ff`, DEPLOYED 2026-09-14 16:13 CDT, live-verified 16:18 on LAN (`handle_info` lists trickled remote candidates).** *Fix:* `TrickleCandidates` sends `{"janus":"trickle","candidates":[...]}` with the candidates it is given, through a shared `SendTrickle` helper. `TrickleCompleted` still sends `{"candidate":{"completed":true}}`. `TrickleReq(pVSession, pCandidates)` no longer adds `viewer_session`, which is not part of the Janus trickle API. *In practice:* the live region log of 2026-09-13 has 444 `VoiceSignalingRequest: <n> candidates` requests (6 to 28 each) and 456 `candidate completed`, so stock viewers do trickle, and every batch was turned into an end-of-candidates marker. *Tests:* `Signalling_Candidates_AreForwardedToJanus`, `Signalling_Completed_SendsTheEndOfCandidatesMarker`, and the singular-candidate test now checks the forwarded content. `WebRtcJanusService.Tests` 213/215; the two `VisibilityBatchSenderTests` failures predate this change. *Off-LAN ICE:* O-79 is not the primary cause, and fixing it is not expected to restore off-LAN voice on its own. (1) The mixer's `nat_1_1_mapping` holds only 192.168.1.225: `JS_PUBLIC_HOST=legiongrid.ddns.net` resolves to the LAN address inside the container, and the entrypoint WARNs "off-LAN viewers will fail ICE". An off-LAN viewer therefore gets no Janus candidate it can reach, which is enough on its own for the failure. (2) O-79 removed the only remaining path, Janus checking toward the viewer's server-reflexive candidate. With the fix Janus learns that candidate, but its checks arrive at the viewer from the router's public address, to which the viewer has never sent. A NAT with address- or port-dependent filtering drops them, and an endpoint-independent NAT still needs the RTP range reachable back through the router. Nothing on hand shows which NAT the off-LAN testers were behind, so O-79 can at most contribute for some NAT types. An off-LAN fix needs a public candidate regardless (`JS_NAT_EXTRA_IPS=<router public IPv4>` and UDP 10000-10200 forwarded). *Deployed:* 2026-09-14 16:13 CDT with the servers down: `WebRtcJanusService` and `WebRtcVoiceRegionModule`, dll+pdb, from `968640ed04` (Release `1.1.406-alpha+968640ed04`). §8a showed only those two assemblies changed. The second carries V-1's defaults, which the live `OpenSim.ini` sets explicitly, so they change nothing live. Record `D:\legiongrid\_ops\handoffs\DEPLOY-voice-20260914.md`; rollback `D:\legiongrid\_backup\regionserver-20260914-1612`. *Live evidence (grid started 16:17; Legion Hienrichs in Ebony on voice, Firestorm, LAN):* the region log reads `VoiceSignalingRequest: 14 candidates` at 16:18:33.198, then `VoiceSignalingRequest: candidate completed` at 16:18:33.279. Janus admin `handle_info` for her handle in Ebony's room 226001844 (session 6870204629941962, handle 8226132611963417) lists five remote candidates: four trickled ones, `192.168.1.225 60000 typ host`, `172.18.128.1 60000 typ host`, `172.22.0.1 60000 typ host` and `174.82.163.190 60000 typ srflx raddr 192.168.1.225 rport 60000`, plus the peer-reflexive `172.23.0.1 54778 typ prflx`. Her remote SDP has no `a=candidate` line, so the four arrived by trickle, not in the offer; before the fix that trickle reached Janus only as an end-of-candidates marker. Flags `trickle` and `all-trickles` are true, ICE is `ready`, and the selected pair is `192.168.1.225:10089 [prflx,udp] <-> 172.23.0.1:54778 [prflx,udp]`. Her child-agent handle in Transylvania's room 1578726032 lists the same four candidates on port 60001. *Still open:* the viewer's batch held 14 candidates but Janus keeps 4, and nothing at the current log level shows which 10 it dropped or why. The check ran on the LAN only, so off-LAN ICE remains unverified and the `JS_NAT_EXTRA_IPS` requirement above still stands | spec coverage 2026-09-14 (SC-135); fix V-5 |
| O-80 | **Mixer ignores a room's `spatial_audio` flag**: the flag is stored (mixer `src/janus_slvoice.c:590`), but the tick never reads it. `git grep -n -E 'spatial_audio' -- src` finds only the field, the set, config, create and list output. Avatar-to-avatar calls therefore still get the distance cull (`:3144`) | **FIXED IN CODE `34935a2`, deployed 2026-09-14 14:29 CDT** (local image `14ccf97d`, still reporting 1.1.0; rollback `legion-voice-mixer:rollback-pre-v2` = `7936e030`). *Fix:* the tick's per-pair spatial stage is now `janus_slvoice_spatial_pair_locked`. For a room created with `spatial_audio` false it returns the flat gain before the cull, falloff and pan, so no hysteresis slot is written. Room create, dynamic and static, logs `spatial_audio=true\|false`. *Upgrade:* a room created without `spatial_audio=true` is now flat. The sim sets it for "local" rooms and clears it for "multiagent" rooms; static jcfg rooms and harness rooms omit it. *Tests:* `test_room_lifecycle` 42/42, including 5 new checks: a far and an in-range source in a spatial room (culled; attenuated and panned) and in a non-spatial room (flat, no hysteresis write). *Harness:* 9/9 PASS in 244.0 s against the deployed image. *Live:* the log carries 12 `Created room … spatial_audio=false (flat mix: no distance cull, falloff or pan)` lines, all harness rooms. No avatar-to-avatar call has run since the deploy, and the per-tick skip itself logs nothing, so the live skip is not yet observed | spec coverage 2026-09-14 (SC-19); fix V-2 |
| O-81 | **10 ms ingest leaves stale samples in the mix**: the SDP answer offers `minptime=10` (mixer `src/janus_slvoice.c:2043`), but the mix always reads a full 20 ms from `decbuf` (`:3175`, `SLV_FRAME_TOTAL`); a decoded 10 ms frame fills only half (`:2875` `dec_samples`). A viewer sending 10 ms packets gets stale samples in the second half of every frame. Either stop advertising `minptime=10` or make the read match the negotiated ptime | **FIXED IN CODE `15f52cb`, deployed 2026-09-14 14:29 CDT** (same image as O-80). *Choice:* honour the packet duration, not drop `minptime=10`. `minptime` is advisory, so dropping it does not stop a 10 ms sender. The fmtp is the viewer's own mangle, which SC-136 records as honoured, and a 20 ms stream still decodes once per tick. *Fix:* `slv_mix_fill_frame` (`src/mixer/mix.c`) decodes up to `SLV_FILL_MAX_CHUNKS` (8) jitter-buffer packets until the 960-sample frame is full. A lost packet is concealed for the last packet's duration, capped at what the frame still needs; a tail the source did not fill is zeroed. *Tests:* `test_mix` 46/46, adding 10 ms (two packets, no stale sample), 20 ms (one packet), partial (zeroed tail), empty and decode-error cases. *Harness:* 9/9 PASS. No 10 ms sender was exercised live | spec coverage 2026-09-14 (SC-60); fix V-2 |
| O-82 | **Encode-skip drops quiet but audible mixes**: the mixed output is compared against a fixed RMS of 0.02 (mixer `src/janus_slvoice.c:3200`, `:147` `SLV_VAD_RMS`), and a mix at or below it is not encoded or sent (`:2922`). A mix that is audible but quiet, such as one distant attenuated talker, is skipped entirely, so the threshold works against distance attenuation | **FIXED IN CODE `36eae88`, deployed 2026-09-14 14:29 CDT** (same image as O-80). *Fix:* encode-skip now skips only when nothing was summed or every sample is exactly zero (`slv_mix_encode_skip`); there is no level floor. `SLV_VAD_RMS` keeps its VAD role. *Why no floor:* the default falloff is gain = ((60 − d)/50)², and a centred constant-power pan scales each channel by 1/√2, so a talker at RMS r gives frame RMS r · gain / √2. The old 0.02 floor skipped r = 0.1 once gain ≤ 0.283, i.e. beyond d = 60 − 50·√0.283 = 33.4 m. At 59.9 m the same mix is ~2.8e-7 RMS, and hysteresis keeps the pair audible up to the cutoff, so no positive floor sits below the quietest audible mix. Opus DTX (`enc_len <= 2`) still suppresses near-silent frames on the wire. *Tests:* `test_mix` adds the 59 m quiet-mix case (the old floor skips it; the new decision encodes it), plus nothing-summed and exact-zero cases. *Harness:* 9/9 PASS. *Cost:* the encode now runs for every listener with an active in-range talker, however quiet (SC-64) | spec coverage 2026-09-14 (SC-63); fix V-2 |
| O-83 | **V-2 regression: a room created without `spatial_audio` became a flat mix.** O-80 (`34935a2`) read the flag with `json_is_true`, and the static-room loader required `true`/`yes`, so static jcfg rooms and the integration harness's rooms lost spatialisation on upgrade. Before O-80 every room was spatialised | **FIXED IN CODE `fc48ea6`, deployed 2026-09-14 14:55 CDT (V-3, image `8f423e86`), verified by S12 (V-4).** *Fix:* only an explicit JSON `false` (jcfg `false`/`no`) gives a flat mix; an absent key stays spatial; room create logs `(key absent: default)`. *Tests:* `test_room_lifecycle` checks absent, true, false, string and null (JSON) and absent, true, yes, false and no (jcfg). *Live:* after the V-3 deploy, all 13 room-create lines read `spatial_audio=true (key absent: default)`. *Live, sim-created rooms (2026-09-14 16:17 grid start, image `6c443c7d`):* `Created room 226001844 (c44606b1-43e1-45fb-8ae8-201545dc2f6a/local/-999/) spatial_audio=true` (Ebony) and `Created room 1578726032 (0e99ab97-d710-4714-9230-ddd50b722000/local/-999/) spatial_audio=true` (Transylvania). The sim sends the key explicitly for local rooms, so there is no `(key absent: default)` suffix. Legion Hienrichs joined 226001844 at 16:18 (`Participant 8323321833400442 (4fbdfd2a-…) joined room 226001844`), so her live session runs in a spatial room. *Harness:* S12 passes on V-4 and fails on the V-2 image, which is flat: with the source 10 m to the side, `last_mix_rms_l == last_mix_rms_r` = 0.1483. *Affected window:* rooms without the key were flat from 14:29 to 14:55 CDT | V-2 review; fix V-3 |
| O-84 | **SC-87: a voice dot lit for sources the listener could not hear.** The power batch carried each source's own level to every listener that did not exclude it. A source distance-culled or moderation-muted for a listener still lit that listener's dot | **FIXED IN CODE `4fbfaf4`, deployed 2026-09-14 14:55 CDT (V-3), verified by S11 (V-4).** *Fix:* `janus_slvoice_dot_batch_for_listener_locked` builds each listener's batch from the state its mix uses. A source moderation-muted for the listener, muted by it, or culled for it by the last tick (a cull latch whose `seen_tick` is the current `tick_seq`) reports `{p:0, v:false}`; an excluded source is omitted; when none applies the listener gets the shared batch. *Tests:* `test_room_lifecycle` checks moderation-muted, culled (plus a stale latch), personally muted and excluded, each on its own. *Harness:* S11 passes on V-4 and fails on the V-2 image, where the muted B stays `p=19, v=true` for A | spec coverage SC-87; fix V-3 |
| O-85 | **SC-96: the recording tap was not consent-gated** (spec §3.5, Tier 0). The recorder joined from its env with no registration or consent check, and so did the injector's `RECORD=1` | **FIXED IN CODE `ae159b0` (opt-in gate and recorder marker). The mixer part was deployed 2026-09-14 14:55 CDT (V-3); the connector images were not rebuilt.** *Gate:* the recorder, and the injector with `RECORD=1`, exit 1 unless `RECORDING_OPT_IN=yes`; the default is off, and the first start-up line prints the value. *Marker:* a recording peer joins with `"recorder": true`; the mixer logs `[slvoice] RECORDER <display> joined/left room <id>` and sets `"recorder": true` on its `listparticipants` row and in `handle_info`. *Tests:* `connectors/common/test_config.py` 3/3; `test_room_lifecycle` recorder-row checks. *Not closed:* per-participant consent. A full flow needs: a sim prompt per avatar, with consent recorded, expiring and revocable; the sim excluding non-consenting sources from the recorder's column; the mixer failing closed for a recorder before its first batch, which is blocked by O-78's mixer half; a sim-issued join capability, so only registered taps can record (O-46 amend); and retention and jurisdiction defaults (spec §10 Q3). *Limit:* this is not a security boundary, since a peer holding `JS_API_SECRET` can still join undeclared | spec coverage SC-96; fix V-3 |
| O-86 | **Two harness gaps: S11 could not see SLData, and no scenario exercised the spatial path.** (a) The test peer listened only on the `SLData` channel it created. The plugin relays SLData with no label, so Janus opens its own `JanusDataChannel` toward the peer, and nothing reached the handler; S11 failed in V-3 with `[]`. (b) Cull, falloff and pan need geometry from two peers. Only S7 sent geometry, alone in its room, so O-83 went uncaught | **DONE (V-4): (a) `fd2289f`, (b) `dced461`.** *(a):* a raw-message capture showed the mixer sent A 52 power entries for B (50 lit), all on `JanusDataChannel`, and 0 arrived on `SLData`. `ConnectorPeer` now listens on channels the far side opens (`pc.on("datachannel")`), and `connectors/README.md` documents the behaviour. *(b):* S12 puts two peers with geometry in a room created without the flag. B 10 m to the side is audible and hard-panned, at 110 m it is culled (`last_mix_rms` exactly 0), and back at 20 m it is re-added. *Regression check:* against the V-2 image, S11 fails (the muted B stays lit) and S12 fails (no pan in a flat room). *Full harness on V-4:* 11/11 in 271.7 s | V-3 / V-4 |
| O-87 | **TURN cannot be delivered to stock viewers (slice A.4).** No stock viewer takes TURN servers or credentials from the sim. The LL viewer builds hardcoded STUN URLs (`llvoicewebrtc.cpp:2806`–`:2820`, `// TODO: Pull these from login`). Firestorm Beta 7.2.4 and later (FIRE-36421, upstream `d72e47dc71`) reads `SimulatorFeatures["stun-servers"]` as one comma-separated string into `mUrls` only (`llviewerregion.cpp:2720`–`:2722`, `llvoicewebrtc.cpp:2977`). In all four viewer trees read, `mUserName` and `mPassword` are never assigned; the only writes are the copies into libwebrtc (`llwebrtc.cpp`). A viewer that cannot reach the mixer over UDP therefore has no relay path, whatever the sim or the mixer (A.3) configures | **BLOCKED — stock-viewer compatibility gap, recorded 2026-09-14.** A.4 Part 1 finding (b), no mechanism. Part 2 (sim TURN config, per-agent ephemeral credentials) was not built. *Unblocks when* an upstream viewer reads ICE credentials from the sim before `VOICE_STATE_START_SESSION`; the proposal is in the finding §5. *Do not* put `turn:` URLs in `StunServers`: they would reach libwebrtc with empty credentials, and what libwebrtc then does is unverified (finding §3) | `turn-viewer-delivery-finding.md` |
| O-88 | **Connector peers and recorder taps are not armed in Phase 0.** The position settled 2026-09-14 is that they ARE armed, not exempted. The recorder is the most privacy-sensitive participant in the system, and the one peer writing audio to disk must not be the one with no authority behind it. Slice 0.2 arms avatars only (the scene presences holding a voice session), because connector and recorder arming is UNDESIGNED: which display a connector uses, which room, and how a recorder tap differs. *(2026-09-16, slice 0.7a: that premise was a sentence in the design doc, never a property of 0.2's code. See the status cell.)* | **BUILT both halves; closes when the 0.9 gate step passes live. NOT closed (2026-09-16, slice 0.7b).** *Capability half (0.7b):* connector peers are NOT exempted from `JS_JOIN_CAP_REQUIRED`. The sim serves `POST /voice/connector/<name>/join-cap` behind `[VoiceConnector.<name>] CapabilitySecret` (unset = no handler; under 32 characters = WARN and no handler), minted by 0.4's `JoinCapability.Mint` for the NPC id, the `ViewerSessionId` and the recorded room. Every pre-authentication failure is an empty 404, and 503 comes only after authentication. The peers fetch before every join with `CONNECTOR_CAP_URL` + `CONNECTOR_CAP_SECRET` (both or neither) and never join bare. *Proof:* sim U1-U7 (`ConnectorJoinCapEndpointTests`); harness **S34** on a `JS_JOIN_CAP_REQUIRED=1` scratch mixer (bare `cap_missing`, configured joins, replay `cap_replayed`, 404 retries with no join). Against a peer that joins bare on a failed fetch it fails on its own text, `S34: with the capability endpoint answering 404 the peer sends no join at all, never a bare one`. Design section 11.10 amendment 2026-09-16. Findings filed alongside: O-93 (connector room vs parcel), O-94, O-95. *Arming half (0.7a):* ARMING DONE *Design, as ruled:* display = the derived NPC id (O-63), room = the estate room, and a recorder tap = the same record with `MayInject=false` plus the existing moderation mute. There is no separate mechanism. `VoiceConnectorRegistrar.Register` adds the NPC's voice session (`VoiceConnectorRegistrar.cs:85`) and calls `pRecordRoom` -> `svc.OnListenerProvisioned(npcId, estateRoom)` (`:91`, `VoiceConnectorModule.cs:299`), which is design section 2 trigger 2. The session admits the NPC through `FeederWorldFromScene.cs:63` into the population that triggers 1, 3 and 4 and the heartbeat read. `Unregister` removes it (`:127`), the next heartbeat omits it, and section 1.3 omission disarms the still-connected peer. *Finding (a), armed as built:* nothing on the arming, heartbeat or partitioner path excludes an NPC. `ForEachScenePresence` skips only deleted presences (`SceneGraph.cs:1469`). The one NPC check, `IsNpcProvisionRefused` (`WebRtcVoiceServiceModule.cs:448`), is on the viewer provision path and exempts connector identities. *Sim proof:* `ConnectorArmingTests` T1-T6 on a real Scene, service and sink, with an NPC-typed presence. All passed with **no production change**. Mutations: skipping NPC presences fails T1-T5, and keeping the session on `Unregister` fails T5. T6: knob-off payloads with a `MayInject=true` connector are byte-identical to the knob-off golden. *Mixer proof:* harness **S33** on a fail-closed scratch mixer. A recorder tap and a non-recorder connector source are each silent/inaudible unarmed, heard armed, and back at row 1 within 3 s of a heartbeat that omits them. Against an image whose omission does not disarm, S33 fails on its own text: `S33: a heartbeat omitting the recorder disarms it at once (row 1, not the staleness window)`. *Still open:* the capability half. Connector peers mint no join capability, so `JS_JOIN_CAP_REQUIRED=1` would refuse them (design section 11.10, slice 0.7b). The fail-closed gate below still stands, and soak step 3 must now show connector peers ARMED. *History:* **DEFERRED, not decided (recorded 2026-09-15).** A scope boundary in 0.2, not a reversal of the position. *Gate:* `JS_VIS_FAIL_CLOSED` must not be enabled until the design exists and is built. Under fail-closed an unarmed peer is silent: the safe outcome for a recorder tap, the WRONG outcome for a connector NPC someone expects to hear. *Check:* the 0.6 shadow soak lists every connector peer and recorder tap in a declared room with its arming state, and FAILS while any is unarmed or the design is unbuilt (design §6.4 step 3). Closing this row requires the design, the build, and a passing soak step. *Sibling row (added 2026-09-15, slice 0.4):* **O-46's join capability has the same hole for the same peers** — connector and recorder peers join from their own environment with `api_secret` and mint no capability, so `JS_JOIN_CAP_REQUIRED=1` would refuse them exactly as `JS_VIS_FAIL_CLOSED=1` would silence them. Whoever designs connector arming **must design connector capabilities in the same pass** (design §11.10); two parked rows on one underlying question is how one gets forgotten. *Measured 2026-09-16 (slice 0.5):* the silencing is no longer an argument from the rule — **S30 observes it**. A tap joined with `"recorder": true` into a declared room reports `recorder` true in `query_session` and sits at `vis_row` 1, exactly silent, for as long as it is unarmed; one arming `replace` makes it hear the room. So the gate protects against a measured outcome, not a predicted one, and the soak's step 3 has a scenario that already reproduces it | `nonspatial-phase0-design.md` amendments 2026-09-15, 2026-09-16 (0.5) and 2026-09-16 (0.7a), §6.4, §10 OQ3, §11.10; harness S30, S33; `ConnectorArmingTests`, `VisibilityKnobOffGoldenTests` |
| O-89 | **Two sender stall-guard tests fail on every run.** `VisibilityBatchSenderTests.StalledSend_GuardFiresAfterThreshold_ClearsFlag_NextPumpSnapshots` and `AbandonedSendLateCompletion_DoesNotClearANewerSendsFlag` both fail at `Assert.That(StallLogCount(appender), Is.EqualTo(1))`: expected 1, got 0 (`Tests/WebRtcJanusService.Tests/VisibilityBatchSenderTests.cs:508-509`, `:551`). They failed identically on 2026-09-15 at `2fa978c254`, **before** slice 0.2 changed anything, and still fail at `b0da9f64c9`. The stall guard's behaviour assertions in those tests (flag cleared, snapshot next) are masked by the log assertion that precedes them | **OPEN, recorded 2026-09-15 so a permanently red pair does not become background noise.** *Suspected cause, not verified:* the test captures with a log4net `MemoryAppender` on the repository root (`:497-505`), while `VisibilityBatchSender` logs through `LoggerProvider.CreateLogger` (Microsoft.Extensions.Logging), so the guard's ERROR may never reach that appender. *Not yet known:* since when the pair has failed; `git log` on the helper last touched it in `078f77390c` (2026-08-17). *Closing requires:* the cause confirmed, the capture fixed or the assertion replaced, and both tests green. The guard itself must not be changed to make them pass | slice 0.2 baseline test run |
| O-90 | **The `Docs/voice` cross-repo mirror drifted in four files; one is reconciled, three are open.** O-28 closed 2026-08-31 on a verified full mirror. Measured again 2026-09-15 during slice 0.4, then examined file by file 2026-09-16 with the diff direction established before anything was copied. **Reconciled — the mixer genuinely held newer content:** `per-room-visibility-emission-design-brief.md`, a single hunk in which the mixer rewrote one paragraph as a dated *Amended 2026-09-13 (O-54, mixer slice 5)* supersession describing grace-destroy, carrying the superseded statement forward in its own closing parenthetical. The mixer copy was taken as canonical; both sides now hash `c6b4ec56`. *What that rewrite drops:* the sim paragraph cited `janus_slvoice.c:1920` for the idle-room test and the mixer text carries no file:line at all — restore a current citation when that brief is next edited. **Left alone because they drifted in the SIM direction, where a mixer-wins copy would have destroyed sim content:** `connector-build-plan.md` (25 lines only in the sim, 0 only in the mixer — the entire "Connector NPC identity (O-63)" section), `parcel-voice-semantics.md` (10 lines only in the sim, 0 only in the mixer — the O-48 server-derived-parcel amendment), and `voice-config-register.md` (**absent from the mixer entirely**). | **PARTLY RESOLVED.** The three remaining are sim->mixer syncs rather than judgement calls, but they are still outside the slice that found them, and none was chosen blindly. **Gate:** the next slice that edits any of the three reconciles that file first and re-verifies the whole mirror by blob hash on both sides before it closes. Slice 0.4's own documents were never affected: `nonspatial-phase0-design.md` and `voice-programme-ledger.md` hash SAME throughout. | this row; O-28 |
| O-91 | **The 0.4 "did it land" capability check cannot pass as written, and the mixer cannot attribute a capability to a session.** The check named `join_cap seen == 0` as proof that a knob-off sim mints nothing. `seen` is **process-wide from the mixer's last restart** and counts any join that carried a capability, so harness traffic moves it: on 2026-09-16 it read **3** (`accepted 0`, `key_set false`, `cap_missing 12`) on handles that had genuinely joined, while the sim provably sent none (`JanusRoom.cs:78` guard; `WebRtcJanusService.cs:144` default false and `:146-149` forced false on an empty secret; neither key in live `config\OpenSim.ini`; the `:152` enable line absent from the region log). One of the 3 is `scenarios.py:908-913` - S24 mints and joins with a capability before it skips, even with no secret configured - and two are unreconciled. **No per-session indicator exists:** nothing in `handle_info` says whether *this* join carried a capability, so "did that join carry one" is unanswerable mixer-side. | **CLOSED 2026-09-16 (slice 0.6): the marker is built, proven and LIVE.** The replacement criterion is unchanged and durable: a knob-off deploy is judged by mixer-side `accepted == 0` **and** `enforced_refusals == 0`, plus the sim-side guard proof - never `seen == 0`. **The gate is met in code:** every session record now carries `join_cap_present` (did THIS join carry a capability) and `join_cap_verdict` (`ok`, `cap_missing`, `cap_wrong_agent`, ...). Both are set under the session mutex at the verdict site, before any refusal, so a join refused for the capability, for capacity, or by a failed negotiate still carries what it attempted; and both are emitted in `query_session` **outside** the declared-room branch, so a session that never joined reads `none` rather than being absent. **Three proofs, not one:** S22 PASSES against the 0.6 image; the SAME S22 FAILS against `rollback-pre-06` with `{"join_cap_present": null, "join_cap_verdict": null}` - the fields do not exist there, and S22 otherwise runs to completion, so that failure is about the marker rather than a skipped feature; and a mutation expecting `ok` for the peer that carried no capability fails with observed `cap_missing`, proving the assertion checks the values and not merely their presence. **Live evidence** (image `e06e7e5d`, deployed knobs off 2026-09-16, both knobs verified off at startup): the sim re-provisioned within 10 s and **both** live sessions report `join_cap_present: false` with `join_cap_verdict: "cap_missing"` - room 226001844 (Ebony) and room 1578726032 (Transylvania). The sim mints nothing with `JoinCapabilityEnabled` absent, and the mixer can now say so **per session** rather than only in a counter shared with harness traffic. That is the attribution this row was opened for, so it closes. | this row; O-46; harness S22; design §11 |
| O-92 | **`show voice visibility` reports the FALLBACK room, not the rooms traffic actually goes to — and one batch really was sent to a room that does not exist.** The reader prints `sink.FallbackRoom` (`JanusPeerCtlBatchSink.cs:143`), a number fixed at construction from the region's estate/local channel. Sends are partitioned per agent through the room resolver, with the fallback only as a default: `PeerCtlBatchPartitioner.Partition(excl, RoomOf, _fallbackRoom)` (`:186`). When every agent has a record, **no traffic goes to the fallback at all**, so the number on screen is a room the mixer may never have created. Measured 2026-09-16: Elm's line reads `fallback room 1967062692` while its real room is **1966197062** (`806332b8-…/local/1/`, a per-parcel room); `1967062692` has never been created in the mixer's entire log. An operator cannot discover the real room from this command. **The sharper half:** at 17:59:36 the sim addressed a batch to that phantom fallback, before `RoomOf` held a record for the joining agent, and the mixer answered `{"slvoice":"error","reason":"unknown_room","status":"unknown_room"}` — logged as `peer_ctl_batch inner-reply anomaly`. It self-healed: the real room was created 15 s later, the epoch adopted, `1 listener(s) newly armed`, and **0** further `unknown_room` replies (6 batches to the real room against 1 to the phantom). So this is a transient at the join edge, not a standing fault — but the batch that hit it went nowhere. **NOT a reader bug in the zero case:** Elm later read `0 room(s), 0 listener(s)` correctly, because its listener had detached and the room was grace-destroyed after 60 s empty. | **FIXED sim-side 2026-09-17 (slice 0.8c2), pending live proof in 0.8d. NOT closed** - the ruling and the fix are at the END of this cell; what follows first is the history, kept because the measurements in it are the evidence. Originally: **OPEN, filed during slice 0.6 and deliberately not fixed during the soak** (an operator-visible change mid-measurement would muddy what the soak observes). Two things to do together, because they are one blind spot: (1) the reader should print the rooms the last send actually addressed — the partition's own room set — with the fallback labelled as the default it is, not as the region's room; (2) the same substitution should be visible when it happens on the wire, so a batch addressed to a never-created room is attributable rather than a lone anomaly WARN. **Note the shape of the trap:** the room allowlist used for the 0.6 soak filter was built from these same fallback numbers and would have silently excluded every Elm measurement; the filter now derives from the three region UUIDs and the rooms they create. A magnitude test (`>= 9e8`) is also wrong — Transylvania (1,578,726,032) and Elm (1,966,197,062 / 1,967,062,692) all exceed it. **Diagnosis 2026-09-17 (slice 0.8, read-only, no fix).** *Why a batch can be addressed to a room that was never created:* a Janus room is created on ONE path only, a viewer provisioning voice - `SelectRoom` -> `CreateRoom` (`JanusAudioBridge.cs:309-323`, reached from `WebRtcJanusService.cs:447`). The feeder never creates a room. The sim learns an agent's real room only from that provision's reply, `svc?.OnListenerProvisioned(agentID, provisionedRoom)` (`WebRtcVoiceRegionModule.cs:809-813`). Until then the agent has NO record, and both addressing paths substitute the estate/local number computed by formula: `PeerCtlBatchPartitioner.Resolve` (`Visibility/PeerCtlBatchPartitioner.cs:166-178`, `room = record ?? estateRoom`) reached from `Partition(excl, RoomOf, _fallbackRoom)` (`JanusPeerCtlBatchSink.cs:186`, the number from `:124-125`), and the arming resolver `agent => roomOf(agent) ?? fallbackRoom` (`VoiceVisibilityService.cs:137`), which also feeds the HEARTBEAT (`VisibilityBatchSender.cs:347` -> `VisAuthority.cs:402-405`). That number hashes (region, "local", -999), so in a region whose avatars sit on a parcel with its own channel it is a room nobody ever creates - Elm's 1967062692 against its real 1966197062. *Who takes that path:* (1) an agent in the population whose provision has not returned yet, or returned no room (a failure or logout map leaves the record untouched) - the join-edge transient this row already measured; (2) **a connector NPC, permanently** - its record IS the estate room (O-93), so in a parcel-channel region it is addressed at a room that exists only if someone provisions the estate channel there. Observed live this slice: with soak-injector registered in Ebony and no avatar voiced, the sim re-armed it into 226001844 and the mixer answered `unknown room ...; dropped` at **~3.2 lines/second** (964 in 5 minutes) - standing, not transient. *Under fail-closed:* the batch is dropped and answered `unknown_room`, so the authority disarms those listeners and backs off `UnknownRoomRetryMs` (`VisAuthority.cs:296-299`, `:344-357`). A listener really in another room is therefore never armed **in the room it is actually in**: row 1, silent, for as long as the sim addresses the phantom - bounded by provision-record latency for an avatar (~15 s measured here), **unbounded for a connector**, whose record never moves. *Smallest correct fix:* stop using the fallback as an ADDRESS while keeping it as the column FILTER default - an agent with no record is omitted from the batch and from the heartbeat (it cannot be disarmed, having never been armed) and counted in the existing fallback counters, because an agent the sim cannot place is exactly one whose room it must not guess. *What that breaks:* an agent that genuinely belongs to the estate room but lost its record (a close/leave race) stops receiving policy until it re-provisions, which under fail-closed is silence rather than a wrong room; §7.5's "one policy for a missing room record" has to be restated as filter-only; the O-92 console reader needs the same change. *O-93 does NOT share the cause:* this row is the ABSENCE of a record, O-93 is a record that is present and deliberately the estate room even when the NPC's Position is on a parcel with its own channel. The common root is narrower - a room number derived by formula rather than taken from a join that actually happened. Fixing either does not fix the other. **Connector half FIXED 2026-09-17 (slice 0.8c); avatar half still OPEN, for 0.8c2.** The connector no longer addresses a room derived by formula and hoped for: it resolves the room an avatar there would get and ensures it exists (O-93). The standing storm this row measured is gone at its root as well - the arming pass short-circuited on a standing re-arm request and so ignored the authority's backoff, which is why one connector produced 8,933 `unknown_room` lines at ~3.8/s; `CanArmNow` now gates every arming path and the delay doubles 1 s -> 30 s, measured 480 attempts -> 8 over 120 s. **FIXED sim-side 2026-09-17 (slice 0.8c2), pending live proof in 0.8d. NOT closed.** The avatar half is now fixed the same way the connector half was - by resolving, not guessing. **Ruling (Claude, 0.8c2): this row's own proposed fix was NOT adopted as written.** The proposal above was to omit an agent with no room record. Omission alone would have made every avatar unaddressable for the ~15 s of provision-record latency this row measured, and would have left a whole region unaddressed after a feeder restart, when the record table is empty but every avatar is still in the room its viewer provisioned. The rule adopted instead is **record, else resolved, else omitted**: an agent with no record is addressed at the room `ConnectorRoomResolver`'s logic says an avatar at its current position would be provisioned into, which is the same rule the provisioning path itself applies, so the answer is the truth rather than a default. Only when neither source can place it is it omitted - from every batch, every column and every heartbeat entry - and counted. The estate number is never used as an address merely because it is the default; on an estate-channel parcel the resolved number IS today's number, so both knob-off goldens and the knob-on payloads are byte-identical there (O2). Ruling C, the same slice: any event proving a room exists - an ensure at a capability fetch, a viewer provision into it, an applied reply - resets that room's `unknown_room` backoff for **every** listener it was holding, not just the one named, so arming goes out on the very next tick instead of up to 30 s later (C1). *Reader:* `show voice visibility` now prints the rooms the last send actually addressed, by number, plus how many agents it could not place; the estate room is reported as the region's own number, not as anybody's address (O5). *Where it lands:* the parcel is resolved on the feeder tick thread, the only thread already reading `LandChannel` (`FeederWorldFromScene.SnapshotAgents`), and the composition lives in `VoiceVisibilityService.ResolveRoom`; the resolver is nullable end to end through the sender, the authority and the partitioner. *Tests:* O1 (own-channel parcel: armed at the parcel's room, and no generation is ever allocated for the estate number), O2 (estate-channel parcel: byte-identical, arming on and off), O3 (a record arrives and differs: armed at the record, named nowhere at the resolved room), O4 (unplaceable: omitted, counted, nothing allocated, picked up as soon as either source answers), O5 (the reader), C1 (both proofs release the backoff at the cap), C2 (the heartbeat follows a connector whose parcel channel changed), C3 (a connector with no `CapabilitySecret` ensures nothing). All shown failing first against the pre-0.8c2 resolver and against a disabled `RoomExists`. *Closing requires:* the 0.8d live proof - an avatar on an own-channel parcel armed in the parcel's room with no estate-room traffic at all, and the reader naming that room | this row; O-88; design §6.2; harness soak notes; 0.8 diagnosis; 0.8c connector half |
| O-93 | **A connector's room ignores the parcel at its Position.** `StartRecord` records the estate room: `CalcRoomNumber("", regionId, "local", REGION_ROOM_ID, "")` (`VoiceConnectorModule.cs:316`). An avatar standing at the same Position, on a parcel with its own voice channel (not `UseEstateVoiceChannel`), is provisioned into that parcel's room: `map["parcel_local_id"] = land.LocalID` (`WebRtcVoiceRegionModule.cs:751`), hashed at `WebRtcJanusService.cs:424`. So a connector on such a parcel sits in, arms at, and is minted a capability for the estate room, while the people around it talk in the parcel room. It interacts with **O-92**: the fallback room that reader prints is exactly the room a connector uses, so the reader looks right for a connector and wrong for the avatars beside it | **FIXED sim-side 2026-09-17 (slice 0.8c), pending live proof in 0.8d. NOT closed.** A connector's room is now the room an avatar at its Position would be provisioned into: `ConnectorRoomResolver.RoomFor` applies the provisioning rule (the parcel's own channel unless `UseEstateVoiceChan`) and the same `CalcRoomNumber`, at registration and again at **every capability fetch**, moving the record with one INFO line when the parcel's channel has changed. On an estate-channel parcel the number is unchanged, so Ebony behaves exactly as it did. The sim also makes the room EXIST, through the new seam `IWebRtcVoiceService.EnsureSpatialRoom`, which creates it exactly as a viewer's provision would (same flags, declared when this sim arms) and is idempotent. *Tests:* T1 (own-channel parcel records the parcel's room, estate-channel records the old number), T2 (a fetch ensures before minting; a second fetch creates nothing), T5 (the channel changes, the record and the capability follow), T7 (nothing ensured after Unregister). *Closing requires:* the 0.8d live proof - a connector on an own-channel parcel joining a room the sim created, with no avatar present | 0.7b F4; 0.8c fix |
| O-94 | **Harness S5, S10 and S14 read the live compose container's logs whatever `--container` says.** S5 (`scenarios.py:166`) and S10 (`:260`) call `compose(... "logs" ... "janus")`, and S14 (`:394`) calls `compose(... "exec" ... "janus" ...)`, instead of `mixer_logs` (`harness.py:886`), which honours `cfg.container`. Against a scratch mixer they search the grid mixer's log and fail, whatever the scratch mixer did (seen 2026-09-16 on the 0.7a board: S5, S10 and S14 FAIL) | **CLOSED 2026-09-16 (slice 0.7c).** Every docker read, exec and restart in the harness now goes through the `--container` target: `mixer_logs`, the new `mixer_exec` and `mixer_restart` in `harness.py`, with `mixer_target` naming the target in Fail details. *Found and fixed:* S4's restart (`scenarios.py:117`, it called `compose restart janus` directly, so a scratch board without `--no-restart` would have restarted the GRID mixer), S5's logs (`:166`), S10's logs (`:260`) and S14's exec (`:394`). *Left as they are:* S13's skip message (a `docker compose --profile turn-test` hint, text only) and `source_probe.py:73` (a compose-file path passed to Config; no docker call). Nothing else in `tests/integration` names a container or the compose service. *Proof:* against a scratch mixer (`lvm-scratch-07c-fc`), S5, S10 and S14 each PASS alone; then 3 full scratch boards WITHOUT `--no-restart` (S4 and S20 restarted only the scratch container), each 26 passed, 0 failed, 7 skipped (explained); and the live-mixer board with `--no-restart` still passes (12 passed, 0 failed, 21 skipped, S5/S10/S14 PASS through the compose path) | 0.7a board; 0.7c |
| O-95 | **S26 is intermittent on the full board.** On the 0.7a full board (fail-closed scratch, `--no-restart`) S26 failed at `§9 14: a replace for L restores it` (L at `last_mix_rms` 0.0). Run alone immediately afterwards against the same mixer, it passed twice. No mixer code changed in 0.7a. Not investigated | **CAUSE FOUND by construction and FIXED; the original intermittent was never directly reproduced; closes when the 0.9 confirm soak is clean (2026-09-17, slice 0.7d). NOT closed.** *Cause:* heartbeats and batches are separate flights (`VisibilityBatchSender.cs:91` vs `:162`), and a heartbeat carried nothing that ordered it against batches (its `policy_generation` is the highest ALLOCATED, `VisAuthority.cs:414`, and the mixer never read it). A heartbeat built before a batch applied could arrive after it. Constructed, both orderings drop audio under fail-closed on the pre-0.7d mixer. **A**, a replace for L at N+1 then a heartbeat naming L at N: L is listed stale and silenced (`test_visauth`: `FAIL: ordering A: a heartbeat built before L's replace (as_of 1 < applied 2) does not list L stale`; S35: `S35 leg A: a heartbeat built before L's replace does not list L in stale_listeners`). **B**, an arming replace for L2 then a heartbeat built before L2 existed: L2 is disarmed (`FAIL: ordering B: ... does not disarm L2, which keeps hearing S`; S35: `S35 leg B: a heartbeat built before L2's arming leaves L2 armed, and L2 hears SRC`, observed `last_mix_rms` 0.0). S26's one failure, a replace at 100 followed by silence, is ordering A. *Fix:* the sim sends `as_of`, the highest generation APPLIED per room (`VisAuthority._roomAsOf`, updated on an applied outcome). The mixer skips the listeners map of an entry whose `as_of` is below the room's applied `policy_generation`; the entry still counts for liveness and in the new counter `heartbeats_outdated`. No `as_of` (an older sim) behaves as before. Design §1.3 and §3 amendments 2026-09-17. *History:* **OPEN, UNREPRODUCED (2026-09-16, slice 0.7c). Still blocks `JS_VIS_FAIL_CLOSED`. Not fixed, and not called fixed.** *What the assertion waits on:* a poll with a deadline, not a sleep or an event: `ctx.until_info(l, _audible, ...)` polls `handle_info` every 0.1 s for `POLL_TIMEOUT` = 5 s (`harness.py:43`, `:134`), started right after the generation-100 replace returns and the heartbeat's listener map is updated to L:100. *Runs on a fail-closed scratch mixer (`legion-voice-mixer:07b`, `lvm-scratch-07c-fc`):* S26 alone **20 of 20 PASS**, then the full board **3 of 3 with S26 PASS** (26 passed, 0 failed, 7 skipped each). **0 failures in 23**, so it cannot be classified as H, I or M. *Capture now built in* for the next occurrence: on that assertion's failure S26 writes `$O95_CAPTURE_DIR/o95-<room>.json` holding L's full `handle_info` (`vis_row`, `vis_stale`, `vis_listener_generation`, `vis_confirmed_age_ms`, the room's visibility block), the replace reply, send-to-reply, send-to-listener-update and send-to-failure timings, every heartbeat from 2 s before the replace (send and reply times, the L generation it carried, the room's reply), and the mixer log lines for the room since the replace. *Closing requires:* a captured failure, and the classification it supports **Slice 0.8 Part 0 (2026-09-17):** the other half of the ordering - the reply LOST after the mixer applied - needs no fix. The sim's `as_of` then sits below the mixer's applied generation and every heartbeat it builds is outdated, but the transport error leaves the sender unsynced AND leaves those listeners unarmed, so the next tick re-arms the room's whole population with a replace at a higher generation (design section 2 trigger 3) and `as_of` catches up. Test `ALostReplyAfterTheMixerApplied_IsRepairedByTheNextTicksResyncReplace`; it fails only when a failed send is made to set `_synced` AND the unarmed-re-arm path is disabled, i.e. two independent paths guarantee it.  | 0.7a board; 0.7c runs |
| O-96 | **A join capability carries the generation the sim ALLOCATED, not the one the mixer APPLIED, so the first join into a room is refused `cap_stale_generation`.** The sim publishes a room's arming state for minting inside `VisAuthority.NextGeneration` - `JoinCapabilityAuthority.Publish(room, EpochString, next)` (`VisAuthority.cs:133-139`) - which runs when a batch is STAMPED, before it is sent. `JanusRoom.cs:80` mints from that published value. The mixer checks the capability against the highest generation it has **applied** in the adopted epoch (`joincap.c:229-238` against `room->vis_policy_gen`). Any generation the sim allocated but the mixer never applied - every batch to a room that does not exist yet is exactly that - makes the next minted capability claim a generation ahead of the mixer. This is the same allocated-versus-applied mistake 0.7d fixed for heartbeats with `as_of` (**O-95**), still present in the capability path | **FIXED; closes when the re-run join-cap soak reads `cap_stale_generation` 0 (2026-09-17, slice 0.8b). NOT closed.** *Claude ruled AGAINST the fix this row proposed.* The 0.8 row (filed at `e0c2990`) proposed publishing the APPLIED generation instead of the allocated one, so that a capability could never be ahead of the mixer. The ruling went the other way: **the generation is information, never a refusal.** Publishing the applied generation would still leave the capability behind whenever a batch lands between minting and joining, and it would make every mint wait on a round trip the join does not need - a capability admits a join, it does not adopt an epoch, so it has no business carrying a bound the mixer can enforce. What refuses instead is the one thing that proves the minting authority is gone: a **non-zero** epoch strictly below the room's adopted epoch. *Built:* `171af94` made the generation non-refusing and added the accepted-but-different counters (`cap_generation_ahead`, `cap_generation_behind`, `cap_epoch_ahead`); this slice added the second half of the ruling - **a capability with NO epoch is accepted whatever the room has adopted**, counted `cap_no_epoch` - because a zero epoch means the sim held no authority state at mint, not that an older authority minted it, and it is reached by an ordinary rejoin while the mixer still holds an epoch through the empty-room grace. `171af94` still refused that case. *Proof:* `test_joincap` 57 checks (the flipped case fails on `171af94` with `FAIL: a capability with no epoch at all, against a room that has adopted one: accepted (O-96, 0.8b)`); harness **S36** legs a-d, where leg d fails on the `171af94` image with `S36 leg d: a capability with NO epoch joins a room that has adopted one (0.8b)` against `error_code 496 reason cap_stale_generation`. Mutation: with the epoch check removed, S36 fails on leg c's own check, `a join with cap_stale_generation is refused, not admitted`. **S23's stale-generation leg was rewritten** by the `171af94` session and is kept: its old leg minted a capability one generation above the room's applied value and expected a refusal, which is now an accepted case, so it would have pinned the defect in place; it now exercises an older epoch instead, which is the refusal that remains. Design §11.4 and §11.5 amendments 2026-09-17. *Closing requires:* the join-cap soak re-run on a deployed 0.8b mixer reading `cap_stale_generation` 0 with every avatar and connector join accepted | 0.8 soak; `VisAuthority.cs:133-139`, `JanusRoom.cs:80`, `joincap.c:229-238`; 0.8b ruling and fix |
| O-97 | **The harness could not run against a capability-required mixer.** Ordinary joins went out bare: only S22, S23, S34 and S36 minted capabilities, because until 0.8e the harness had no way to carry one on a normal join. Measured in 0.8b: a full board against a mixer with `JS_JOIN_CAP_REQUIRED=1` failed **15 scenarios**, every one of them `Join capability refused (cap_missing)` where a bare join met a declared room. After slice 0.9 the LIVE mixer runs with that knob on, so the live board - and any board shaped like production - would have been unusable, and the failures would have looked like mixer defects rather than a harness gap | **CLOSED 2026-09-17 (slice 0.8e). Harness only; no file under `src/` changed.** `--join-cap-secret-file <path>` reads the key from a file (a bare value, or a dotenv file's `JS_JOIN_CAP_SECRET=` line, so the mixer's own `.env` can be passed as it stands) instead of a command line, where it would reach the shell history and the process list. With a key given, every ordinary join into a **declared** room carries a freshly minted capability - right agent, session and room, a fresh nonce, and the epoch the scenario last addressed that room with, else none. A room nobody declared, a scenario that passes its own capability, and a join asked for with `bare=True` are all untouched, so the capability scenarios keep full control of what they test. With no key the joins are byte-for-byte what they were. *Proofs:* (a) full board on a scratch mixer with `JS_VIS_FAIL_CLOSED=1` + `JS_JOIN_CAP_REQUIRED=1` and the key file: **27 passed, 0 failed, 8 skipped**, with S23, S34 and S36 RUNNING and passing where they used to skip; every remaining skip is a wrong-mode, wrong-image, no-TURN or clamp-mixer reason. (b) the SAME mixer with no key file, three scenarios that join declared rooms bare: all three fail on `Join capability refused (cap_missing)` - `A join into room 909350400 refused` (S16) and `S join into room 909350401 refused` (S25) - so (a) is not passing vacuously. (c) fail-closed on, capability requirement OFF, no key: **24 passed, 0 failed, 11 skipped** against 0.8b`s 25/0/10; the one difference is S22, which needs a key to mint with and now skips for that stated reason rather than being handed one on the command line. (d) the live board with `--no-restart` and the live `.env` as the key file: **13 passed, 0 failed, 22 skipped**. The live mixer is still in SHADOW (`JS_JOIN_CAP_REQUIRED` unset), so this proves only that carrying capabilities does no harm, not that the required path works live. (e) the key values appear **0 times** across all four run outputs and 0 times in the mixer container log; they live only in the key file and the mixer `.env`. *Also fixed here:* S36 leg d asserted an ABSOLUTE `cap_stale_generation` of 1, which only held when S36 ran alone - the counters are process-wide, and on a full board S23 refuses one too; it is now the delta this scenario adds | 0.8b board; 0.8e |

Closed items, kept so nobody re-files them: OnRemovePresence teardown (implemented 2026-08-22);
estate CAP TaxFree flip on absent `override_public_access` (implemented 2026-08-23); parcel
access/ban list not persisted and UseBanList clobber (implemented, *fix(land): persist access/ban
list edits and preserve UseBanList on properties save*); ban-add silence (implemented,
instrumented); About Land access list rendering empty (resolved viewer-side); REGION_FLAGS_ALLOW_VOICE
bit-28 (not a defect); OPEN #12 estate-change event (exists — `OnEstateInfoChange`, subscribed at
`VoiceVisibilityService.cs:94`–`:96` [SRC]); scaling items 1 (non-deterministic truncation →
join-time cap) and 4 (lazy echo ring) [SRC]; connector Q1 [DOC]; per-room OQ1–OQ7 [DOC]; ALC
split-identity rule (documented, no fix owed); the sink-counter mute-blindness investigation (resolved 2026-08-28, `0190d864ef` — counters/logging only, enforcement never broke; §3).

### 4.2 KnownDefects in the mixer repo
**There is none** [SRC: `find` for `*knowndefect*` returns nothing]. Mixer-side defects are
filed in this tree's `Docs/KnownDefects.md` (O-2, O-5, O-13) and in `parcel-voice-semantics.md`
§M, which is synced to the mixer.

### 4.3 Where documents disagree with code or with each other

- **(a)** `KnownDefects.md` "Mixer applies peer_ctl_batch exclusions by display string… **Status:
  not started**" vs. mixer commits *fan visibility exclusions out to every session matching the
  listener display* and *join-time duplicate-display detection + deterministic dot-batch merge*
  [SRC], and vs. `parcel-voice-semantics.md` §M addendum which treats those as landed. The
  KnownDefects status is stale.
- **(b)** `KnownDefects.md` feeder-thread entry "registration implemented **(uncommitted)**" vs.
  commit *feat(voice): register visibility feeder tick thread with the Watchdog* (2026-08-17)
  [SRC]. Stale.
- **(c)** mixer `README.md` "**Status: Phase 1B**" vs. plugin description "Phase 3b" and the git
  log [SRC]. Stale by five phases.
- **(d)** `phase3a-feeder-acceptance.md` "the feeder is off by default — no consumer emits its
  output to Janus yet" vs. the sender (*peer_ctl_batch sender — wire the visibility matrix to the
  mixer*) and the live config `VisibilityEmitEnabled = true` [SRC]. Stale.
- **(e)** `protocol-compat.md` "Status: ACTIVE (Phase 0 / bring-up)… lifted at the flat-mix
  parity milestone" vs. Phase 2 landed at v0.6.0. Never updated; O-23.
- **(f)** `webrtc-voice-spec.md` §3.3 "version epochs … fails closed" vs. the wire and the
  mixer's keep-last-state behaviour [SRC]. Spec ahead of code, unacknowledged in the spec.
- **(g)** `webrtc-voice-spec.md` §7.1 "estate-configurable leash" vs. per-process jcfg [SRC];
  acknowledged in the 3b brief as a deviation, not in the spec.
- **(h)** `current-architecture.md` is a survey of this tree at `0bdeb0bf08` on branch
  `feature/membership-tiers` (2026-08-12), before 3a existed; its "no per-listener filtering"
  finding is now false [SRC]. Historical, not corrected.
- **(i)** `mixer-feed-protocol.md` §3.2 "per-parcel rooms use the `CalcRoomNumber` hash" reads
  as served, while §3.4's correction and O-1 establish per-parcel rooms receive nothing.
- **(j)** `KnownDefects.md` OnListenerProvisioned entry cites `WebRtcVoiceRegionModule.cs:408`–`:411`;
  the hook is at `:546`–`:553` [SRC]. Citation drift.
- **(k)** `mixer-feed-protocol.md` §3.3.1 was corrected 2026-08-25 in both repos; the mixer's
  `docs/voice/parcel-voice-semantics.md` was **not** re-synced after this tree's §P (O-28).
  Resynced 2026-08-31.
- **(l)** The 3b brief carries a 2026-08-25 header stating its body is pre-implementation; its
  body still says `SLV_MAX_MIX` = 64 and "no vector helpers" [SRC: 110; `vec3.h` etc. exist].
  Flagged by the header, not corrected in the body, by design.

*Testing gotcha (added 2026-08-31): sim working copies of some `Docs/voice` files are CRLF on
disk from pre-pin checkouts, so a working-tree diff shows every line changed; git normalises on
add under the LF pin — compare blob hashes, not files.*

### 4.4 Documents in scope and their freeze states (claims, not evidence)

| Document | Repo | Stated status | Note |
|---|---|---|---|
| `webrtc-voice-spec.md` | both | Draft for review | never frozen, undated |
| `current-architecture.md` | both | inventory at `0bdeb0bf08` | stale baseline (h) |
| `parcel-voice-semantics.md` | both | living, append-only addenda through §P | synced 2026-08-31 |
| `mixer-feed-protocol.md` | both | living; §3.3.1 version-scoped | in sync 2026-08-31 |
| `phase3a-feeder-acceptance.md` | both | acceptance notes | stale claim (d) |
| `phase3b-design-brief.md` | both | FROZEN 2026-08-18 + Amendments 1–8 + staleness header | body deliberately unedited |
| `scaling-assessment.md` | both | DRAFT + Amendments 1–2 | open items O-19 |
| `connector-design-brief.md` | both | DRAFT, not frozen | Q1 resolved, Q2–6 open |
| `voice-moderation-design-brief.md` | both | slice 1 verified; OQ1/2 open | parity gap section 2026-08-24 |
| `per-room-visibility-emission-design-brief.md` | both | DECIDED 2026-08-26 + build plan | S1 done |
| `a2a-assessment-20260830.md` | both | ground-truth map against `b7fbc717fa` + 2026-08-30 addendum | row added 2026-08-31 |
| `a2a-build-plan.md` | both | DECIDED; S-A2A-1..5 done, §5 watch items | row added 2026-08-31 |
| `voice-programme-ledger.md` | both | Ledger — LIVING | this file; mixer copy new 2026-08-31 |
| `connector-assessment-20260831.md` | both | ground truth vs `6d012a40d2` / mixer `0eb38f1` | row added 2026-08-31; mixer copy due (Maintenance rule) |
| `connector-build-plan.md` | both | DECIDED; slices S-CON-1..7, none started | row added 2026-08-31; mixer copy due (Maintenance rule) |
| `protocol-compat.md` | mixer only (mixer `docs/`, not the synced `docs/voice/`) | ACTIVE (Phase 0) | expiry condition met, not updated (e) |
| `voice-mute-wiring.md`, `sldata-extensions.md`, `phase1-bringup.md`, `docker-notes.md` | mixer only (mixer `docs/`, not the synced `docs/voice/`) | recon / runbook | no status headers |
| `Docs/audit/webrtc-upstream-audit.md` | this tree only | point-in-time 2026-08-23 vs upstream `cbdfba2811` | self-declares as as-of |
| `Docs/KnownDefects.md` | this tree only | living | statuses (a), (b), (j) stale |

---

## 5. Deployed versus committed, as of 2026-08-27

*Superseding amendment 2026-08-27. Two deploys happened on 2026-08-26 after the previous
reconciliation; the §5.1 text below it describes 08-26 06:19 and is retained only as history.*

### 5.0 Current state (2026-08-27)

*Superseding amendment 2026-08-31 (night) — a SECOND regionserver deploy today at 21:59, staged,
SHA-256-verified, rollback-backed [SRC: deploy verification; live region log 22:01–22:06]. The
19:01 block below is history for the three assemblies this deploy replaced:*

| # | Time | Content | Binaries | Rollback |
|---|---|---|---|---|
| 5 | 2026-08-31 21:59 | `d95754509d` — S-CON-1 (`7240797fc8`) + S-CON-2 (`db858f4cff`) + S-CON-3 (`d95754509d`): connector policy record + registry, NPC lifecycle/voice registration, three disclosure layers; the AllowNpcVoice deny; the CreateViewerSession dispatch fix | `WebRtcVoiceRegionModule` 121,344 B, `WebRtcVoiceServiceModule` 23,040 B, `WebRtcVoice` 20,480 B — all **Debug** (+ PDBs 50,024 / 19,888 / 21,052 B) | `20260831-215949` |

**Per-DLL running build now** [SRC: nbgv stamps read at the deploy]: `WebRtcVoiceRegionModule.dll`
`1.1.160-alpha+d95754509d` (was `1.1.150-alpha+2ff8bf57af`); `WebRtcVoiceServiceModule.dll`
`1.1.160-alpha+d95754509d` (was `1.1.148-alpha+d23e41c762`); `WebRtcVoice.dll`
`1.1.160-alpha+d95754509d` (was `1.1.114-alpha+119fea881e` — its first change since the original
baseline: the S-CON-1 `IVoiceConnectorRegistry` type; AssemblyVersion pinned `1.1.0.0`, so the
stayed-behind `WebRtcJanusService.dll` binds unchanged). Unchanged:
`OpenSim.Framework.Servers.HttpServer.dll` `1.1.154-alpha+c972136380` (Release);
`WebRtcJanusService.dll` `1.1.154-alpha+c972136380`; `VoiceVisibility.dll`
`1.1.135-alpha+18640868dc`. Mixer unchanged (`5e0d637` / image `1bf042e373dc`). **Nothing
committed to either branch is undeployed** (HEAD `d95754509d` is the deployed build). Live
verification of the connector slices: O-40 (§4.1) and `connector-build-plan.md`.

**Cosmetic (should-fix, deliberately NOT an O-item):** `[CONNECTOR] loaded <name> …` logs once per
region INSTANCE for a Region-scoped record — the non-shared module loads the registry per region,
so a record pinned to one region still prints a "loaded" line in each. NPC creation is correctly
scoped by the record's Region filter; the duplicate line is log noise only.

*Superseding amendment 2026-08-31 (evening) — one regionserver deploy today at 19:01, staged,
SHA-256-verified, rollback-backed [SRC: deploy verification]. The 2026-08-30 table and
running-build paragraph below are history for the two assemblies this deploy replaced:*

| # | Time | Content | Binaries | Rollback |
|---|---|---|---|---|
| 4 | 2026-08-31 19:01 | `c972136380` — O-41 logout disconnect + O-43 caps-wrapper exception logging (`b72afc9fb8`) | `OpenSim.Framework.Servers.HttpServer` 141,824 B **Release** (+ PDB 69,944 B); `WebRtcJanusService` 90,112 B Debug (+ PDB 40,568 B) | `20260831-190101` |

**Per-DLL running build now** [SRC: nbgv stamps read at the deploy]:
`OpenSim.Framework.Servers.HttpServer.dll` `1.1.154-alpha+c972136380` (**Release**; was
`1.1.114-alpha+119fea881e`); `WebRtcJanusService.dll` `1.1.154-alpha+c972136380` (Debug; was
`1.1.145-alpha+d2506aab55`). The other voice DLLs are unchanged: `WebRtcVoiceRegionModule.dll`
`1.1.150-alpha+2ff8bf57af` (S-A2A-6 — the stamp read live at this deploy's baseline check; the
2026-08-30 paragraph below predates the S-A2A-6 deploy that the 08-31 morning amendment's
"Regionserver unchanged at `2ff8bf57af`" records); `WebRtcVoiceServiceModule.dll`
`1.1.148-alpha+d23e41c762`; `VoiceVisibility.dll` `1.1.135-alpha+18640868dc`; `WebRtcVoice.dll`
`1.1.114-alpha+119fea881e`. Mixer unchanged (`5e0d637` / image `1bf042e373dc`). **Nothing
committed to either branch is undeployed** (HEAD `c972136380` is the deployed build).

**Deploy convention, recorded as standing:** core DLLs deploy **Release** (the deploy root's core
is the Release@`119fea881e` build; this deploy is the first core DLL this programme has replaced),
voice DLLs deploy **Debug**. Each DLL ships with the PDB from its own build.

**Lineage note:** O-43 lives in core (`Source/`), so it reaches the gridserver only via the
voice→integration merge per the deploy-lineage rule below (recorded 2026-08-30); the regionserver
deploy does not wait on it.

*Superseding amendment 2026-08-30 (evening) — three regionserver deploys today; the morning amendment
below is history.* All three staged, SHA-256-verified, rollback-backed [SRC: deploy verifications]:

| # | Time | Content | Binaries | Rollback |
|---|---|---|---|---|
| 1 | 13:55 | `d2506aab55` — counter fix (`0190d864ef`), logout fix, S-A2A-1..5 | `WebRtcVoiceRegionModule` 97,280 B, `WebRtcJanusService` 90,112 B, `WebRtcVoiceServiceModule` 20,992 B (+ PDBs) | `20260830-135503` |
| 2 | 15:43 | `182660547b` — S-A2A-2.1 (invite gating) + S-A2A-2.2 (body keys) | `WebRtcVoiceRegionModule` 97,792 B (+ PDB) | `20260830-154344` |
| 3 | 16:27 | `d23e41c762` — S-A2A-3.1 (shared non-spatial instance; no-session logout) | `WebRtcVoiceServiceModule` 21,504 B (+ PDB) | `20260830-162716` |

**Running build = sim through `d23e41c762` for the shipped voice binaries**, per-DLL stamps [SRC:
nbgv stamps read at deploy]: `WebRtcVoiceRegionModule.dll` `1.1.147-alpha+182660547b`;
`WebRtcVoiceServiceModule.dll` `1.1.148-alpha+d23e41c762`; `WebRtcJanusService.dll`
`1.1.145-alpha+d2506aab55`; `VoiceVisibility.dll` `1.1.135-alpha+18640868dc` and `WebRtcVoice.dll`
`1.1.114-alpha+119fea881e` (both unchanged-source, deliberately not redeployed — MVID/stamp-only
churn). **Mixer** *(amended 2026-08-31)*: **now `5e0d637` / image `1bf042e373dc`, deployed
2026-08-31 ~05:2x, container verified** [SRC: operator confirmation; the 05:27 live call ran on it].
Commit lineage from the long-standing `27977c8`/`a5a6c7b7189a`: `6ee39be` (M-A2A-1 presence fence +
counters) → `3f4780d` (M-A2A-2 announce every removal + leave counter) → `5e0d637` (M-A2A-3
attach-window backlog re-send); image lineage — **all three deployed** — `ed2448458d48` (M-A2A-1,
deployed 08-30) → `de95be523675` (M-A2A-2, deployed ~04:40 2026-08-31, container verified; it ran
the 04:42 call whose `presence_leave_pushed=1` with a stranded callee was the FALSIFIER that led to
M-A2A-3) → `1bf042e373dc` (deployed, the 05:27 closing call).
`query_session` now carries the presence delivery counters: `presence_pushed`,
`presence_leave_pushed`, `presence_dropped_dc_closed`, `backlog_resent`. **Regionserver unchanged
at `2ff8bf57af`.** Nothing committed to either branch is undeployed.

**Watch-list results (the four items below, answered 2026-08-30 evening)** [SRC: live region log]:
item 1 — **`addressed 1 room(s) [226001844:excl0+mute1]`** on the 18:07 moderation mute: the counter
fix (`0190d864ef`) is confirmed live and the 2026-08-28 sink-counter investigation is **fully
closed**. Item 2 — logout teardowns confirmed on the wire: `decision=logout` lines, **no**
`refusing provision with channel_type ""`. Item 3 — no ghost-participant accumulation observed
across the session's toggles. Item 4 — **the first live A2A call PASSED** (§3, 17:50).

*Superseding amendment 2026-08-30 (morning, retained as history).* **The running build is unchanged** since the 2026-08-28 amendment
below: sim through `2b58c74f9a` (+ docs), mixer `27977c8` / image `a5a6c7b7189a` [SRC: no deploy since;
§5.0 rollback stamps]. HEAD of `feature/voice-visibility-matrix` is now **`116dc8e3aa`**. **Committed
but NOT deployed, in order:** `0190d864ef` (the mute-channel counter fix — still the first undeployed
sim commit), then the A2A slices `636842d8ee` (S-A2A-1), `f0fe15ab03` (S-A2A-2), `f73fa4584b`
(S-A2A-3, which also carries the **logout-403 fix**), `90d74cfc69` (S-A2A-4), `116dc8e3aa` (S-A2A-5);
the docs commits in between deploy nothing. **The next sim deploy therefore carries three things:**
the counter fix, the logout fix, and all five A2A slices — binaries `WebRtcVoiceRegionModule.dll`,
`WebRtcJanusService.dll`, `WebRtcVoiceServiceModule.dll` (+ PDBs); `VoiceVisibility.dll` only if its
source changed (it did not in the A2A slices). **Watch list for that deploy, in order:**

1. **`addressed 1 room(s)`** on the first moderation event — owed since `0190d864ef` (§3 sink-counter
   item); `[VOICE VISIBILITY] +1/-0` alongside it.
2. **`[A2A PROVISION] … decision=logout`** on every voice teardown, and **NO** `refusing provision
   with channel_type ""` on relog or voice toggle (§3 item-0 finding).
3. **Mixer participant counts stable across voice toggles within a login** (`handle_info` /
   `janus list rooms`): no ghost participant accumulates per toggle.
4. **The first live A2A call**, per `a2a-build-plan.md` §4 — the seven steps in order; any deviation
   from the wire trace's predictions is recorded against the trace before code changes.

**Deploy lineage (recorded 2026-08-30 from session decisions):** `D:\legiongrid\regionserver` builds
from **`feature/voice-visibility-matrix`** until the trusted-hypergrid work is deliberately landed
there; `D:\legiongrid\gridserver` is **already integration-lineage**
(`integration/legiongrid-trusted-hg`). **Merge voice → integration remains the rule** for any
integration-lineage deploy root; the regionserver's voice deploys do not wait on it.

*Superseding amendment 2026-08-28.* The evening `b80efffa36` line below is itself superseded: on
**2026-08-27 20:49** a six-file staging deploy landed `d9fa72c351` + `33fc3b412e` + `2b58c74f9a` over
`b80efffa36` (`VoiceVisibility.dll` 20,480 B, `WebRtcVoiceRegionModule.dll` 76,800 B,
`WebRtcJanusService.dll` 88,576 B + matching PDBs; rollback `20260827-204551`), and on **2026-08-28**
moderation-mute delivery was **verified live in-world and via the admin API** on that build
(`mod_muted_entries` 1→0 across a menu mute/unmute; §3 sink-counter item). **The running build is now
sim through `2b58c74f9a`** (docs commit `18640868dc` carried no binary) **plus mixer `27977c8` / image
`a5a6c7b7189a`.** **`0190d864ef` (the mute-channel counter fix) is the FIRST commit past the running
build** — committed 2026-08-28, undeployed, counters/logging only, rides the next staging batch.

*Superseding amendment 2026-08-27 (evening) — today's two sim deploys and the mixer deploy.* HEAD of
`feature/voice-visibility-matrix` is now **`b80efffa36`**. Two sim staging deploys happened today, each
staged, SHA-256-verified and rollback-backed [SRC: deploy reports]: **(1) afternoon — `02ce1b9b10`**
(moderation mute channel) copied `VoiceVisibility.dll` + `.pdb` and `WebRtcVoiceRegionModule.dll` +
`.pdb` into `D:\legiongrid\regionserver` (build 16:17; rollback `20260827-161903`); **(2) evening —
`b80efffa36`** (empty-column pending guard) copied **only** `WebRtcVoiceRegionModule.dll` (71,168 B,
SHA `03F8122B…`) + `.pdb` (build 19:17; rollback `20260827-191855`) — `VoiceVisibility.dll` was
deliberately **not** redeployed: no Visibility-project source changed, and its rebuilt SHA differed only
by Debug-build non-determinism (identical byte size), confirmed via `git diff`. The Robust/gridserver
side got nothing. **Mixer: now at `27977c8`** (`354e9fe` join backlog + `0d6d0d0` mute channel +
`27977c8` join-window deferral); image rebuilt (`.so` 306,600 B) and **deployed**. **Unlike every prior
reconciliation, this evening's work was verified LIVE in-world and via the admin API** — moderation
mute greys/ungreys, the late-joiner backlog, and the deferral/replay all confirmed running. The
pre-evening detail below is retained as history.


**Region: still stopped.** No `OpenSim.Server.RegionServer.exe`; 9000/9001/9002/8003 all free
[SRC: process and port query at both deploys and at this reconciliation]. **Nothing committed to
this branch is undeployed, and nothing deployed has been exercised in-world.**

**Two deploys on 2026-08-26**, both staged, hash-verified and rollback-backed [SRC: deploy
reports; file timestamps and SHA-256 recorded at each]:

| # | Time | Content | Rollback |
|---|---|---|---|
| 1 | 16:22–16:23 | S1 + S1b + S2 + S3a + S3b — `WebRtcVoiceRegionModule.dll` (16:23:03), `WebRtcJanusService.dll` (16:23:03), `VoiceVisibility.dll` (16:22:44) | `regionserver-20260826-162213-backup` |
| 2 | 18:42–18:48 | Moderation console commands (`935bd5b6d2`) — `WebRtcVoiceRegionModule.dll` **and** `.pdb`, both built 18:43:23 | `regionserver-20260826-184209-backup` |

**Deploy-root voice binaries now** [SRC: read 2026-08-27]:
`WebRtcVoiceRegionModule.dll` 2026-08-26 18:43:23 / 68,608 B (SHA-256 `32A7DFF9…8807276E`);
`WebRtcVoiceRegionModule.pdb` 18:43:23 / 33,112 B; `WebRtcJanusService.dll` 16:23:03 / 86,528 B;
`VoiceVisibility.dll` 16:22:44 / 17,920 B; `WebRtcVoice.dll` and `WebRtcVoiceServiceModule.dll`
**unchanged at 2026-08-25 15:47** — no source in either changed this cycle.

Deploy 2 also replaced a **stale PDB**: the deployed `.pdb` had been dated 2026-08-25 20:11 while
its DLL was 2026-08-26 16:23, so Debug stack traces from that assembly carried wrong line numbers
between the two deploys. DLL and PDB now share a build time.

**Committed but NOT deployed: nothing.** HEAD `935bd5b6d2` is the deployed build.
**Deployed but not verified in-world: everything since 2026-08-25 20:29** — S1, S1b, S2, S3a, S3b
and the moderation console commands have never run on a started region (U-11, U-12).

**Mixer: unchanged.** Still `872f0d9`, still image `0.9.0`; no mixer commit this cycle [SRC:
`git log`]. Whether the running container still matches is U-1, unchanged.

### 5.1 History — the 2026-08-26 06:19 reconciliation (retained, superseded by §5.0)

### Region side — `D:\legiongrid\regionserver`
- **Not running at reconciliation time.** No `OpenSim.Server.RegionServer.exe` process; nothing
  listening on 9000/9001/9002/8003; last line of today's log is `Hosting stopped` at 04:54:31
  [SRC: process/port query; log].
- **Last run:** started 2026-08-25 20:29:28, host build `OpenSim-NGC Tranquillity Release
  1.1.114-alpha+119fea881e` [SRC: log `[STARTUP]: Version`], ran until 04:54 today.
- **Voice binaries in the deploy root** [SRC: file timestamps and SHA-256 recorded at deploy]:
  `WebRtcVoiceRegionModule.dll` built 2026-08-25 20:11:58 from *fix(voice): enforce parcel
  ban/restrict on the estate voice channel* (`ec3ad9b2f2`), hash `F61BD8D13C90…`, copied 20:19,
  loaded at the 20:29 start; `WebRtcJanusService.dll`, `WebRtcVoice.dll`,
  `WebRtcVoiceServiceModule.dll`, `VoiceVisibility.dll` from a 2026-08-25 15:47 build. [INF] that
  15:47 build corresponds to `119fea881e` for these four assemblies, because no voice source
  outside the region module changed between *voice: convert the last four log4net call sites to
  ILogger* (2026-08-23) and `ec3ad9b2f2`.
- **Proof the visibility path ran on that build** [SRC: log 2026-08-25 20:29:33–35]:
  `[JANUS PEERCTL SINK]` constructed for Ebony (estate room 226001844), Transylvania
  (1578726032), Elm (1967062692); `[VOICE VISIBILITY] feeder started … @ 250ms (emit=True)` for
  each; zero `GIVING UP` / `ProtocolError` / latch / stuck-in-flight / derivation-error lines
  through shutdown. Elm's number matches the float-hash replication in `KnownDefects.md`.
- **Live config** [SRC: `config\OpenSim.ini`]: `[WebRtcVoice] Enabled = true`,
  `SpatialVoiceService` and `NonSpatialVoiceService` = `WebRtcJanusService.dll:WebRtcJanusService`
  (local-Janus topology, not the connector), `VisibilityFeederEnabled = true`,
  `VisibilityEmitEnabled = true`, `VisibilityTickMs = 250`; `[JanusWebRtcVoice]` gateway
  `192.168.1.225:24223/voice`, admin `:24225/voiceAdmin`, `PluginName = janus.plugin.slvoice`.
- **Committed but NOT deployed:** S1, *feat(voice): return the joined room in the provision
  success response* (`3c95ddea0e`). Today's 05:55 build hashes differ from every deploy-root
  voice DLL, all of which are dated 2026-08-25 [SRC]. Documentation commits deploy nothing.

### Mixer side — container `legion-voice-mixer-janus-1`
- **Running.** Image `ghcr.io/johnlegionh/legion-voice-mixer:latest`, id `sha256:86d6ec82…`,
  container started 2026-08-25 20:01:56 local, "Up 10 hours" at reconciliation [SRC: `docker ps`,
  `docker inspect`]. Plugin log: `Legion SLVoice mixer initialized! (API v106, 0.9.0)` [SRC:
  `docker logs`]. The region log's burst of Janus session errors at 20:01:38 is the mixer restart
  seen from the sim [SRC].
- **What it was built from** [SRC: `docker history`]: `COPY src` at 2026-08-25 19:49:41 local,
  then `make test && make && make install` at 19:49:43 — so the mixer unit tests **passed at
  image build** for that source. Commit `872f0d9` is timestamped 19:51:55 local — **two minutes
  after the image**. No image label carries a commit; the Dockerfile has no revision ARG.
- [INF] The image contains the `872f0d9` change set. Basis: the working tree at 19:49 carried
  the same 35-insertion/11-deletion diff that was committed unchanged at 19:51, and the tree was
  clean immediately after. This is inference, not proof; §6 says what would prove it.
- **Committed but not deployed (mixer):** nothing, if the inference holds. The optional M1
  version bump does not exist yet.

### On the statement "both halves are deployed as of today"
Both halves were deployed on **2026-08-25** — the region module fix at 20:19–20:29 and the
mixer image at 19:49–20:01 — and the region ran on them overnight. As of this reconciliation the
region is **stopped**, and the one code commit made today (S1) is **not** deployed. If "today"
meant the 08-25 evening deploy, the statement holds; if it meant the current HEAD, it does not.

---

## 6. Could not determine, and what would settle each

| # | Unknown | What settles it |
|---|---|---|
| U-1 | Whether the running mixer image contains `872f0d9` (inferred from timestamps only) | Any of: `strings janus_slvoice.so \| grep unknown_request` inside the container; an admin `message_plugin` with an unknown `request` (expect `{"slvoice":"error","reason":"unknown_request"}`); or M1's version bump on the next image |
| U-2 | Which commit the four 15:47 voice DLLs were built from | No build stamp in the deploy root; a `git describe` or `AssemblyInformationalVersion` stamped into addon DLLs would settle it going forward |
| U-3 | Whether Phase 2's two-party mix was ever formally accepted (CHECK 3) | No dated record exists; the 3a acceptance runs imply it; a one-line dated note in `phase1-bringup.md` would close it |
| U-4 | Whether Phase 3b spatial rendering has been verified by ear in-world | No record; the brief says numeric-only. A dated listening check (two avatars, azimuth left/right, distance fade) |
| U-5 | Whether a stock viewer re-provisions on intra-region parcel crossing (drives O-11's severity) | A crossing with `MessageDetails = true` and watching for a second `ProvisionVoiceAccountRequest` |
| U-6 | Whether the protocol-compat audiobridge-superset constraint is still meant to bind (O-23) | An owner decision recorded in `protocol-compat.md` |
| U-7 | Whether ILogger output reaches log4net in production (bears on O-20's scope: are stall logs visible live?) | Read the region's logging bootstrap; or force a stall and look for the line |
| U-8 | The mixer admin round-trip under load (the per-room brief's crossover uses a 2.5–3.3 ms loopback floor for a trivial request) | Timing a real `peer_ctl_batch` from the sink |
| U-9 | Exact membership of every parcel's `UseEstateVoiceChan` on this grid (four parcels sampled; Elm clear) | `SELECT RegionUUID, LocalLandID, Name, LandFlags & 0x40000000 FROM land` |
| U-10 | The OpenMetaverse `ParcelFlags` enum text (binary package only; values taken from the SL header, corroborated twice) | A reflection dump of `UtopiaSkye.OpenMetaverse` or its source tag |
| U-11 | *Added 2026-08-27.* Whether per-room emission (S3b) actually addresses per-parcel rooms correctly in-world — the brief named S3b the first in-world-testable step and it has never run on a started region | Start the region, put two avatars on different parcels of one region, and confirm each is addressed at its own room number (mixer `handle_info`, `excluded_entries`) rather than the estate room |
| U-12 | *Added 2026-08-27.* Whether the two moderation console commands register and function on a live region | Start a region with `VisibilityFeederEnabled = true`, run `help Voice`, then `show voice moderation` and `voice moderation unmute <uuid>` |
| U-13 | *Added 2026-08-27.* Whether a viewer can actually drive `multiagent` provisioning today (drives O-29's severity: latent vs live) | Grep the viewer for `"multiagent"` and for its handling of `ChatterBoxSessionStartReply` with `voice_enabled:false` — out of scope for the sim-side assessment |
| U-14 | *Added 2026-08-27.* Whether any **other** Janus plugin loaded in the same container exposes a client-reachable signalling path (bounds the §7.1 no-P2P finding to this plugin) | Read the container's `janus.jcfg` plugin list |
| U-15 | *Added 2026-08-27.* Runtime confirmation of the no-P2P guarantee (§7.1 is static analysis) | Packet capture on a two-avatar session confirming no client-to-client ICE candidate ever appears |

---

## 7. Reviewer-condition assessment, 2026-08-27

*Added 2026-08-27. Recorded here because it existed nowhere else. Every finding below is [SRC],
established by reading source in both repos at the basis commits; nothing here is [DOC] or
[INF] unless marked.*

### 7.1 The no-peer-to-peer guarantee — HOLDS, and is structural

**The condition:** person-to-person voice must never be true P2P, because ICE candidate exchange
between clients exposes each party's IP address to the other. Media must route through the
server.

**Finding: no code path can return another client's ICE candidates, SDP, or transport addresses
to a client.** This is a property of the architecture, not an unimplemented feature.

**How it was established — this is the re-checkable part.** By enumeration, not by assumption:

1. **All three response builders.** Every map a client can receive from provisioning is built in
   one file, `Janus/ProvisionResponseBuilder.cs`, whose header states it is the single definition
   of the shape and that `ProvisionResponseShapeTests` pins it byte-for-byte:
   `BuildSuccess` → `{ jsep, viewer_session, room }` (`:21`–`:29`); `BuildFailure` →
   `{ response, error, error_code? }` (`:34`–`:44`); `BuildClosed` → `{ response }` (`:47`–`:53`).
2. **The provenance of the only SDP in there.** `viewerSession.Answer` is assigned exactly once,
   at `JanusRoom.cs:83`, from `joinResp.Jsep` — **the media server's own answer to this session's
   join**. On the mixer side that answer is synthesised by `janus_slvoice_negotiate`
   (`janus_slvoice.c:1389`–`:1470`), which parses the client's offer and calls
   `janus_sdp_generate_answer` to describe *the server's* transport. No participant's SDP is
   copied into another's.
3. **Both CAP handlers.** `ProvisionVoiceAccountRequest` returns only the maps above.
   `VoiceSignalingRequest` (`WebRtcVoiceRegionModule.cs:591`–`:637`) computes a response, logs it,
   and then **unconditionally** writes `llsdUndefAnswerBytes` at `:634` — the service's response is
   discarded and the client receives `<llsd><undef /></llsd>`. The handler is structurally
   incapable of returning anything. (This is the load-bearing role of the TODO at O-36.)
4. **The Janus event loop.** ICE flows one way. Client → sim → Janus via `TrickleCandidates` /
   `TrickleCompleted` (`WebRtcJanusService.cs:334`–`:386`). In reverse, Janus's own trickle events
   arrive at `JanusSession.cs:530`–`:535` under the in-source comment *"this is for reverse
   communication from Janus to the client and we don't do that"* and fire `OnTrickle` —
   **which has no subscribers**: the whole addon yields only the declaration (`:466`), the
   null-on-teardown (`:480`) and the invocation (`:534`). Server candidates are logged and dropped.
5. **Both connector hops.** `WebRtcVoiceServiceConnector.cs:95`–`:115` wraps the request and
   returns the service map unchanged; `WebRtcVoiceServerConnector.cs:95`–`:125` unwraps and assigns
   `pResponse.Result = resp`. Transparent forwarders; neither synthesises nor cross-references.
6. **The mixer's client-facing emissions.** `janus_slvoice_participant_summary`
   (`janus_slvoice.c:1493`–`:1502`) emits exactly `id`, `display` (agent UUID), `setup`, `muted`.
   The data channel carries only `j`/`l` presence, `p`/`v` power and VAD, `m` mute, `ug` gain. No
   SDP, no candidates, no addresses.

**Why a security reviewer should accept it.** Each client negotiates one PeerConnection *with the
media server* and receives only an answer the server generated about itself. There is no
session-to-session lookup anywhere in provisioning or signalling — no handler takes another
agent's id and returns transport state for it, and the only cross-session structure in the mixer
is the four-field summary above. Media is genuinely mixed server-side: pass 1 decodes each source
into `s->decbuf`, pass 2 builds a per-listener N-minus-one mix and relays it on that listener's own
handle (`janus_slvoice.c:2434`–`:2470`). A client receives **one** synthesised stream, never
per-peer streams. Even if a client wanted to connect directly to another, it is never given
anything to connect to.

**Not overstated:** `janus list rooms` (`WebRtcJanusService.cs:449`–`:470`) prints participant ids,
names, muted/talking and spatial position — to the **region operator's console**. Server-side
operator output, same non-transport fields, not a client-reachable path.

**Scope bounds:** this covers `janus.plugin.slvoice` and the sim. A stock plugin left enabled in
the same Janus instance is outside it (U-14). The analysis is static (U-15).

### 7.2 The `multiagent` authorisation gap

Every access check in the region module's provisioning path — estate `AllowVoice`, `LandChannel`
presence, parcel resolution, `AllowVoiceChat`, `UseEstateVoiceChan`, `IsRestrictedFromLand`,
`IsBannedFromLand` — is nested inside `if (channelType == "local")`
(`WebRtcVoiceRegionModule.cs:472`–`:547`). A request with `channel_type="multiagent"` skips all of
it and reaches `voiceService.ProvisionVoiceAccountRequest` directly. Nothing drives that path
today (§7.3), so it is **latent** — but it is one viewer change from being live, and it sits
directly beneath the avatar-to-avatar feature. **O-29; ship-blocking (§8).**

### 7.3 Avatar-to-avatar voice — has never worked

`CalcRoomNumber` accepts `"multiagent"` and derives a grid-unique room
(`JanusAudioBridge.cs:207`–`:211`), and `SelectRoom` passes `pSpatial=false` through. That is the
whole of the working plumbing. The handshake terminates before a second party exists:

- **The callee is never invited.** `ChatterBoxInvitation` is defined at
  `EventQueueGetHandlers.cs:219` and **has no callers anywhere** in `Source/` or `Addons/`. The
  other party learns nothing, never provisions, never joins.
- **`voice_enabled` is sent `false`.** Matching `WebRtcVoiceRegionModule.cs:715`–`:724` against the
  signature at `EventQueueGetHandlers.cs:259`–`:262`, the fourth argument is literal `false`.
- **The session name is the caller's own** (`sp.Name`, `:717`), not the other party's.
- **The credential handshake does not exist.** `credentials` is read into a local at
  `WebRtcJanusService.cs:242` and **never used again** in that file.
- **Every other ChatSession method is a stub** — `"decline p2p voice"`, `"decline invitation"`,
  `"start conference"`, `"fetch history"` all return bare `OK` under the comment *"we don't know
  how to handle. Just return OK for now."* (`:690`–`:697`).

**Minimum to make it work:** invite the callee; fix `voice_enabled` and the session name;
implement accept/decline; and close O-29 so only the two agents named in the session id can join
that room. **O-30; deferred (§8).**

### 7.4 Hypergrid visitors

**No difference from local users, at all.** The voice addon contains **zero** references to
`Hypergrid`, `IsLocalGridUser`, `ForeignAgent`, `UserAgentService` or `scopeID`. Caps are
registered per-agent in `OnRegisterCaps` with no origin check, and every downstream authorisation
keys on the local `agentID`, so parcel and estate controls apply to HG visitors identically —
that part is sound. It also means a visitor from any federated grid receives voice provisioning on
exactly the same terms as a resident, with no additional gate, and their agent UUID is what the
mixer uses as `display` and what appears in other clients' rosters. Whether that is acceptable is
policy, unanswered — spec §3.2 and §10 item 1. **O-38; deferred (§8).**
UNKNOWN: whether an HG visitor can hold parcel-voice-moderator rights (turns on group powers and
estate-manager status, which HG visitors normally cannot hold — not traced end to end).

### 7.5 Connector hooks — nothing exists; tap is days, injection is weeks

No tap, no recording, no RTP forwarding, no file source, no injection point: grepping the mixer
for `rtp_forward`, `forwarder`, `record`, `recording`, `.wav`, `fopen`, `inject`, `file_source`,
`announcement`, `hook`, `tap` yields no functional hits. The participant abstraction does **not**
admit a server-originated source: `janus_slvoice_session` is bound to `janus_plugin_session
*handle` (`:348`), the codebase dereferences `->handle` in 19 places, pass 2 skips any session
with `!webrtc_up || !media_ready` (`:2447`–`:2448`), and pass 1's decode expects a jitter buffer
fed by `incoming_rtp`.

**A tap is days [INF, from the above SRC facts].** Pass 1 already decodes every source exactly once
into `s->decbuf` at a known frame size and rate, tick-owned and stable for the rest of the tick
(`:2434`–`:2442`). A per-source copy handed to a writer thread is purely additive and cannot
perturb mix timing. The control surface already exists: `janus_slvoice_handle_admin_message`
(`:1328`) currently accepts exactly one request, `peer_ctl_batch` (`:1337`), so a `start_tap` /
`stop_tap` request extends a proven path.

**Injection is weeks [INF].** It needs a session variant whose pass-1 decode pulls PCM from a
source, an audit of all 19 `->handle` dereferences, and guards in `relay_data`, `relay_rtp`,
`push_presence` and `query_session`. It also needs **semantics decisions that have not been
made**: does an injected source appear in the roster, is it subject to spatial attenuation and the
visibility matrix, can moderation exclude it? Those are O-17's Q2–Q6, still open.

### 7.6 Findings that would embarrass us in front of testers

Ranked by how likely a tester or reviewer is to hit them. All [SRC].

1. **`multiagent` provisioning bypasses every access check** — O-29, §7.2. Latent today; the one
   with real consequences.
2. **Methods named `…BAD` on production paths** — `ProvisionVoiceAccountRequestBAD` (`:211`),
   `VoiceSignalingRequestBAD` (`:334`). Anyone reading a stack trace sees "BAD" in it. O-31.
3. **Sync-over-async on the request path** — six `.Result` calls in `WebRtcJanusService.cs`
   (`:137`, `:208`, `:331`, `:437`, `:449`, `:466`), including provisioning and signalling. The
   classic deadlock shape; works under the current host, first thing a reviewer flags. O-32.
4. **`Math.Abs(hashed.GetHashCode())` can throw** — `JanusAudioBridge.cs:219`;
   `Math.Abs(int.MinValue)` raises `OverflowException`. Roughly 1-in-4-billion, fails hard, and
   trivially avoidable. O-33.
5. **A stale comment that misstates the security posture** — `WebRtcJanusService.cs:239` claims
   `channel_type` "has already been checked to be 'local'". False, and it hides O-29 from a reader
   who trusts it. O-34.
6. **`CalcRoomNumber` `multiagent` grid collision** — hashes only `channelID` + `channelType`,
   in-source comment "should add a GridId here" (`:207`–`:211`). O-35.
7. **Unfinished TODO on the signalling response** — `WebRtcVoiceRegionModule.cs:632`, directly
   above the line that discards the response. Cosmetic, and see §7.1 item 3 for why that discard is
   currently load-bearing. O-36.

### 7.7 Viewer-side work — cross-reference, not duplicated

Two documents live in `phoenix-firestorm` on branch `fix/voice-webrtc-fixes` and are **the
authority for their subjects**. Do not copy their content here; amend them there.

- **`docs/voice-participant-row-suppression.md`** — OPEN defect, mechanism UNKNOWN: a stored
  per-avatar volume in `volume_settings.xml` can permanently suppress that avatar's participant
  row while audio keeps working, surviving grid restart, viewer restart, relog and teleport.
  Carries the trigger, the workaround, the ruled-out list and three discriminating tests. O-37.
- **`docs/voice-moderation-menu-acceptance-test.md`** — PENDING acceptance test for the
  Conversations-floater Mute/Unmute fix (`4e205cad31`), never run. Carries the four-combination
  table, the sim-log and `show voice moderation` confirmations, the
  `voice moderation unmute` recovery path, a required group non-regression spot check, and the
  procedure for telling the row-suppression defect apart from a failure of the fix.

---

## 8. Release candidate — 2026-08-27

*Added 2026-08-27. **The classification below was given by the programme owner, not decided by
this ledger.** Where the reconciler disagreed, the disagreement is recorded in the reconciliation
report, not by moving an item.*

### 8.1 SHIP-BLOCKING

*Updated 2026-08-27: the `multiagent` gap (O-29, `d9fa72c351`) and build-plan steps S4
(`33fc3b412e`) and S5 (this ledger) are DONE and struck from this list. One item remains before the
tester line.*

*Updated 2026-08-28: those three commits WERE deployed (2026-08-27 20:49 six-file staging) and
moderation-mute delivery is now proven live in-world and via the admin API (§3, §5.0). The acceptance
test remains the sole ship-blocker; its outstanding parts are enumerated below.*

| Item | Why | Ref |
|---|---|---|
| Run the formal in-world acceptance test to completion | Moderation-mute *delivery* is proven; the documented menu/parity acceptance procedure has still not been run through | §7.7, U-11, U-12; O-37 |

**Outstanding parts of the acceptance test:** the **group-session spot check** (needs a third avatar
in voice, to prove the nearby normalisation does not moderate the whole session); the
**`volume_settings.xml` confirm-or-note** (clear in both accounts so O-37 cannot confound a missing
row); the **viewer-repo doc amendments** (`voice-participant-row-suppression.md` and the acceptance-test
doc — phoenix-firestorm, not this tree); and the **results entry** recording the run. The counter fix
`0190d864ef` is committed but undeployed, so a re-run after the next staging should additionally show a
mute logging `addressed 1 room(s)`.

### 8.2 SHOULD-FIX BEFORE TESTERS

*Updated 2026-08-27: O-33 (the `Math.Abs` overflow, fixed `2b58c74f9a`) and O-34 (the stale `:239`
comment, now accurate as of `d9fa72c351`) are struck.*

| Item | Ref |
|---|---|
| The `…BAD` method names on production paths | O-31 |
| The visibility ini keys missing from **both** the shipped ini and the example | O-21 |

### 8.3 JUST OUTSIDE THE LINE — the connector tap

**Deliberately not in 8.1 or 8.2, and deliberately not in 8.4.** It is **days** of work (§7.5:
pass-1 `decbuf` is the seam, `handle_admin_message` the control surface, both proven paths), and
it is **a named reviewer's outstanding ask**. It is therefore **the first candidate to pull in if
the blockers clear early**. Injection is a different matter and stays deferred (§8.4).

### 8.4 DEFERRED, WITH THE REASON RECORDED

*Updated 2026-08-30: the avatar-to-avatar row is superseded — S-A2A-1..5 are built and committed
(§3), not deployed and not run in-world. The row is retained as the RC-time classification.*

| Item | Reason for deferral | Ref |
|---|---|---|
| Avatar-to-avatar voice | Has never worked; needs invitation, accept/decline, correct `voice_enabled`, and O-29 closed first. A feature, not a fix | O-30, §7.3 |
| Connector **injection** | Weeks, and blocked on semantics decisions nobody has made: roster visibility, spatial attenuation, matrix and moderation applicability | O-17, §7.5 |
| Voice morphing (spec §7.4) | Behind injection anyway — there is no server-originated source to morph | O-24, spec §7.4 |
| Trust domains and HG policy | Policy question, unanswered. Today HG visitors are treated identically to local users and the addon has no HG-aware code | O-38, §7.4, spec §3.2 / §10.1 |
| Viewer row-suppression defect | Workaround documented and effective; **mechanism unknown**, so a fix would be speculative. Three discriminating tests are written and unrun | O-37, §7.7 |
| OpenSim default land-flags divergence | Upstream question, not ours to settle in this programme | `Docs/audit/webrtc-upstream-audit.md` |

---

## Maintenance

Amend this file on every voice commit that changes a status above, and on every deploy. Bump
**Last reconciled** and name the basis commits. When a [DOC] claim is later verified, upgrade it
to [SRC] with the citation; when a [SRC] citation drifts, fix the citation, not the claim. Keep
the mixer copy in sync per `Docs/voice/.gitattributes`.
