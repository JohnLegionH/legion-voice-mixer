# Mixer Feed Protocol — the C#→mixer visibility interface

**Status:** Version 1.2.0. This is the canonical description of the region-server→mixer interface
for the **per-listener visibility matrix** and its companions, as the 1.2.0 mixer implements it; the
region server's own copy of the document describes that side's view and points here for the protocol.
Sections 1–6 are the original design, with notes on the shipped handler in §3.3.1 and §3.4. Sections
3.5–3.8, 4.1 and 7 are the 1.2.0 wire protocol: the admin transport, room `create`
(`vis_authority`, `sim_created`), the join capability, the reply, authority epochs and policy
generations, `peer_ctl_heartbeat`, and `peer_pos` (`vis_protocol` 3). They were checked against the mixer
source; statements about what the region server does are marked *region-server behaviour* because that
code is not in this repository. It is the counterpart to the viewer→mixer data-channel format
documented in [`../sldata-extensions.md`].

> **Authority.** Where this doc and training disagree, the mixer repo's `docs/` win. Sim-side
> `path:line` citations refer to the sim source as it stood when each passage was written, so line
> numbers may have drifted.

---

## 0. Why a new feed at all (the problem in one paragraph)

Every voice room today is a **flat, symmetric membership set**: `JanusRoom.JoinRoom` issues an
identical `AudioBridgeJoinRoomReq(room, display)` for every session — no per-participant mute,
hide, or allow list. All parcels that resolve to the estate
channel collapse onto one room (`REGION_ROOM_ID = -999`). The sim has rich per-avatar exclusion
semantics (parcel ban/restrict, `SeeAVs` hiding, estate ban) but **no way to express them into a
shared room** — which is exactly why the estate-channel ban bypass exists: the only ban
check is a whole-session join gate for the *requesting* parcel, skipped entirely on the estate
branch (`WebRtcVoiceRegionModule.cs:301`, the `else` of `:297`). Per-listener audibility, the
estate-channel ban and `SeeAVs` applied to voice all require the
same missing thing: **a per-listener exclusion feed from the sim into the mixer.** This document
specifies that feed.

The mixer already *applies* per-listener exclusions — the existing `peer_ctl` map
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

These **must not** be computed independently. The sim's existing divergence — coarse map dots
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
sim's own parcel tracking is not uniform across root and child agents.

### 2.1 Inputs (how the feeder learns each avatar's parcel)

| Agent kind | Parcel source | Why |
|---|---|---|
| **Root** | event-driven: `OnAvatarEnteringNewParcel` (root-only, fired `LandManagementModule.cs:550`) refreshes that avatar; `ScenePresence.currentParcelUUID` is the cached value | Root agents get the crossing event and movement-driven `currentParcelUUID` updates. The event fires on **any** `GlobalID` change, including crossings between two parcels on the same channel — so same-channel moves are seen. |
| **Child** | position-derived: `LandChannel.GetLandObject(x, y)` on `ScenePresence.AbsolutePosition`, evaluated on the **feeder tick** | Child agents receive **no** crossing event (guard `LandManagementModule.cs:538`) and their `currentParcelUUID` is unreliable / often `UUID.Zero`. They must be polled. This is expected, not a defect. |

Enumerate with `Scene.ForEachScenePresence` (children **included**) so cross-region
audibility works. Root agents can be refreshed reactively on the event and only re-derived on tick
as a cheap consistency check; child agents are re-derived every tick from position.

**Feeder tick.** A fixed cadence (proposed **default 250–500 ms**, configurable) drives child-agent
re-derivation and matrix diffing. Rationale under §3 (movement is frequent; this is the dominant
update driver, not parcel edits). The tick is decoupled from the mixer's 20 ms mix tick and 100 ms
roster push — the feeder emits only on **change**, not every tick.

### 2.2 The predicates that fill the matrix (all sim-authoritative, never viewer-supplied)

For each ordered pair (listener L, source S) with parcels P_L, P_S, `V[L][S] = excluded` iff any:

- **Parcel hide (`SeeAVs`)** — symmetric. If `P_S.SeeAVs == false` and `P_L ≠ P_S` and L is not a
  god, S is hidden from L (`LandData.SeeAVs` → `ParcelHideThisAvatar`, predicate
  `currentParcelUUID != peer.currentParcelUUID && !peer.IsViewerUIGod`). Apply symmetrically (also
  hide L from S if `P_L.SeeAVs == false`) so the pair never has one-way audibility — see §3 on
  symmetric hiding.
- **Parcel ban/restrict** — S banned or restricted from **L's** parcel P_L (and vice-versa):
  `IsBannedFromLand` / `IsRestrictedFromLand` / `IsEitherBannedOrRestricted`
  (`LandObject.cs`). This is the data the estate-channel ban needs but the current voice path skips. Key change from today: the check is evaluated **per (L,S) pair against each
  one's parcel**, not once for the requesting parcel at provision time.
- **Estate ban** — `EstateSettings.IsBanned`. No current voice consumer; the
  feeder is the first.
- **Voice disabled** — if voice is off for P_S (`!EstateSettings.AllowVoice` OR, absent `TaxFree`,
  `!(P_S.Flags & AllowVoiceChat)`) S is a source to no one.

**Exemptions** (applied consistently): estate `TaxFree`, admin
(`IsAdministrator`), estate manager/owner (`IsEstateManagerOrOwner`), parcel owner
(`LandData.OwnerID`) short-circuit the ban/restrict tests. `IsViewerUIGod` exempts the `SeeAVs`
hide. Keep the exemption set identical to the sim's so voice never diverges from the visual model.

