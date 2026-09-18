# Phase 0 wire format: arming, epochs, heartbeat, fail-closed rule

**Slice 0.1, design only, 2026-09-14.** No code, build, deploy or restart was part of this slice.
It is mirrored byte-identical at `D:\legion-voice-mixer\docs\voice\` (O-28).

**Sources read:**
- tranq-ais `feature/ais-v3` at `748b7fbb6f`;
- legion-voice-mixer `main` at `69444f0`.

Every `path:line` below was read at those commits.

**Goal:** the mixer must be able to fail closed. It has to tell "this listener may hear everyone" from
"the sim never said anything about this listener", and act on the difference.

**Open question 3 (§10): arming is DESIGNED and PROVEN; the capability half is still open (amended 2026-09-16,
slice 0.7a).** Whether an avatar's sessions share one arming record is settled here (§1, §7.4). How connector
peers and recorder taps get armed is answered by the slice 0.7a amendment below; the 2026-09-15 deferral is kept
for the record. §8 flags the other things that make the design harder than the brief assumed.

### Amendment 2026-09-15 (slice 0.2, sim half)

1. **Connector peers and recorder taps: DEFERRED, not decided.** *(Ledger O-88.)*
   - **What 0.2 does:** Phase 0 arms avatars only (the scene presences holding a voice session, §0.1
     "Population"). *Superseded 2026-09-16 (slice 0.7a amendment): this sentence was never checked against
     the code. 0.2 excludes nothing on presence type, and a registered connector NPC is armed as built.*
   - **The settled position:** connector peers and recorder taps ARE armed, not exempted. The recorder is the
     most privacy-sensitive participant in the system, and the one peer writing audio to disk must not be the one
     with no authority behind it.
   - **Why 0.2 stops short:** arming them is UNDESIGNED. It needs detail this document does not give: which display
     a connector uses, which room, and how a recorder tap differs. Inventing that inside 0.2 would build on
     guesses, so this is a scope boundary, not a reversal.
   - **Gate:** `JS_VIS_FAIL_CLOSED` must not be enabled until that design exists and is built. Under fail-closed
     an unarmed peer is silent. That is the safe outcome for a recorder tap and the WRONG outcome for a connector
     NPC someone expects to hear.
   - **Enforcement:** the 0.6 shadow soak checks the gate explicitly (§6.4 step 3), not through this note.
2. **The sim has its own knob, contrary to §6.1.** 0.2 is gated by `[WebRtcVoice] VisibilityArmingEnabled`,
   default **false**, rather than riding `VisibilityEmitEnabled`. That key already defaults to true (V-1) and is
   true on Legion Grid (`config/OpenSim.ini:141`), so riding it would have changed the wire on deploy. With the
   knob off, the payloads and the room-create body are byte-identical to before 0.2
   (`Tests/WebRtcJanusService.Tests/VisibilityKnobOffGoldenTests.cs`, golden captured from `2fa978c254`).
3. **Citation drift.** The mixer reports `last_batch_age_ms` at `src/janus_slvoice.c:1512` at mixer `ce792a7`
   (§0.2 and §8 item 11 give `:1497-1498` at `69444f0`; ledger O-78 gave `:1475`).

### Amendment 2026-09-15 (slice 0.3, mixer half)

Built at mixer `4492dbc`. Where this section and the body below differ, this section describes the mixer as built.

1. **Keying, as found: per session.** Before 0.3 a listener's policy lived on each session, fanned out by display.
   - `excluded` and `mod_muted` are sets on each session.
   - Every batch entry is merged into each session whose display matches.
   - Any room exit empties the sets (`janus_slvoice_reset_room_state_locked`).
   - The room held only the deferred store (by display, until the listener joins) and `vis_epoch`, a count of
     applied batches.
2. **Keying, as built: per room by avatar.** Each room holds one record per display (`vis_records`).
   - **Contents:** the epoch that armed it, its `listener_generation`, its last confirmation, a stale mark, and the
     columns the current authority set.
   - **Shared:** every session with that display follows the one record, and it survives a leave and a rejoin.
   - **Removed by:** a new epoch, a heartbeat that omits the display, or the room's destruction.
   - The per-session sets are unchanged.
3. **Pre-join arming (§2, §8 item 3) needs no deferred-store change.**
   - The record exists whether or not the listener has joined, so an empty arming `replace` for an absent listener is
     kept by the record, and the joiner is armed from its join.
   - The deferred store still drops a record whose two columns are empty, because arming no longer rides it.
   - The epoch check at join is the record's own: adopting a new epoch removes every record.
4. **Open question 2, decided for fail-closed only.**
   - **Under fail-closed:** a session that joins with an armed display takes the record's columns, and anything
     deferred for that display is dropped as superseded. A reconnect is then audible at once (§7.4) without hearing
     what its policy excludes.
   - **With fail-closed off:** a join is exactly as before (harness S3 asserts a rejoin starts with empty sets).
   - The sets are not moved onto the record, because that would change knob-off behaviour.
5. **Shadow mode means every check runs and nothing is refused.**
   - With `JS_VIS_FAIL_CLOSED=0`, and in any room without `vis_authority`, every batch is applied exactly as before.
     That includes a lower epoch, an out-of-order `policy_generation` (§1.2) and an add/remove whose `base` does not
     match (§1.3).
   - The checks still run, update the records only when they pass, and are reported (`status`, `stale_generation`,
     `stale_listeners`). This follows §6.1: "audio behaves exactly as today".
   - The refusals in §1.2 and §1.3 apply only under fail-closed in a declared room.
6. **Row 2 does not occur in the mixer.**
   - Adopting a new epoch, or a takeover, removes every record, so no record from another epoch is left to evaluate.
   - Such a listener stands in row 1, with the same outcome: silence.
   - Row 2 is unit-tested on the rule itself (`src/visauth.h`, `slv_vis_row`).
7. **Rejected heartbeats.**
   - A malformed heartbeat is answered `{slvoice:"error", reason:"malformed"}`, with `vis_protocol` and
     `mixer_instance`.
   - So is one whose `interval_ms` needs a window above `JS_VIS_STALE_MS` (`2 × interval + 5000 + 250`), with
     `reason:"interval_too_long"`.
   - The 0.2 sim reads a non-heartbeat reply as "not heartbeat-capable" and stops heartbeating. Under fail-closed its
     rooms then go silent: loud, not quietly unenforced.
8. **Replies as built.**
   - Every `peer_ctl_batch` reply (applied, empty or error) gains `vis_protocol` 2 and `mixer_instance`, appended
     after the existing keys.
   - A stamped batch also gets `status`, `authority_epoch`, `policy_generation`, `stale_listeners` and
     `unarmed_listeners`.
   - A refused one is `{slvoice:"error", reason:"stale_epoch"|"stale_generation"}`.
   - An unknown request is answered as before.
9. **Observability as built** (additive `query_session` keys).
   - **Per room:** `vis_protocol`, `mixer_instance`, `fail_closed`, `stale_ms`, `vis_authority`, `enforced`,
     `authority_epoch`, `policy_generation`, `authority_age_ms`, `armed_listeners`, `would_silence_listeners`,
     `would_silence_pairs`, `heartbeats`, `stale_epoch_rejects`, `stale_generation_rejects`, `records_full`.
   - **Also per room:** `would_silence_listener_ticks`, the listener gauge summed over every tick, so a soak that
     polls cannot miss a sub-second window.
   - **Per session:** `vis_row`; when armed, `vis_listener_generation`, `vis_confirmed_age_ms` and `vis_stale`.
   - The would_silence counts cover only rooms created with `vis_authority`, with fail-closed on or off.
10. **Skew as built.**
    - **Old sim, new mixer:** no stamp, no `vis_authority`, no heartbeat. Rooms are undeclared, nothing is counted or
      enforced, and batches apply as before. The only wire change is the two reply keys, which the old sink ignores.
    - **New sim (0.2), old mixer:** the old parser ignores `room_epoch`, `policy_generation`, `base` and
      `vis_authority`, so batches and creates apply as unstamped ones. Old replies carry no `vis_protocol`, so the
      0.2 sim never heartbeats; a heartbeat would get `unknown_request`.
    - Neither degrades to silence. Harness S21 checks the second against `legion-voice-mixer:rollback-pre-03`.
11. **O-88 is unchanged.** 0.3 builds the knob. Nothing arms connector peers or recorder taps, so
    `JS_VIS_FAIL_CLOSED` stays 0 until O-88's design is built (§6.4 steps 3 and 4).

---

### Amendment 2026-09-16 (slice 0.5, harness)

Built at mixer harness `<this commit>`. **No mixer code changed:** 0.5 adds scenarios, not behaviour. The image
rebuilt for it (`70808a66`) is a stamp-only rebuild of 0.4's `ea88de16`, and the live mixer was not redeployed.
The §9 coverage map is at the end of that section.

1. **No Phase 0 defect was found.** Every assertion §9 specifies is implemented and observable in the mixer as
   built — including the two that looked like gaps from the coverage map: out-of-order rejection exists
   (`janus_slvoice.c:2336`, `gen <= room->vis_policy_gen` → `stale_generation`, counted in
   `stale_generation_rejects`), and the lower-epoch takeover exists (`visauth.h:78` `SLV_VIS_EPOCH_TAKEOVER`,
   logging `%u listener record(s) disarmed`). The defects this slice found were in its **own scenarios**, not in
   Phase 0; they are listed in the slice report.
2. **§9 6 is not observable through `query_session`.** "S is absent from L's roster" exists only in the
   `joined` event's `participants` array, which `janus_slvoice_join_commit_locked` filters by the listener's own
   exclusion set and, under fail-closed, by the pair rule. The harness now keeps that event
   (`TestPeer.joined_event` / `roster()`). **Consequence for 0.6:** the roster a listener already holds cannot be
   re-derived from the admin API afterwards — it must be captured at the join or not at all.
3. **The fail-first rule needs two proofs for a harness-only slice, not one.** 0.5 adds no mixer code, so every
   new scenario passes against the pre-0.5 image; and against a pre-0.3 image each one merely *skips*. Running
   them there with `--prove-fail` turns the skip into a failure, but all eight then fail with the **same**
   message ("the mixer reports no visibility authority"), which proves the image lacks the authority and nothing
   about what each scenario detects. Only the second proof — invert the one condition the scenario is about and
   confirm it fails on **its own** assertion — establishes that. Both are recorded in the harness README.
4. **Recorder taps are gated, and now proven so (ledger O-88).** S30 shows a tap joined with `"recorder": true`
   in a declared room is silent at row 1 until armed. That is the safe outcome for a recorder and the wrong one
   for a connector NPC, which is exactly why §6.4 blocks `JS_VIS_FAIL_CLOSED` while O-88 is open.

### Amendment 2026-09-16 (slice 0.7a, connector and recorder arming)

Read at tranq-ais `feature/ais-v3` `bfccf0946a` and legion-voice-mixer `main` `d03ab9b`. The 0.7a commits change
tests and docs only: **no production line changed in either repo.** Ledger O-88.

1. **The design: a connector needs no arming mechanism of its own.** The answers O-88 called undesigned were
   already in the connector module:
   - **Display:** the connector's derived NPC id (O-63, `VoiceConnectorModule.cs:288`), the id the operator
     copies into the peer's config.
   - **Room:** the estate room (`VoiceConnectorModule.cs:281`), derived exactly as the sink's fallback room
     (`JanusPeerCtlBatchSink.cs:124`). The arming resolver maps an unrecorded agent to that same room
     (`VoiceVisibilityService.cs:137`), so the room agrees even without a record.
   - **Arming, trigger 2 (§2):** `VoiceConnectorRegistrar.Register` adds a `VoiceViewerSession` for the NPC
     (`VoiceConnectorRegistrar.cs:85`), then calls `pRecordRoom` (`:91`), which the module binds to
     `svc.OnListenerProvisioned(npcId, estateRoom)` (`VoiceConnectorModule.cs:299`). In arming mode that is
     `RequestArm` (`VisibilityBatchSender.cs:137`).
   - **Triggers 1, 3, 4 and the heartbeat:** the session puts the NPC through `FeederWorldFromScene`'s only gate,
     `IsAgentInRegion` (`FeederWorldFromScene.cs:63`), into the matrix population (`VisibilityMatrix.cs:80`). The
     arming pass walks that population (`VisibilityBatchSender.cs:250`), and the heartbeat is built from it
     (`VisibilityBatchSender.cs:347`, `VisAuthority.cs:393`).
   - **Disarm:** `Unregister` removes the session (`VoiceConnectorRegistrar.cs:127`). The NPC leaves the
     population, `PruneTo` forgets its record (`VisAuthority.cs:196`), and the next heartbeat omits it. §1.3's
     omission rule then disarms the peer, which is still connected at the mixer (`janus_slvoice.c:2472-2480`).
   - **A recorder tap is the same record with `MayInject=false`:** the same arming, plus the existing moderation
     mute (`VoiceConnectorRegistrar.cs:100`). There is no separate mechanism.
2. **Finding: (a), armed as built.** Nothing on 0.2's arming, heartbeat or partitioner path excludes a connector
   NPC. The only presence filter is the voice-session gate above. `SceneGraph.ForEachScenePresence` skips only
   deleted presences (`SceneGraph.cs:1469`), so NPC-typed presences are enumerated. There is no presence-type,
   session-type or viewer check in the matrix, the sender, `VisAuthority` or the sink's partitioner. The one NPC
   check in the voice addon, `IsNpcProvisionRefused` (`WebRtcVoiceServiceModule.cs:448`, used at `:533`), is on
   the viewer **provision** path. It exempts connector identities and does not touch arming. "Phase 0 arms
   avatars only" (the 0.2 amendment above) was a sentence in this document, never a property of the code. During
   the 0.6 soak it was quoted as fact without being checked.
3. **Sim proof** (`Tests/WebRtcVoiceRegionModule.Tests/ConnectorArmingTests.cs`). The rig is a real Scene with
   a parcel, a real `VoiceVisibilityService` with arming on and its own tick thread, and a real
   `JanusPeerCtlBatchSink` whose transport captures every body and answers as a vis_protocol 2 mixer. The
   registrar is driven with `StartRecord`'s own delegate bodies, and the NPC presence is added as
   `NPCModule.CreateNPC` adds it, with `PresenceType.Npc`.
   - T1: a `MayInject=true` record gets an arming replace naming the NPC at the estate room, with empty columns.
   - T2: the same for `MayInject=false`. The store's mute key is (parcel, NPC id), and the avatar's mute column
     names the NPC.
   - T3: a changed `mixer_instance` gets one replace re-arming the NPC with the avatar, and a new epoch's first
     snapshot does the same.
   - T4: the heartbeat lists the NPC as armed while it is registered.
   - T5: after `Unregister`, a heartbeat that lists the avatar omits the NPC, and no later batch or heartbeat
     names it (checked across a forced re-arm-all).
   - T6: with the knob off, the golden scenario with a `MayInject=true` connector registered and unregistered in
     it is byte-identical to the knob-off golden (`VisibilityKnobOffGoldenTests`). The real knob-off service
     sends no heartbeat, no authority stamp and no body naming the NPC. A `MayInject=false` connector is outside
     the byte comparison on purpose: its mute is enforced with the knob off too (S-CON-2), so it adds mute
     entries the pre-0.2 golden never had.
   - All six passed without a production change. **Mutation proofs:** skipping `sp.IsNPC` presences in
     `FeederWorldFromScene` fails T1 to T5 with their own timeouts, and T6 still passes. Leaving the session in
     place on `Unregister` fails T5 at `membership off`.
4. **Mixer proof: harness S33** (fail-closed scratch mixer, test rooms, `--no-restart`). In a declared room with
   SRC talking:
   - A `"recorder": true` tap is silent at row 1 while unarmed. An arming replace naming its display makes it
     hear the room at row 4. A heartbeat that omits it returns it to row 1 within 3 s, well inside the 7250 ms
     minimum window, so the disarm itself is asserted, not staleness.
   - A non-recorder connector as a source is inaudible to an armed listener while unarmed and audible once armed.
     After a heartbeat omits it, it is back at row 1 and inaudible to that listener, and the listener stays armed.
   - **Mutation proof:** against an image whose heartbeat keeps omitted records (the
     `g_hash_table_iter_remove` in the disarm loop replaced by a no-op), S33 fails on its own text, `S33: a
     heartbeat omitting the recorder disarms it at once (row 1, not the staleness window)`, observed at
     `vis_row` 4 and `last_mix_rms` 0.15.
5. **What this does not close.** The connector **capability** half (§11.10) is untouched. A connector peer
   still joins with `api_secret` and mints no join capability, so `JS_JOIN_CAP_REQUIRED=1` would refuse it.
   That is slice 0.7b. `JS_VIS_FAIL_CLOSED` also stays off: the §6.4 soak steps still gate it, and step 3 must
   now show connector peers **armed** rather than unarmed. S33 proves the mixer end with a peer whose display
   is the NPC id. It does not prove that an operator's peer config carries that id; the registration log line
   (`VoiceConnectorModule.cs`, `registered ... npc= room=`) is still what they copy.

## 0. The mechanism today

### 0.1 Sim side: one feeder, sender and sink per region

**Owner.** `VoiceVisibilityService` runs one per region, on one dedicated thread
(`Addons/os-webrtc-janus/WebRtcVoiceRegionModule/VoiceVisibilityService.cs:110-132`).
- The loop ticks the feeder, pumps the sender, then waits `m_cadenceMs` (`:181-209`, wait at `:203`).
- The cadence is `[WebRtcVoice] VisibilityTickMs`, default 250 (`WebRtcVoiceRegionModule.cs:95`, `:128`).
- The thread is registered with the OpenSim Watchdog with a 5000 ms alarm (`VoiceVisibilityService.cs:123-130`).
- The region module builds one service per region when `VisibilityFeederEnabled` is set
  (`WebRtcVoiceRegionModule.cs:251-266`).

**Feeder.** `VoiceStateFeeder.Tick` rebuilds the whole matrix from live world state every tick and diffs
it against the previous one (`Addons/os-webrtc-janus/Visibility/VoiceStateFeeder.cs:75-99`).
- It raises a batch only when the diff is non-empty (`:96-97`).
- A derivation exception keeps the last matrix and emits nothing (`:84-91`).

**Population.** The population is every scene presence holding a voice session in this region
(`FeederWorldFromScene.cs:61-66`, gated by `VoiceViewerSession.IsAgentInRegion`,
`WebRtcVoice/VoiceViewerSession.cs:94-102`).

**Matrix.** `VisibilityMatrix.Build` computes, for every ordered pair, whether the source is excluded
(ban or visibility) or moderation-muted (`Visibility/VisibilityMatrix.cs:56-93`).
- **A listener is stored only if it has at least one entry** (`:87-90`).
- `Listeners` and `MutedListeners` therefore enumerate non-empty columns only (`:34-38`).
- An agent that is allowed to hear everyone is not in the matrix at all.

**Delta.** `DeltaComputer.Diff` names only listeners whose set changed (`Visibility/DeltaComputer.cs:18-39`).

**Sender.** `VisibilityBatchSender` (`WebRtcVoiceRegionModule/VisibilityBatchSender.cs`) has three paths.
- **Snapshot**, when not `_synced` (`:294-349`):
  - a `replace` naming every non-empty listener, plus an explicit empty list for listeners it sent before
    and that are now empty ("clear-tracking", `:314-319`);
  - **when both channels are empty it sends nothing** and marks itself synced (`:321-325`).
- **Delta** (`:270-292`): at most one `add` and one `remove` per tick.
- **Join** (`:193-267`): a bounded blind re-send of the listener's column on provisioning success, 6
  attempts (`:44`).
  - **It skips a listener whose columns are both empty** (`:210-219`).
  - The call site passes only "local" (spatial) provisions, not A2A (`WebRtcVoiceRegionModule.cs:785-795`).

**Single-flight and resync.**
- Sends are single-flight; a skipped tick forces a snapshot (`VisibilityBatchSender.cs:118-140`).
- A send stuck for 8× the admin timeout is force-cleared, and the abandoned send "is left to complete or
  hang harmlessly" (`:164-190`).
- `_synced` goes false only on a transport error, a skipped tick or that guard.
- Three consecutive `ProtocolError`s latch emission off until the region restarts (`:50`, `:407-423`).

**Sink.** `JanusPeerCtlBatchSink` partitions each op by the listener's recorded room
(`JanusPeerCtlBatchSink.cs:177-258`) and stamps `room` on each request (`:220`).
- The recorded room comes from `AgentRoomTable`. An agent with no record falls back to the estate room,
  as listener and as source (`Visibility/PeerCtlBatchPartitioner.cs:166-178`).
- Each request goes through Janus Admin `message_plugin` (`JanusAdminClient.cs:140`).
- Any `janus:"success"` maps to `Ok` (`JanusAdminClient.cs:151-173`).
- The mixer's inner reply is parsed and logged, but **never changes the result** (`JanusPeerCtlBatchSink.cs:273-290`, `:321-329`).

**Wire body.** Built by `PeerCtlBatchSerializer.BuildRequest`, with `room` added by the sink
(`Visibility/PeerCtlBatchSerializer.cs:41-57`):

```json
{ "request":"peer_ctl_batch", "op":"add|remove|replace", "room":<int>,
  "excl":{ "<L>":["<S>",...] }, "mute":{ "<L>":["<S>",...] } }
