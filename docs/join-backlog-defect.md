# DEFECT — a joining listener receives no roster backlog, so occupants already in the room have no participant row until a later transition

**Status:** OPEN — root cause PROVEN (viewer instrument captures + mixer source). Fix designed
below, not yet implemented. The fix lives in `src/janus_slvoice.c` (this repo), which is why the
record is here.

**Affects:** ALL viewers, including stock official Firestorm. The missing data is **server-side**
(this mixer never sends it), so no viewer can render the row on join. Earlier viewer-side theories
are retracted — see "Supersedes" below.

---

## Symptom

An avatar who is **already in voice** in a room has **no participant row** in the viewer of a
second avatar who logs in afterwards. Audio works both ways throughout. The row appears only later,
when some unrelated transition happens to fire — the other avatar speaks, moves across a
parcel/visibility boundary, or a third party joins/leaves. Because that trigger is incidental, the
symptom looked intermittent and survived viewer/grid restarts: whether a row appeared depended
entirely on whether a transition fired after login, not on any persistent state.

## Proof (do not re-derive — recorded from the investigation)

From `Voice`-tag debug instrument captures in the viewer:

- The viewer's `getParticipantList` returned the other participant **correctly once present**:
  `mProcessChannels=1 mSession=1 size=2 hasOther=1`. **The viewer core and panel are not at fault.**
- The core map size went **1 → 2 roughly EIGHT MINUTES after login** (13:14:31 `size=1` →
  13:22:12 `size=2`), with the observer having logged in while the other avatar was **already in
  voice**.
- `hasOther` was already true while `size` was still 1: the viewer *knew of* the other avatar (from
  the periodic dot batch) but had **not been given a join event** to materialise them as a list
  entry.
- The row appeared only when a later transition forced a refresh — not on join.

The eight-minute gap and the "known-but-not-a-row" state are the signature of a **missing
join-backlog on the server side**, not a viewer bug.

## Mechanism, in source (`src/janus_slvoice.c`)

The viewer creates a participant row only on a `"j"` (join) presence message on its WebRTC data
channel (`OnDataReceivedImpl`; a power/VAD dot without a prior `"j"` does not create a participant).
This mixer emits `"j"` **only on a transition**, and sends a joining listener **no backlog** of the
participants already present:

1. **`janus_slvoice_push_presence` (`:583`–`:621`) is transition-only and points the wrong way for
   a newcomer.** On a join/leave it builds `{who:{"j":{"p":true}}}` (or `{"l":true}`) for the
   subject `who`, then iterates `room->participants` and relays it to **every OTHER** participant,
   skipping the subject itself (`:610`–`:611`). So when the newcomer joins, the **existing**
   occupants are told about the **newcomer** — the newcomer is told about **nobody**. Callers:
   the join path (`:1891`, via `presence_join`) and the leave path (`:1801`).

2. **The initial roster IS built, but it goes over signalling, not the data channel.** The join
   handler assembles a `participants` array — filtered by the joiner's own exclusion set
   (`:1745`–`:1753`) — and attaches it to the **`audiobridge: "joined"` event** (`:1756`–`:1760`).
   That event is the Janus API/plugin response to the sim's Janus service; it is **not** relayed on
   the listener's WebRTC data channel, which is the only channel the viewer parses for row
   creation. So this roster never reaches the viewer's row-creation path.

3. **`janus_slvoice_data_ready` (`:2778`–`:2785`) sends no backlog.** When the listener's data
   channel becomes writable it only flips `session->dc_open` and logs `Data channel open`. There is
   no "send existing participants to this listener" step here or anywhere on the join path.

4. **The only thing that later delivers an existing occupant to a joined listener is another
   transition:** a fresh `push_presence` (someone else joins/leaves), or the visibility feed's
   corrective join — `apply_visbatch`'s `SLV_VIS_OP_REMOVE` (un-exclude) emits
   `janus_slvoice_relay_presence_one(L, who, TRUE)` (`:1273`, built at `:1129`). Both are
   transitions; neither is a join-time backlog. This is why the row eventually appears, minutes
   late, on movement/resync.

**The source matches the captured behaviour exactly.** No mismatch — the mechanism is as described,
not more complex.

## Why it looked intermittent

Row appearance is a pure function of "did any transition fire after I joined?" A quiet room with
two still, silent occupants can leave the second-joiner rowless indefinitely; any speech, movement,
parcel crossing, visibility re-derive, or third-party join flips it on. Restarts change nothing
because there is no persistent state involved — each fresh login re-enters the same
no-backlog-on-join path.

---

## Fix (design, not code)

**Send the joining listener a synthetic `"j"` for each participant already in their room, at the
moment their data channel becomes writable, filtered by that listener's own exclusion set.**

