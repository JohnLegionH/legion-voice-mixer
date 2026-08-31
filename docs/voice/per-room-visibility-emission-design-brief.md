# Design Brief — Per-Room Visibility Emission

**Status:** IMPLEMENTED 2026-08-27 (S5). The S1–S5 build plan (§8) is COMPLETE: S1/S1b
(`3c95ddea0e`/`7b08786d19`), S2 (`98465dc662`), S3a/S3b (`ef119f2a90`/`e35463a088`), and S4
(`33fc3b412e`) have shipped; S5 is this flip plus the ledger write-up. NOTE — the shipped S4 DIVERGED
from §8's plan: it did NOT add a `PeerCtlSendResult.NotApplied` enum and changed no
`VisibilityBatchSender` flow; instead it kept the 3-value result, put classification in the sink, and
surfaced counts via a read-only stats property (same observability, smaller blast radius). M1 (mixer
version bump) remains optional. The seven open questions are resolved in §7; §8 is the build plan.
*Originally DECIDED 2026-08-26, nothing implemented; superseded by this line.*
**Date:** 2026-08-26.
**Basis:** `tranquillity-develop` at *docs(voice): file the per-parcel visibility delivery gap,
split #13 by status* (branch `feature/voice-visibility-matrix`); `legion-voice-mixer` at
*fix(voice): clear stale exclusions on leave, error on unknown room and request* (plugin
`janus.plugin.slvoice` 0.9.0 — the version string was not bumped by that commit, so "0.9.0"
alone does not identify whether the `unknown_room` reply is present; see §5).
**Authority for the problem:** `Docs/KnownDefects.md`, *"WebRTC voice: the visibility feed is
addressed only to the estate room, so per-parcel agents receive no exclusions"*. Every claim
below about current behaviour carries a `file:line` citation against the basis above.

## Purpose

Make sim-authoritative voice visibility — parcel ban/restrict, parcel voice moderation,
`SeeAVs` hiding — take effect for agents on per-parcel voice channels, not only for agents
on the estate channel. Today the Phase-3a matrix is computed correctly for every voiced agent
in the region and then delivered to one room, so it is enforced for a subset of them.

## Binding constraints — decided, not re-opened here

- **No data migration.** No existing parcel's `LandFlags` changes, on this grid or any other.
  Running grids upgrade by deploying code only.
- **`LandData.cs`'s default flag word is out of scope.** OpenSim's default omits
  `ParcelFlags.UseEstateVoiceChan` where SL's `PF_DEFAULT` includes it (`LandData.cs:60`–`:64`
  composes seven flags, `0x2800204B`; bit 30 is absent). That five-bit divergence is a
  separate parity question on its own timeline. **This fix must make the default
  irrelevant, not correct it** — a default-flagged region must be fully covered after this
  change with its parcels left exactly as they are.
- **Per-parcel rooms stay.** See *Rejected: one shared room per region* below.
- **The estate channel must not regress.** Agents on the estate channel are fully covered
  today; every path they take must be byte-for-byte the same or strictly better.

## 1. Problem statement, from source

**The sink fixes its room once, at construction.** `JanusPeerCtlBatchSink`'s constructor
computes `_room = JanusAudioBridge.CalcRoomNumber(regionId.ToString(), "local",
JanusAudioBridge.REGION_ROOM_ID, string.Empty)` (`Addons/os-webrtc-janus/WebRtcVoiceRegionModule/JanusPeerCtlBatchSink.cs:38`–`:39`;
`REGION_ROOM_ID = -999` at `Addons/os-webrtc-janus/Janus/JanusAudioBridge.cs:176`) and stamps that
one number on every request it ever sends (`request["room"] = new OSDInteger(_room)`,
`JanusPeerCtlBatchSink.cs:49`). It is the sole `CalcRoomNumber` call site in the module.

**Everything upstream of the sink is room-agnostic by design.** `VisibilityBatchSender`'s header
says "no Janus / no room number here — the sink stamps the room"
(`WebRtcVoiceRegionModule/VisibilityBatchSender.cs:3`); `IPeerCtlBatchSink.SendAsync` takes only
an op and a listener→sources map (`Visibility/PeerCtlBatchSink.cs:30`–`:33`);
`PeerCtlBatchSerializer.BuildRequest` produces a room-less body (`Visibility/PeerCtlBatchSerializer.cs:34`–`:60`);
and `VoiceVisibilityService` hands the feeder a placeholder, `EstateRoomPlaceholder = -999`
(`VoiceVisibilityService.cs:32`, passed at `:64`), which nothing downstream reads
(`VisibilityBatch.Room`, `Visibility/VisibilityBatch.cs:22`, is carried but never consulted by
the sink).

**Room membership is chosen per agent, per parcel, at provisioning.**
`ProvisionVoiceAccountRequest` removes `parcel_local_id` from the forwarded request only when
the parcel sets `UseEstateVoiceChan` (`WebRtcVoiceRegionModule.cs:502`–`:505`). With the flag
clear the viewer's `parcel_local_id` survives, and the service hashes it:
`int parcel_local_id = pRequest.TryGetInt("parcel_local_id", out int pli) ? pli :
JanusAudioBridge.REGION_ROOM_ID` (`Janus/WebRtcJanusService.cs:243`), then
`SelectRoom(pSceneID.ToString(), channel_type, isSpatial, parcel_local_id, channel_id)`
(`:261`–`:262`) → `CalcRoomNumber` (`JanusAudioBridge.cs:195`), which for `"local"` hashes region
ID + `"local"` + parcel local ID. So a per-parcel agent joins room
`H(region, "local", parcelLocalID)` while its exclusion column is addressed to
`H(region, "local", -999)`.

**At the mixer the mismatch is a listener the room does not contain.** `apply_visbatch` scans
`room->participants` for a session whose display equals the entry's listener UUID
(`legion-voice-mixer/src/janus_slvoice.c:1199`); zero matches increments
`vis_dropped_listener_entries` (`:1284`) and `vis_last_batch_dropped_listeners` (`:1319`) and logs
at `LOG_VERB`. The admin reply is `{"slvoice":"applied", …}` regardless (`:1349`).