```

**What the sim never sends:**
- anything for a listener with two empty columns;
- anything on a quiet tick;
- anything identifying the sender instance;
- any per-room liveness signal;
- anything at all for A2A rooms, static rooms, harness rooms or connector-only rooms.

### 0.2 Mixer side

**Parse.** `slv_visbatch_parse` (`src/visbatch.c:126-189`) requires `op` (`:149-164`) and an integer
`room` (`:167-172`).
- It parses the `excl` and `mute` channels (`:175-183`) and **ignores every other key**.
- An old mixer therefore accepts extra fields silently.

**Admin handler.** `janus_slvoice_handle_admin_message` (`src/janus_slvoice.c:1916`) dispatches
`peer_ctl_batch` (`:1925`).
- It applies the exclusion channel, then the mute channel.
- The inner reply is `{slvoice:"applied", op, room, entries, mute_entries, skipped, deferred_listeners}`, or
  `{slvoice:"error", reason:"unknown_room"}` (`:1944-1961`).
- An unknown request gets `reason:"unknown_request"`.
- Janus wraps every one of these in `janus:"success"`, so the sim sees `Ok` whatever the inner status.

**Apply** (`:1597-1768`). The batch is applied under `room->mutex`, the lock the tick holds for its whole pass.
- **Fan-out:** a listener key is a *display* (agent UUID) and is applied to every session in the room with
  that display (`:1622-1650`).
- **Deferral:** an entry whose listener is not in the room is deferred and replayed at join
  (`:1722-1731`, `:1897-1914`, called from the join branch at `:2346`).
- **Emptied records are discarded:** a deferred record whose two channels are both empty is removed
  (`src/deferred.h:23`).

**State.**
- Each session owns `excluded` and `mod_muted` hash sets (`:490`, `:499`).
- `create_session` creates them **empty** (`:1336-1337`).
- Any room exit resets them (`:721-724`).
- `vis_epoch` is a **count of applied batches**, not an authority identity (`:358`, `:1757`).
- `vis_last_ts` records the last apply (`:359`).

**Enforcement.**
- The mix silences a source for listener `s` if it is in `s->mod_muted` (`:3250`) or `s->excluded` (`:3265`).
- The roster, presence and backlog paths filter through `slv_roster_excludes` (`:892`, `:948`).
- `slv_roster_excludes` treats a NULL or empty set as "excludes nothing" (`src/roster.h:26-28`).
- **So an empty set means "hear everyone", whether the sim said so or never spoke.**

**Staleness.** `query_session` reports `last_batch_age_ms` (`:1497-1498`). Nothing reads it.

**Brief citation drift.** The brief's `:1315` is a blank line; the empty-set creation is at `:1336`. Its
`:1475` is a comment in the `room_participants` block; the age is reported at `:1497-1498`. Ledger O-78
carries the same stale `:1475`.

---

## 1. Identifiers

All three are scoped to one mixer **room**. A listener is identified as the mixer already identifies it: by
`display` (agent UUID), fanned out to every session in the room with that display.

### 1.1 `room_epoch`

**Meaning.** Identifies one incarnation of the sim's authority over a room. If it changes, every policy
the mixer holds for that room is void.

**Generated by** the sim, once per `VoiceVisibilityService` instance, when `StartLoop` builds the sender.
- A region restart, a region-server restart, or a `RemoveRegion`/`RegionLoaded` cycle each produce a new value.
- Every room addressed by that region's service carries the same value.
- **Value:** `(unix_ms_at_start << 16) | random16`, an unsigned 64-bit integer.
  - It is ordered, so a newer incarnation normally compares greater.
  - The random low bits separate two services started in the same millisecond.

**Carried** on every `peer_ctl_batch` and every heartbeat entry as a **string of 16 lowercase hex digits**:
`"room_epoch":"0000018f3a2b4c5d"`.
- It is a string because the sim builds bodies with `OSDInteger`, which is 32-bit (`JanusPeerCtlBatchSink.cs:220`).
- A 64-bit value in a JSON number would also need care in jansson.

**Stored** by the mixer as `room->auth_epoch` (u64; 0 = none yet). Adoption rules:
- **Greater than stored (or none stored):** adopt it and **disarm every listener record in the room**.
- **Equal:** normal processing.
- **Less than stored:** reject with `stale_epoch`, *unless* the stored epoch is itself stale (no accepted
  message for `JS_VIS_STALE_MS`). Then adopt it as a **takeover** and disarm everything.
  - This rule stops a zombie instance from flapping authority.
  - It also survives a sim clock that stepped backwards.

**Naming.**
- Do not reuse the mixer's existing `vis_epoch` batch counter (`:358`), or the sender's `_sendEpochSeq`
  (`VisibilityBatchSender.cs:66`).
- The admin output key for the new value is `authority_epoch`.

### 1.2 `policy_generation`

**Meaning.** The version of the sim's policy for one room within one epoch.

**Generated by** the sim: one u32 counter per (epoch, room) in the sender.
- It starts at 1 with the first arming batch.
- It advances by one for every batch the sim sends to that room: `replace`, `add` or `remove`, including arming.
- Room sends are already sequential per room: single-flight, and an `add` then a `remove` are awaited in
  order (`VisibilityBatchSender.cs:286-289`). So generations reach a room in order unless a send is abandoned.
- **Why u32 is enough:** at 4 batches per second it lasts about 17 years.

**Carried** as the integer `"policy_generation"` on every batch, and in every heartbeat entry.

**Stored** by the mixer as `room->policy_gen`, the highest applied.
- It is used for observability, and to reject whole batches that arrive out of order (`gen <= policy_gen`).
- A send abandoned by the in-flight guard (`VisibilityBatchSender.cs:184-189`) can complete after a newer
  one. **Today that late delta is applied over newer state;** with this check it is rejected.

### 1.3 `listener_generation`

**Meaning.** The `policy_generation` of the last batch that set or changed **this listener's** column in
this room and epoch. It lets one listener's policy be detected stale while the room stays live.

**Generated by** the sim, tracked per (room, listener) in the sender. On a successful send, every listener
named in the batch gets `listener_gen = batch.policy_generation`.

**Carried in two places:**
- **Deltas** (`add`/`remove`) carry `"base":{"<L>":<prev listener_gen>}` for every named listener: the
  generation the sim believes the mixer holds for L.
- **Heartbeats** carry `"listeners":{"<L>":<listener_gen>}` for **every** listener the sim addresses at
  that room, including listeners with empty columns.

`replace` needs no base, because it is absolute.

**Stored** by the mixer per (room, display), in a room-level record: `{epoch, listener_gen, confirmed_us, stale}`.
- It is a room-level record, not per session, so every session with that display follows one record (fan-out).
- A reconnect in the same room keeps it (§7.4).
- The record lives alongside the existing per-session sets; the mixer slice decides whether to move the sets
  onto it too.

**Mixer checks:**

| Event | Check | Outcome |
|---|---|---|
| `replace` names L | `batch.policy_generation > L.listener_gen` | Set L's columns, `listener_gen`, `epoch = auth_epoch`; `confirmed_us = now`; clear `stale`. **This is arming.** |
| `add`/`remove` names L | `base[L] == L.listener_gen` and L armed in the current epoch | Apply; set `listener_gen = batch.policy_generation`; `confirmed_us = now`. |
| `add`/`remove` names L | base mismatch, or L unarmed | Do not apply L's entry; mark L `stale`; list L in the reply's `stale_listeners`. |
| Heartbeat names L with `g` | `g == L.listener_gen` and `L.epoch == auth_epoch` | `confirmed_us = now`. |
| Heartbeat names L with `g` | `g != L.listener_gen`, or L unarmed / from an older epoch | Mark `stale` (or leave unarmed); list L in `stale_listeners` / `unarmed_listeners`. |
| Heartbeat omits an armed L | — | Disarm L (the authority no longer addresses it). |

**Amendment 2026-09-17 (slice 0.7d, ledger O-95): a heartbeat can predate a batch it arrives after.** Heartbeats and
batches are separate flights (§3: the heartbeat has its own in-flight flag), so a heartbeat built before a batch was
answered can reach the mixer after that batch applied. Two orderings were constructed in 0.7d and both caused an
audible dropout under fail-closed on the pre-0.7d mixer:
- **A:** a `replace` for L applied at N+1, then a heartbeat built at N named L at N. The fourth row above marked L
  stale, and a stale record stays stale until the next `replace` (`test_visauth` "ordering A"; harness S35 leg A).
- **B:** an arming `replace` for a new listener L2 applied, then a heartbeat built before L2 existed omitted it. The
  last row disarmed L2 (`test_visauth` "ordering B"; S35 leg B). This one happens at every join.

The heartbeat existing `policy_generation` key could not order it: the sim fills it from the highest generation
ALLOCATED (`VisAuthority.NextGeneration`, advanced when a batch is stamped, before it is sent), and the mixer never
read it. The fix adds a key rather than redefining that one:
- the sim sends `"as_of"` in each heartbeat room entry: the highest `policy_generation` the mixer has APPLIED in that
  room for this authority when the heartbeat was built. It is read under the same lock as the listener generations the
  entry reports;
- the mixer compares it with the room's `vis_policy_gen` (highest accepted in the adopted epoch). **Below it, the entry
  is OUTDATED:** it still counts for room liveness (epoch adoption, `vis_heartbeats`), but its `listeners` map is not
  evaluated. There is no confirm, no stale and no disarm. It is counted in `heartbeats_outdated` (`handle_info`
  visibility block);
- an entry with no `as_of` (a pre-0.7d sim) is evaluated exactly as the table above says;
- epoch adoption, takeover and the graceful stop are unchanged. The key exists only when
  `VisibilityArmingEnabled` is true, because only then are heartbeats sent, so the knob-off payloads are unchanged.

---

## 2. Arming

**Definition.** Arming is a `replace` naming listener L with `room_epoch = E` and a fresh
`policy_generation`, **including when both of L's columns are empty**.
- An empty arming `replace` is `"excl":{"<L>":[]}, "mute":{"<L>":[]}`.
- It is the only thing that turns an unknown listener into one allowed to hear.

**When the sim arms:**
1. **Service start or new epoch.** The first send from a new `VoiceVisibilityService` goes to every room
   that has at least one listener. It arms **every** listener in the population, not only non-empty columns.
2. **Listener provisioned.** `OnListenerProvisioned` arms L at its recorded room, even with empty columns.
   - This removes the empty-column skip at `VisibilityBatchSender.cs:210-219`.
   - It applies to spatial provisions, the ones recorded today (`WebRtcVoiceRegionModule.cs:785-795`).
3. **Resync.** Every snapshot after a transport error, a skipped tick or the in-flight guard arms every
   listener. The "nothing to send" early return at `:321-325` goes away.
4. **Mixer told us.** Arm the listed listeners on the next tick when a reply shows any of these:
   - `unarmed_listeners` or `stale_listeners` non-empty;
   - `reason:"unknown_room"` for a room that still has listeners (arm again once it exists);
   - a `mixer_instance` different from the last one seen (arm **everything**, all rooms).
5. **Room change.** When `AgentRoomTable` records a new room for L (newest wins), arm L at the new room.

**Before arming** (fail-closed enabled, room declared, §6):
- **As a listener,** the unarmed listener gets a silent mix, and no presence, power or roster entries for
  any source.
- **As a source,** it is inaudible and invisible to every armed listener (the pair rule in §4).

**Pre-join arming** must survive deferral. An arming `replace` for a listener not yet in the room is deferred
today (`:1722-1731`). But a deferred record with both channels empty is *removed* (`deferred.h:23`), so
**an empty arming would be lost** and the joiner would wait for the next heartbeat's `unarmed_listeners` round
trip.
- **Fix:** the deferred store keeps a record carrying `{epoch, listener_gen}`, even with empty columns.
- On join, replay sets the room-level record only if its epoch still equals `auth_epoch`.

---

#### Amendment 2026-09-17 (slice 0.8c): a connector's room, and the unknown_room backoff

Ledger O-93, and the connector half of O-92. Sim-side only; no mixer change.

1. **A connector's room is resolved the way an avatar's is.** `ConnectorRoomResolver.RoomFor(regionId, land)` applies
   the provisioning path's own rule — the parcel's own channel unless the parcel carries `UseEstateVoiceChan`, then the
   estate channel — and hashes it with the same `CalcRoomNumber`. It is resolved at registration and **re-resolved at
   every capability fetch**; if the parcel's channel has changed the record MOVES, with one INFO line, the way an
   avatar's re-provision moves it. On an estate-channel parcel the number is what 0.8b recorded, so that case is
   unchanged.
2. **The sim makes the room exist.** A Janus room is created on one path only, a viewer provisioning voice
   (`SelectRoom` -> `CreateRoom`), which is why a connector in a region with nobody voiced had nothing to join. The new
   seam `IWebRtcVoiceService.EnsureSpatialRoom(sceneId, parcelLocalId)` creates it exactly as a viewer's provision
   would — same flags, declared when this sim arms — through the service session the console already uses, and is
   idempotent because `SelectRoomCoalesced` reuses a live room. It is called at registration and before minting at
   every capability fetch. Never for a room holding only avatars, and never from the feeder.
3. **unknown_room retries back off.** 1 s doubling to a 30 s cap, per listener, reset by an applied batch or a
   provision; one WARN entering backoff per room episode and one INFO on recovery, instead of a line per attempt. The
   defect that made this necessary was not the delay but a bypass: the arming pass short-circuited on a standing
   re-arm request (`VisibilityBatchSender`), and a connector NPC never leaves the population, so its request never
   expired and it was re-armed on every tick — 8,933 `unknown_room` lines at ~3.8/s in the 0.8 soak, with the
   authority's own `_retryAt` sitting there unread. `CanArmNow` now gates **every** arming path, including a snapshot.
   Measured on the real sender: a room that stays unknown for 120 s drew **480** attempts before and draws **8** now.

**R2b is DROPPED, not deferred** (ruling, slice 0.8c2). The 0.8c brief's R2b would have created a room in response to
an `unknown_room` reply for a room holding an active connector record — a callback from the visibility authority back
into the connector registry. It is not being built, in 0.8d or later. It served only connectors with **no**
`CapabilitySecret`: one that has a secret ensures its room at every capability fetch, which is both the retry and the
re-creation R2b was for. A connector without a secret cannot fetch a capability, so once join capabilities are
required it cannot join a declared room at all — a room created for it would be a room nothing can enter. The fix for
such a connector is to give it a `CapabilitySecret`, not to have the sim create rooms on its behalf. What remains is
bounded and deliberate: a room destroyed by the empty-room grace mid-life is re-created at the peer's next fetch, and
the backoff (1 s → 30 s, released the moment anything proves the room exists) keeps the interval cheap.

---

## 3. Heartbeat

**Purpose.** Proves the authority for a room is alive in epoch E, and states which listeners it addresses
at which generation. **It is not a policy.**

**Carrier.** A new admin request, one per region per interval, holding every room that region addresses.
- Semantics stay strictly per room.
- One message per region avoids adding R round-trips a second to the per-room cost the sink already
  budgets (`JanusPeerCtlBatchSink.cs:52-58`).
- **Size:** at most one entry per listener in the region, about 50 bytes each, far under
  `SLV_VISBATCH_MAX_BYTES` (`visbatch.h:39`).

```json
{ "request":"peer_ctl_heartbeat",
  "room_epoch":"0000018f3a2b4c5d",
  "interval_ms":1000,
  "rooms":{
    "226001844":{ "policy_generation":42,
                  "listeners":{ "4fbdfd2a-e0c6-4003-b2f8-8714fcc7b968":41, "<L2>":12 } } } }
