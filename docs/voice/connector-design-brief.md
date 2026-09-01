# Design Brief — Voice Connectors

**Status:** DECIDED 2026-08-31 — build plan: `connector-build-plan.md`.
**Date:** 2026-08-21.
**Basis:** CC recon 2026-08-21 against `legion-voice-mixer @ b4672ff`.
**Origin:** Balpien Hammerer's second deficiency, raised 2026-07-31 — no connector hooks
for post-processing, recording, transcription, or NPC TTS voice. The last of his three
unbuilt; spatialization shipped 2026-08-20, avatar-to-avatar remains separate work.

## Purpose

Let external processes receive room audio and inject audio into it, so the voice system
can support recording, transcription, effects processing, and synthesised speech without
those capabilities being built into the mixer.

## SETTLED: the plain-RTP participant does not exist here

The mechanism originally identified was Janus AudioBridge's plain-RTP participant. **This
mixer does not use AudioBridge** — it is a custom plugin, `janus.plugin.slvoice`, and has
no plain-RTP, socket-based, or non-WebRTC ingress of any kind. A search across `src/` for
socket, bind, listen, plain_rtp and equivalents returns nothing.

Every audio path is a Janus core callback keyed on a `janus_plugin_session *handle`:
`incoming_rtp` (`janus_slvoice.c:2474`) and `gateway->relay_rtp` (`:2123`). Join requires
a JSEP offer and runs SDP negotiation (`:1508`, `:1539`), so a participant is definitionally
a negotiated WebRTC PeerConnection. There is no listen-only or monitor participant type.

**The July identification was correct for AudioBridge and does not transfer.**

## SETTLED: the connector is another peer

The architecture already supplies the mechanism. A connector process joins the room as an
ordinary headless WebRTC peer — offer, negotiate, `setup_media`, `relay_rtp`, data channel
— reusing join, roster, presence, mix and relay wholesale, **with no new mixer code**.

It can record (it receives a real per-listener mixdown) and inject (it sends Opus like any
talker). It is visible in the roster by construction, which §Privacy makes decisive.

It **cannot** provide per-source separated audio. A WebRTC peer receives the mixed N-1
stream, not each talker's individual PCM. Per-speaker transcription or per-speaker
recording would need an in-tick tap, which is a different and larger change.

## Tap and inject points, if an in-tick hook is ever built

Recorded so the option is understood, not endorsed:

- **Per-source PCM** exists at `janus_slvoice_tick_decode_locked` (`:2013`), where
  `opus_decode_float` fills `s->decbuf` with `s->dec_samples` (`:2035`-`:2049`) under
  `s->mutex`. One buffer per source per tick.
- **Per-listener mixdown** exists as the stack `frame` in Pass 2 before
  `opus_encode_float` (`:2100`).
- **Injection** has no raw-PCM path. Pass 1 zeroes `dec_samples` (`:2018`) and repopulates
  strictly from an Opus decode of the jitter buffer (`:2035`), so a direct `decbuf` write
  is overwritten. Injection means Opus-encoding and feeding a session's jitter buffer.

An in-tick callback would run **under `room->mutex` inside the 20 ms tick**, so a slow or
blocking connector stalls the entire room — the same single-core wall the scaling
assessment records. A crashing connector `.so` takes Janus with it. Neither applies to a
headless peer, which is a separate process behind a real transport.

## Privacy — the finding that shapes the design

**A tap is a mechanism for recording people without their knowledge.** Two findings bear
on whether that can be made visible.

**There is no protocol affordance to disclose a tap.** The SLData field set is
`j, l, sp, sh, lp, lh, m, ug, echo` (`sldata.h:48`-`:56`) and nothing else. No record,
tap, monitor, or listen-only field exists. Disclosure through the wire protocol would
require a new field implemented on both the mixer and the viewer.