> **Do not key voice on `CanBeOnThisLand`.** It enforces ban only below `BanLineSafeHeight` and
> uses `else if` for restriction — a banned avatar above the ban line
> would leak. Use `IsBannedFromLand`/`IsRestrictedFromLand` directly.

### 2.3 Invalidation (when the feeder recomputes)

| Change | Signal | Notes |
|---|---|---|
| Parcel flags / `SeeAVs` / access list / ban list | `OnLandObjectAdded` (covers "updated" too — forwards via `TriggerLandObjectUpdated`) | Rare. Recompute affected parcels' rows/cols. |
| Root avatar crosses a parcel boundary | `OnAvatarEnteringNewParcel` | Root only. |
| Child avatar moves | feeder tick position re-derivation | The polling path; dominant driver. |
| Estate ban/manager/`AllowVoice` change | `IEstateModule.OnEstateInfoChange` (see §5) | Coarse today — re-read `EstateSettings` and diff. |

---

## 3. Wire format — how per-listener exclusions reach the plugin

### 3.1 Transport is server-to-server, NOT the viewer data channel

The viewer's `m`/`ug` ride the peer's WebRTC **data channel** (viewer→mixer). The visibility matrix
must **not** — its source is the sim, and the whole point of the estate-channel ban fix is that **the viewer's own
parcel/ban status cannot be trusted** (`WebRtcVoiceRegionModule.cs:245` already comments
"*do fully not trust viewers voice parcel requests*"). The feed therefore travels over the
**existing sim→Janus signaling path** the region module already uses to provision
(`WebRtcVoiceServiceConnector` JSON-RPC → Robust service → Janus plugin), as a new
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

- Parcel/estate **edits are rare**; but **avatar movement across parcels is
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
`ParcelHideThisAvatar` derives from the *sender's* parcel. Voice deliberately
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

- **A batch entry whose listener is not currently in the room is dropped.**
  **AMENDED 2026-08-27 (mixer `27977c8`): the entry is no longer dropped — it is now DEFERRED
  and replayed on that listener's join. See the new §3.4. The `deferred_listeners` reply field
  and the `deferred_*` counters added by that change also mean the "byte-identical response"
  property noted below no longer holds. The v0.7.0/v0.9.0 text is retained for history.**
  If the listener
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

> **Operational note (verified against `0.7.0-debug`; superseded in 1.2.0).** In 0.7.0 an unrecognised
> `room` number was dropped with a `WARN` in the mixer log (`peer_ctl_batch for unknown room <n> …
> dropped`) but still returned `{"slvoice":"applied", …}`, so a wrong room was undetectable from the
> response. **In 1.2.0 the reply is `{"slvoice":"error","reason":"unknown_room"}`** (§4.1), and, for a
> stamped batch, `"status":"unknown_room"`. The warning is still logged. Room identity can still be
> confirmed out of band (the same `CalcRoomNumber` the mixer uses).

---

### 3.4 Moderation MUTE channel + join-window deferral (added 2026-08-27, shipped)

Two additive changes shipped this cycle. Both are **backward-compatible in both skew directions**
(old sim ↔ new mixer, new sim ↔ old mixer); no existing field changes meaning.

**(a) The additive `mute` channel (Option A, LL parity).** A batch may now carry an optional
top-level `mute` object with the **same per-listener → source-list shape as `excl`**, parsed by the
same resilient helper:

```json
{
  "op": "replace",
  "room": -999,
  "excl": { "<L-uuid>": ["<bannedS-uuid>"] },
  "mute": { "<L-uuid>": ["<moderatedS-uuid>"] }
}
```

- A source in `mute` is **moderation-muted, not excluded**: it is silenced in L's mix (the mix
  gates on `mod_muted` OR the viewer's own `muted`) but **stays in the roster**, greyed — no `l`
  leave is synthesised. `excl` still removes the row. The two are **disjoint per source**: the sim's
  `VisibilityMatrix.Build` guarantees ban wins, so no source is ever in both channels for one
  listener.
- **A new sim always emits the `mute` key on a `replace`, even as an empty `{}`**, so the mixer can
  tell a new sim (key present) from an old one (key absent) — an absent `mute` means "old sim / no
  mutes" and is handled exactly as before (skew-safe). On `add`/`remove` the key is emitted only
  when non-empty, so a pure-ban delta is byte-for-byte the pre-mute wire.
- An **old mixer** silently ignores the unknown `mute` key; a **new mixer** treats an absent `mute`
  as "no mutes." (Mixer parse/keep+grey `0d6d0d0`.)

**(b) Join-window deferral + replay (mixer `27977c8`).** An `excl` or `mute` entry whose listener is
not yet a participant is **no longer dropped** (§3.3.1): the mixer keeps it in a per-room deferred
store (keyed by listener, holding that listener's latest `excl` and `mute` columns under
**op-faithful merge** — `replace` sets, `add` unions, `remove` subtracts, an emptied column clears)
and **replays it into the session the instant that listener joins, under the room lock, before the
join roster is built and before any presence is emitted** — so a deferred exclusion/mute is in force
before anything derived from it is revealed. Op-fidelity (not wholesale last-write) is deliberate: a
post-window un-ban (`remove`) must never be resurrected into an exclusion on join. The store is
capped per room (`SLV_VIS_MAX_DEFERRED`, oldest evicted and counted), freed on room teardown, with
**no TTL** (a proven join arrived at +8 minutes; a timer would recreate the very loss this fixes).