```

**Interval:** 1000 ms (4 feeder ticks). The sim sends it from the feeder loop.
- **It has its own in-flight flag, and does not use the sender's single-flight.** Otherwise a slow batch
  send, bounded only by `AdminTimeoutMs` = 5000 (`WebRtcVoiceRegionModule.cs:104`, `:141`), would starve it.
- **Rooms:** the distinct resolved rooms of the current population. A room with no listeners gets no entry (§7.5).
- **Graceful stop:** `VoiceVisibilityService.Stop` sends a final heartbeat with `"state":"stopping"`. The
  mixer then treats those rooms as stale at once instead of after the window.

**What a heartbeat does at the mixer, per room entry:**
1. Apply the epoch adoption rule (§1.1). A new epoch **disarms** everything. A heartbeat never arms.
2. On an accepted epoch, record the entry as the room's last sign of life. Then, for each listener L in `listeners`:
   - if `L.epoch == auth_epoch` and the generation matches, confirm L;
   - otherwise mark L stale or unarmed and report it.
3. Disarm any armed listener the entry omits.

**Amendment 2026-09-17 (slice 0.7d):** each room entry also carries `"as_of"`, the highest `policy_generation` the mixer
had applied in that room when the sim built the heartbeat, for example
`"226001844":{ "policy_generation":42, "as_of":42, "listeners":{...} }`. Steps 2 and 3 are skipped for an entry whose
`as_of` is below the room's applied `policy_generation`: the entry is outdated, it keeps the room live, and it is counted
in `heartbeats_outdated`. The ordering it closes is in the §1.3 amendment. An entry without `as_of` follows steps 1-3
as written.

**Normative:** a heartbeat **MUST NOT** confirm, arm or revalidate a listener record whose `epoch` differs
from the heartbeat's `room_epoch`.
- After a sim restart, the new instance's heartbeat proves the authority is alive. It says nothing about
  policies the dead instance set.
- Those listeners stay silent until the new instance's arming `replace` arrives.
- Matching generations do not help: a new epoch restarts `policy_generation` at 1, so a stale record could
  *coincidentally* match. That is why the epoch is compared first.

**Reply** (inner, inside `janus:"success"`):

```json
{ "slvoice":"heartbeat", "vis_protocol":2, "mixer_instance":"9c1d...",
  "rooms":{ "226001844":{ "status":"ok|stale_epoch|unknown_room|undeclared_room",
                          "authority_epoch":"0000018f3a2b4c5d", "policy_generation":42,
                          "unarmed_listeners":[...], "stale_listeners":[...] } } }