**A back-door tap is unreliably invisible, which is the worst case.** A synthetic
participant inserted directly into `room->participants`, bypassing the join path, fires no
`push_presence` (`:1590`-`:1593`), so nobody sees it arrive. A later joiner's initial
roster is built by scanning `room->participants` (`:1565`-`:1578`), so it may appear there
— but even that tell is not guaranteed: `participant_summary` reports `setup` from
`webrtc_up` (`:1348`), and a synthetic that fakes `webrtc_up=1` shows `setup:true`,
indistinguishable from a real peer. So a back-door tap can be undisclosed on arrival AND
undetectable in the roster afterwards — which strengthens the case that back-door insertion
is the worst shape and that the only honestly-visible tap is a real joining peer.

**Consequence:** a headless peer that joins properly is the only tap shape that is
honestly visible today, because its visibility comes from being a real participant rather
than from an affordance that does not exist.

## Scope

**First slice — read-only mixdown tap via a headless WebRTC peer.** Recording and
transcription. No injection. Essentially no mixer changes; the work is a WebRTC client on
the connector side.

**GATE — exclusion is not inherited; the first slice cannot ship until Q1 is answered.**
A headless peer does NOT automatically hear only what a listener could hear. Its
`s->excluded` set is created EMPTY at session-create (`janus_slvoice.c:815`) and is
populated ONLY by the sim's incoming visibility feed (the merge at `:1083`-`:1135`). The
visibility matrix is keyed on avatar position / parcel / estate, so it has NO row to compute
for a participant with no avatar position — meaning the sim has nothing to send, `s->excluded`
stays empty, and the listener-mix exclusion at `:2276` (`slv_roster_excludes(s->excluded,
disp)`) drops nobody. **The connector therefore hears everyone.**

This holds unless Q1 is resolved so the connector carries a matrix-legible identity the sim
will compute an exclusion row for, OR the sim explicitly mints and pushes an exclusion set
for the connector's identity. Until one of those is decided, a read-only tap bypasses
`slv_roster_excludes` and **defeats parcel and estate voice semantics wholesale** — so the
first slice is blocked on Q1, not merely informed by it.

**Explicitly not in the first slice:**

- **Per-speaker separated audio** — needs the in-tick tap and its stall and fault risks.
- **Injection** — TTS, NPC voice, effects returned to the room. Needs a synthetic source
  identity, roster presence, and a consent position. The harder half.
- **Any invisible or per-source tap** — should not be built before disclosure is decided.

## Constraints

- **A headless peer consumes an encode slot.** Per the scaling assessment, cost tracks
  audible listeners. Every connector is one more encode per tick.
- **No new mixer ingress.** If the first slice needs mixer changes, that is a signal the
  design is wrong.
- Mixer changes deploy by container rebuild; **tags trigger release CI**.

## Open questions — resolve before freeze

1. **Connector identity — gates the first slice (see §Scope GATE).** A headless peer joins
   with a `display` value, an agent-UUID string, and exclusion is matched BY that string
   (`:1576`, `:2276`). So identity is not only about legibility ("Recorder" vs an unknown
   UUID) — it decides whether the sim's visibility matrix can compute an exclusion row for
   the connector at all. The answer must give the connector an identity the matrix can key
   on AND a position/parcel the matrix can reason about, or it must specify that the sim
   mints and pushes the connector's exclusion set explicitly. A `display` alone, with no
   position, leaves `s->excluded` empty and the tap hears everyone. Resolve: what UUID (real
   avatar / reserved range / sim-minted) AND what position/parcel identity a connector
   carries.

2. **Disclosure model.** Roster visibility is what exists today. Is that sufficient, or
   does a tap warrant an explicit signal — a new SLData field, a viewer-side indicator, a
   region notice? A new SLData field is a THREE-place change, not two: the mixer's parser
   (`sldata.h:48`-`:56` / `sldata.c`), the sim leg that emits it, and the viewer — the last
   being work in a codebase you fork but do not control.