**Why the default matters.** With `LandData.cs`'s default word, a freshly created region's
initial parcel has `AllowVoiceChat` set and `UseEstateVoiceChan` clear — exactly the
triggering state. On this grid Elm's parcel reads `0x2800204B`, the untouched default. This is
a default-configuration defect with grid-wide reach, which is why the constraint above
forbids fixing it by touching flags.

**What already landed and is a prerequisite.** *fix(voice): clear stale exclusions on leave,
error on unknown room and request* (mixer) makes a session that leaves and rejoins start with
an empty exclusion set. Per-room emission moves a listener's column between rooms when the
agent re-provisions; without that fix the old room's session would have kept the stale set.

## Rejected: one shared room per region

Considered and rejected; recorded so it is not revisited. Collapsing every agent in a region
into the `-999` room would make scoping purely an exclusion problem and trivially fix delivery,
but the mixer's limits make it untenable:

- **`SLV_MAX_MIX` = 110 is a per-room admission ceiling** (`janus_slvoice.c:140`, enforced at
  join with `ROOM_FULL` at `:1681`). Today it bounds each per-parcel room separately; one room
  per region makes 110 the region-wide voice population cap.
- **The mix tick is O(N²) in one thread per room.** `janus_slvoice_room_tick` enumerates only its
  own `room->participants` (`:2423`) and, per listener, walks every source. Merging rooms
  multiplies N inside a single 20 ms tick thread and removes the parallelism separate rooms
  give across cores.
- **The visibility-batch caps are sized for sparse exclusions** (§3). One room per region makes
  exclusion the only scoping mechanism, so columns become dense — most of the region for most
  listeners — and the 128-source and 64 KB caps are reached at populations well under 110.
- **Per-listener state scales with room population:** `cull_hyst[SLV_MAX_MIX]` per session runs
  at capacity and LRU-evicts; the sender's shared-full-batch fast path for listeners with no
  exclusions (`janus_slvoice_sender`) effectively disappears.

Per-parcel rooms are therefore load-bearing for capacity, and this brief keeps them.

## 2. The proposed change

**Principle:** the feeder and sender stay exactly as they are. The sink — already the only
room-aware component — becomes room-*resolving* instead of room-*fixed*: it groups each
`SendAsync` call's listeners by their current room and emits one `peer_ctl_batch` per room.
The wire format does not change; only the `room` values do.

### 2a. Where the listener's room is known, and the new seam

The room an agent is actually in is decided at exactly one place: the successful JSEP-offer
branch of `WebRtcJanusService.ProvisionVoiceAccountRequest`, where `viewerSession.Room` is set
from `SelectRoom` (`WebRtcJanusService.cs:261`–`:262`) and its `RoomId` is the joined room
(`Janus/JanusViewerSession.cs:65`; `Janus/JanusRoom.cs:40`). Three places could read it:

1. **Live, from `JanusViewerSession.Room.RoomId`.** Ground truth for mixer membership, but
   (i) `IVoiceViewerSession` does not expose a room (`WebRtcVoice/IVoiceViewerSession.cs:36`–`:61`),
   so the sink would cast to the Janus type; (ii) the per-agent index in `VoiceViewerSession`
   is private — only `IsAgentInRegion` is public (`WebRtcVoice/VoiceViewerSession.cs:75`–`:76`,
   `:94`–`:102`) — so a new query is needed anyway; and (iii) in the grid-service topology
   (`SpatialVoiceService = WebRtcVoice.dll:WebRtcVoiceServiceConnector`,
   `Addons/os-webrtc-janus/os-webrtc-janus.ini.example`) the region holds
   a `VoiceViewerSession` with no room at all — the `JanusViewerSession` lives on the ROBUST side.
   The sink runs region-side, so this source is topology-dependent.
2. **Recomputed region-side from the agent's parcel** via `CalcRoomNumber`. Topology-independent
   and needs no new plumbing, but it computes the room the agent *should* be in from where it
   is standing now — which diverges from the room it *is* in whenever the agent crossed a parcel
   without re-provisioning (§2b). Batches addressed on that basis land in the wrong room for
   exactly the agents the crossing gap affects. It also couples the sink to `CalcRoomNumber`'s
   encoding (§2c). Rejected as the primary source.
3. **Recorded region-side at provisioning success, from the service's response.** Proposed.

**The proposed seam.** The provision response gains an additive `room` field alongside `jsep`
and `viewer_session` (built at `WebRtcJanusService.cs:275`–`:279`), carrying
`viewerSession.Room.RoomId`. The region module's provisioning handler, at the point where it
already forwards a success to the visibility service (`svc?.OnListenerProvisioned(agentID)`,
`WebRtcVoiceRegionModule.cs:553`, after the service call at `:526`), reads `room` from the
response and passes it: `OnListenerProvisioned(agentID, room)`. `VoiceVisibilityService`
(`:126`) records it in a per-region **agent → room** table and hands the sink a resolver
delegate, `Func<UUID, int?>`, at construction. The sink's `IPeerCtlBatchSink.SendAsync`
signature is unchanged; internally it partitions `excl` by `roomOf(listener)` and sends one
admin message per distinct room.

Why this seam:

- **Topology-independent.** The connector topology forwards the leaf service's response map
  (`WebRtcVoice/WebRtcVoiceServiceConnector.cs:88`–`:95`), so `room` rides through to the region
  from a remote `WebRtcJanusService` exactly as from a local one.
- **Ground truth, not recomputation.** The recorded room is the one the service actually
  joined. No `CalcRoomNumber` call is added on the sim side; the float-encoding coupling in §2c
  does not widen.
