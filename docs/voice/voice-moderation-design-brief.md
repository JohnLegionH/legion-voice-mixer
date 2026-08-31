# Design Brief — Voice Moderation

**Status:** Slice 1 complete and VERIFIED end to end (2026-08-22) — see the resolution at
the end, including the viewer requirement: the feature needs a Firestorm master-tracking
build, not any release as of 7.2.4. Open Question 1 unanswered; Open Question 2 partially
answered by the verification run.
**Date:** 2026-08-21.
**Basis:** CC recon 2026-08-21 against `tranquillity-develop` at `392c0efc1d` (*test(voice):
engine coverage for the §E TaxFree ban path*) and `D:\phoenix-firestorm` (read-only
reference); SL viewer release notes 26.1.0 and 26.2.0.
**Target:** parity with the voice moderation LL shipped in viewer 26.1, Dec 2025.

## Purpose

Let an authorised user silence voice on their land. An event host mutes the room; a
parcel owner deals with a griefer; a group officer moderates their own venue. Not in
the original spec — added because SL shipped it and a social grid will be expected to
have it.

## SETTLED: the viewer is already built and the transport is fixed

**Firestorm 26.2.0 has the moderation UI and already sends the command.** Right-click a
name in the Nearby chat participant list → Moderator Options → Mute everyone / Unmute
everyone / individual mute, wired through `LLNearbyVoiceModeration` to a CAP POST
(`llnearbyvoicemoderation.cpp:73`, `:92`-`:95`, `:127`-`:130`).

**Capability:** `SpatialVoiceModerationRequest`, HTTP POST.
**Body:** `{ "operand": "mute" | "unmute", "agent_id": <uuid> }` for individual,
`{ "operand": "mute_all" | "unmute_all" }` for everyone.

**The body carries no parcel identifier.** Neither operand form names a parcel or region.
The server therefore resolves the target parcel from the requesting agent's
`ScenePresence` position at request time (see slice 1). This is not a defect in the
protocol — it is what makes parcel scope achievable *without trusting the viewer*: the
sim decides which parcel the moderator is standing on, so the viewer cannot widen the
scope by lying about it.

**The server does not implement it.** Zero matches for `SpatialVoiceModerationRequest`
across `Source/`. The command is sent and silently dropped.

**This is the inverse of the channel-full case.** There, the server produced a
condition the client could surface but never reached. Here the client is ready and the
server is the missing half — so the feature is verifiable end to end the moment the
handler exists, with no viewer work at all.

**The server does not get to design the transport.** The viewer has settled it. The
server conforms.

## Distinct from group-chat moderation

The tree already contains an older, unrelated feature: `ChatterBoxSessionAgentListUpdates`
with `is_moderator` and `can_voice_chat` (`EventQueueGetHandlers.cs:230`-`:249`). That
is IM-session moderation, not parcel voice. Do not extend or reuse it.

## SETTLED: moderation is a source-side rule

`VisibilityRules.IsExcluded` is four short-circuit rules: source voice audibility
(`:23`), estate ban (`:27`), parcel ban and restrict (`:34`), SeeAVs (`:38`). The first
is source-side; the rest are symmetric.

**Moderation is source-side** — a moderated avatar is inaudible to everyone, so the rule
reads only the source and the source's parcel and never the listener. It is the simplest
rule in the set.

It slots in after the estate ban and before the pairwise parcel ban, keeping cheap
source-side checks first. The rules are independent early-returns, so placement does not
affect correctness.

**What it needs:** one new predicate on `ParcelView` — an `IsVoiceModerated(UUID)`
delegate mirroring the existing ban delegate (`FeederWorld.cs:53`), fed by the adapter.
Exemptions live inside the delegate, exactly as the ban delegate does. Nothing new is
needed from `AgentView`; moderation is keyed by avatar id, already present as `source.Id`.

**The semantics are parcel-sticky, not avatar-sticky — and this is correct, not a bug.**
Because the rule tests the source's *current* parcel (`sourceParcel.IsVoiceModerated(source.Id)`),
a moderated avatar who walks off the parcel is no longer moderated (its `sourceParcel`
changes), and an avatar who arrives onto the parcel is (mute-everyone is parcel state,
checked against whoever is currently there). The mute follows the *parcel*, not the
avatar. That matches SL and is the desired behaviour; a later reader must not "fix"
`leaving the parcel escapes the mute` into avatar-stickiness.

**Fan-out cost.** A source-side mute excludes that source in *every* listener's set — the
same shape as an estate ban. So `mute_all` on a crowded parcel is an N-per-source fan-out
into the peer_ctl feed. This is expected and is what acceptance §1's `excluded_entries`
check observes; it is not a new cost class, but it should not surprise anyone.

## SETTLED: authorisation composes from existing pieces

SL authorises three ways, and all three have server-side equivalents already in use by
the ban path:

| Case | Mechanism |
|---|---|
| Land owner | `avatar.Equals(LandData.OwnerID)` |
| Estate manager or owner | `EstateSettings.IsEstateManagerOrOwner(avatar)` |
| Group Moderate Group Chat | `IsGroupMember(LandData.GroupID, user, (ulong)GroupPowers.ModerateChat)` when `IsGroupOwned` |

No combined "may this user moderate voice here" helper exists; it is composed, the same
way the ban path composes owner, estate-manager and admin exemptions.

**The server must re-authorise independently.** The viewer's own
`isNearbyChatModerator()` gate (`llnearbyvoicemoderation.cpp:196`-`:220`) is UI-only and
spoofable. This is the same reasoning the ban path already applies. Because the target
parcel is resolved from the requester's position (above), the authorisation is always
against the parcel the moderator actually occupies.

## Scope

**Slice 1 — the feature, in memory:**

1. **Advertise and handle** the `SpatialVoiceModerationRequest` CAP, parsing all four
   operands. Advertising is not optional: the viewer calls
   `region->getCapability("SpatialVoiceModerationRequest")` (`llnearbyvoicemoderation.cpp:73`)
   and will not POST if the capability is absent, so it must appear in the region's
   seed-capabilities list — handling the route alone is not sufficient and the acceptance
   test cannot fire until the cap is advertised.
2. **Resolve the target parcel** from the requesting agent's `ScenePresence` position, and
   reject the request if it cannot be resolved. The CAP body names no parcel, so this
   resolution is the only trustworthy source of scope; it is also what pins `mute_all` to
   the moderator's parcel rather than the region.
3. **Authorise server-side** by composing the three checks above, against the resolved
   parcel.
4. **Hold sticky per-parcel state** — a mute-everyone flag plus a per-avatar muted set —
   in memory, so late joiners are muted for the process lifetime.
5. **Feed the matrix** through the new source-side `ParcelView` predicate.

**Slice 2, deferred:**

- **Persistence across restart.** `LandData` has no extensible field, so this means a
  scalar on the land row plus a `landvoicemoderation` table mirroring `landaccesslist` —
  a schema change across MySQL, PGSQL and SQLite. `landaccesslist` is the proven pattern:
  a separate `LandUUID`-keyed table with delete-and-reinsert on store
  (`MySQLSimulationData.cs:740`, `:745`, load at `:960`).
- **The group-owned-parcel-with-its-own-voice-channel case** from SL's spec.

## Constraints

- **The viewer's body shape is fixed.** Conform to it exactly; do not invent fields. In
  particular it carries no parcel id — do not add one; resolve scope from position.
- **Never trust the viewer's authorisation.** Re-check server-side on every operand,
  against the position-resolved parcel.
- **The matrix is the single enforcement point.** Moderation must not be enforced at
  provision time or anywhere else, for the same reason §E's TaxFree fix lives only in
  the matrix — one enforcement point or none.
- **In-memory state is deliberate for slice 1** and must be recorded as non-persistent,
  so nobody assumes a restart preserves a mute.

## Acceptance

Verifiable end to end in Firestorm 26.2.0 with no viewer change:

1. Moderator right-clicks a name, chooses Mute everyone — the room goes silent for
   non-exempt speakers, confirmed by `excluded_entries` on the mixer and by ear.
2. A late joiner arrives and is also muted, confirming stickiness.
3. Unmute everyone restores speech.
4. A non-authorised user attempting the same is refused, and the server logs the refusal
   rather than silently dropping it — per the ban-add instrumentation precedent, a silent
   refusal is a defect.

## Open questions — answer in-world before freezing slice 1

1. **Does SL's `mute_all` scope agree with parcel?** The scope is already *settled by the
   body shape*: the CAP names no parcel, so the server resolves it from the moderator's
   position and pins `mute_all` to that parcel — a parcel owner cannot silence a region.
   The viewer logs the operand as "all residents in this region"
   (`llnearbyvoicemoderation.cpp:132`), while SL's documentation says parcel and the
   authorisation gate is parcel-based. What remains is not a design choice but a
   confirmation: verify that SL's live behaviour matches parcel scope rather than assuming
   the viewer's log string is literal.

2. **Who is exempt from `mute_all`?** Is the moderator self-exempt? Are estate managers
   and the parcel owner exempt? SL's exact exemption set is not documented in the release
   notes and should be confirmed in-world. The ban delegate's exemption pattern is the
   model, but the answer is a behaviour question, not a code one.

---

## Status: slice 1 complete and deployed; end-to-end verification BLOCKED on the viewer (2026-08-21)

The moderation menu could not be made to appear in Firestorm 26.2.0 despite every documented
gate condition being satisfied. Recorded here so the next attempt does not repeat today's
investigation.