**(c) Reply now reports deferrals (additive).** The `peer_ctl_batch` response gains an additive
`deferred_listeners` integer — the count of listener entries deferred this batch (excl + mute) —
so a sim can finally observe, from the response alone, that its entry was retained rather than
silently dropped. `query_session`'s `visibility` object gains `deferred_current`, `deferred_adds`,
`deferred_replaced`, `deferred_replayed`, `deferred_evicted`, and each session gains a
`mod_muted_entries` count (the moderation flag was previously invisible to the admin API). An old
sim ignores the extra reply key; an old mixer never emits it (a new sim must treat its absence as
"no information").

**(d) Presence delivery guarantee + counters (added 2026-08-30).** The joiner's `"j"` to
already-present members (`push_presence`) and the roster backlog a newly-writable listener receives
are now provably complementary: `data_ready` flips the recipient's `dc_open` and takes the
join-backlog snapshot in ONE critical section under `room->mutex` — the same mutex every
`push_presence` recipient-side `dc_open` read runs under — so for every (joiner, recipient) pair at
least one of {live push, backlog} delivers the `"j"`, by mutual exclusion alone (the three orderings
are enumerated at the fence in `janus_slvoice_data_ready`). A duplicate (fence between insert and
push) is benign: a second `"j"` is idempotent at the viewer. This matters most for rooms that
receive **no** `peer_ctl_batch` (the avatar-to-avatar rooms), where the
visibility-transition repair path never runs and a dropped one-shot `"j"` used to be unrecoverable.
`query_session` gains two additive per-session counters (this session as RECIPIENT):
`presence_pushed` and `presence_dropped_dc_closed`; after the fence the dropped counter should only
ever count a genuinely dead channel (PeerConnection down), never the join race. Old sims are
unaffected; the fields are additive.

**(e) Leave announcement on EVERY removal path + leave counter (added 2026-08-31).** The
leave presence (`"l"`) used to be pushed only by the `"leave"` request arm; a participant whose
teardown arrived as PC-death + session-destroy was removed **silently**, so the remaining members
never received `"l"` and a stock viewer's `hangup_on_last_leave` never fired — the remaining party
of a two-party (A2A) call sat in the room until manual End Call. The push now lives in
`janus_slvoice_leave_room` itself, before the participant is removed, so the `"leave"` request,
`destroy_session`, and any future removal caller all announce; the room-transition guarantee makes
it exactly-once per removal. A downed PeerConnection (`hangup_media`) deliberately does NOT
announce: it never removes the participant, and Janus can renegotiate media on the same handle — an
`"l"` during an ICE restart would erase the row with no `"j"` to restore it. `query_session` gains
`presence_leave_pushed` (additive): the leave subset of `presence_pushed`, so a live read can
decompose pushed=N into joins vs leaves. `presence_pushed` itself now counts at the actual
relay_data hand-off (a serialisation failure no longer inflates it).

**(f) The viewer attach window + proof-of-attachment backlog re-send (added 2026-08-31).**
The stock viewer discards data-channel frames that arrive before its data observer is attached
(`llwebrtc.cpp:1763-1771` — an observer list with no buffering, attached via a main-thread hop after
`OnDataChannelReady`). The join backlog fires at SERVER-side `data_ready`, inside that window, so a
delivered-and-counted `"j"` can be silently lost viewer-side — the peer never enters the viewer's
participant map and a later `"l"` no-ops, leaving `hangup_on_last_leave` unfired (the
observed failure: `presence_leave_pushed=1` with no hangup). The viewer's own `"j"` SLData (`sendJoin`) is
sent only after its data interface is set — proof the window has closed — so on receiving it the
mixer re-sends that participant the roster backlog, once per attachment (CAS-guarded
`backlog_confirmed`, cleared with `dc_open` on `hangup_media` and on leave, idempotent at the
viewer). The participant's own presence is deliberately NOT re-pushed to existing members — their
windows are closed by their own proofs. `query_session` gains `backlog_resent` (additive; normally 1
per attachment). Old viewers/sims unaffected.

### 3.5 Admin API transport, result and error latch (1.2.0)

Feed messages (`peer_ctl_batch`, `peer_ctl_heartbeat`, `peer_pos`) reach the plugin through the Janus
Admin API as an HTTP POST with a JSON body:

```json
{
  "janus": "message_plugin",
  "transaction": "<random string>",
  "admin_secret": "<admin secret>",
  "plugin": "janus.plugin.slvoice",
  "request": { "request": "peer_ctl_batch", "...": "..." }
}
```

- Janus reads the secret from the body field `admin_secret` (the `apisecret` field of the normal API is
  not used) and hands `request` to the plugin's `handle_admin_message`
  (`vendor/janus-gateway/src/janus.c`, `message_plugin`).
- The plugin's answer is returned inline, nested under the top-level key `response`:
  `{"janus":"success","transaction":"…","response":{…}}`. There is no ack or long-poll step.
- The plugin dispatches on `request.request`: `peer_ctl_heartbeat`, `peer_pos` or `peer_ctl_batch`. Any
  other value gets `{"slvoice":"error","reason":"unknown_request"}`.

**What a sender can conclude from the result.**

| Result | Meaning |
|---|---|
| HTTP 2xx, `"janus":"success"` | Janus delivered the request to the plugin. It does **not** mean the batch was applied; read `response.slvoice` (§4.1). |
| HTTP 2xx, `"janus":"error"` (wrong `admin_secret`, plugin not found, a plugin without admin support), or a 2xx body that cannot be parsed | Protocol error. |
| Non-2xx, connection failure, timeout | Transport error. |