- **Naturally gated on success.** Only the JSEP-offer success branch has a `Room`, so only a
  real join produces a `room` field. This also sidesteps *"WebRTC voice: OnListenerProvisioned
  runs on failed provisions, queuing a doomed re-send"* for the room record (the pending-join
  queueing itself is unchanged and that entry stands).
- **Preserves the separation.** Feeder, matrix, delta, sender and serializer are untouched;
  the sender's `_synced` / `_knownListeners` / `_pending` state remains single-instance because
  the sink, not the sender, does the splitting. The only new coupling is a one-way
  `Func<UUID,int?>` from the service into the sink.

**Clearing the record.** A record need not be actively cleared. Matrix membership is already
gated by `VoiceViewerSession.IsAgentInRegion` (`WebRtcVoiceRegionModule/FeederWorldFromScene.cs:61`–`:67`),
so a departed agent is never a listener and its stale record is never consulted; a returning
agent re-provisions and overwrites it. Clearing on close is an optional tidy-up, not a
correctness requirement.

**Sink send semantics across rooms.** `SendAsync` aggregates the per-room results in
severity order: any `ProtocolError` → `ProtocolError`; else any `TransportError` →
`TransportError`; else any `NotApplied` (OQ5) → `NotApplied`; else `Ok`. This preserves the
sender's contract: a transport failure makes the sender re-snapshot next tick, and `replace`
is per-listener idempotent, so re-sending the rooms that succeeded is harmless. `NotApplied`
is counted and otherwise handled as `Ok` for flow control (§7 OQ5).

**Agents with no record.** A listener *or source* the matrix names but the table does not
know is either an agent that never reached the success branch this region saw, or — in the
connector topology against a service that does not yet return `room` — an agent in a room the
region cannot see. Both roles resolve a missing record to the **estate room** (OQ4, and the
consistency resolution in §7). The sink's existing constructor-computed estate room number
becomes the fallback value, and one fallback counter per role records how often it was used.

### 2b. Tracking room changes — and whether this depends on the channel-change gap

Nothing pushes a channel change on parcel crossing today. This module registers
`ProvisionVoiceAccountRequest`, `VoiceSignalingRequest`, `ChatSessionRequest` and
`SpatialVoiceModerationRequest` (`WebRtcVoiceRegionModule.cs:250`–`:275`) and no
`ParcelVoiceInfoRequest`; only the Vivox and FreeSwitch modules do. So an agent's room changes
only when the *viewer* sends a new provision (the service leaves the old room at
`WebRtcJanusService.cs:230`–`:234` and joins the new one at `:261`–`:272`) or on close. Whether
a stock viewer re-provisions on an intra-region parcel crossing cannot be determined from
either repo.

**This fix does not depend on that gap being closed.** The proposed record is written at
provisioning success, which is the only moment membership actually changes. An agent that
crosses from parcel A to parcel B without re-provisioning stays in room A, the record still
says A, and its exclusions land in A — where it is. Its *column* is computed from parcel B
(the feeder resolves parcels by position/`currentParcelUUID`), which is the semantically
right set for where it stands. Any residual wrongness — being audible in A's room while
standing on B — is the crossing gap's, not this fix's, and it is the same wrongness the
estate channel exhibits today.

If the gap is later closed by a push, the push path will terminate in a re-provision, which
writes the record. No change to this design is needed then.

### 2c. The float-encoding defect in `CalcRoomNumber`

*"Parcel local IDs are hashed as `float`, not as an integer"* (`Docs/KnownDefects.md`):
`hasher.Add(pParcelLocalID)` at `JanusAudioBridge.cs:205` binds to `Add(float)` because
`IBHasher` has no `Add(int)`. Both sides that must agree on a room number — the provision
path and, today, the sink — call the same function, so they agree.

**Recommendation: fix it separately, not in this change, and keep this change from touching
the derivation at all.** Three reasons:

- Under the proposed seam rooms arrive from the service. The sink's one remaining
  `CalcRoomNumber` call is the constructor's estate-room computation
  (`JanusPeerCtlBatchSink.cs:38`–`:39`), kept as the OQ4 fallback value. The two derivation
  sites that exist today — provision path and sink — remain the same two, calling the same
  function with the same binding; this change adds none.
- That KnownDefects entry states the consequence of changing the encoding: every room number on
  the grid changes, and every region and the mixer must cut over together or a rolling upgrade
  splits voice mid-flight. That is a grid-wide renumbering with its own deploy choreography, and
  bundling it into a delivery fix makes the delivery fix undeployable in isolation.
- The encoding is deterministic and collision-free at present parcel counts, so it does not
  impair this fix's correctness.

If **Option 2** in §2a were chosen instead, the sink would gain a second derivation site and
the two would have to be kept binding-identical — a reason not to choose it.

## 3. Capacity analysis

This is the highest-risk section, because over-cap items on the mixer are **skipped and
counted, not errored** — overrun degrades silently.

### Caps, verified against source

| Cap | Value | Where | On overrun |
|---|---|---|---|
| `SLV_VISBATCH_MAX_BYTES` | 65,536 | `src/visbatch.h:39` | whole batch rejected `TOOBIG` (`src/visbatch.c:61`); reply `{"slvoice":"error","reason":"too_big"}` (`janus_slvoice.c:1367`–`:1369`) |
| `SLV_VIS_MAX_ENTRIES` | 128 listeners/batch | `src/visbatch.h:42` | extra listeners skipped, `n_skipped++` (`visbatch.c:119`–`:122`) |
| `SLV_VIS_MAX_EXCL` | 128 sources/listener | `src/visbatch.h:45` | extra sources skipped, `n_skipped++` (`visbatch.c:143`–`:146`) |
| `SLV_MAX_MIX` | 110 participants/room | `janus_slvoice.c:140`, `:1681` | join rejected `ROOM_FULL` |

The byte cap applies to the **inner** request object: Janus core passes only
`json_object_get(root, "request")` to the plugin (`vendor/janus-gateway/src/janus.c:2457`–`:2458`),
and the handler measures `json_dumps` of that (`janus_slvoice.c:1342`–`:1344`). The outer envelope
(`admin_secret`, `transaction`, `plugin`) does not count.

