# Mixer Feed Protocol — the C#→mixer visibility interface (Phase 3a)

**Status:** DRAFT / design-only. No code has crossed the boundary. This document defines the
sim(C#)→mixer(plugin) interface for the **per-listener visibility matrix** *before* it is built,
so both sides agree on the seam. It is the Phase-3a counterpart to the viewer→mixer data-channel
format documented in [`../sldata-extensions.md`] and grounded in the sim-side inventory
[`parcel-voice-semantics.md`] (read its **ADDENDUM** first — it corrects #12).

> **Authority.** Where this doc and training disagree, the mixer repo's `docs/` win. Sim-side
> `path:line` citations are relative to `parcel-voice-semantics.md`'s baseline; see that doc's
> addendum for current line numbers. Nothing here is committed until reviewed.

---

## 0. Why a new feed at all (the problem in one paragraph)

Every voice room today is a **flat, symmetric membership set**: `JanusRoom.JoinRoom` issues an
identical `AudioBridgeJoinRoomReq(room, display)` for every session — no per-participant mute,
hide, or allow list (`parcel-voice-semantics.md` §3.2). All parcels that resolve to the estate
channel collapse onto one room (`REGION_ROOM_ID = -999`). The sim has rich per-avatar exclusion
semantics (parcel ban/restrict, `SeeAVs` hiding, estate ban) but **no way to express them into a
shared room** — which is exactly why #13 (estate-channel ban bypass) is a live bug: the only ban
check is a whole-session join gate for the *requesting* parcel, skipped entirely on the estate
branch (`WebRtcVoiceRegionModule.cs:301`, the `else` of `:297`). Fixing #11/#13/#5 all require the
same missing thing: **a per-listener exclusion feed from the sim into the mixer.** This document
specifies that feed.

The mixer already *applies* per-listener exclusions — the Phase-2 `peer_ctl` map
(`{uuid, has_mute, muted, has_gain, gain}` per listener, `sldata-extensions.md` §"Per-source mute
and gain") drops/scales sources per listener in the mix loop. The visibility matrix reuses that
application machinery; what's new is the **source** (the sim, not the viewer) and the **transport**
(a server-to-server control message, not the viewer's data channel).

---

## 1. The single-source-of-truth rule (non-negotiable)

**One matrix. One producer. Both consumers.**

The sim produces exactly one per-region **visibility matrix** `V[L][S] ∈ {audible, excluded}`
("may listener L hear/see source S"). From that same matrix the mixer derives **both**:

1. **Audio exclusion** — S omitted from L's N-1 mix (extends the existing `muted` path).
2. **Data-channel omission** — S omitted from the per-peer batch the mixer pushes to L
   (the `{ "<uuid>": {"p":…,"v":…} }` roster + `j`/`l` join/leave, `sldata-extensions.md`
   §"What's implemented"). An excluded S must not appear as a dot, must not animate, and must not
   emit a join notice to L.

These **must not** be computed independently. The baseline's #14 divergence — coarse map dots
already ignore `SeeAVs` while full avatar data honours it (`SceneGraph.cs:256` vs the
`ParcelHideThisAvatar` sites) — is the cautionary example: two code paths answering "is S visible
to L?" drifted, and dots leaked through the hide boundary. If the mixer computes audio exclusion
from the matrix but computes the roster from raw room membership, dots and audio will drift the
same way. **Rule: the roster builder and the mix loop read the same per-listener exclusion set.**

Concretely in the plugin: the visibility matrix folds into the *same* per-listener `peer_ctl` map
that mute/gain already populate, as a new independent dimension (`has_vis`, `visible`) — see §4.
Both the mix loop and the roster builder already iterate that map per listener; neither gets a
second source of truth.

---

## 2. Sim side — the `VoiceStateFeeder`

A single region-module component. **One code path produces the one matrix.** It exists because the
sim's own parcel tracking is not uniform across root and child agents (baseline §2.1–§2.3).

### 2.1 Inputs (how the feeder learns each avatar's parcel)

| Agent kind | Parcel source | Why |
|---|---|---|
| **Root** | event-driven: `OnAvatarEnteringNewParcel` (root-only, fired `LandManagementModule.cs:550`) refreshes that avatar; `ScenePresence.currentParcelUUID` is the cached value | Root agents get the crossing event and movement-driven `currentParcelUUID` updates. The event fires on **any** `GlobalID` change, including crossings between two parcels on the same channel (baseline §2.1(2)) — so same-channel moves are seen. |
| **Child** | position-derived: `LandChannel.GetLandObject(x, y)` on `ScenePresence.AbsolutePosition`, evaluated on the **feeder tick** | Child agents receive **no** crossing event (guard `LandManagementModule.cs:538`) and their `currentParcelUUID` is unreliable / often `UUID.Zero` (baseline §2.2). They must be polled. This is expected, not a defect (baseline #9). |

Enumerate with `Scene.ForEachScenePresence` (children **included**; baseline §2.3) so cross-region
audibility works. Root agents can be refreshed reactively on the event and only re-derived on tick
as a cheap consistency check; child agents are re-derived every tick from position.

**Feeder tick.** A fixed cadence (proposed **default 250–500 ms**, configurable) drives child-agent
re-derivation and matrix diffing. Rationale under §3 (movement is frequent; this is the dominant
update driver, not parcel edits). The tick is decoupled from the mixer's 20 ms mix tick and 100 ms
roster push — the feeder emits only on **change**, not every tick.

### 2.2 The predicates that fill the matrix (all sim-authoritative, never viewer-supplied)

For each ordered pair (listener L, source S) with parcels P_L, P_S, `V[L][S] = excluded` iff any:

- **Parcel hide (`SeeAVs`)** — symmetric. If `P_S.SeeAVs == false` and `P_L ≠ P_S` and L is not a
  god, S is hidden from L (baseline §1.2: `LandData.SeeAVs` → `ParcelHideThisAvatar`, predicate
  `currentParcelUUID != peer.currentParcelUUID && !peer.IsViewerUIGod`). Apply symmetrically (also
  hide L from S if `P_L.SeeAVs == false`) so the pair never has one-way audibility — see §3 on
  symmetric hiding.
- **Parcel ban/restrict** — S banned or restricted from **L's** parcel P_L (and vice-versa):
  `IsBannedFromLand` / `IsRestrictedFromLand` / `IsEitherBannedOrRestricted`
  (baseline §1.1, `LandObject.cs`). This is the data #13 needs but the current voice path skips on
  the estate channel. Key change from today: the check is evaluated **per (L,S) pair against each
  one's parcel**, not once for the requesting parcel at provision time.
- **Estate ban** — `EstateSettings.IsBanned` (baseline §1.4). No current voice consumer; the
  feeder is the first.
- **Voice disabled** — if voice is off for P_S (`!EstateSettings.AllowVoice` OR, absent `TaxFree`,
  `!(P_S.Flags & AllowVoiceChat)`; baseline §1.3/§3.1) S is a source to no one.

**Exemptions** (baseline §1.1 preamble, applied consistently): estate `TaxFree`, admin
(`IsAdministrator`), estate manager/owner (`IsEstateManagerOrOwner`), parcel owner
(`LandData.OwnerID`) short-circuit the ban/restrict tests. `IsViewerUIGod` exempts the `SeeAVs`
hide. Keep the exemption set identical to the sim's so voice never diverges from the visual model.

> **Do not key voice on `CanBeOnThisLand`.** It enforces ban only below `BanLineSafeHeight` and
> uses `else if` for restriction (baseline §1.1 height nuance) — a banned avatar above the ban line
> would leak. Use `IsBannedFromLand`/`IsRestrictedFromLand` directly.

### 2.3 Invalidation (when the feeder recomputes)

| Change | Signal | Notes |
|---|---|---|
| Parcel flags / `SeeAVs` / access list / ban list | `OnLandObjectAdded` (covers "updated" too — forwards via `TriggerLandObjectUpdated`, baseline §2.4) | Rare. Recompute affected parcels' rows/cols. |
| Root avatar crosses a parcel boundary | `OnAvatarEnteringNewParcel` | Root only. |
| Child avatar moves | feeder tick position re-derivation | The polling path; dominant driver. |
| Estate ban/manager/`AllowVoice` change | `IEstateModule.OnEstateInfoChange` (see §5) | Coarse today — re-read `EstateSettings` and diff. |

---

## 3. Wire format — how per-listener exclusions reach the plugin

### 3.1 Transport is server-to-server, NOT the viewer data channel

The viewer's `m`/`ug` ride the peer's WebRTC **data channel** (viewer→mixer). The visibility matrix
must **not** — its source is the sim, and the whole point of #13 is that **the viewer's own
parcel/ban status cannot be trusted** (`WebRtcVoiceRegionModule.cs:245` already comments
"*do fully not trust viewers voice parcel requests*"). The feed therefore travels over the
**existing sim→Janus signaling path** the region module already uses to provision
(`WebRtcVoiceServiceConnector` JSON-RPC → Robust service → Janus plugin; baseline §3.3), as a new
plugin **control request** addressed to the room/session. It is authenticated server-to-server and
never exposed to the viewer. (Exact carrier — plugin request vs Janus admin API — is an
implementation detail to confirm against the live path; the *shape* below is transport-agnostic.)

### 3.2 Recommendation: extend `peer_ctl` (incremental per-listener deltas), NOT a periodic bulk matrix

Two candidates were considered:

- **(A) Bulk matrix message** — sim pushes the whole `V[L][S]` (or per-room adjacency) on every
  change. Simple to reason about; **O(N²)** per push and re-sent on every avatar movement.
- **(B) Extend `peer_ctl`** — sim pushes, per affected listener, the set of source UUIDs to
  exclude, merged into the same per-listener control map that `m`/`ug` already use.

**Recommend (B), with a bulk *snapshot* as a sub-mode of the same message (not a separate path).**
Reasoning keyed to the update rate:

- Parcel/estate **edits are rare** (#12/#2.4 events); but **avatar movement across parcels is
  frequent** and is the dominant driver (§2.1). A full O(N²) matrix on every crossing is wasteful —
  when one avatar crosses a boundary, only that avatar's row and column change. Delta cost scales
  with **avatars crossing**, not **avatars present**.
- (B) reuses proven machinery: the mixer already keeps a **persistent, incrementally-merged**
  per-listener map and already looks it up by source UUID in the mix loop
  (`sldata-extensions.md` §"Per-source mute and gain"). Visibility is just another independent
  field in that map — a mute update never wipes a gain; likewise a visibility update never wipes a
  mute. Same merge discipline, same lookup.
- **Snapshot sub-mode** covers bootstrap/resync: on a listener's join or room (re)assignment the
  feeder sends that listener's **full current exclusion column** in one message (a `replace` for
  that listener), then reverts to deltas. This is still (B)'s shape — a per-listener set — just
  flagged `replace` instead of `add`/`remove`. Avoids a cold-start hole without a standing O(N²)
  broadcast.

**Symmetric-hiding note.** `SeeAVs` hide and ban are treated as **symmetric** for voice (if S is
excluded from L, L is excluded from S). The feeder computes both directions and emits **both**
listeners' entries — it does not rely on the mixer to infer the reverse. This keeps the mixer's
rule dead simple ("exclude S from L's mix iff S ∈ L's exclusion set") and prevents one-way
audibility. (Note the *visual* `SeeAVs` model is technically one-way at the source — an occupant of
a hidden parcel is hidden from outsiders, but outsiders remain visible to the occupant, because
`ParcelHideThisAvatar` derives from the *sender's* parcel; baseline §1.2. Voice deliberately
chooses symmetric so a listener never hears someone they can't see. This is a design choice, called
out here so it isn't mistaken for a mismatch with the visual code.)

### 3.2.1 Worked churn example — does the arithmetic support (B)?

A concrete venue, to check the recommendation survives real numbers rather than intuition.

**Scenario (stated assumptions).**
- **40 avatars**, one estate room (channel `-999`).
- Parcel distribution: **H** (`SeeAVs=false`, hidden) **8 occupants**; **A/B/C** = 10 / 10 / 7;
  open space = 5. (6 parcels; 4 are "exclusion-relevant": H + the 3 banned.)
- **Bans**: 5 avatars banned across A/B/C. Each banned avatar is excluded (symmetric) from the
  current occupants of the parcel it's banned from — assume ≈9 occupants per banned parcel →
  ≈**45 unordered ban-exclusion pairs** (overlap ignored).
- **Movement**: **3 parcel crossings/sec** (busy venue). Feeder tick **250 ms** (4 ticks/s),
  emit-on-change.

**Resident matrix (identical for both approaches — it's the same matrix).**
- SeeAVs exclusions: 8 hidden × 32 outsiders = **256 unordered**.
- Ban exclusions: **≈45 unordered**.
- Total ≈ **301 unordered = 602 directed entries**, out of a 40×39 = **1560** possible directed
  pairs (≈39% filled — *not* sparse, because one 8-occupant hidden parcel dominates).
- Per-listener resident (directed): **avg ≈15** (602/40); **max ≈32–34** (a hidden-parcel
  occupant excludes all 32 outsiders, plus any bans); **min 8** (an ordinary avatar excludes only
  the 8 hidden occupants). Both approaches store this same set — resident memory is a wash.

**What one crossing actually changes.** A crossing only mutates exclusions when it changes a
`P_L == P_S` relationship or a banned parcel's occupant set:
- **Into/out of a SeeAVs (hidden) parcel** — the fan-out the first draft undercounted. Entering H
  does not merely re-pair the mover with H's occupants; the mover *itself* becomes hidden from
  **every** outsider on a different parcel. So the change is **−(occupants) removed** (co-occupants
  no longer hidden from the mover) **+ (N − occupants − 1) added** (mover now hidden from all
  outsiders). For the 40-avatar / H=8 case: **−8 + 31 = 39 unordered (78 directed)**, touching ~40
  listener entries. This crossing is **O(N)**, not O(1).
- **Into/out of a banned parcel** (mover not the banned one): adds/removes the mover ↔ each avatar
  banned from that parcel → typically **1–2 unordered (2–4 directed)**.
- **Ordinary ↔ ordinary** (neither hidden nor banned): the mover stays an outsider to every hidden
  parcel and touches no ban set → **0 entries**. A banned avatar simply *moving* also changes
  nothing (its exclusions track the banned parcel's occupants, not its own position). **The
  majority of crossings touch zero exclusion entries.**
- **Worst-case single crossing** is that SeeAVs-boundary case: **≈ 2·(N − occupants) directed**, up
  to **N−1 unordered** when stepping onto an *empty* hidden parcel (hidden from everyone at once).
  For N=40 that is ~39 unordered / ~78 directed.

> **Executable pin.** This fan-out is asserted by
> `Tests/WebRtcJanusService.Tests/VoiceStateFeederTests.SeeAvsFanOut_MoverEntersHiddenParcel_AddsExclusionWithEveryOutsider`:
> a mover stepping onto an *empty* hidden parcel among 17 outsiders yields exactly **17 additions**
> (mover ↔ each outsider, both directions) and **0 removals**. If this worked example and that test
> ever disagree, the test is the source of truth.

**(B) extend `peer_ctl` — delta:**
| Metric | Value |
|---|---|
| Messages/sec at mixer | ≤4 (one per 250 ms tick), typically **2–3/s**; empty ticks skipped |
| Bytes/msg | a SeeAVs-boundary crossing ~78 directed ≈ **<2 KB**; ordinary crossings <200 B |
| **Mixer entry-writes/sec** | movement-dependent: ordinary crossings ~0, a SeeAVs-boundary crossing ~78 directed. At ~3 crossings/s with occasional hidden-parcel boundaries, **low tens/s typical, ~80/s peak** |
| Entries touched / crossing | **0 (ordinary) … ~2·(N−occ) ≈ 78 (a SeeAVs boundary)** |

**(A) bulk matrix — full push per change:**
| Metric | Value |
|---|---|
| Messages/sec at mixer | **3/s** (every crossing → recompute + ship the whole matrix; it cannot tell a 0-change crossing from a real one without doing the diff it's trying to avoid) |
| Bytes/msg | dense bitmap 1560 bits ≈ **195 B**; *as* per-listener UUID lists (to fit the mixer's structure) ≈ 602 × ~40 B ≈ **~24 KB** |
| **Mixer entry-writes/sec** | 3/s × replace 602–1560 directed ≈ **1,800–4,700/s** |
| Entries touched / crossing | **602–1560 (always the whole matrix)** |

**Verdict — recommendation (B) survives, and here's exactly why.**
- **Mixer-side churn** is still the decisive axis, though the honest per-crossing gap is narrower
  than a first cut suggested: a SeeAVs-boundary crossing touches ~78 directed (not ~15), while an
  ordinary crossing touches ~0. Bulk re-touches the full **602–1560** every crossing regardless. So
  the delta advantage runs from **unbounded** (0 vs the whole matrix, the common ordinary crossing)
  down to **~20× at N=40** on the worst SeeAVs crossing (1560 / 78) — never against delta.
- **Raw bandwidth is *not* the argument at this size**: a bulk *bitmap* is only ~195 B × 3/s ≈
  **0.6 KB/s**, comparable to the delta stream. If someone argues bulk on bytes alone, they're not
  wrong at N=40 — so the recommendation rests on churn and structure, not bandwidth.
- **Structure fit**: the mixer already keeps a **sparse, UUID-keyed, incrementally-merged**
  per-listener map (the `m`/`ug` path). Delta updates drop straight in. The bulk *bitmap* needs a
  dense N×N with a stable avatar-index map the mixer doesn't have; the bulk *list* form is just a
  delta message that always sends everything. Either bulk variant fights the existing design.
- **Scaling** settles it. Delta cost per crossing is **O(N)** worst-case (a SeeAVs boundary fans out
  to all outsiders) and ~0 for ordinary crossings; bulk is **O(N²)** every crossing. At **N=200**
  (multi-region estate, busy sim): bulk ≈ 200×199×3 ≈ **119,000 entry-writes/s**; delta is ~0 for
  ordinary crossings and at most a few × 2·200 ≈ **hundreds–low-thousands/s** even when hidden-parcel
  boundaries are being crossed. The O(N)-vs-O(N²) gap widens with population.

**Conclusion:** extend `peer_ctl` with delta `add`/`remove` + a per-listener `replace` snapshot
(§3.2). The corrected arithmetic narrows the per-crossing margin at N=40 (to ~20× on a SeeAVs
boundary, unbounded on ordinary crossings) but does not change the decision, which turns on
**O(N)-per-crossing vs O(N²)** scaling and structure fit.

### 3.3 Proposed message shape (illustrative — names to be finalized in code review)

Per-room control message, `op ∈ {add, remove, replace}`, keyed by **listener** UUID, valued by the
set of **excluded source** UUIDs:

```json
{
  "slvoice_vis": {
    "op": "add",
    "room": -999,
    "excl": {
      "<listenerL-uuid>": ["<sourceS-uuid>", "<sourceS2-uuid>"],
      "<listenerM-uuid>": ["<sourceS-uuid>"]
    }
  }
}
```

- `add` / `remove` — merge/unmerge these (L, S) exclusions into the persistent map (the frequent,
  movement-driven case; a crossing sends the handful of changed pairs, both directions).
- `replace` — set L's exclusion set exactly to the given list (bootstrap/resync for one listener;
  empty list clears L).
- `room` scopes it (the estate room is `-999`; per-parcel rooms use the `CalcRoomNumber` hash).
- Reason codes (ban vs `SeeAVs` vs estate-ban vs voice-off) are **not** on the wire — the mixer only
  needs the boolean exclusion. Keep reasons in the feeder's logs for debuggability.

The mixer folds `excl` into each listener's `peer_ctl` entry as a `has_vis`/`visible=false` flag
(§4), independent of `has_mute`/`has_gain`.

**Wire invariant — empty is reason-free.** Because reason codes are off the wire (above), an
**empty per-listener set carries no cause**: an empty `add`/`remove` delta means "nothing changed,"
and an empty `replace` snapshot means "this listener excludes no one." That empty state is
**deliberately ambiguous between "no exclusions" (voice on, nothing hidden) and "voice denied"** —
the mixer **treats them identically**, because it only ever applies the exclusions it is handed, and
in the deny-voice case there is no audible room to mix regardless (the outcome turns on whether the
room/audio exists, not on the visibility feed). *(The current feeder does not exercise the
ambiguity: whole-room deny-voice is emitted as a **full** per-listener exclusion set, not an empty
one — `VisibilityRules` rule (1) excludes every source. The empty-means-deny reading is reserved so
a future feeder may drop those redundant exclusions without a wire change.)* If a consumer ever
needs to distinguish the two — e.g. to drive a "voice disabled" UI — add a **room-level flag in
v1.1** rather than overloading the empty set.

### 3.3.1 Shipped-handler behaviour (v0.7.0)

> **VERSION SCOPE - READ THIS FIRST.** The notes in this section were written against
> `janus.plugin.slvoice` **v0.7.0**. The shipping mixer is **v0.9.0**. One property below
> (the listener-drop bullet) was no longer true at v0.9.0 and has been corrected in place;
> the other two still hold. Treat everything in this section as a version-pinned
> observation of a shipped handler, never as a contract - re-verify against the plugin
> source before relying on it.

Three properties of the shipped `handle_admin_message` → `apply_visbatch` path that the wire format
above leaves implicit. Verified against `janus.plugin.slvoice` v0.7.0 source and a `0.7.0-debug`
container run. These are **descriptive** notes on the shipped handler; they add nothing normative
to the `op`/`room`/`excl` contract and do not change §3.2.

- **Op scoping is per-listener, never room-wide — for all three ops.** The apply loop iterates only
  the listeners *named in this batch's `excl` map* (`janus_slvoice.c:955`, `for i < vb->n_entries`);
  it never iterates the room's participants to clear anyone. `replace` rebuilds and swaps **only the
  named listener's** set (`:1014`–`:1015`, `g_hash_table_destroy(L->excluded); L->excluded = newset`);
  `add`/`remove` mutate only the named listener's set. So **a `replace` naming one listener leaves
  every other listener's set intact** — a listener *omitted* from the batch keeps whatever it had.
  This is why "empty list clears L" (above) matters: to clear a listener you must **name it with an
  empty list**; there is no room-wide reset op. (A full-room snapshot must therefore name every
  listener whose column changed, including those cleared to empty — omission is not a clear.)

- **A batch entry whose listener is not currently in the room is dropped.** If the listener
  key matches no session in the room, the entry is skipped and stored nowhere.
  **CORRECTED AT v0.9.0 - the drop is no longer silent.** The zero-match case increments two
  room counters (`vis_dropped_listener_entries`, cumulative since room creation, and
  `vis_last_batch_dropped_listeners`, this batch only) and emits a `LOG_VERB` line naming the
  listener, the op and the room (`janus_slvoice.c:1268`-`:1275`). `LOG_VERB` rather than
  `WARN` is deliberate: normal churn fires it a few times per departure, so the counters, not
  the log, carry the operational signal. Both counters are readable without log access via
  `query_session`, under the `visibility` object as `dropped_listener_entries` and
  `last_batch_dropped_listeners` (`:1068`-`:1071`).
  **What has NOT changed:** the admin *response* is still byte-identical to a fully applied
  batch (`:1331`-`:1337`) - `entries` counts *parsed* entries (including dropped ones) and
  `skipped` counts **only parse-time malformed items**, not application-time drops. A sender
  still cannot observe a drop from the response alone; it must poll `query_session` or read
  the log at VERB. **This is precisely why §3.2 makes the *feeder* responsible for sending a
  listener's full column (a `replace`) on that listener's join/reassignment** - the mixer
  holds nothing for an absent listener, so the feeder must re-send when it appears. The two
  halves are one contract; a reader of §3.2 alone cannot see that the mixer-side drop is the
  reason the feeder-side snapshot is mandatory, not optional.

- **The "at most one batch per tick" bound is per-op, not per-wire-message.** A feeder tick that
  carries both additions and removals emits **at most one `add` message and one `remove` message**
  (the handler parses a single `op` per message; a `replace` snapshot is one message). The bound
  exists to prevent per-listener fan-out (§3.2.1) — one message *per op kind*, never one per listener
  — and that property is preserved.

> **Operational note (verified against `0.7.0-debug`).** An unrecognised `room` number is dropped
> with a `WARN` in the mixer log (`peer_ctl_batch for unknown room <n> … dropped`) but still returns
> `{"slvoice":"applied", …}` to the sender. **A wrong room is therefore undetectable from the
> response** — the sender sees `applied` whether or not the room existed. Confirm room identity out
> of band (e.g. the same `CalcRoomNumber` the mixer uses); do not treat `applied` as proof of effect.

---

## 4. Mixer side — folding visibility into the existing per-listener map

No new per-listener structure. Extend the Phase-2 record
(`{uuid, has_mute, muted, has_gain, gain}`) with `{has_vis, visible}`:

- **Mix loop** — for listener L, source S: S contributes iff `visible != false` **and**
  `muted != true`. Visibility is an AND-gate on top of mute; a viewer's own mute still works, and a
  sim exclusion the viewer cannot see or override still works. Exclusion is enforced **in the mix**
  (like mute, `spec §3.4`) so a modified viewer cannot defeat it — which is precisely what #13
  requires.
- **Roster / data-channel push** — the per-peer batch builder skips any S with `visible == false`
  for that L: no `p`/`v` entry, no `j` join notice, and an `l` (leave) is synthesized for a source
  that transitions visible→excluded so L's dot for S disappears (single-source-of-truth §1). This
  is the seam that keeps dots and audio in lockstep.
- **Merge discipline** — identical to `m`/`ug`: incremental, field-scoped. A `slvoice_vis` update
  touches only `has_vis`/`visible`; `replace` for a listener rewrites only that listener's vis
  dimension.

This closes #11 (per-listener audibility), #13 (estate-channel ban), and #5 (`SeeAVs` applied
to voice) from one feed - **but only for agents that are in the room the feed is addressed
to.** See the correction immediately below.

> **CORRECTION - the "regardless of room sharing" claim was wrong.** Verified against mixer
> v0.9.0 and `feature/voice-visibility-matrix`.
>
> `JanusPeerCtlBatchSink` computes its room **once, in its constructor**, hard-coded to the
> region-wide `REGION_ROOM_ID` (-999), and stamps that number on every batch
> (`JanusPeerCtlBatchSink.cs:38`-`:39`, `:49`). The feeder and `VisibilityBatchSender` are
> deliberately room-agnostic, so this is the **only** room the matrix is ever delivered to.
> An agent provisioned onto a per-parcel room (`UseEstateVoiceChan` clear) therefore receives
> no exclusion batch at all: its column is computed and sent to a room it is not in, where
> the mixer drops it as an absent listener (§3.3.1). **#11, #13 and #5 are closed by this
> feed on the estate channel only.**
>
> #13 also had a second, independent half on the sim side that this feed never touched: the
> provisioning ban/restrict check was chained as the `else` of the `UseEstateVoiceChan`
> room-selection branch, so setting the estate-channel flag skipped it entirely. That half
> was fixed by de-chaining the check in commit `ec3ad9b2f2`
> (`WebRtcVoiceRegionModule.cs`), not by this feed. The `TaxFree` short-circuit in
> `LandObject.IsBannedFromLand` remains a third, still-open problem (see
> `parcel-voice-semantics.md` addendum §E).

---

## 5. The `OnEstateSettingsChanged` event (#12) — standalone, upstreamable

**Correction carried from the re-baseline (see `parcel-voice-semantics.md` addendum D):** an estate
event already exists — `IEstateModule.OnEstateInfoChange` (`ChangeDelegate(UUID regionID)`,
`IEstateModule.cs:41`), fired on estate access deltas including bans/managers
(`EstateManagementModule.cs:871`) and owner/name/link/settings changes
(`:370/:414/:443/:2221/:2250`). The feeder **subscribes to it today** via
`scene.RequestModuleInterface<IEstateModule>()` for correctness now.

**The "Voice Enabled" toggle is covered — confirmed both viewer paths.** The estate `AllowVoice`
flag reaches an `OnEstateInfoChange` fire site on both transports:

- Legacy UDP `EstateOwnerMessage` → `HandleEstateChangeInfo` (`EstateManagementModule.cs:2162`):
  `AllowVoice` set at `:2205`/`:2207` (parms1 bit `0x10000000`) → `StoreEstateSettings` `:2214` →
  `TriggerEstateInfoChange()` `:2221`.
- CAP path → `handleEstateChangeInfoCap` (`:2226`): `AllowVoice = alloVoiceChat` `:2239` →
  `StoreEstateSettings` `:2246` → `TriggerEstateInfoChange()` `:2250`.

Both fire *after* the store, so a subscriber reads committed state. The feeder's subscription to
`OnEstateInfoChange` therefore catches voice-enable/disable (row 6), estate bans/managers
(`:871`), and owner/name/link changes — every estate input the matrix needs.

**What's missing is granularity, not coverage.** `OnEstateInfoChange` carries only the region UUID
(delegate `ChangeDelegate(UUID regionID)`), so on every estate change the feeder must re-read
`RegionInfo.EstateSettings` and diff to learn *what* changed — cheap (rare events) but coarse. The
upstreamable addition is therefore a **payload-carrying** estate event, not a brand-new
subscription point.

### Design (shareable with Iain)

- **Name / home.** Add `OnEstateSettingsChanged` alongside the existing `OnEstateInfoChange`. Prefer
  extending the estate seam (`IEstateModule` / `EventManager`) rather than a voice-private hook, so
  it is upstreamable and Iain's planned version and this one converge on one event.
- **Fire sites.** The same points that already call `TriggerEstateInfoChange` /
  `OnEstateInfoChange.Invoke` — critically `ExecDeltaRequests` (`EstateManagementModule.cs:871`,
  ban/manager deltas) and the estate-settings save paths (`:2221`, `:2250`, `:370`, `:414`, `:443`).
  Fire *after* `StoreEstateSettings`, so subscribers read committed state.
- **Payload (the whole point).** Carry enough to skip a full re-read/diff:
  `(UUID regionID, EstateChangeKind kind, EstateSettings newSettings)` where `kind` is a flag set
  over `{ Bans, Managers, AllowVoice, Access/Groups, Other }`. At minimum `regionID + kind`; ideally
  the settings snapshot so the feeder needn't reload. This is a strict superset of
  `OnEstateInfoChange`, so the coarse event can be left in place (or implemented by raising the new
  one with `kind = Other`).
- **Backward-compatible.** Additive event; no existing signature changes. Safe to upstream
  independently of the mixer.

Until the payload-carrying event lands, the feeder uses `OnEstateInfoChange` + re-read as the
fallback; the two are wire-compatible from the mixer's perspective (both just trigger a matrix
recompute), so adopting the richer event later needs no mixer change.

---

## 6. Forward-compatibility with Phase 3b (spatial DSP) — no v2 needed

Phase 3b adds distance attenuation / panning / HRTF. What it needs from feeds, and why this protocol
already covers it:

- **Geometry (`sp/sh/lp/lh`)** — already **viewer-supplied** over the data channel and stored per
  participant (`sldata-extensions.md`: parser stores them as int = value×100; flat until 3b). No sim
  feed required for root-agent-controlled geometry.
- **Child-agent positions** — the one gap. A child avatar's `AbsolutePosition` is already resolved
  every feeder tick to derive its parcel (§2.1); the same tick can carry position for spatial math
  when the viewer isn't supplying `sp` for that participant. This rides the **same feeder tick and
  the same transport** — an additional per-source field, not a new channel. Because §3.2's message
  is a per-listener/per-source map already flowing at movement cadence, adding an optional
  `pos:[x,y,z]` per source is an additive field, not a protocol v2.
- **Visibility precedes spatialization** — an excluded source is dropped from the mix *before* any
  DSP, so 3b never spatializes audio a listener shouldn't hear. Ordering: matrix exclusion → mute →
  gain → spatial. The single-source-of-truth rule (§1) still governs.

**Conclusion:** the visibility feed (§3) and the feeder tick (§2.1) are the same plumbing 3b needs
for child positions; keep the `slvoice_vis` message extensible (optional per-source fields) and no
v2 is forced when spatial DSP arrives.

---

## Open items to confirm in code review (not blockers to the design)

1. **Exact transport carrier** for `slvoice_vis` (plugin request vs Janus admin API) against the
   live `WebRtcVoiceServiceConnector` path (baseline §3.3 flags two provisioning overloads).
2. ~~**`AllowVoice` fire path**~~ **RESOLVED** — the "Voice Enabled" toggle reaches
   `OnEstateInfoChange` on both viewer paths (UDP `:2221`, CAP `:2250`); see §5.
3. **Feeder tick default** (250–500 ms) — tune against child-agent movement responsiveness vs
   `GetLandObject` cost.
4. **God/exemption parity** — assert the feeder's exemption set stays bit-identical to
   `LandObject`'s preamble and `IsViewerUIGod`, ideally by reusing the sim predicates directly
   rather than re-implementing them.