*Region-server behaviour (not verifiable from this repository).* The region server applies a per-call
timeout (`[JanusWebRtcVoice] AdminTimeoutMs`, default 5000). Three consecutive protocol errors on a
region latch feed emission off for that region until the region server restarts, with one prominent
log line; any success resets the count, so a single stray error does not disable emission. A plugin
answer whose inner `slvoice` is `error` (for example `unknown_room`) is a successful *transport* result
and does not count toward the latch: it is logged and counted only.

### 3.6 Room create: `vis_authority` and `sim_created` (1.2.0)

The region server creates the room before every viewer join; an existing room answers error 486
("already exists"), which the sender treats as success. Besides the usual room settings, the plugin
reads these fields from `create` (`janus_slvoice.c`, the `create` arm of the request handler):

```json
{ "request": "create", "room": 12345, "spatial_audio": true,
  "vis_authority": true, "sim_created": true }
```

- `vis_authority`: only a JSON `true` counts; absent means false. It declares that a region server will
  arm this room's listeners (§7), and is fixed for the room's lifetime. Fail-closed enforcement
  (`JS_VIS_FAIL_CLOSED`, §7.1) applies only to rooms created with it; in any other room the authority
  state is not enforced. A static room from the plugin's config file is created without it.
- `sim_created`: only a JSON `true` counts; absent means false. It says a simulator owns the room, so
  the join-capability requirement (§3.7) applies to it even when it is not `vis_authority`. That covers
  avatar-to-avatar rooms and conferences, which receive no visibility batches. A mixer under an older
  region server (no field) keeps the older rule: a capability is required only in a `vis_authority` room.
- `spatial_audio` is a boolean; an absent key takes the plugin's default. A room created with it false
  is a flat mix (no distance cull, falloff or pan).

### 3.7 Join capability (1.2.0)

When the region server and the mixer share a secret, each join carries a capability signed by the
region server:

```
join_cap = "v1." + b64url(payload) + "." + b64url(HMAC-SHA256(secret, "v1." + b64url(payload)))
payload  = "<agent>|<session>|<room>|<epoch>|<generation>|<iat>|<exp>|<nonce>"
```

It is carried in the `join` request as `join_cap`, with `session_id` (`janus_slvoice.c`, the `join` arm;
`src/joincap.h`):

```json
{ "request": "join", "room": 12345, "display": "<agent id>",
  "jsep": { "type": "offer", "sdp": "..." },
  "join_cap": "v1....", "session_id": "<viewer session id>" }
```

- `b64url` is base64 with `+` and `/` replaced by `-` and `_` and no padding. A capability longer than
  1024 bytes is malformed without being parsed. No field may contain `|`.
- `agent` must equal the join's `display`, `session` the join's `session_id` and `room` the join's room.
- `iat` and `exp` are Unix seconds; the region server mints a 60-second lifetime. The mixer accepts a
  capability from `iat - 120` to `exp + 120` seconds (`SLV_JOINCAP_SKEW_S`) and counts one accepted only
  by that tolerance.
- `nonce` is 16 random bytes in lowercase hex, spent on first use. The replay store holds 4096 live
  nonces per mixer process; when it is full the verdict is `cap_replay_store_full`, because replay
  cannot be ruled out.
- `epoch` is the 16-hex-digit authority epoch of the room (§7.1) at minting, or `0000000000000000` when
  the room has none; `generation` is the room's `policy_generation` at minting, or 0. The generation
  never refuses a join: the sender allocates a generation before the mixer applies it, so a capability
  can legitimately be ahead of or behind the room. The mixer counts these races (`cap_epoch_ahead`,
  `cap_no_epoch`, `cap_generation_ahead`, `cap_generation_behind` in `query_session`) and flags only an
  `epoch` strictly **lower** than the room's adopted one (`cap_stale_generation`). A room that has
  adopted no epoch accepts any.
- The secret is the mixer's `JS_JOIN_CAP_SECRET`. It must match the region server's
  `[JanusWebRtcVoice] JoinCapabilitySecret` and be a different value from `JS_API_SECRET`, so that a leak
  of one cannot forge the other. Neither the secret, a capability, its payload nor its nonce is ever logged.

**When the mixer refuses.** The check runs before negotiation, so a refusal leaves nothing to undo. A
join is refused only when `JS_JOIN_CAP_REQUIRED` is on **and** the room is `vis_authority` or
`sim_created` (§3.6). Otherwise the verdict is counted and the join proceeds as if no capability had been
sent. A refusal is error 496 with the verdict as its reason: `cap_missing`, `cap_malformed`,
`cap_bad_signature`, `cap_wrong_agent`, `cap_wrong_session`, `cap_wrong_room`, `cap_expired`,
`cap_replayed`, `cap_replay_store_full` or `cap_stale_generation`. With the requirement on and no secret
set, nothing can be verified, so the requirement is not enforced (the container entrypoint refuses to
start in that state).

A capability admits one join and grants no audibility. It travels only from the region server to the
mixer: the region server's own Janus session performs the join, so a viewer never sees it.

Other join errors the region server acts on: 485 (no such room), 486 on create (exists) and 495 (room
full, at `SLV_MAX_MIX` participants).

### 3.8 Connector join capability endpoint (region-server side)

*Region-server behaviour (not verifiable from this repository).* External connector peers (recorder or
injector processes) join the mixer themselves, so the region server offers them a capability over HTTP
when a connector record carries a `CapabilitySecret` of at least 32 characters:

```
POST /voice/connector/<name>/join-cap
Authorization: Bearer <CapabilitySecret>
```

A success returns `200` with `{"display","room","session_id","join_cap","expires","position"}`, where
`position` is `{"x","y","z"}` in integer centimetres. Every failure before authentication succeeds
returns an empty `404`, so the endpoint does not reveal which connector names exist; when the region
server cannot mint after authentication it answers an empty `503`. No handler is registered unless some
connector record has a `CapabilitySecret`. The connector puts the returned `join_cap` and `session_id` in
its `join`, which the mixer checks as in §3.7.