Two properties of "skipped and counted" matter here. First, `n_skipped` is reported only in the
reply's `skipped` field, which `JanusAdminClient` never reads — it classifies solely on the outer
`janus:"success"` (`Janus/JanusAdminClient.cs:142`–`:165`). Second, the `too_big` case is a
plugin-level *error* that also rides inside `janus:"success"`, so a rejected-whole batch is as
invisible to the sim as a truncated one. **Today no sim-side instrument can see either.**

### Wire arithmetic

Compact JSON, 36-character UUIDs. A listener entry with k sources costs ≈ 42 + 39k bytes; the
header `{"request":"peer_ctl_batch","op":"…","room":<int>,"excl":{` ≈ 73. A batch with N
listeners of k sources each ≈ 73 + N(42 + 39k).

- **Byte cap:** ≈ 1,670 source entries per batch with few listeners; ≈ 1,540 with 128 listeners
  (keys cost ≈ 5.3 KB). So 128 listeners × more than ~12 sources each is already `TOOBIG`.
- **Dense worst case, one room of N with every listener excluding every other source**
  (mute-everyone via voice moderation is exactly this shape, moderators excepted):
  ≈ 73 + 3N + 39N². **N = 40 → 62,593 (fits); N = 41 → 65,755 (rejected whole).**

### What per-room emission changes

Today one batch per op per tick carries **every** listener in the region's matrix — including
the per-parcel agents whose entries are then dropped — addressed to one room. Per-room
emission partitions that same content by room. Consequences:

- **`SLV_VIS_MAX_ENTRIES` (128 listeners) becomes unreachable.** A per-room batch holds at most
  that room's population, ≤ 110 by `SLV_MAX_MIX`. Today it is reachable: a region whose voiced
  population exceeds 128 across all channels overflows the single estate batch on a snapshot,
  and the 129th-plus listeners are silently skipped.
- **`SLV_VIS_MAX_EXCL` (128 sources) becomes unreachable *only with same-room source
  filtering*** (OQ2, decided). A listener's matrix column contains every excluded source in
  the region, in any room. Sources in other rooms are inert at the mixer — room membership
  already prevents them being heard, and the dot/presence paths iterate `room->participants`
  too — but they consume cap. The symmetric `SeeAVs` rule makes columns wide: an occupant of a
  `SeeAVs=false` parcel excludes every outsider, so its column is (region population − parcel
  population). **Unfiltered, a `SeeAVs=false` parcel in a region of 130+ voiced agents hits the
  cap; filtered to the listener's own room, a column is ≤ 109 by construction.** Filtering
  requires the sink to resolve sources' rooms as well as listeners' — the same table and the
  **same resolver**, so a source with no record resolves to the estate room exactly as a
  listener does (§7, consistency resolution). The ≤ 109 bound holds whenever records exist;
  in the all-unrecorded skew state every column is today's full column, which is precisely
  the state the no-regression constraint requires us to reproduce — no worse than today.
- **Byte cap per batch improves** because each room's batch is a subset of today's single
  batch. It does **not** remove the dense case: mute-everyone in a room of 41+ still produces a
  `TOOBIG` delta, and that case exists today on the estate channel unchanged. See *Guard* below.
- **Batch count per tick rises from ≤ 2 to ≤ 2R**, where R is the number of rooms with at least
  one listener whose column changed this tick; a snapshot is R messages. Steady-state deltas are
  sparse (a crossing touches two rooms), so typical R is 0–2. The worst case is a region-wide
  invalidation — an estate-settings change, or a ban-list edit on a parcel with many
  outsiders — which changes every room at once.
- **Sender single-flight interacts with R.** `Pump` skips a tick if the previous send is still
  in flight and forces a snapshot next (`VisibilityBatchSender.cs:123`–`:135`). All R sends of one
  `SendAsync` run inside one flight. At an admin round-trip of t ms, sequential sends fit the
  250 ms cadence only while 2R·t < 250; beyond that every other tick is skipped, each skip
  forces an R-message snapshot, and the storm self-sustains until R·t drops. **Measured on
  this deployment, reported 2026-08-26: 2.5–3.3 ms over loopback for a trivial Janus core
  request.** That puts the sequential crossover near **R ≈ 40** occupied rooms (125/t). The
  figure is a **floor, not a benchmark**, for three reasons: a real `peer_ctl_batch` parses
  and does per-listener work under `room->mutex` where a core request does neither; an
  operator may run the mixer on a separate host, adding real network latency; and these calls
  contend with the per-room 20 ms tick threads for the same mutexes. Nothing in either tree
  records this figure — it lives here. Mitigation is bounded concurrency (OQ3, decided):
  rooms are independent and `apply_visbatch` takes only its own room's mutex, so per-room
  sends are issued in parallel with a small cap c, making the budget ⌈2R/c⌉·t < 250 ms.
- **Mixer tick cost is unaffected.** Each per-room message takes its room's mutex for one
  sub-millisecond apply; K messages across K rooms contend with nothing but their own room's
  20 ms tick. Sim-side, grouping and filtering are O(total entries), negligible against the
  O(N²) matrix build.

### The parcel/population shape at which a cap is first approached

1. **Dense exclusion inside one room — first cap hit, and pre-existing.** Mute-everyone on a
   room of **41 or more** produces a `TOOBIG` add/replace that is rejected whole and invisible
   to the sim. Same today on the estate channel. Per-room emission neither causes nor cures it.
2. **Wide `SeeAVs` columns without filtering** — `SLV_VIS_MAX_EXCL` at a region of ~130 voiced
   agents with one `SeeAVs=false` parcel. Removed by same-room filtering.
3. **Region voiced population > 128** — `SLV_VIS_MAX_ENTRIES` on today's single batch. Removed
   by per-room emission regardless of filtering.