3. **Authorisation.** Who may attach a connector? Nothing currently gates it — anyone who
   can reach the mixer and complete a join can tap a room. Is this estate-owner only,
   grid-operator only, config-gated, or unrestricted?

4. **Injection identity.** A TTS-speaking NPC arriving as a peer needs to *be* somebody:
   a roster entry, a name, and a position. The position is not only for spatialization —
   it is required for the exclusion rules to apply to injected audio *at all*. An injected
   source with no matrix identity and no position is excluded for no listener, so it is
   heard region-wide regardless of parcel or estate voice rules — the inject-side mirror of
   the §Scope GATE. So "needs to be somebody" is a correctness requirement for parcel/estate
   semantics on injection, not a spatialization nicety. Still a sim question (it connects to
   the existing bot/NPC framework) and out of scope for the first slice, but it shapes what
   the second needs.

5. **Hypergrid interaction.** The unenforced policy — grid-service Janus default,
   region-local opt-in, possibly refusing region-local voice for HG visitors — becomes more
   pointed if region operators can also attach recorders. Worth deciding together rather
   than separately.

---

**AMENDMENT 1 — connector identity resolved: NPC presence plus policy record (2026-08-22)**

Q1 asked which UUID a connector should use. An external review reframed it: a connector
must be a first-class principal with an explicit policy record, so that the sim's exclusion
authority holds by construction rather than by whichever identity scheme is chosen. Recon
then established what the tree can support.

**The matrix needs less than expected.** VisibilityRules reads two things from an
AgentView — the Id and the god flag (SeeAVs exemption) — plus two separately resolved
parcel views and one estate view. Position is used solely to resolve a parcel and is then
discarded; the root/child flag only selects which way the parcel is resolved. So a
connector needs an identity, a parcel it resolves to, and a root/child disposition
determining how that resolution happens; everything else is incidental.

**NPCs already satisfy almost all of it.** NPCModule creates a real ScenePresence with a
real position via a full circuit and client-view path (NPCAvatar is an IClientAPI). It is
enumerated by SnapshotAgents, resolves a parcel, and produces a matrix row with no feeder
code change — after one registration step: the feeder's voice-participant filter admits
only agents holding a registered voice session in the region, so the connector's provision
step must register one for the NPC's identity. That registration is the entire integration
delta. IsNPC / PresenceType is never consulted anywhere in the voice path.

**But an NPC alone is the wrong model.** An NPC is an avatar to every subsystem. A
recorder would appear in radar, be clickable, be bannable, and be indistinguishable from a
person. That inherits an identity model built for something else and makes the connector's
nature invisible where it matters.

**Decision: hybrid.** An NPC-backed presence supplies the matrix row; a separate policy
record makes the connector a first-class principal.

- The presence gives parcel resolution, position, and a matrix row at zero cost.
- The policy record carries what the presence cannot: whether the connector may inject,
  its authorised scope, who authorised it, whether it is disclosed, and its nature as a
  connector rather than an avatar.

**Disclosure, now decidable.** The body records that no SLData field can signal a tap.
That remains true, but an NPC-backed connector is visible through existing channels —
radar, the Nearby Voice participant list, and the mixer roster. Disclosure is therefore
achievable without a protocol change. Whether roster visibility is sufficient, or the
policy record should also carry an explicit disclosure obligation, is recorded as open
question 6 below.

**What the policy record needs.** Recon found no existing per-entity policy template in
the tree; estate and parcel permissions are per-avatar-per-land, not per-entity. It must
be built. Minimum fields: connector identity, authorised scope, may-inject flag,
authorising principal, disclosure state.

**What this does not solve:**

- **Injection semantics.** A connector that speaks is a source, and sources are excluded
  by the same rules. An injected voice with a parcel row is constrained correctly; the
  policy record must gate whether injection is permitted at all.
- **Authorisation.** Nothing gates connector attachment today. The policy record is where
  that gate lives, but the mechanism — who creates records, and how — is unspecified.