---

## 4. Mixer side — folding visibility into the existing per-listener map

No new per-listener structure. Extend the existing record
(`{uuid, has_mute, muted, has_gain, gain}`) with `{has_vis, visible}`:

- **Mix loop** — for listener L, source S: S contributes iff `visible != false` **and**
  `muted != true`. Visibility is an AND-gate on top of mute; a viewer's own mute still works, and a
  sim exclusion the viewer cannot see or override still works. Exclusion is enforced **in the mix**
  (like mute, `webrtc-voice-spec.md` §3.4) so a modified viewer cannot defeat it — which is
  precisely what the estate-channel ban requires.
- **Roster / data-channel push** — the per-peer batch builder skips any S with `visible == false`
  for that L: no `p`/`v` entry, no `j` join notice, and an `l` (leave) is synthesized for a source
  that transitions visible→excluded so L's dot for S disappears (single-source-of-truth §1). This
  is the seam that keeps dots and audio in lockstep.
- **Merge discipline** — identical to `m`/`ug`: incremental, field-scoped. A `slvoice_vis` update
  touches only `has_vis`/`visible`; `replace` for a listener rewrites only that listener's vis
  dimension.

This closes per-listener audibility, the estate-channel ban, and `SeeAVs` applied
to voice from one feed - **but only for agents that are in the room the feed is addressed
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
> the mixer drops it as an absent listener (§3.3.1). **Per-listener audibility, the
> estate-channel ban and `SeeAVs` for voice are closed by this feed on the estate channel only.**
>
> The estate-channel ban also had a second, independent half on the sim side that this feed never touched: the
> provisioning ban/restrict check was chained as the `else` of the `UseEstateVoiceChan`
> room-selection branch, so setting the estate-channel flag skipped it entirely. That half
> was fixed by de-chaining the check in
> `WebRtcVoiceRegionModule.cs`, not by this feed. The `TaxFree` short-circuit in
> `LandObject.IsBannedFromLand` remains a third, still-open problem.

### 4.1 The reply to `peer_ctl_batch` (1.2.0)

The plugin's answer is the inner object under `response` (§3.5):

```json
{ "janus": "success",
  "response": { "slvoice": "applied", "op": "add", "room": 12345,
                "entries": 3, "mute_entries": 1, "skipped": 0, "deferred_listeners": 0,
                "vis_protocol": 3, "mixer_instance": "9f2c61d04b7a83e1" } }
```

| Field | Meaning |
|---|---|
| `slvoice` | `applied` on success. `empty` for a valid batch with no applicable entry. `error` with a `reason` otherwise. |
| `entries`, `mute_entries` | Listener entries parsed in the exclusion and the mute channel. |
| `skipped` | Malformed items dropped at parse time (both channels). Greater than zero is real loss. |
| `deferred_listeners` | Entries kept for listeners that have not joined yet (§3.4(b)); not a fault. |
| `vis_protocol` | The protocol this mixer speaks: 3 (§7.3 and §7.6). Present on every `peer_ctl_batch`, `peer_ctl_heartbeat` and `peer_pos` reply, including errors. |
| `mixer_instance` | A random 16-hex-digit value chosen when the plugin starts. A change means the mixer restarted. |

All fields are optional for a sender: a reply with no inner object is "no information". The
`error` reasons for a batch are `unknown_room` (the room does not exist in this process; nothing was
applied), `malformed`, `too_big` (over 65536 bytes), and, only while fail-closed is enforced for the room,
`stale_epoch` and `stale_generation` (§7.2). An
unknown room is therefore visible from the reply; this replaces the earlier behaviour described in the
operational note of §3.3.1.

When the batch carries `room_epoch` (§7.2), the reply also carries the per-room authority keys:

| Field | Meaning |
|---|---|
| `status` | `ok`; `undeclared_room` (the epoch was processed, but the room was not created with `vis_authority`, so nothing is enforced); `stale_epoch`; or `unknown_room` (also carried as `reason`). |
| `authority_epoch` | The room's adopted epoch after this message (16 hex digits, zero for none). |
| `policy_generation` | The highest batch generation the room has applied in this epoch. |
| `stale_listeners` | Listeners whose delta did not follow on from the mixer's record (§7.2). |
| `unarmed_listeners` | Listeners the mixer cannot arm: in a batch, those a `replace` could not arm because the room already holds 1024 listener records. (In a heartbeat reply, §7.3, those it holds no current-epoch record for.) |
| `stale_generation` | `true` when the batch's `policy_generation` was not above the room's (out of order). |

The reply does not change a send's transport result: anomalies are for the sender to log and count.

---

## 5. The `OnEstateSettingsChanged` event — standalone, upstreamable

**Note:** an estate
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
`OnEstateInfoChange` therefore catches voice-enable/disable, estate bans/managers
(`:871`), and owner/name/link changes — every estate input the matrix needs.

**What's missing is granularity, not coverage.** `OnEstateInfoChange` carries only the region UUID
(delegate `ChangeDelegate(UUID regionID)`), so on every estate change the feeder must re-read
`RegionInfo.EstateSettings` and diff to learn *what* changed — cheap (rare events) but coarse. The
upstreamable addition is therefore a **payload-carrying** estate event, not a brand-new
subscription point.

### Design (shareable upstream)

- **Name / home.** Add `OnEstateSettingsChanged` alongside the existing `OnEstateInfoChange`. Prefer
  extending the estate seam (`IEstateModule` / `EventManager`) rather than a voice-private hook, so
  it is upstreamable and any upstream version of this event converges with this one.
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