4. **Many occupied per-parcel rooms changing at once** — the single-flight storm above. Not a
   mixer cap; a sim-side latency budget with an unmeasured constant.

### A heavily subdivided region

Take 64 parcels, all with `UseEstateVoiceChan` clear (the default), each occupied by one or
two agents. R = 64 rooms with listeners. Steady state: almost every tick touches no room or one;
fine. A region-wide invalidation: 64 rooms × up to 2 ops = 128 sequential admin messages in one
flight; at the measured 3 ms floor that is ~380 ms, exceeding 250 ms, so the next tick is
skipped, a 64-message snapshot follows, and the region oscillates until it settles. Each
message is tiny (a room of two has columns of ≤ 1), so no mixer cap is near; the whole cost is
round-trips. Bounded concurrency at c = 4 turns 128 sequential round-trips into 32 rounds,
~100 ms at the floor — inside the cadence with margin for the floor being a floor. Conversely a region
of 64 parcels on the **estate** channel is one room and one message per op, exactly as today —
the subdivided cost is paid only where per-parcel rooms exist, which is the population this
fix is for.

### Guard against silent truncation

Needed. Split across this change and a separate one:

- **Reading the inner reply — in this change (OQ5, decided).** `JanusAdminClient` learns to read
  the plugin's `slvoice` field and returns a fourth, never-latching result for `error` (any
  reason: `unknown_room`, `too_big`, `malformed`), `empty`, and `applied` with `skipped > 0`.
  This gives the sim its first direct view of both silent failure modes — the whole-batch
  `too_big` rejection and the item-level skip count — and confirms per-room delivery far-end.
- **Sim-side counters** in the sink: per-role fallback counts (§7), rooms addressed per tick,
  and `NotApplied` results by reason. With OQ5 these are no longer the only signal, but they
  are the only ones that attribute a failure to a room and a listener.
- **Chunking — a separate change (OQ6, decided).** Splitting a per-room batch by listener into
  messages of ≤ 128 listeners and ≤ ~60 KB would remove `SLV_VIS_MAX_ENTRIES` and the byte cap
  as failure modes and turn the mute-everyone-at-41 case into two messages. It is deferred
  because it requires mirroring the mixer's cap constants into C#, a coupling that drifts
  silently and deserves its own decision, and because the dense case is pre-existing and
  per-room emission does not worsen it. It is filed as its own defect: *"WebRTC voice: a
  dense exclusion batch over 64 KB is rejected whole by the mixer, and the sim reads it as
  applied"* in `Docs/KnownDefects.md`. Note for that change: a per-listener column over 128
  sources cannot be chunked under `replace`; with OQ2's filtering it cannot occur.

## 4. Version skew

The wire format is unchanged: every batch already carries `op`, `room` and `excl`, and the
mixer keys application on `room` per batch (`visbatch.c:94`–`:100`, `janus_slvoice.c:1160`).
Per-room emission only changes the *values* in `room`. The parser ignores unknown keys, but
this change adds none, so that property is not what the skew analysis rests on.

**New sim against old mixer (0.7.0 through 0.9.0 without the `unknown_room` commit).**
Fully functional, not merely graceful. The old mixer applies each batch to the room it names,
which now exists and contains the listener. The one difference is diagnostic: a batch to a
room the mixer does not hold gets `{"slvoice":"applied"}` instead of an error — and since
`JanusAdminClient` reads only the outer envelope, the sim behaves identically either way. No
new mixer is required to deploy this fix. Established from source: the old
`apply_visbatch`/`handle_admin_message` path is the one documented in
`mixer-feed-protocol.md` §3.3.1 and unchanged in shape.

**Old sim against new mixer.** The old sim still addresses everything to the estate room. If
that room exists, the new mixer applies the batch and drops per-parcel listeners with the
`LOG_VERB` line and counters, as before. If it does not exist — a region where no parcel uses
the estate channel, i.e. a default-flagged region — the reply is now
`{"slvoice":"error","reason":"unknown_room"}` (`janus_slvoice.c:1358`–`:1359`) inside
`janus:"success"` (`janus.c:2460`–`:2463`) rather than `applied`. The sim classifies both as `Ok`
(`JanusAdminClient.cs:155`–`:158`). The `WARN` "peer_ctl_batch for unknown room … dropped"
pre-dates the change (it was at the same `LOG_WARN` in 0.9.0), so log volume is unchanged. No
regression.

**Sim-internal skew (connector topology).** A new region module against an old remote
`WebRtcJanusService` receives no `room` in the provision response. The record is never
written for any agent, so every listener **and every source** resolves to the estate room
(OQ4 and the §7 consistency resolution): the sink emits one estate-room batch carrying full
columns — byte-for-byte today's behaviour. Had sources with no record been dropped instead,
every column in that state would have been empty and estate-channel enforcement would have
silently collapsed. The source-side fallback is therefore load-bearing for this skew, not
cosmetic; see §7.

**When `unknown_room` can legitimately occur after this fix.** Rooms are never destroyed on
empty: the `g_hash_table_size(room->participants) == 0` test at `janus_slvoice.c:1920` is the
sender skipping idle rooms, and no sim-side path calls `JanusAudioBridge.DestroyRoom`. A
recorded room therefore exists until the mixer restarts. After a mixer restart every recorded
room is unknown until its agents re-provision — a pre-existing condition for the estate room,
now visible per room in the mixer log. This is why OQ5's inner-reply result never latches:
`unknown_room` is the normal signature of a mixer restart, and it heals by itself through the
pending-join path as agents re-provision.

## 5. Verification plan

Instruments that exist today, and what each should show after the fix.

**Before deploying, establish the baseline on a per-parcel room.** Pick a parcel with
`UseEstateVoiceChan` clear (`SELECT LandFlags FROM land WHERE LandFlags & 0x40000000 = 0`,
read-only). Provision two avatars there and ban one from the parcel *after* it has joined (the
provisioning check now denies a banned avatar at join, *fix(voice): enforce parcel
ban/restrict on the estate voice channel*, so the ban must come second to be a mixer-side
test). Then:

1. **`handle_info` → `excluded_entries`** (`janus_slvoice.c:1014`) on the un-banned listener's
   handle. Before: 0 — the column went to the estate room. After: 1.
2. **`handle_info` → `visibility.dropped_listener_entries` and
   `visibility.last_batch_dropped_listeners`** (`:1079`, `:1081`) on any estate-room handle.
   Before: `dropped_listener_entries` climbs by the number of per-parcel listeners on every
   changed tick and `last_batch_dropped_listeners` is non-zero after each. After: both stop
   moving; `last_batch_dropped_listeners` reads 0 in steady state. **This counter pair is the
   single best regression signal** — it should read zero on every room on a healthy grid.
3. **`visibility.epoch`** on the per-parcel room's handle. Before: 0 forever. After: increments
   on each applied batch. Confirms the room is being addressed at all.
4. **The sink's start-up log** (`JanusPeerCtlBatchSink.cs:40`–`:41`) currently prints one
   estate room number "compare vs handle_info". Replace with a per-send debug line naming the
   room(s) addressed, and a counter of distinct rooms addressed per tick.
5. **Audible check**, the only end-to-end proof: the banned avatar's voice is inaudible to the
   other occupant of the per-parcel room; on the estate channel, unchanged.
6. **The `unknown_room` reply.** With today's `JanusAdminClient` it is invisible — the client
   maps every `janus:"success"` to `Ok` (`JanusAdminClient.cs:155`–`:158`) and discards the body.
   Correctness of this fix does not rest on it: addressing the right room is what fixes
   delivery, and §4 shows the fix works against a mixer that never sends the error. It is
   nonetheless taught **in this change** (OQ5, build step S4), because it is the only sim-side
   view of `too_big`, `skipped`, and `unknown_room`. After S4 the operator sees a `NotApplied`
   counter by reason on the sink; it must read zero for `too_big` and `skipped` in steady
   state, and `unknown_room` only around a mixer restart. The result class is new, not
   `ProtocolError`, whose K-consecutive latch would disable emission on that benign case.
7. **Console: `janus list rooms`** (`WebRtcJanusService.cs:431`–`:432`) to see per-parcel rooms
   exist and their populations; `show voice closing` for parked sessions that would explain a
   missing record.

**Existing tests to extend.** `Tests/WebRtcJanusService.Tests/VisibilityBatchSenderTests.cs`
(16 tests) drives the sender through a fake sink; the sender's only change is the `NotApplied`
handling in S4, so all 16 must pass unmodified and S4 adds cases. New unit coverage belongs on
the pure partitioner introduced in S3a — partitioning by resolver, same-room filtering, the
per-role estate fallback and its counters, result aggregation across rooms — and on
`JanusAdminClient`'s inner-reply classification in S4 (`JanusAdminClientTests.cs`).
`PeerCtlBatchSerializerTests.cs` covers the body builder, which is unchanged.

## 6. What this fix does NOT address

- **The `TaxFree` void.** `LandObject.IsBannedFromLand` and `IsRestrictedFromLand` return
  `false` under `EstateSettings.TaxFree` (`LandObject.cs:847`–`:848`, `:878`–`:879`), so the
  provisioning ban check is a no-op on `TaxFree` estates on both channels. The matrix overrides
  this on its own side via `LandBan.IsBannedIgnoringTaxFree`; the two layers disagree under
  `TaxFree`. Documented in `parcel-voice-semantics.md` addendum §E and §P; untouched here.
- **The missing channel-change push.** No `ParcelVoiceInfoRequest` CAP; an agent crossing
  parcels intra-region stays in its old room until the viewer re-provisions. §2b shows this fix
  is independent of it. Not filed in `KnownDefects.md` as of the basis commit — it is
  referenced only in the commit message of *fix(voice): enforce parcel ban/restrict on the
  estate voice channel* ("tracked separately with the missing channel-change push"). It should
  be filed.
- **The `LandData.cs` default divergence.** Bit 30 absent from OpenSim's default word. Binding
  constraint: out of scope; this fix must work with it in place.
- **The dense-room byte cap.** Mute-everyone in a room of 41+ is `TOOBIG` today and stays so
  after this change. Chunking is deferred to its own change (OQ6); the defect is filed as
  *"WebRTC voice: a dense exclusion batch over 64 KB is rejected whole by the mixer, and the
  sim reads it as applied"* in `Docs/KnownDefects.md`. What this change does add is
  visibility: after S4 the rejection is counted sim-side instead of read as success.
- **`OnListenerProvisioned` on failed provisions.** The pending-join queueing for failed
  provisions is unchanged; only the room record is gated on success.

## 7. Decisions (2026-08-26)

Each question keeps its identifier. The decision is stated first, then the reasoning, then
enough of what was rejected that a future reader sees what was on the table. One conflict
between two of the decisions is resolved at the end of this section.

**OQ1 — Room source. DECIDED (a): additive `room` in the provision response, recorded
region-side at success.** Ground truth — it is the room the service actually joined —
topology-independent, since the connector forwards the leaf's response map, and it adds no
derivation site. *Rejected:* (b) a live read of `JanusViewerSession.Room.RoomId` through a new
registry query — ground truth without a wire change, but the region holds no
`JanusViewerSession` in the connector topology and the per-agent index is private; (c)
region-side recompute via `CalcRoomNumber` from the agent's current parcel — rejected
specifically because it addresses the room the agent *should* be in, which is the defect
being fixed (§2b), and it would add a second derivation site bound to the float encoding.