- **Downstream sensitivity.** A transcript is a durable record of a conversation that was
  subject to per-listener exclusions. Nothing in this design constrains what happens to
  connector output once it leaves the mixer.

**Open question 6:** is roster visibility sufficient disclosure, or must the policy record
carry an explicit obligation — and if so, what enforces it?

---

**AMENDMENT 2 — all remaining questions DECIDED (2026-08-31)**

**Basis:** `Docs/voice/connector-assessment-20260831.md` (ground truth against
`tranquillity-develop @ 6d012a40d2` / `legion-voice-mixer @ 0eb38f1`). Q1 was decided by
Amendment 1; this amendment closes Q2–Q6 and the §Scope gate. Build plan:
`connector-build-plan.md`.

**D1 — Authorisation (Q3): the ini-declared policy record is the gate.** `[VoiceConnector.<name>]`
records in the region config are the sole authorisation: operator-only by construction (writing
the ini IS the grant), no runtime grant/revoke surface in v1. At registration the sim logs the
connector's UUID and room at INFO so the operator can configure the peer from the log line alone.
The peer→Janus leg stays protected by network isolation only — the assessment's §7(c) finding
(the mixer join is ungated and `display` is trusted) is pre-existing and not connector-specific,
so it is filed as its own ledger O-item rather than solved here.

**D2 — Injection (Q4): IN SCOPE.** The origin request (2026-07-31) named NPC TTS voice
explicitly, so injection is part of this programme, not deferred. A record's `may_inject=true`
lifts the default refusal. `may_inject=false` is enforced with existing machinery: the sim pushes
a **moderation mute for the NPC identity at registration** (the mute channel, Option A — every
listener's mix silences the connector; **no mixer change**). Defence in depth for the wider NPC
surface: `[WebRtcVoice] AllowNpcVoice=false` (the default) refuses ANY NPC voice provision except
the connector module's own registration path — NPC voice exists only through a policy record.

**D3 — Disclosure (Q2/Q6) — the governing requirement.** A person must be able to tell they are
talking to an NPC, on a STOCK viewer, during the conversation — not only at the door. Three
mandatory layers:

- **(i) NAME MARKER** — `[WebRtcVoice] NpcNameToken` (set once by the operator, e.g. last name
  "NPC"); every connector's NPC name must carry it or the record is **refused at load**. The name
  is the one channel every stock-viewer surface shows (roster, radar, nametag, speaking dot).
- **(ii) DOOR NOTICES** — a region alert on connector attach and detach, and an entry notice to
  any agent becoming root within the connector's scope while it is attached.
- **(iii) PROXIMITY NOTICE** — the first time an agent comes within voice range of a voiced
  (`may_inject`) NPC, once per agent per NPC per login session, one local chat line stating it is
  an NPC whose voice is automated or remotely operated.

Appearance is optional (a legible name outranks a costume). `disclosed` is ALWAYS true; **no
undisclosed mode exists** — the record field carries the obligation, not an option. This answers
Q6: roster visibility alone is NOT sufficient; the record carries the obligation and the module
enforces it.

**D4 — Hypergrid (Q5): decided as a provisioning rule.** Trusted-HG visitors have region voice
and are recordable/addressable under the same disclosure as residents; untrusted-HG visitors get
NO region voice (their provision is refused), so the recording question is moot for them. This is
a provisioning rule; enforcement ships with the trusted-HG regionserver rollout, not with this
plan — no code here, and the owed enforcement is filed in the ledger as its own O-item so it is
not lost.

**D5 — Runtime: aiortc, containerised, both directions.** The connector peer is an aiortc process
in a container declared in the mixer repo's compose file (it deploys where the mixer deploys).
One codebase serves both directions — recv for recording, send for injection. The Janus
signalling layer is kept deliberately small so a possible later port (to a compiled runtime)
moves little.