## 6. Forward-compatibility with spatial DSP — no v2 needed

Spatial DSP adds distance attenuation / panning / HRTF. What it needs from feeds, and why this protocol
already covers it:

- **Geometry (`sp/sh/lp/lh`)** — already **viewer-supplied** over the data channel and stored per
  participant (`sldata-extensions.md`: parser stores them as int = value×100; flat until spatial DSP). No sim
  feed required for root-agent-controlled geometry.
- **Child-agent positions** — the one gap. A child avatar's `AbsolutePosition` is already resolved
  every feeder tick to derive its parcel (§2.1); the same tick can carry position for spatial math
  when the viewer isn't supplying `sp` for that participant. This rides the **same feeder tick and
  the same transport** — an additional per-source field, not a new channel. Because §3.2's message
  is a per-listener/per-source map already flowing at movement cadence, adding an optional
  `pos:[x,y,z]` per source is an additive field, not a protocol v2.
- **Visibility precedes spatialization** — an excluded source is dropped from the mix *before* any
  DSP, so spatial DSP never spatializes audio a listener shouldn't hear. Ordering: matrix exclusion → mute →
  gain → spatial. The single-source-of-truth rule (§1) still governs.

**Conclusion:** the visibility feed (§3) and the feeder tick (§2.1) are the same plumbing spatial DSP needs
for child positions; keep the `slvoice_vis` message extensible (optional per-source fields) and no
v2 is forced when spatial DSP arrives.

*As shipped in 1.2.0:* child-agent and root-agent positions as the region sees them arrive in a separate
`peer_pos` request (§7.6, `vis_protocol` 3), which the mixer currently only observes. The per-source
`pos` field suggested above was not used.

---

## 7. Authority, epochs, heartbeats and positions (`vis_protocol` 2 and 3)

This section is the optional layer on top of §3.3–§3.4. A mixer that has been told a region server is the
**authority** for a room can silence any listener or source it has not been told to carry, and stop
carrying audio in that room if the authority goes quiet (*fail-closed*). A region server that does not
send `room_epoch` leaves every message in §3 behaving exactly as described there, and none of the fields
below appear. On the region-server side the layer is switched on by `[WebRtcVoice] VisibilityArmingEnabled`
(default off; it also needs `VisibilityFeederEnabled` and `VisibilityEmitEnabled`). Checked against
`src/janus_slvoice.c`, `src/visauth.h`, `src/visbatch.h` and `src/possync.h`.

### 7.1 Concepts and mixer settings

- **Epoch** (`room_epoch`): exactly 16 hex digits, not all zero (zero means "none"). The mixer accepts
  either case and reports lowercase. *Region server:* chosen once each time its feed service starts, from
  the Unix time in milliseconds shifted left 16 bits with a random low 16 bits, so a restarted feed
  never reuses an earlier value.
- **Policy generation** (`policy_generation`): a per-room counter on `peer_ctl_batch`, an integer from 1 to
  2³²−1, that rises with every batch sent to that room (the first is 1).
- **Listener generation:** the `policy_generation` of the batch that last set or changed a listener's
  columns in a room. The mixer keeps one *record* per listener (by agent UUID) per room, holding the epoch
  that armed it, its listener generation, its columns, a "stale" flag and the time it was last confirmed.
  Records survive a leave and rejoin. A listener is **armed** when it has a record. At most 1024 records
  are held per room.
- **Arming:** a `replace` that names a listener, in either channel, arms it. The region server sends both
  columns for every listener, empty ones included. Until a listener is armed, a mixer enforcing authority
  carries nothing for it. A heartbeat never arms.