**OQ2 — Same-room source filtering. DECIDED (a): filter each listener's column to sources in
the listener's room.** This makes `SLV_VIS_MAX_EXCL` unreachable **by construction** — a
per-room column is bounded by `SLV_MAX_MIX` − 1 = 109 — rather than by a guard that has to be
maintained, and it is semantically lossless because a cross-room source is inert at the mixer
(room membership already prevents it being heard, and the dot/presence paths iterate
`room->participants` too). The sink resolves source rooms with the same table and resolver it
uses for listeners. *Rejected:* (b) send full columns — simpler, but the symmetric `SeeAVs`
rule makes the cap reachable at ~130 voiced agents with one `SeeAVs=false` parcel, silently.
As first drafted, (a) dropped a source with no room record; that part is **superseded** by the
consistency resolution below.

**OQ3 — Per-room send concurrency. DECIDED (b): bounded parallel sends per `SendAsync`, with
an aggregated result.** Basis: the admin round-trip on this deployment measured 2.5–3.3 ms
over loopback for a trivial Janus core request (operator measurement, reported 2026-08-26),
which puts the sequential crossover 2R·t < 250 ms near **R ≈ 40** occupied rooms. That figure
is recorded here as a **floor and not a benchmark**: a real `peer_ctl_batch` parses and does
per-listener work under `room->mutex` where a core request does neither; an operator may run
the mixer on a separate host; and these calls contend with the per-room tick threads. A
40-room region is not exotic on the default flag word, and the floor only rises, so sequential
sending is not safe. Concurrency cap c is a `[WebRtcVoice]` config key with a small default
(4). *Rejected:* (a) sequential — the storm in §3; (c) per-room single-flight in the sender —
re-opens the sender, which this design keeps untouched.

**OQ4 — No-record policy. DECIDED (a): fall back to the estate room, with a counter.** This is
the strongest form of the no-regression guarantee — any agent the record does not cover is
handled exactly as every agent is today — and it is what makes the connector-topology skew in
§4 safe. The counter is the evidence that would later justify (b). *Rejected for now:* (b)
drop the entry — cleaner in a fully-recorded world, since an unrecorded agent is in no room
the region knows about, but it forfeits the skew guarantee; revisit when the counter has read
zero across a deployment.

**OQ5 — Inner-reply reading. DECIDED (b): teach `JanusAdminClient` to read the plugin's
`slvoice` field, returning a fourth result that NEVER LATCHES.** Without it there is no
sim-side view of `too_big` at all — a whole-batch rejection reads as success — and no
confirmation that per-room delivery landed. Design detail, not a re-opening: the new
`AdminSendResult.NotApplied` (mirrored as `PeerCtlSendResult.NotApplied`) is returned for
`slvoice:"error"` of any reason, for `"empty"`, and for `"applied"` with `skipped > 0`; the
sender counts it by reason and otherwise treats it as `Ok` for flow control — no
`NoteProtocolError`, no forced snapshot, no retry. Retrying is futile for `too_big` (the
same batch is the same size) and unnecessary for `unknown_room` (a mixer restart, healed by
re-provision through the pending-join path); latching would disable emission on that benign
case. *Rejected:* (a) not in this change — correctness does not need it, but §3's guard has
no far-end signal without it.

**OQ6 — Chunking guard. DECIDED (b): a separate change.** Chunking requires mirroring the
mixer's cap constants into C#, a coupling that drifts silently and deserves its own decision;
and the dense case is pre-existing — per-room emission neither causes nor worsens it. The
pre-existing failure is filed on its own as *"WebRTC voice: a dense exclusion batch over 64 KB
is rejected whole by the mixer, and the sim reads it as applied"* in `Docs/KnownDefects.md`,
because it is a live silent-failure mode independent of this work. *Rejected:* (a) chunk in
this change with mirrored constants.

**OQ7 — Multiple sessions per agent. DECIDED (a): the newest provision wins the record.** The
older session of a relog overlap is addressed at the new room until teardown; under OQ4 that
session is covered by the estate fallback only if it has no record at all, and otherwise it
simply misses exclusions for the existing teardown window. *Rejected:* (b) a set of rooms per
agent — the mixer already fans out by display within a room (`janus_slvoice.c:1199`), so this
would only matter across rooms, for a window bounded by teardown.

### Resolved: one policy for a missing room record

OQ2 as first drafted dropped a *source* with no room record; OQ4 estate-defaults a *listener*
with no room record. Those are two answers to one missing datum, so the question was whether
the asymmetry is correct or an accident. **It is an accident, and the consistent policy is a
single resolver for both roles: `roomOf(agent) = record ?? estateRoom`.** The behaviour that
changes is OQ2's: a source with no record is *not* dropped; it is treated as an estate-room
source — kept for estate-room listeners, filtered out for per-parcel listeners. OQ4's
listener behaviour is unchanged.

Why symmetry is required rather than merely tidy. A missing record means one of two things:
the agent never reached the success branch this region saw (it is in no room, and any
exclusion naming it is inert either way), or — the connector-topology skew — the agent is in
a room the region cannot see because the remote service does not yet return `room`. In that
second case *nobody* has a record. With asymmetric handling every listener falls back to the
estate room (good) while every source is dropped (bad): every column arrives empty, and
estate-channel enforcement silently collapses for the whole region. That violates the
no-regression constraint and defeats the exact skew coverage OQ4 cites as its reason. With
the symmetric fallback both roles map to the estate room, the sink emits one estate-room
batch carrying full columns, and the skew state is today's behaviour byte-for-byte.

The other cases check out. Fully recorded (no skew): an unrecorded source is in no room, so
keeping it for estate listeners costs cap but not correctness, and dropping it for per-parcel
listeners is right. Mid-deploy transition (service upgraded, some agents provisioned before):
a pre-upgrade source in per-parcel room P defaults to estate and is filtered out of a
post-upgrade listener's column in P — that listener hears it until the source re-provisions,
which is today's behaviour for P (no exclusions at all), so not a regression; in the estate
room the pre-upgrade source defaults to estate and is kept, correct. Capacity: with records
present a column is ≤ 109 as OQ2 promises; in the all-unrecorded state it is today's full
column, which is the thing the constraint requires us to reproduce.