```

- `mixer_instance` is a random u64, as hex, chosen at plugin init.
- `peer_ctl_batch` replies gain the same `vis_protocol`, `mixer_instance`, `authority_epoch`,
  `policy_generation`, `stale_listeners` and `status` keys.
- The sim acts on these (§2 item 4). **This is new sim behaviour:** today the inner reply never changes
  anything (`JanusPeerCtlBatchSink.cs:273-278`).

---

## 4. The decision rule

Evaluated per (listener L, source S) pair on every mix tick, and by the same predicate for roster, presence,
power and backlog (the single-source-of-truth rule, `roster.h`).

**Gates, in order:**
- **G0:** `JS_VIS_FAIL_CLOSED` is enabled.
- **G1:** the room is declared (§6.2).

If either gate fails, the table does not apply and **today's behaviour holds**: the exclusion and mute sets
apply, and an empty set passes.

**Terms:**
- **armed:** the room holds a record for this display from an arming `replace`, or from a deferred arming
  replayed at join.
- **epoch match:** `record.epoch == room.auth_epoch`, and `auth_epoch` was not rejected or superseded.
- **policy fresh:** `now - record.confirmed_us <= JS_VIS_STALE_MS` **and** `record.stale == false`.

**Listener rule** (L's own standing). **Three rows are reachable in the mixer as built; row 2 collapses into row 1**
(amended 2026-09-15, slice 0.4, after the 0.3 finding):

| # | armed? | epoch match? | policy fresh? | L's mix |
|---|---|---|---|---|
| 1 | no | — | — | **silence** |
| 2 | yes | no | — (not evaluated) | **unreachable as built; the listener is row 1 instead.** Adopting a new epoch, or a takeover, removes every record, so no record from another epoch survives to be evaluated. Same outcome: **silence**, and a current-epoch heartbeat does not change it |
| 3 | yes | yes | no | **silence** |
| 4 | yes | yes | yes | **pass**, subject to L's `excluded` and `mod_muted` sets and the viewer's own mutes |

Row 2 keeps its unit tests (`src/visauth.h` `slv_vis_row`, `tests/test_visauth.c`) as a **guard**: they fail if a
future change ever leaves a foreign-epoch record in place, which is the condition row 2 describes. Only the live
evidence split changes — rows 1, 3 and 4 have harness evidence, row 2 has unit tests only, by construction.

**Pair rule.** S is audible and visible to L only if **L satisfies row 4 and S also satisfies row 4** in the
same room.
- **Why:** a source the current authority has not armed has not been evaluated against anyone's policy.
  Symmetric rules (SeeAVs, ban) are only present in L's set once the sim has included S in its matrix.

**"Silence" means:**
- no audio contribution to L's mix;
- no presence (`j`/`l`) and no power (`p`/`v`) for sources;
- no roster or backlog rows.

The session stays joined and ICE stays up.

**Transitions:** an unarmed source becoming armed emits a join presence, and the reverse emits a leave.
These use the same transition machinery the exclusion set drives today (`:1650-1712`).

**Knob disabled, "shadow mode":** the mixer still parses, stores and checks everything, and counts
`would_silence_pairs` and `would_silence_listeners` per room in `query_session`. Audio is unaffected. That
counter is how the deploy proves the sim is arming correctly before the knob is turned on (§6.3).

---

## 5. Staleness window

**Proposal:** `JS_VIS_STALE_MS` = **8000 ms**, with a heartbeat interval of 1000 ms.

**Constraint:** at startup the mixer refuses (WARN, then clamps) a window below
`2 × interval + 5000 + 250`.

**Reasoning.** The longest gap a *healthy* sim can leave between two confirmations of L:
- **heartbeat interval,** 1000 ms;
- **a slow or hung admin round-trip:** the heartbeat send is bounded by `AdminTimeoutMs` = 5000 ms before it
  fails and the next one is attempted;
- **one feeder tick of scheduling slip:** `VisibilityTickMs` = 250 ms, since the heartbeat is driven off the
  tick loop (`VoiceVisibilityService.cs:203`);
- **total: about 6250 ms.**

8000 ms leaves 1750 ms of margin for:
- a slow sim (GC pauses, a heavy tick, a derivation that throws and retries);
- the mixer's own 20 ms tick granularity.

It also tolerates **one lost heartbeat plus one slow one** (1000 + 1000 + 5000 + 250 = 7250 ms).

**Ordering with the sim's own alarm.** The feeder thread's Watchdog alarm fires at 5000 ms
(`VoiceVisibilityService.cs:123-130`). A sim whose tick thread really stalls has logged its own alarm
before the mixer silences its rooms at 8 s.

**Cost:** after a sim dies, listeners keep the last policy for up to 8 s, then go silent. That policy was
correct when set, so this is bounded exposure to stale policy, not fail-open.
- A graceful stop (`"state":"stopping"`) removes even that.
- A shorter window would silence rooms whenever one admin call hits its timeout.

**Tuning:** if `AdminTimeoutMs` is raised, the window must be raised with it; the clamp enforces this.
The value to record in the config register is `[JanusWebRtcVoice] AdminTimeoutMs` 5000 against
`JS_VIS_STALE_MS` 8000.

---

## 6. Compatibility and deploy

### 6.1 Knobs

| Knob | Where | Default | Meaning |
|---|---|---|---|
| `JS_VIS_FAIL_CLOSED` | mixer env (exported by the entrypoint like `JS_EMPTY_ROOM_GRACE_S`) | **`0`, DISABLED** | `1` enforces §4 in declared rooms. `0` is shadow mode: all state is kept and counted, and audio behaves exactly as today. |
| `JS_VIS_STALE_MS` | mixer env | `8000` | Staleness window (§5). Clamped to the constraint. |

~~The sim needs no new enable knob: 0.2's behaviour rides `VisibilityEmitEnabled`.~~ **Superseded
2026-09-15:** the sim has `[WebRtcVoice] VisibilityArmingEnabled`, default **false** (see the amendment at the
top). The spatial-room `vis_authority` declaration (§6.2) follows the same key. The heartbeat interval is
a sim constant (1000 ms). Promote it to `[WebRtcVoice] VisibilityHeartbeatMs` only if the config register
rules require it. The mixer must reject an `interval_ms` that breaks the §5 constraint.

The mixer banner prints both knobs at startup, following the `RECORDING_OPT_IN` precedent, including
"fail-closed DISABLED (shadow mode)".

### 6.2 Room declaration

Fail-closed applies only to rooms whose creator declared a sim authority:
- `AudioBridgeCreateRoomReq` (`Janus/JanusMessages.cs:503-521`) adds `"vis_authority": true` for spatial
  "local" rooms when visibility emission is enabled;
- the mixer stores `room->declared`;
- the room-create log line gains `vis_authority=true|false`, following the O-83 `spatial_audio` line.

**Undeclared rooms keep today's behaviour even with the knob on:** static jcfg rooms, integration-harness
rooms, A2A "multiagent" rooms and rooms created by an old sim. Each is exempt for a reason:
- A2A rooms get no visibility batches at all (`WebRtcVoiceRegionModule.cs:785-795`), so fail-closed there
  would silence every call.
- The harness has no sim.

**Declaring at create, not on first batch,** is what closes the window between room creation and the first
arming. A joiner in a declared room is silent from the first tick.

The mixer logs once per room when the knob is on and a room is undeclared:
`fail-closed enabled but room <id> has no vis_authority: NOT enforced`. It also reports
`vis_authority:false` in `query_session`.

### 6.3 Skew matrix

| Sim | Mixer | Result |
|---|---|---|
| Old | Old | Today. |
| New (0.2) | Old | Extra batch keys are ignored by `slv_visbatch_parse` (`visbatch.c:126-189`). The sim sends **no heartbeats** until a reply carries `vis_protocol >= 2`; this capability gate exists because an old mixer answers `unknown_request` and the sink would WARN every second (`JanusPeerCtlBatchSink.cs:321-342`). The extra `vis_authority` create key must be confirmed ignored by the old create parser (mixer slice check). |
| Old | New, knob off | Rooms undeclared; shadow counters stay 0 because nothing is declared. Today's audio. |
| Old | New, knob on | Rooms undeclared, so **not enforced**; the per-room WARN makes that visible. Today's audio. |
| New | New, knob off | Shadow mode: full protocol, audio as today, `would_silence_*` counters live. |
| New | New, knob on | Fail-closed. |

### 6.4 Deploy order

1. Mixer slice live, **knob off**. It can go before or after the sim: shadow mode is inert.
2. **Sim 0.2 live:** arming, epochs, generations, heartbeats, `vis_authority`, acting on replies.
3. **Soak (slice 0.6):** with real traffic, `would_silence_listeners` stays 0 in steady state. It may be
   non-zero only in the sub-second window between a join and its arming, and around restarts. **The soak also
   checks the connector/recorder gate explicitly (ledger O-88), as a pass/fail step, not a note:** list every
   connector peer and recorder tap in a declared room and its arming state. The soak fails, and step 4 is
   blocked, while any of them is unarmed, or while the connector/recorder arming design (amendment item 1) is
   not built.
4. Enable `JS_VIS_FAIL_CLOSED=1` (a mixer recreate, which is John's). **Never enable it before step 2 is
   live, and never while O-88 is open.**

**Rollback:** set the knob to 0. No sim change is needed.

---

## 7. Failure modes

In all of these, "silenced" means §4 rows 1 to 3 with fail-closed enabled in a declared room.

### 7.1 Region restart mid-call

1. `RemoveRegion` stops the service (`WebRtcVoiceRegionModule.cs:184-195`, `VoiceVisibilityService.cs:155-170`).
   Its final heartbeat says `"state":"stopping"`, so the mixer marks the region's rooms stale. The rooms'
   listeners are silenced at once, or within 8 s if the stop was not graceful.
2. Viewers normally lose the region and tear down, but any session left joined stays silenced.
3. The region comes back with a new service and a new epoch E2, greater than E1.
   - Its first message to each room is adopted, and **every E1 record is disarmed**.
   - The new instance's startup snapshot arms its whole population (§2 item 1).
   - Listeners still in the region become audible after their arming. Sessions of agents no longer
     present are never armed and stay silent until torn down.
4. **Hazard:** if the wall clock went backwards across the restart, E2 < E1. It is accepted only once E1 is
   stale (the takeover rule), so the region is silent for up to 8 s, then proceeds.

### 7.2 Sim process dies

- No stop heartbeat is sent. Every declared room from every region in that process goes stale 8 s after its
  last confirmation, then silent.
- Nothing arms them until a region server comes back with a new epoch, which then behaves as §7.1 step 3.
- An old sim still alive elsewhere (a zombie) cannot re-take a room from a newer epoch while that epoch is
  fresh (`stale_epoch`).

### 7.3 Mixer restarts while the sim is up

1. The mixer loses every room, session and record. It picks a new `mixer_instance`.
2. Viewers' PeerConnections fail; viewers re-provision. The sim re-creates rooms (declared) and joins them.
   Joiners are **silenced**: the room has no epoch and no records.
3. **Today the sim cannot see this:** `unknown_room` arrives as `janus:"success"`, which maps to `Ok`
   (`JanusAdminClient.cs:163-166`), and `_synced` stays true. Under this design:
   - the next heartbeat reply, or any batch reply, carries a new `mixer_instance`;
   - the sim re-arms **every** listener in every room;
   - re-provisions also arm through `OnListenerProvisioned`.
   - Worst case to audible: re-provision time plus one tick plus one admin round-trip.
4. A heartbeat to a room that is not re-created yet returns `unknown_room`; the sim keeps its per-room
   state and arms when the listener's provision creates the room.

### 7.4 A listener reconnects

**Same room, same epoch.** The new session has the same display.
- It joins the room-level record, so it is armed if that record is still confirmed. There is no silence gap
  from the mixer side.
- Re-provision also re-arms (`OnListenerProvisioned`), advancing `listener_gen`, and the next heartbeat
  confirms it.
- The O-68 leave reset (`:721-724`) clears the **session's** sets. The room-level record survives the leave
  because it belongs to the avatar's standing in the room, not to the session.
- An avatar the sim no longer addresses is removed from the record by the next heartbeat's omission rule.

**Different room** (parcel change, relog into another parcel). The new room has no record, or an older one,
so the listener is silenced until `AgentRoomTable` records the new room and the arming lands (§2 item 5).

**Two overlapping sessions (relog overlap).** Both follow the one record (fan-out, `:1622-1650`), as
exclusions do today.

### 7.5 A room with no listeners

- The sim sends no heartbeat entry for it (§3), so it goes stale. With no participants there is nothing to
  silence.
- The empty-room grace destroy still removes it after 60 s (`SLV_EMPTY_ROOM_GRACE_S`, `:118`, sweep
  `:777-800`), and its records go with it.
- **A later joiner:** provision creates or reuses the room (declared). The joiner is silent until its arming
  `replace` arrives with the current epoch. That fresh message confirms the listener again.
- **A room nobody is placed in is not addressed at all** (slice 0.8c2, ledger O-92 — this replaces the
  "one policy for a missing room record" this section used to state). An agent is addressed at the room it is
  in, and the sim never guesses which that is. The order is:
  1. the agent's room **record**, from the provision that actually happened;
  2. else the room the agent's own parcel would provision it into — the provisioning rule applied without a
     viewer request (`ConnectorRoomResolver`: the parcel's own channel unless it carries `UseEstateVoiceChan`);
  3. else **nothing**: the agent is omitted from every batch, every column and every heartbeat entry, and
     counted. Omission is not a disarm — it was never armed anywhere — and it costs no generation.

  The estate/local room is never an address merely because it is the default. Where it is the **resolved**
  number — an estate-channel parcel, the common case — every byte is what it was before 0.8c2, which is why the
  knob-off goldens are unchanged; such a room gets heartbeats like any other room with listeners. What has gone
  is the estate number standing in for a room the sim could not name, which on a parcel-channel region was a
  room that had never been created. An agent that genuinely belongs to a room but lost its record is placed by
  rule 2 rather than left to a guess, so a feeder restart no longer addresses a whole region at the estate
  number.

---

## 8. Harder than described

1. **The sim cannot observe mixer outcomes.**
   - Every inner status, `unknown_room` included, rides `janus:"success"`, which maps to `Ok`
     (`JanusAdminClient.cs:151-173`), and the sink never acts on the inner reply
     (`JanusPeerCtlBatchSink.cs:273-278`). Mixer restarts are invisible today.
   - Fail-closed needs the sim to act on replies: `mixer_instance`, `stale_listeners`, `unarmed_listeners`.
     That is a behaviour change in the sender, not only a wire change.
   - `mixer-feed-protocol.md` §3.3.1's operational note still says an unknown room returns `applied`; the
     code now returns `unknown_room` (`:1956-1961`). That doc drift should be corrected when 0.2 touches the protocol doc.
2. **The sim's data model has no "empty listener".**
   - `VisibilityMatrix` stores only non-empty columns (`VisibilityMatrix.cs:87-90`).
   - The sender drops empties in three places: `Diff` (`DeltaComputer.cs:18-39`), the snapshot early return
     (`VisibilityBatchSender.cs:321-325`), and the join skip (`:210-219`).
   - Arming needs the population as a first-class set. The matrix must keep it (`Build` already enumerates
     it at `:58-61`), and per-(room, listener) generations must be tracked alongside.
3. **The deferred store discards empty arming.** A deferred record with both channels empty is removed
   (`deferred.h:23`). It must keep `{epoch, listener_gen}`, and replay must check the epoch.
4. **Policy is per session today, but must be per room-level display.** `excluded`/`mod_muted` live on each
   session, are fanned out by display (`:1622-1650`), and are reset on leave (`:721-724`). Generations and
   arming must be keyed (room, display) or reconnects and relog overlaps break. This is a state-model change
   in the mixer, not a parser addition.
5. **Many rooms have no sim authority:** A2A rooms (no batches, `WebRtcVoiceRegionModule.cs:785-795`),
   static jcfg rooms, harness rooms, **connector peers and recorder taps** (SC-96).
   - A global fail-closed would silence all of them. §6.2 scopes enforcement per declared room.
   - A recorder tap or connector peer *inside a declared spatial room* is never armed by today's sim, so it
     would record or hear silence. See open question 3.
6. **Room addressing can be wrong without anyone noticing, and fail-closed makes that audible.**
   - **Slice 0.8c2 (O-92) removed the guess that made this sharp.** An agent with no record is resolved from
     its parcel and, failing that, omitted — §7.5. It is no longer armed in the estate room and silenced in its
     real one; it is armed nowhere until the sim can name its room, which under fail-closed is silence rather
     than policy delivered to the wrong room.
   - The counters at `JanusPeerCtlBatchSink.cs` and `VisibilityBatchSender.Unplaced` now count **omissions**,
     not substitutions, and `show voice visibility` names the rooms the last send addressed. They should read 0
     before the knob is enabled; a standing non-zero is a bug to chase, not a fallback working.
7. **Abandoned sends can apply late.** The in-flight guard lets a stuck send complete later
   (`VisibilityBatchSender.cs:184-189`), so today a late delta can overwrite a newer `replace`.
   `policy_generation` / `base` fix this only if the mixer enforces them.
8. **A 64-bit epoch does not fit the sim's `OSDInteger`** (32-bit, `JanusPeerCtlBatchSink.cs:220`), hence
   the hex string.
9. **Unarmed sources are audible today.** The pair rule (§4) is needed: without it, an armed listener hears
   a new joiner before the authority has evaluated that joiner against the listener's policy.
10. **The heartbeat cannot share the sender's single-flight** (`VisibilityBatchSender.cs:118-140`). A slow
    batch would starve it up to `AdminTimeoutMs`, or 8× that under the hang guard. It needs its own
    in-flight flag, and the window has to include the admin timeout (§5).
11. **Brief line citations drifted:** `:1315` → `:1336`; `:1475` → `:1497-1498`. Ledger O-78 has the same stale `:1475`.

---

## 9. What 0.5's harness must assert

Against the new mixer image; "the emulator" is the harness acting as the sim over the admin API. Unless
stated otherwise, rooms are created declared and the knob is **on**. "Audible" and "silent" use the harness
oracle (`last_mix_rms`), with a test tone as the source.

1. **Knob off, no epoch fields (old-sim emulation):** an un-batched listener hears the source; an exclusion
   `replace` still silences that source. This is today's behaviour, unchanged.
2. **Knob off, full new protocol, listener never armed:** audio is identical to assertion 1, and
   `query_session` reports `would_silence_listeners >= 1` for that room.
3. **Knob on, undeclared room, no batches:** the listener hears the source. The mixer logs the
   "NOT enforced" line once for that room and reports `vis_authority:false`.
4. **Knob on, declared room, listener joined, no arming:** the listener's mix is silent, and it receives no
   presence or power entries for the source.
5. **Empty arming `replace`** for listener and source: the listener becomes audible within 3 mix ticks of
   the reply, and gets the source's join presence.
6. **Arming with S excluded:** L hears T and does not hear S; S is absent from L's roster.
7. **Pair rule:** an armed L does not hear an unarmed S; after S is armed, L hears S.
8. **Heartbeats alone keep policy fresh:** 1 s heartbeats with matching generations for 30 s and no batches
   give continuous audio (no silent window over 100 ms beyond the source's own pauses).
9. **Staleness:** stop heartbeats.
   - L is still audible at `JS_VIS_STALE_MS` − 500 ms.
   - L is silent by `JS_VIS_STALE_MS` + 100 ms.
10. **Recovery in the same epoch:** after assertion 9, resuming heartbeats with matching generations makes L
    audible again without a new arming.
11. **A new-epoch heartbeat does not revalidate:**
    - after arming in E1, a heartbeat with E2 > E1 listing L at the same generation leaves L **silent**;
    - the reply lists L in `unarmed_listeners`;
    - `authority_epoch` reads E2.
12. **New-epoch arming:** after assertion 11, a `replace` in E2 makes L audible.
13. **Lower epoch:**
    - while E2 is fresh, a batch or heartbeat with E1 < E2 gets `status:"stale_epoch"` and changes nothing
      (audio and `authority_epoch` unchanged);
    - after E2 has been stale for the window, E1 is adopted (takeover) and all records are disarmed.
14. **Per-listener staleness in a live room:** a heartbeat naming L with a generation greater than stored
    silences L only; M in the same room stays audible. The reply lists L in `stale_listeners`, and a
    `replace` for L restores it.
15. **Omission:** a heartbeat that omits an armed L silences L.
16. **Delta base check:**
    - an `add` whose `base[L]` does not match L's stored generation is not applied, and L is silenced and
      listed in `stale_listeners`;
    - an `add` with a matching base is applied and advances `listener_gen`.
17. **Out of order:** after a `replace` at generation 10, a delayed `add` at generation 9 is rejected and
    leaves L's set unchanged.
18. **Pre-join arming survives deferral:** an empty arming `replace` sent before L joins is replayed at join,
    and L is audible within 3 ticks of joining with no heartbeat in between.
19. **Reconnect:**
    - L's second session (same display, same room) is audible immediately while the record is confirmed;
    - after the first session leaves, the second stays audible;
    - a session in a different room is silent until armed there.
20. **Fan-out:** two sessions with the same display follow one record (both silenced or armed together).
21. **Mixer restart** (container recreate under the harness):
    - `mixer_instance` in replies differs from before;
    - re-created rooms start with `authority_epoch` 0 and all joiners silent;
    - an emulator that re-arms on the instance change restores audio.
22. **Graceful stop:** a heartbeat with `"state":"stopping"` silences the room's listeners within 3 ticks,
    without waiting for the window.
23. **Room with no listeners:** no heartbeat for 70 s. The room is grace-destroyed as today; a new join plus
    an arming `replace` is audible.
24. **Recorder in a declared room:** silent until armed (documents today's decision; see open question 3).
25. **Reply shape:**
    - every `peer_ctl_batch` and heartbeat reply carries `vis_protocol` 2, a `mixer_instance` stable across
      calls, `authority_epoch` and `policy_generation`;
    - `policy_generation` echoes the highest applied value.
26. **Old image regression catch:** against `legion-voice-mixer:rollback-pre-v3`, batches carrying
    `room_epoch`, `policy_generation` and `base` are applied exactly as without them. The emulator sends no
    heartbeat because no `vis_protocol` is advertised.
27. **Window clamp:** starting the mixer with `JS_VIS_STALE_MS` below the §5 constraint logs the clamp, and
    the effective window in `query_session` equals the constraint.

### Coverage, as built (slice 0.5, 2026-09-16)

Scenarios are in `tests/integration/scenarios.py`; **bold** ones were built by 0.5. S25-S30 and S32 need a
fail-closed scratch mixer, S31 a mixer started below the §5 minimum, S21 a pre-0.3 image — never the live grid's.

| # | assertion | covered by |
|---|---|---|
| 1 | knob off, no epoch fields | S15 (audible unbatched; an unstamped batch applied) + **S27** (the exclusion half: an exclusion `replace` with no epoch fields still silences) |
| 2 | knob off, never armed | S15 |
| 3 | knob on, undeclared room | S17 |
| 4 | knob on, declared, no arming | S16 |
| 5 | empty arming `replace` | S16, and `tests/test_visauth.c` for the 3-mix-tick bound |
| 6 | arming with S excluded | **S25** |
| 7 | pair rule | S16 |
| 8 | heartbeats alone keep policy fresh | S16 — **but for 10 s, not the design's 30 s.** A deliberate shortfall: the extra 20 s buys the same property at triple the runtime. Named here rather than claimed as coverage |
| 9 | staleness | S16 at ±1 s (a polling limit), and `test_visauth` at the exact −500 / +100 ms |
| 10 | recovery in the same epoch | S16 |
| 11 | a new-epoch heartbeat does not revalidate | S16 |
| 12 | new-epoch arming | S16 |
| 13 | lower epoch | S18 (refused while fresh; takeover after a graceful **stop**) + **S32** (takeover after the **window**, the path this section specifies, with the records disarmed) |
| 14 | per-listener staleness in a live room | **S26** |
| 15 | omission | S18 |
| 16 | delta base check | S18 |
| 17 | out of order | **S27** |
| 18 | pre-join arming survives deferral | **S28** |
| 19 | reconnect | S19 |
| 20 | fan-out | S19 |
| 21 | mixer restart | S20 |
| 22 | graceful stop | S18 |
| 23 | room with no listeners | **S29** |
| 24 | recorder in a declared room | **S30** |
| 25 | reply shape | S15 (the keys, and one `mixer_instance` across calls) + **S26** (`policy_generation` echoes the highest applied) |
| 26 | old image regression catch | S21 |
| 27 | window clamp | **S31** (the effective window and the startup WARN) + `test_visauth` (the arithmetic) |

---

## 10. Open questions

1. **Takeover rule.** The takeover rule for lower epochs trusts a stale window of 8 s. Is that enough for a
   region moved between two sim processes, or does a move need an explicit release?
2. **Move the sets?** Should the per-session `excluded`/`mod_muted` sets move onto the room-level record
   (one copy per display)? Recommended, since fan-out already treats them as one.
3. **Connector peers and recorder taps. ARMING DONE 2026-09-16 (slice 0.7a); capability half open (§11.10,
   slice 0.7b). Ledger O-88.** They are ARMED, not exempted (an exemption is a fail-open hole, and the recorder
   is the most privacy-sensitive participant). *Deferred 2026-09-15 as undesigned; answered 2026-09-16:* the
   display is the derived NPC id, the room is the estate room, and a recorder tap is the same record with
   `MayInject=false` plus the existing mute. Registration arms through trigger 2, and unregistration disarms
   through heartbeat omission. 0.2's code arms a registered connector as built, with no production change. See
   the slice 0.7a amendment for the evidence (sim T1 to T6, harness S33). `JS_VIS_FAIL_CLOSED` still stays off
   behind §6.4, and `JS_JOIN_CAP_REQUIRED` stays off until connector capabilities exist.
4. **A2A rooms.** They stay undeclared. Fail-closed for A2A needs the invitation registry to become an
   authority, which is a later phase.

---

## 11. The sim-issued join capability (slice 0.4)

**Added 2026-09-15.** This section did not exist when 0.4 was briefed: the feature was specified only by one line
in ledger O-46 ("a join secret, sim-minted, carried through provision") and one in the 2026-09-09 audit
("sim-issued join capability (avatar, room, generation, expiry)"). It is written here before being built.

**Correction to the 0.4 brief's field list.** The brief bound agent + session + room + expiry + nonce. That
omitted **generation**, and the audit's list is right to include it: the session id ties a capability to one
viewer session, but only the arming state ties it to the authority that was current when it was minted, so a
capability minted before an epoch change does not survive one.

### 11.1 What it is for

O-46: the plugin's join is ungated. Anything holding `JS_API_SECRET` with network reach to the Janus client API
can attach and join any room claiming any avatar's UUID as `display`, inheriting that avatar's exclusion column
and roster identity. Janus itself checks `apisecret` on session create (`janus.c:1129`) and on every
session-scoped request (`:1195`), so the secret plus network reach is the whole of today's gate.

The capability is **defence in depth against a leaked `JS_API_SECRET`**, not a replacement for it. A holder of
the secret can still create sessions and attach; it can no longer join a declared room as an avatar the sim did
not just admit.

### 11.2 Shape

Minted by the sim, verified by the mixer, **never seen by the viewer**: the viewer talks to a region capability
URL, and the sim's own Janus session performs the join (`JanusRoom.JoinRoom`). Nothing about this reaches the
client, so it cannot be captured from a viewer.

```
join_cap = "v1." + b64url(payload) + "." + b64url(HMAC-SHA256(key, "v1." + b64url(payload)))
payload  = "<agent>|<session>|<room>|<epoch>|<generation>|<iat>|<exp>|<nonce>"
```

| Field | Meaning |
|---|---|
| `agent` | the avatar UUID the join will claim as `display` |
| `session` | the sim's viewer-session id (`JanusViewerSession.ViewerSessionID`), sent alongside as `session_id` |
| `room` | the mixer room number the join names |
| `epoch` | the sim authority's `room_epoch` at issue, 16 hex digits; `0000000000000000` when arming is off |
| `generation` | that room's `policy_generation` at issue; `0` when arming is off |
| `iat`, `exp` | issue and expiry, unix seconds; lifetime **60 s** |
| `nonce` | 128 random bits, hex; one join per nonce |

Base64url without padding, HMAC compared in constant time, key = a shared secret that is **not**
`JS_API_SECRET` (a leak of one must not forge the other).

### 11.3 Knobs, and what is required where

| Knob | Side | Default | Meaning |
|---|---|---|---|
| `[WebRtcVoice] JoinCapabilityEnabled` | sim | **false** | mint and send `join_cap` + `session_id` |
| `[JanusWebRtcVoice] JoinCapabilitySecret` | sim | *(unset)* | the HMAC key; unset means it cannot mint |
| `JS_JOIN_CAP_REQUIRED` | mixer | **0** | `1` refuses a join to a **declared** room without a valid capability |
| `JS_JOIN_CAP_SECRET` | mixer | *(unset)* | the same key. `JS_JOIN_CAP_REQUIRED=1` with this unset **refuses to start** (the O-65 discipline: never enforce a security control with no key) |

**Scope: declared rooms only** (rooms created with `vis_authority`, §6.2), exactly as fail-closed is scoped. A2A
rooms, static jcfg rooms, harness rooms and connector-only rooms are never gated, whatever the knob says.

### 11.4 Validation, in order, with a distinguishable reason for each refusal

Every refusal answers `error_code` 496 with a `reason` a sim can branch on; none echoes the capability.

| Reason | Condition |
|---|---|
| `cap_missing` | the knob is on, the room is declared, and the join carried no capability |
| `cap_malformed` | not three dot-separated parts, bad base64url, wrong field count, unparsable numbers |
| `cap_bad_signature` | HMAC mismatch (constant-time) |
| `cap_wrong_agent` | payload `agent` is not the join's `display` |
| `cap_wrong_session` | payload `session` is not the join's `session_id` |
| `cap_wrong_room` | payload `room` is not the join's `room` |
| `cap_expired` | `exp` is in the past, or `iat` in the future, beyond the skew tolerance |
| `cap_replayed` | this nonce has been seen |
| `cap_replay_store_full` | the nonce store is full, so replay cannot be ruled out (§11.6) |
| `cap_stale_generation` | the capability's epoch is NON-ZERO and LOWER than the room's adopted epoch (§11.5, amended 2026-09-17) |

**Amendment 2026-09-17 (slice 0.8b, ledger O-96).** That last row used to read "the epoch or generation is not the
room's current arming state", and the mixer refused any generation above the room's `policy_generation` and any epoch
that differed. Measured live in the 0.8 soak with the knob off, that refused **two of four ordinary avatar joins**
(`cap_stale_generation` 2 of `seen` 5), each the first join into a room the mixer had not yet applied a generation
for. **The generation is now information, never a refusal, in either direction; and only a NON-ZERO epoch below the
adopted one refuses.** A capability with no epoch at all is ACCEPTED. The three accepted-but-different cases are
counted instead — `cap_generation_ahead`, `cap_generation_behind`, `cap_epoch_ahead`, and `cap_no_epoch` — reported
in the Admin API's visibility block beside the refusal counters, and logged once per join.

### 11.5 Generation, and why it is not the session id again

The capability carries the room's `(epoch, generation)` as the sim believed them at issue.

**Amendment 2026-09-17 (slice 0.8b, ledger O-96): the generation never refuses; only a non-zero epoch below the
adopted one does.** The rule is now:

| the capability carries | against the room's adopted epoch | verdict | counted as |
|---|---|---|---|
| epoch E, generation above `policy_generation` | same E | **accepted** | `cap_generation_ahead` |
| epoch E, generation below `policy_generation` | same E | **accepted** | `cap_generation_behind` |
| epoch above the adopted one | lower E, or none adopted | **accepted** | `cap_epoch_ahead` |
| **no epoch (0)** | any, including an adopted E | **accepted** | `cap_no_epoch` |
| non-zero epoch BELOW the adopted one | higher E | **refused** `cap_stale_generation` | the refusal counter |

Why each:
- **Ahead** is the normal case, not a fault. The sim publishes a room's `(epoch, generation)` when a generation is
  **allocated** (`VisAuthority.NextGeneration` -> `JoinCapabilityAuthority.Publish`, read at `JanusRoom.cs:80`),
  while the mixer holds what it has **applied**. Every first join into a fresh room, every room whose batch is in
  flight or was dropped, and every room re-created after a grace destroy is ahead. Refusing it refused real joins.
- **Behind** means a batch landed between minting and joining. Also a race, also not evidence.
- **No epoch** means the sim held no authority state for that room when it minted — not proof of an older
  authority. It is easily reached: the sim forgets a room while the mixer still holds its epoch through the
  empty-room grace, and the same avatar rejoins. Refusing it would refuse an ordinary rejoin. **This is the 0.8b
  ruling change**; the first 0.8b pass (`171af94`) still refused it, as numerically below any adopted epoch.
- **A non-zero epoch below the adopted one** is the one case that proves the authority which minted the capability
  is gone: the mixer has since adopted a higher epoch. That, and only that, refuses.

A capability minted under an older authority therefore still dies with that authority, which is what §11.5 was for;
it simply no longer takes ordinary joins with it.

The audit's field list says "generation" alone. **Generation alone cannot express "did not survive an epoch
change"**: generations restart at 1 in a new epoch, so an old capability can coincidentally match a new one. The
epoch is bound with it for that reason; the refusal reason stays `cap_stale_generation` for both.

**While `JS_JOIN_CAP_REQUIRED=0`, a generation mismatch is counted and logged, never refused.** That is what
0.6's soak measures: a mismatch rate above zero in steady state means minting and arming disagree, and the knob
must not be turned on until it is zero.

**A capability admits; it does not arm.** It is not arming, grants no audibility, and does not touch a listener
record; arming does not admit a join either. The two are independent controls that happen to read the same
`(epoch, generation)`.

### 11.6 The nonce store, bounded, and what happens when it fails

One store per mixer process: nonce → expiry, capped at **4096** live entries, entries dropped once
`exp + skew` has passed.

- **Full:** the mixer refuses with `cap_replay_store_full` while enforcing, rather than admitting a join whose
  replay status it cannot determine. Failing closed on the security control while the feature is off means: with
  the knob off the join is admitted as today and the event is counted.
- **After a mixer restart:** the store is empty, so **a capability replayed inside its remaining lifetime would
  be accepted**. The window is at most the 60 s lifetime and only follows a restart. This is a real hole, small
  and time-boxed, and is written down rather than implied. Closing it needs persistence or a
  restart-crossing epoch in the capability, and neither is in Phase 0.
- **Sizing:** 4096 nonces covers 4096 joins inside one 60 s lifetime, far above any real join rate on one mixer;
  the cap exists so a flood cannot grow memory without bound.

### 11.7 Clock skew

Tolerance **±120 s**: an `exp` up to 120 s in the past and an `iat` up to 120 s in the future are accepted, and a
join that is admitted only because of the tolerance logs a rate-limited WARN naming the observed offset. Beyond
it, `cap_expired`.

The number is chosen for **two hosts in different datacentres**, not for a single box: it is far above the drift
an NTP-managed host shows (seconds at worst), and far below the value of a stolen capability, whose usefulness
is measured in its 60 s lifetime. A tighter tolerance would refuse valid joins on ordinary clock wobble; a wider
one would extend the life of a capability someone captured.

### 11.8 Mixed versions

| Sim | Mixer | Result |
|---|---|---|
| Old (no capability) | New, knob off | Today's behaviour. The join carries no `join_cap`; nothing is validated, counted or refused. |
| Old (no capability) | New, knob **on** | Joins to **declared** rooms are refused `cap_missing`. This is the operator's explicit choice and the reason the knob exists; the deploy order is sim first, then the mixer knob (§11.9). Undeclared rooms are unaffected. |
| New (minting) | Old | The old join branch reads `room`, `display`, `recorder` and the offer, and ignores every other key, so `join_cap` and `session_id` are ignored and the join succeeds exactly as today. |
| New | New, knob off | Validated and counted, never refused. |
| New | New, knob on | Enforced in declared rooms. |

Neither direction degrades to a refused join while the knob is off, which is the default on both sides.

### 11.9 Deploy order

1. Mixer with `JS_JOIN_CAP_REQUIRED=0` (shadow): it validates what arrives and counts.
2. Sim with `JoinCapabilityEnabled=true` and the shared secret set.
3. Soak: every join to a declared room carries a capability that validates, and `cap_stale_generation` is 0.
4. `JS_JOIN_CAP_REQUIRED=1`. Rollback is `0` and a container recreate; no sim change.

### 11.10 What this does not close

- **O-46 is NARROWED, not closed.** The capability attests what the sim decided; it does not make the sim's
  decision safer. For a child agent the sim still picks the room from the viewer's own `parcel_local_id`
  (**O-77**, `ProvisionParcelResolver`), so a capability can be minted for a room the viewer nominated. Closing
  O-46 needs that proximity test too, and 0.4 does not attempt it.
- **Connector peers and recorder taps** join from their own environment with `api_secret` and no sim-issued
  capability, so a declared room with the knob on would refuse them. That is the **sibling of ledger O-88**
  (connector and recorder arming): the same peers, the same undesigned question. **Whoever designs connector
  arming must design connector capabilities in the same pass** — two parked rows on one underlying question is
  how one of them gets forgotten. Until then, `JS_JOIN_CAP_REQUIRED` stays off for the same reason
  `JS_VIS_FAIL_CLOSED` does.

#### Amendment 2026-09-16 (slice 0.7b): the connector join capability

Read at tranq-ais `feature/ais-v3` `9096a4f441` and legion-voice-mixer `main` `be9093e`. Ledger O-88; the arming
half is the slice 0.7a amendment at the top of this document.

1. **Ruling: connector peers are NOT exempted from `JS_JOIN_CAP_REQUIRED`.** The viewer never reaches the Janus
   API, so connector peers are the mixer's only non-sim clients, which is exactly the party the capability checks.
   An exemption would cover everyone the control applies to. The peer fetches a sim-minted capability before every
   join.
2. **Sim.**
   - New key `[VoiceConnector.<name>] CapabilitySecret`. **Unset is the default and reproduces today exactly:** no
     handler is registered (`VoiceConnectorModule.AttachJoinCapEndpoint`), and payloads and logs are unchanged.
   - Set but shorter than 32 characters: a WARN at load, and the record gets no endpoint (never a weak key, the
     O-65 discipline).
   - Set: the region HTTP server (`MainServer.Instance.DefaultServer`, as `WorldMapModule` registers its per-region
     handlers; no new bind knob) serves `POST /voice/connector/<name>/join-cap`. It checks
     `Authorization: Bearer <secret>` with `CryptographicOperations.FixedTimeEquals` over SHA-256 digests, so the
     comparison takes the same time whatever the length.
   - The handler is registered for the whole `/voice` prefix, as a var-path handler. The server matches var paths on
     the first segment only. An exact per-record path would leave an unknown name to the server's own HTML 404, and
     that would be an oracle.
   - **200** returns `{display, room, session_id, join_cap, expires}`, minted by the same `JoinCapability.Mint` as
     0.4. The agent is the NPC id, the session is the record's `ViewerSessionId`, and the room is the record's
     recorded room (the new `VoiceConnectorRecord.Room`, set by `Register`, cleared by `Unregister`). Epoch and
     generation come from `JoinCapabilityAuthority.Resolve(room)`, the source `JanusRoom.JoinRoom` uses for an avatar.
   - **404 with an empty body** for every failure before authentication succeeds: unknown name, inactive record,
     no secret, wrong or missing bearer, wrong method.
   - A record active in more than one region (no `Region=` pin) cannot be resolved. It is a 404 like any other, and
     the operator is told once, in the log.
   - **503** (empty) only after authentication, when the sim cannot mint: `JoinCapabilityEnabled` false, or no
     `JoinCapabilitySecret`.
   - The bearer, the secrets and the capability are never logged. There is one INFO line per mint, naming the
     record, the room and the expiry.
3. **Peers** (`connectors/common/config.py`, `joincap.py`, `peer.py`; the recorder and the injector share the path).
   - `CONNECTOR_CAP_URL` and `CONNECTOR_CAP_SECRET`: both or neither, and one without the other is FATAL at start.
     Neither is today's join, with the same body.
   - When both are set, the peer fetches before every join (each `run()` is one join, so a reconnect fetches
     again), sends `join_cap` and `session_id`, and uses the returned display and room.
   - An env `DISPLAY` or `ROOM` that disagrees with the grant loses, with a WARN.
   - A failed fetch retries with backoff (1 s doubling to 30 s) for as long as the peer runs. **A configured peer
     never joins bare.**
4. **Findings before code.**
   - **F1:** the minter is `JoinCapability.Mint` (`WebRtcVoice/JoinCapability.cs:60`). Generation is **per room,
     not per session**: `JoinCapabilityAuthority.Resolve(RoomId)` (`JoinCapabilityAuthority.cs:33`, called at
     `JanusRoom.cs:80`), published by `VisAuthority.NextGeneration` (`VisAuthority.cs:136`). It maps onto a connector
     unchanged.
   - **F2:** the mixer's validation binds only the join body's `join_cap`, `display`, `session_id` and `room`, plus
     the room's own epoch and generation (`janus_slvoice.c:3053-3061`, `joincap.c:200-213`). Nothing assumes the
     sim's Janus session. Only a comment says so (`joincap.h:12`).
   - **F3:** handlers are added with `IHttpServer.AddSimpleStreamHandler`, and var-path handlers match on the first
     segment (`BaseHttpServer.cs:1109-1123`). The connector module is non-shared, and every region's instance loads
     the same records, so one process-wide endpoint resolves a name across every attached region to the one active
     record.
   - **F4:** the connector records the estate room (`VoiceConnectorModule.cs:316`). An avatar standing on a parcel
     with its own voice channel is provisioned into that parcel's room (`WebRtcVoiceRegionModule.cs:751` ->
     `WebRtcJanusService.cs:424`). They differ; filed as **O-93**, not fixed here.
5. **Proof.**
   - **Sim tests** (`ConnectorJoinCapEndpointTests`), each through a real `BaseHttpServer`'s own handler lookup:
     - U1: secret unset, no handler.
     - U2: a 31-character secret, a WARN and no handler.
     - U3: six pre-authentication failures give one identical answer (404, empty body, no content type), and an
       unknown name never reaches the server's HTML 404.
     - U4: 200, and the capability verifies under a transcription of the mixer's validator with the NPC id, the
       recorded room and the `ViewerSessionId`.
     - U5: 503.
     - U6: no bearer, secret or capability in any log line.
     - U7: 404 after `Unregister`.
     - Mutations: registering the exact path fails U3, and logging the `Authorization` header fails U6.
   - **Harness S34** (`JS_JOIN_CAP_REQUIRED=1`, declared room, the real connector join code against a stub endpoint
     minting with the harness's minter):
     - a peer with no `CONNECTOR_CAP_*` is refused `496 cap_missing`;
     - configured, it joins, and its session records `join_cap_verdict` ok;
     - a replayed capability is refused `cap_replayed`;
     - with the stub answering 404, the peer creates no Janus session and keeps retrying.
     - Mutation: a peer that joins bare on a failed fetch fails S34 on `S34: with the capability endpoint answering
       404 the peer sends no join at all, never a bare one`.
6. **Not closed.** O-88 closes when the 0.9 gate step passes live. Both knobs stay off. S34 proves the peer
   against a stub, not against a live sim endpoint.