**The submenu exists and is being hidden at runtime, not missing.** The menu is
`menu_participant_list.xml`, which does contain a Moderator Options submenu with Mute everyone,
Unmute everyone, and individual mute. It is confirmed present in the file, and confirmed to be
the menu the user sees — the observed entries (View Profile, IM, Call, Share, Zoom In, Block
Voice, Block Text, Add Friend) match that file. So the submenu is hidden at runtime by
`fsparticipantlist.cpp:557`, not absent.

**The surface is the Voice Controls floater, not the nearby chat text floater.** The nearby
chat text floater has no participant list. `FSParticipantList` (which builds
`menu_participant_list.xml`) is instantiated only by `FSFloaterVoiceControls` and the group IM
panel. Firestorm renamed the floater: it is **Comm → Nearby Voice**, registered as
`fs_voice_controls`, not "Voice Controls".

**Every condition of `isNearbyChatModerator()`** (`llnearbyvoicemoderation.cpp:196`-`:219`) was
verified satisfied on the test grid:

- `isRegionWebRTCEnabled()` — voice connects, so `VoiceServerType=webrtc` is being sent.
- proximal channel with null session ID — in nearby voice, not a group call.
- `isActionAllowed("speak")` — confirmed via the free observable: the same predicate drives the
  Talk button, and both toggle and push-to-talk work.
- The ownership branch — `UseEstateVoiceChannel` bit is 0 on both parcels, so `isVoiceRestricted()`
  is true and the gate takes `allowVoiceModeration()`, which needs parcel ownership. Both parcels
  are owned outright by the tester, not group-owned, so `isParcelOwnedByAgent` returns true at its
  direct-owner check before the group-power fallback is reached.
- `canManageEstate()` is also true (Estate tab editable, tester is estate owner), though this
  branch is not the one taken.

**So the predicate should return true and the menu should appear. It does not.** The remaining
unverified assumption is `isNearbyChatSession()`, the second term of the `:557` expression, which
was assumed true throughout rather than checked.

**Next attempt should instrument rather than read.** A single log line at `fsparticipantlist.cpp:557`
printing each term of the visibility expression would resolve in one run what six rounds of source
reading did not. That requires a local Firestorm build.

**Testing prerequisites the brief originally omitted.** The moderator must be actively connected
to proximal voice, and must either own the parcel (when it is on its own voice channel) or hold
`canManageEstate()` (when it is on the estate channel). Neither is obvious from SL's release notes,
which describe SL's own viewer.

**One server-side item surfaced, worth checking independently of this.**
`LLViewerRegion::isVoiceEnabled()` reads `REGION_FLAGS_ALLOW_VOICE`, bit 28 of the region flags
(`llviewerregion.cpp:922`, `llregionflags.h:87`). This is distinct from `VoiceServerType`, from
`EstateSettings.AllowVoice`, and from the parcel's `AllowVoiceChat`. Whether the sim populates it
is unverified. Nothing currently observed depends on it — Talk works, so it is presumably set —
but it is the same shape as the parcel-flag defects already documented: a flag the viewer reads
that the server may never populate, invisible until something depends on it.

**RESOLVED (2026-08-22) — not a defect; the characterisation above is corrected.** A server-side
recon settled this. Bit 28 derives from `EstateSettings.AllowVoice`, which defaults to `true`
(`EstateSettings.cs:139`), and is set at all three flag-assembly sites:
`LLClientView.GetRegionFlags()` for RegionHandshake (`LLClientView.cs:855`-`:856`, sent at
`:885`), `EstateManagementModule.GetRegionFlags()` for RegionInfo (`EstateManagementModule.cs:165`-`:166`,
used at `:1852`), and `EstateManagementModule.GetEstateFlags()` for the detailed estate data
(`EstateManagementModule.cs:2341`-`:2342`). The operator control is the Estate tab's **Allow Voice**
checkbox — parsed inbound from estate-flags bit `0x10000000` (`EstateManagementModule.cs:2204`-`:2207`)
and persisted — which is the *same* `EstateSettings.AllowVoice` the visibility matrix already reads
(`VisibilityRules.cs:56`) and the legacy Vivox/FreeSwitch modules read (`VivoxVoiceModule.cs:684`,
`FreeSwitchVoiceModule.cs:456`). So the "same shape as the parcel-flag defects" line above is wrong:
this flag has a real source, a real default, and a real operator control, unlike the parcel
`AllowVoiceChat` clobber. "Talk works, so it is presumably set" is confirmed by construction, not luck.

One caveat, an observation rather than a defect: because the default is `true`, a region advertises
voice-enabled whether or not a voice backend is actually wired up. That is pre-existing OpenSim
behaviour, orthogonal to this work, and not introduced by the moderation slice.

---

## RESOLVED 2026-08-22 — verified end to end.