Two counters, not one: `fallback_listeners` and `fallback_sources`. The listener counter is
OQ4's evidence; the source counter reads non-zero in exactly the skew and transition states
above and should read zero on a fully upgraded deployment — which is the signal that OQ4(b)
could later be revisited.

## 8. Build plan

Ordered steps. Each is one commit and leaves `Tranquillity.sln` building. **Sim-side** unless
marked. Deploy artefacts are named because the deploy root is flat and keyed on DLL name.

**S1 — Service reports the joined room.** `WebRtcJanusService.ProvisionVoiceAccountRequest`
adds `"room": viewerSession.Room.RoomId` to the JSEP-offer success map
(`Janus/WebRtcJanusService.cs:275`–`:279`). Additive; no consumer breaks. In the connector
topology the same DLL runs on the ROBUST side and the connector forwards the map
(`WebRtcVoice/WebRtcVoiceServiceConnector.cs:88`–`:95`), so nothing else is needed for skew
to resolve once both sides run S1. Test: a response-shape assertion alongside
`ProvisionViewerSessionGuardTests`. Artefact: `WebRtcJanusService.dll`.

**S2 — Region records it.** `VoiceVisibilityService` gains a per-region agent → room table,
`OnListenerProvisioned(UUID agent, int? room)` (the existing overload delegates with `null`),
and a public `Func<UUID,int?> RoomOf`. `WebRtcVoiceRegionModule.ProvisionVoiceAccountRequest`
reads `resp["room"]` and passes it at the existing hook (`WebRtcVoiceRegionModule.cs:550`–`:553`).
Reading from the success map alone gates the *record* on success; the pending-join queueing
is left as it is (that defect stands). Test: table semantics — newest wins (OQ7), missing
key → null. No emission change yet; the sink still stamps one room. Artefact:
`WebRtcVoiceRegionModule.dll`.

**S3a — Pure partitioner.** In `Addons/os-webrtc-janus/Visibility/`, a dependency-free helper
(illustratively `PeerCtlBatchPartitioner`) that takes the listener → sources map, a resolver
and the estate room, and returns per-room maps with same-room filtering, per-role fallback
counts, and nothing else. Sibling of `PeerCtlBatchSerializer` and testable the same way. Tests
in `Tests/WebRtcJanusService.Tests`: partitioning, filtering, both fallbacks, empty map,
single-room fast path. Not wired; builds. Artefact: `VoiceVisibility.dll`.

**S3b — Sink partitions and sends per room. FIRST STEP THAT IS TESTABLE IN-WORLD.**
`JanusPeerCtlBatchSink` gains a settable resolver — it is constructed *before* the service
(`WebRtcVoiceRegionModule.cs:174`–`:176`), so the resolver is a property the service sets in
its constructor, not a ctor argument. `SendAsync` partitions via S3a, sends one message per
room under a `SemaphoreSlim(c)` (`[WebRtcVoice] VisibilityRoomSendConcurrency`, default 4),
aggregates per §2a, keeps the constructor estate room as the fallback value, logs the rooms
addressed at debug, and exposes counters (rooms per send, `fallback_listeners`,
`fallback_sources`). The start-up log at `JanusPeerCtlBatchSink.cs:40`–`:41` is reworded to
name the fallback room rather than "the" room. After this step §5 items 1–3 can be run.
Artefacts: `WebRtcVoiceRegionModule.dll`, `VoiceVisibility.dll`.

**S4 — Inner-reply reading.** `JanusAdminClient` parses `response.slvoice` (and `reason`,
`skipped`) from the 2xx body and returns `AdminSendResult.NotApplied` per OQ5;
`PeerCtlSendResult.NotApplied` added; the sink maps and aggregates it; `VisibilityBatchSender`
counts it by reason and treats it as `Ok` for flow control — no latch, no snapshot, no retry.
Tests: `JanusAdminClientTests` classification cases (`applied`/`applied+skipped`/`empty`/
`error:*` all under `janus:"success"`), new `VisibilityBatchSenderTests` cases, and the
existing 16 unmodified. Artefacts: `WebRtcJanusService.dll`, `WebRtcVoiceRegionModule.dll`,
`VoiceVisibility.dll`.

**S5 — Documentation.** This brief's status → implemented; the `KnownDefects.md` delivery-gap
entry → fixed, citing the S3b commit subject; `mixer-feed-protocol.md` §3.4's correction note
updated to say per-room emission exists; `parcel-voice-semantics.md` §P Part 2 → closed for
per-parcel agents.

**M1 — Mixer, optional, independent.** Bump `JANUS_SLVOICE_VERSION_STRING` so a deployed
plugin can be identified as carrying the `unknown_room` reply (the fix commit did not bump
it). No functional change; required by no sim step. The only mixer touch in this plan.

**Deploy order.** S1 is safe alone on either side. S2–S4 deploy together region-side; in the
connector topology S1 must reach the ROBUST side for records to appear, and until it does
OQ4's fallback reproduces today's behaviour (§4). Roll back is the pre-fix
`WebRtcVoiceRegionModule.dll`/`VoiceVisibility.dll`/`WebRtcJanusService.dll` set.

## Acceptance

- On a per-parcel room, a ban applied after join produces `excluded_entries = 1` on the other
  occupant's handle and inaudibility in-world; on the estate channel, unchanged.
- `visibility.last_batch_dropped_listeners` reads 0 on every room in steady state;
  `dropped_listener_entries` stops climbing.
- A default-flagged region (all parcels `0x2800204B`, `UseEstateVoiceChan` clear) is fully
  covered with no flag changed.
- `fallback_listeners` and `fallback_sources` read 0 on a fully upgraded deployment; the sink's
  `NotApplied` count reads 0 for `too_big` and `skipped` in steady state.
- The existing 16 `VisibilityBatchSenderTests` pass unmodified.
- Against a mixer without the `unknown_room` commit, behaviour is identical (§4).