- **Adoption** (the mixer's `authority epoch` for a room), applied to every message that carries a
  `room_epoch`:

  | The message's epoch against the stored one | Result |
  |---|---|
  | Nothing stored, or greater | **Adopt**: store it, disarm every record, restart the policy generation at 0. |
  | Equal | Normal processing. |
  | Less, and the stored authority is stale (no accepted message within the staleness window, or it said `stopping`) | **Takeover**: the same as adopt. |
  | Less, while the stored authority is fresh | **`stale_epoch`**: the message is rejected and changes nothing. |

- **Mixer settings** (environment, read when the plugin starts; invalid values are ignored with a warning):
  - `JS_VIS_FAIL_CLOSED` (default `0`): `1`, `true`, `yes` or `on` enforce authority in rooms created with
    `vis_authority` (§3.6). With it off (*shadow mode*) the state is kept and counted
    (`would_silence_*` in `query_session`) and no audio or roster changes.
  - `JS_VIS_STALE_MS` (default 8000, at most 600000): the staleness window. It cannot be below
    2 × heartbeat interval + 5000 ms admin timeout + 250 ms tick slip, which is 7250 ms for the region
    server's 1000 ms heartbeat; a lower value is raised to that with a warning.
  - `JS_JOIN_CAP_REQUIRED` and `JS_JOIN_CAP_SECRET`: §3.7.

### 7.2 Fields added to `peer_ctl_batch`

When the region server runs with arming, every batch carries, after `room`:

```json
{ "room_epoch": "0184a3c2d9f1b7e4", "policy_generation": 7,
  "base": { "<listener uuid>": 6 } }
```

A batch with `room_epoch` that is not 16 hex digits, or a `policy_generation` outside 1..2³²−1, is
rejected as `malformed`. `base` appears only on `add` and `remove`; for each named listener it gives the
listener generation the region server believes the mixer holds. The mixer processes a stamped batch in
this order:

1. **Epoch** (§7.1). A `stale_epoch` batch changes nothing.
2. **Generation.** A `policy_generation` not above the room's is out of order: `stale_generation`, the
   authority records are unchanged, and the reply carries `"stale_generation": true`.
3. **Per listener named in either channel.** A `replace` arms the listener at this generation, clears its
   stale flag and stores its columns. An `add` or `remove` is applied to the record only when the
   listener's record is from the current epoch and `base` equals its listener generation; otherwise the
   record (if any) is marked **stale**, the listener is returned in `stale_listeners`, and the sender is
   expected to re-arm it with a `replace`.

Whether a refused or stale part is also *withheld from the mix* depends on enforcement. With fail-closed
on, in a `vis_authority` room, a `stale_epoch` or `stale_generation` batch is refused whole
(`{"slvoice":"error","reason":"stale_epoch"}` or `"stale_generation"`) and a stale listener's entries are
not applied. Otherwise the batch is applied as it was before arming existed, and the reply still reports
`status`, `stale_listeners` and `unarmed_listeners` so the sender sees the same facts.

### 7.3 `peer_ctl_heartbeat`

Sent through the same admin channel as the batches:

```json
{ "request": "peer_ctl_heartbeat", "room_epoch": "0184a3c2d9f1b7e4", "interval_ms": 1000,
  "feed_age_ms": 120,
  "rooms": { "12345": { "policy_generation": 7, "as_of": 7,
                        "listeners": { "<listener uuid>": 7, "<listener uuid>": 0 } } } }
```

- `rooms` lists every room the region currently addresses, with every listener placed there and its
  listener generation (0 = not armed), empty columns included. A key that is not a positive room number is
  ignored.
- `interval_ms` must be a positive integer the staleness window can absorb (§7.1); otherwise the whole
  heartbeat is answered `{"slvoice":"error","reason":"interval_too_long","stale_ms":<window>}`. A malformed
  `room_epoch`, `interval_ms` or `rooms` is answered `{"slvoice":"error","reason":"malformed"}`.
- Each accepted room entry is a sign of life for that room's authority, after the adoption rule of §7.1.
  The mixer then compares the named listeners with its records: a listener whose generation matches is
  *confirmed*; one that names another generation is marked stale and returned in `stale_listeners`; one
  with no current-epoch record is returned in `unarmed_listeners`; and **every armed listener the entry
  omits is disarmed**.
- `as_of` is the highest `policy_generation` the sender had successfully sent to that room when it built
  the heartbeat. A heartbeat built before a batch landed can arrive after it; if `as_of` is below the
  generation the room has applied in this epoch, the entry counts as a sign of life but its `listeners`
  map is not evaluated (no confirm, stale or disarm). An entry with no `as_of` is always evaluated.
- `feed_age_ms` is how old the region's visibility computation was when the body was built. The region
  server only reports it; the **mixer decides**: an age above the staleness window is answered
  `status: "feed_stale"` for that room and does *not* refresh the authority, so the room ages out as if
  the sender had gone quiet. A missing, non-integer or negative age counts as live (an older region
  server). A `stopping` heartbeat is honoured whatever the age.
- `"state": "stopping"` on the final heartbeat of a graceful stop marks every record in the room stale at
  once and lets a lower epoch take the room over without waiting for the window.

The reply:

```json
{ "janus": "success",
  "response": { "slvoice": "heartbeat", "vis_protocol": 3, "mixer_instance": "9f2c61d04b7a83e1",
                "rooms": { "12345": { "status": "ok", "authority_epoch": "0184a3c2d9f1b7e4",
                                      "policy_generation": 7,
                                      "unarmed_listeners": [], "stale_listeners": [] } } } }
```

Per-room `status` is `ok`, `undeclared_room` (the room was not created with `vis_authority`),
`stale_epoch`, `feed_stale` or `unknown_room` (the room does not exist in this process; its other keys are
empty). A reply whose `slvoice` is not `heartbeat` is an error reply.

*Region-server behaviour (not verifiable from this repository).* The region server sends heartbeats every
1000 ms from a timer of its own, independent of the feeder tick, and only after a mixer reply has
advertised `vis_protocol` 2 or higher. A reply that is not a `heartbeat` response stops them until a later
reply advertises `vis_protocol` 2 again.

### 7.4 What fail-closed does to the mix

With fail-closed on, in a room created with `vis_authority`, each display stands in one of four rows, and
a pair is audible only if **both** the listener and the source are in row 4 (`src/visauth.h`):

| Row | Standing | Effect |
|---|---|---|
| 1 | No record (unarmed) | Silence |
| 2 | A record from another epoch | Silence |
| 3 | Current epoch, marked stale or not confirmed within the staleness window | Silence |
| 4 | Current epoch, confirmed within the window | Passes, subject to the exclusion and mute sets (§4) |

A record is confirmed by arming, by an accepted delta, and by a heartbeat whose generation matches it.
Silence is applied in the mix and in the roster, as exclusion is (§4).

### 7.5 How the region server reacts to replies

*Region-server behaviour (not verifiable from this repository).* The region server treats the statuses
above as follows:

| Reply | Reaction |
|---|---|
| Transport failure | Nothing recorded; the next pass sends a snapshot, which re-arms everyone. |
| `mixer_instance` changes | The mixer restarted. Every listener in every room is disarmed and re-armed. |
| `status` or `reason` `unknown_room` | That room's listeners are disarmed and retried after a delay that starts at 1 s and doubles to 300 s while the room stays absent. A provision, a room create or an applied reply for the room ends the back-off at once. Logged once per episode. |
| `stale_epoch` | The same back-off: a newer authority holds the room, so the region server retries rather than escalating. |
| `stale_listeners`, `unarmed_listeners` | Exactly those listeners are disarmed and re-armed. |
| `applied` | A `replace` arms each named listener at this generation; an `add` or `remove` advances an already armed one. |
| Any other status, or a malformed reply | The named listeners are not armed and are retried after the back-off. |

When both the join capability and arming are on, the capability's `epoch` and `generation` come from the
room's last published authority state (§3.7).

### 7.6 Participant positions (`peer_pos`, `vis_protocol` 3)

A viewer reports its own position to the mixer, and nothing in the feed above says where the region
actually has that avatar. A region server that sends `peer_pos` tells the mixer each participant's position
as the region sees it, so the mixer can compare the two. In 1.2.0 the mixer **only observes**: it stores
the points, compares them with the `sp` a viewer reports, and counts what a correction would have done
(`visibility.positions` in `query_session`). It changes no audio and no roster. A room that has accepted no
`peer_pos` is not observed at all.

```json
{ "request": "peer_pos", "room": 12345, "seq": 1760000000001, "full": true, "mode": "observe",
  "room_epoch": "0184a3c2d9f1b7e4",
  "pos": { "<agent uuid>": [x_cm, y_cm, z_cm, speed_cm_s, tolerance_cm] } }
```

- `x_cm`, `y_cm`, `z_cm` are global centimetres, the frame a viewer reports `sp` in (the position times
  100); each must fit a 32-bit integer.
- `speed_cm_s` (at most 200000) is how many centimetres further from the point the participant may be for
  each second since the mixer received it. `tolerance_cm` (at most 500000) is the difference accepted at
  the moment of the sample. An entry outside these ranges, or not five numbers, is skipped and counted in
  `skipped`; the rest of the request still applies. *Region server:* `speed_cm_s` is twice the region's
  own velocity for the avatar (or the object it sits on, if faster), between `PositionFeedSpeedFloorCmS`
  and the physics engine's maximum linear speed; `tolerance_cm` is `PositionFeedMarginCm` for a root
  agent and that margin plus 10 m plus two seconds at `speed_cm_s` for a child agent, because a root
  region reports a child's position only after about 10 m of movement and at most every 2 s.
- `full: true` means the entries are the whole room: a participant not listed has left, and the store is
  emptied first. `full: false` updates only the listed participants. A point older than 6000 ms on the
  mixer's own clock is stale and is not used.
- `seq` must rise per room. A request whose `seq` is not above the last accepted one **of the same
  epoch** is answered `{"slvoice":"error","reason":"stale_seq"}` and ignored. A different epoch replaces
  the old instance's points.
- One request may carry at most 128 entries and 65536 bytes; more is rejected whole (`too_big`). The
  region server sends at most 120 per request (a larger room is split, and only the first request of a
  full snapshot has `full: true`); a room of 110 is about 8 KB. The store holds 256 participants per room.
- `room_epoch`, when present, must be 16 hex digits. A request older than a **fresh** adopted epoch is
  answered `stale_epoch`. A position request only *reads* the room's authority: it never adopts an epoch,
  advances a generation or arms a listener.
- `mode` is `observe` or `enforce`. The mixer observes in both cases and counts `enforce` requests; the
  reply says `"pos_mode": "observe"`.

The reply to an accepted request:

```json
{ "janus": "success",
  "response": { "slvoice": "applied", "op": "pos", "room": 12345, "seq": 1760000000001, "full": true,
                "entries": 3, "skipped": 0, "dropped": 0, "pos_mode": "observe",
                "vis_protocol": 3, "mixer_instance": "9f2c61d04b7a83e1" } }
```

`dropped` counts entries the 256-participant store could not hold. The error replies are `{"slvoice":"error",
"reason":"malformed"|"too_big"|"unknown_room"|"stale_epoch"|"stale_seq"}` (an unknown room is counted in
the process-wide `visibility.positions` totals, since there is no room to charge).

*Region-server behaviour (not verifiable from this repository).* The region server enables this with
`[WebRtcVoice] PositionFeedEnabled = true` and sends `peer_pos` only after a mixer reply has advertised
`vis_protocol` 3 or higher. It learns that from the replies to the `peer_ctl_batch` and
`peer_ctl_heartbeat` requests it already sends, and follows the latest reply: an applied reply that
advertises no version means an older mixer, and positions stop. Until then it reads no positions and sends
nothing, so a mixer that does not know `peer_pos` never receives one. A full snapshot is sent when the room
is new and at least every `PositionFeedKeepAliveMs`; between them, only participants that are new or have
moved at least `PositionFeedMoveThresholdCm`. `seq` starts above the time in milliseconds at which the
region started, so a restarted region is not mistaken for old traffic. `PositionFeedMode = enforce` is
accepted in its configuration but is sent as `observe`.

---

## Open items to confirm in code review (not blockers to the design)

1. **Exact transport carrier** for `slvoice_vis` (plugin request vs Janus admin API) against the
   live `WebRtcVoiceServiceConnector` path (the connector has two provisioning overloads).
2. ~~**`AllowVoice` fire path**~~ **RESOLVED** — the "Voice Enabled" toggle reaches
   `OnEstateInfoChange` on both viewer paths (UDP `:2221`, CAP `:2250`); see §5.
3. **Feeder tick default** (250–500 ms) — tune against child-agent movement responsiveness vs
   `GetLandObject` cost.
4. **God/exemption parity** — assert the feeder's exemption set stays bit-identical to
   `LandObject`'s preamble and `IsViewerUIGod`, ideally by reusing the sim predicates directly
   rather than re-implementing them.