- **Hook:** `janus_slvoice_data_ready` (`:2778`) — this is the earliest point at which
  `relay_data` to the listener is permitted (`dc_open` is being set to 1 here; `relay_data` guards
  on `dc_open`, see `:562`/`:566`). Doing it here rather than in the join handler guarantees the
  channel is writable, avoiding a dropped backlog.
- **Roster source:** `room->participants` (the `GHashTable` iterated by `push_presence` at
  `:605`). For each `p` in the joining listener's room other than the listener itself, emit
  `{p->display:{"j":{"p":true}}}` to the listener — the same shape `push_presence` sends
  (`:591`–`:596`).
- **Exclusion filter:** the listener's own `session->excluded` set, using
  `slv_roster_excludes(listener->excluded, p->display)` — the identical predicate already used for
  the signalling roster (`:1751`), for live join/leave omission (`:613`–`:614`), and for the dot
  batch. A source this listener excludes (banned / moderated / visibility-culled) must be omitted so
  the backlog cannot reveal a participant the per-listener visibility rules hide. Reusing this exact
  predicate keeps the backlog consistent with the single source of truth.
- **Locking:** take `room->mutex` to iterate `participants` (as `push_presence` does at `:603`),
  relay after building, matching the existing presence discipline.

### What could go wrong (must be handled in implementation)

1. **Double-join race.** A real `push_presence` (someone joining at the same moment) could race the
   backlog, so the listener gets two `"j"` for the same source. The viewer's `addParticipant` is
   idempotent on UUID (`findParticipantByID` before insert), so a duplicate `"j"` is harmless — but
   confirm the viewer treats a second `"j"` for an existing participant as a no-op, not a reset.
2. **Ordering vs the listener's own join.** The backlog must be sent to the LISTENER only, and must
   not be confused with the newcomer→others `push_presence` that announces the listener to the room.
   Both can fire around the same time; keep them distinct (backlog = others→listener at data_ready;
   announce = listener→others at join).
3. **Exclusion-set timing.** At `data_ready`, has the listener's `session->excluded` been populated
   yet? Exclusions arrive via the visibility feed (`apply_visbatch`) which may lag the data channel
   opening. If `excluded` is empty at `data_ready`, the backlog could momentarily reveal a source
   that a not-yet-applied batch would exclude. Options to weigh: send the backlog and rely on the
   next batch's `SLV_VIS_OP_REPLACE`/leave to correct it (consistent with how live joins are
   corrected today, `:1273`), or defer the backlog until the first batch has been applied. The
   first matches existing behaviour; the second is safer for moderation but adds a wait. Decide
   explicitly and record it.
4. **`p->display` validity / duplicate displays.** Skip entries with `display == NULL`, and note the
   duplicate-display handling already documented (`parcel-voice-semantics.md` §M) applies here too.

---

## Supersedes / retracts

The viewer repo's `docs/voice-participant-row-suppression.md` chased three theories, **all now
refuted by the proven `size=2 hasOther=1` read-back**:

- **`volume_settings.xml`** as the trigger — refuted (row still missing with the file empty).
- **`mPrimary` gate / a dropped `"j"`** — refuted (the election is unchanged from upstream and the
  add is not gated on `mPrimary`).
- **A viewer read-back returning empty (`mProcessChannels`/`mSession`)** — refuted (`size=2`).

Those were viewer-side and are wrong. The root cause is this mixer's missing join-backlog. **The
viewer document should receive a final retraction pointing here** — that edit is out of scope for
this task (viewer repo untouched).

## Viewer instrument cleanup (out of scope here; flagged)

Three diagnostic `LL_DEBUGS("Voice")` lines were added to the viewer tree during the investigation
and **must be reverted before any viewer commit** — they are diagnostic only, no behavioural value:

- `getParticipantList` (`indra/newview/llvoicewebrtc.cpp`, ~`:1300`)
- `removeParticipantByID` (`indra/newview/llvoicewebrtc.cpp`, ~`:1364`)
- `onParticipantsChanged` (`indra/newview/fsfloatervoicecontrols.cpp`, ~`:190`)

---

## Notes on where this record lives

This repo has **no defect-log convention** (no `KnownDefects.md`; `docs/` holds flat topical notes
and briefs — `phase1-bringup.md`, `docker-notes.md`, `mixer-feed-protocol.md`, etc.). This file
follows that flat convention rather than inventing a heavier structure. It is deliberately **not**
in `docs/voice/` (the cross-repo-synced directory), because it is mixer-specific and its fix is
mixer code; it can be folded into `parcel-voice-semantics.md`'s roster/visibility semantics later if
a synced home is preferred.