**The blocked note's diagnosis was wrong.** It concluded the submenu existed in the file and
was hidden at runtime by a gate returning false, and six rounds of analysis interrogated that
gate. Every one of those conclusions was correct about the source tree at `D:\phoenix-firestorm`
and irrelevant to the running viewer, which did not contain the feature at all.

**The tell was an absence.** Copy Mention URI and Mention User in Chat have no hiding path in
the source — they render unconditionally — yet were absent from the observed menu. That is only
possible if the running binary is not built from that source. Git dates it — the three SHAs
in this paragraph are phoenix-firestorm commits, not this repo: the Mention entries
were added 2025-11-21 (`2a6b5cbde5`) and nearby-voice moderation was transplanted 2026-02-12
(`621009dc42`). A menu without the Mention entries predates the moderation transplant.
Independently confirmed by the floater showing a Voice Morphing dropdown, removed 2026-03-04
(`9e2585cbc0`).

**The viewer requirement, stated precisely:** the feature is NOT in any Firestorm release as of
7.2.4 (80712, built 2026-06-01, parity with LL 26.1.1). It requires a build tracking Firestorm
master — a nightly, an Early Access build, or a local build. Updating to the newest release does
not help.

**Working lesson:** when analysing viewer behaviour against a source tree, first establish that
the tree is what is running. An observable that should be unconditionally present, and is
absent, is the cheapest possible check and would have saved six rounds.

**Verification, on a master-tracking build:**

The Moderator Options submenu appears in Comm → Nearby Voice on right-clicking a participant,
containing Mute this participant, Mute everyone, and Unmute everyone, with Eject from Group
correctly greyed as a non-group session.

Mute everyone produced an accepted CAP with the parcel GlobalID, operand, and requester logged;
the matrix advanced epoch 30 to 31 with `last_mode add`; `excluded_entries` rose to 1 on the
moderator's handle, naming the muted source. Unmute everyone advanced to epoch 32 with
`last_mode remove` and `excluded_entries` back to 0. Confirmed by ear: the muted avatar was
inaudible while the exempt moderator remained audible.

**Stickiness demonstrated incidentally:** the mute was issued while the target stood on a
different parcel and applied the moment they walked onto the moderated one, with no re-issue.
That is the behaviour SL documents for late-joining avatars.

**Open Question 2 partially answered.** The exemption set behaved as designed — the issuing
estate owner remained audible, which is what a moderator expects. Whether blanket
estate-manager exemption is too generous on a grid with several managers remains open; on this
grid the manager list is empty, so it was not exercised.

**One diagnostic detour worth recording:** an early test showed a mute accepted with no
exclusions produced. The cause was that the target stood on a different parcel from the one the
mute was issued on. The rule tests the source's own parcel, so this was correct parcel-sticky
behaviour, not a defect. Prior test state — a parcel ban whose ban lines had pushed the target
off the moderated parcel — created the condition. Clear ban entries between moderation tests.

## Moderation state is reported to no client — SL parity gap (2026-08-24)

Slice 1 applies moderation correctly end to end: CAP → store → feeder → sender → mixer
exclusion → audible silence, tracking the target's parcel position live. All of that was
re-verified on net10 after the develop rebase.

**What it does not do is tell any client that it happened.** The exclusion exists only at the
mixer. No viewer — not the moderator's, not the target's, not a second moderator's — receives
any indication of who is currently moderated.

**How this surfaced.** Firestorm's participant-list moderation menu decides whether to offer
"Mute" or "Unmute" from `LLSpeaker::mStatus == STATUS_MUTED`. For nearby voice that status is
never set, because nothing sets it. The menu therefore only ever offered "Mute", and an
individually-muted agent could not be released through the UI at all — the server implements the
`unmute` operand correctly and nothing sent it.

Worked around viewer-side (phoenix-firestorm, branch `fix/voice-webrtc-fixes`, commit
`3c48a93f7a`) by giving the two menu items distinct parameters and showing both
unconditionally, so the operator chooses and the server authorises. That makes unmute
reachable. It does not close the parity gap: clicking Unmute on an unmuted agent is a no-op, a
second moderator cannot see the first's actions, and no participant list anywhere shows
moderation state.

**In SL**, moderation state rides in the speaker/agent list, so every client's participant list
reflects who is muted, and the mute/unmute control is correct by construction.

**Relationship to slice 2.** Slice 2 plans persistence — a scalar on the `land` row plus a
`landvoicemoderation` table mirroring `landaccesslist`. Reporting state to clients needs the
same underlying query: what is the current moderation state for this parcel. Worth designing
them together rather than sequentially; the persistence work will otherwise be repeated when the
reporting work arrives.

**Open:** which channel carries it. The moderation CAP is per-agent, per-region, per-session and
request-scoped — it has no push path. Whether this rides on the existing voice data channel, the
speaker list, or something else is undecided.
