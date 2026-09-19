# Phase 1 — the conference authority model (slice 1.1a)

**Status:** decided 2026-09-19 from measurement, not from argument. Phase 0 is enforcing on Legion Grid
(`JS_VIS_FAIL_CLOSED=1`, `JS_JOIN_CAP_REQUIRED=1`). Nothing in this brief was deployed; the measurements
come from the harness against a scratch mixer shaped like production.

Mirrored byte-identical to `legion-voice-mixer:docs/voice/` (ledger O-28). Companion: the recon at
`_ops/handoffs/PHASE1-RECON-20260919.md` (untracked), whose F2–F5 this brief settles.

---

## 1. The hypothesis, and the verdict

> **Hypothesis (option c):** ADHOC rooms stay **undeclared**; the mixer requires a join capability for
> **every sim-created room**, keyed on how the room was created rather than on `vis_authority`; membership is
> revoked by eviction; in-call mutes use the existing moderation-mute channel; session **state** is
> grid-scoped, a registry off the audio path rather than an arming authority.

**Verdict: CONFIRMED, and more strongly than expected.** The measurement that was supposed to show
oscillation showed something worse, which removes the main alternative outright.

| claim | verdict | evidence |
|---|---|---|
| A declared room cannot have two authorities | **confirmed — it fails harder than predicted** | S40 |
| An undeclared room admits an uncapability'd join today | **confirmed** | S41 |
| The moderation-mute channel works in an undeclared room | **confirmed** | S42 |
| Eviction alone removes nobody | **confirmed by source** | O-112, O-72 |
| Session state must be grid-scoped | **confirmed by source** | O-110, O-111, Q5 |

---

## 2. What the measurements say

### S40 — two authorities, one declared room, three peers, 30 seconds

Two `Heartbeater`s (epochs `01a0aaaa00000001` = "region A" and `01a0bbbb00000002` = "region B") heartbeat one
declared non-spatial room. Region A arms all three peers with a `peer_ctl_batch` under its own epoch; region
B then starts heartbeating the same room.

```
S40 30 s, two authorities on room 904744300: epoch flips 0; distinct epochs held 1;
samples with 0 armed listeners 30/30; rows seen [1]; stale_epoch_rejects 32;
would_silence listeners/pairs 3/6
S40 first 6 samples: 01a0bbbb00000002/g0/armed0 (x6)
```

**The room did not oscillate — it went silent and stayed silent.** The higher epoch (region B) won
immediately and held for all 30 samples; region A's traffic was refused 32 times as `stale_epoch`; and
because the winning authority had armed nobody, `armed_listeners` was **0 in 30 of 30 samples** and all three
peers sat at **row 1** — the "unarmed, therefore silenced" row — with `would_silence` counting 3 listeners
and 6 pairs.

This is exactly what `slv_vis_epoch_decide` (`src/visauth.h:88-95`) and the adoption path
(`src/janus_slvoice.c:2364-2371`, `g_hash_table_remove_all(room->vis_records)`) specify. It is not a mixer
defect. It is the design working as written, applied to a shape the design never described: **one room, more
than one authority.**

**What it kills.** "Let each member's region arm the conference room" is not a slow-degradation risk, it is a
total outage of that conference for as long as two regions are interested in it. Any design in which more
than one region can arm a non-spatial room is out, unless the mixer's one-authority rule changes — and that
rule is what makes fail-closed safe for spatial voice, so it is not changing for conferences.

**A second consequence, recorded because it will bite:** even a *single* owning region silences the room
whenever its arming is interrupted — a region restart, a feeder stall, an epoch bump — because unarmed means
silent. For a spatial room that is correct: the people are in that region and the region is gone. For a
conference whose members are elsewhere, it silences a call that had nothing to do with the failed region.

### S41 — the ungoverned room, measured

```
S41 required=True bare join into an UNDECLARED room: joined=True cap_present=False
verdict=cap_missing cap_missing before/after 0/0 enforced_refusals=0
```

With `JS_JOIN_CAP_REQUIRED=1` — the live setting — a join carrying **no capability at all** into an
undeclared room is admitted, and the refusal counters do not even move: the gate is keyed on `vis_authority`
(`janus_slvoice.c:1374-1381`, `if(declared)`). Every A2A room on Legion Grid today is such a room
(`JanusAudioBridge.cs:79-80`). See O-105 for the scope: viewers never reach the Janus API, so the exposure is
to holders of `JS_API_SECRET`, and it is live now, not hypothetical-future.

### S42 — the moderation mute in an undeclared room

```
S42 undeclared room 904754900: A mod_muted_entries=1; A dot lit in 0/10 batches after the mute;
C (not muted) lit in 10/10; A last_mix_rms=0.14967
```

The mute lands and holds in a room with no visibility authority: the muted source's dot is dark for A in
every batch after the mute and lit for C in every batch. The mute is per-listener state
(`janus_slvoice.c:2166` `set_mod_muted_locked`), consulted in the mix independently of arming (`:1119-1124`),
so it does not need declaration. *Honest limit of this measurement:* A's non-zero `last_mix_rms` is C's tone,
not B's — the dots are the evidence here; a per-source audio proof would need a single-source room.

### What the harness could not reach

**Eviction (Q3) is not reachable as a scenario** and the reason matters: the mixer has no third-party kick.
The only removal verbs are the participant's own `leave` (`janus_slvoice.c:3257`), a room destroy, and the
no-media reap. So "evict" is something the **sim** does to its own viewer session — and O-112 records what
happens next: the stock viewer re-provisions immediately (O-72), so an eviction without a durable
admission-side refusal is a one-second interruption, not a removal.

---

## 3. The model, stated

1. **ADHOC rooms are not declared.** A conference is everyone-hears-everyone by definition; there is no
   per-listener matrix to enforce, so there is nothing for fail-closed to add — and, per S40, a great deal
   for it to take away. Declaration stays what it is today: spatial `local` rooms only.
2. **Admission is the gate, and it moves.** The capability requirement must key on **how the room was
   created** (a sim-created room of any channel type) rather than on `vis_authority`. That is a small mixer
   change — the room already records its creation flags (`janus_slvoice.c:454`, `:715`) — and it closes
   O-105 for A2A rooms that exist today as well as for conferences.
3. **Membership is revoked at the door, not in the mix.** Eviction = the sim removes the participant **and**
   records a refusal for that (agent, session) so the viewer's automatic re-provision is answered `no`. The
   refusal cache from O-72 (`RefusalCacheSeconds`) is the existing machinery to hang that on.
4. **In-call mute uses the moderation-mute channel**, unchanged and already proven in an undeclared room
   (S42). Per-listener, sim-authored, no new protocol.
5. **Session state is grid-scoped and lives off the audio path.** It is a registry of who is in which
   session, not an arming authority: no epochs, no generations, no heartbeats, no silencing. It answers
   "may this agent provision voice for session X" and "who should get the roster update".

**Where session state lives.** Multi-host is in scope: production grids run regions on several hosts, and a
conference's members can be on any of them. Two candidates, and the recommendation is the first:

- **In Robust, behind the existing `WebRtcVoiceServerConnector`** (`WebRtcVoice/WebRtcVoiceServerConnector.cs:47`).
  It is an `IServiceConnector` built to receive voice requests from region servers, with the region-side
  half (`WebRtcVoiceServiceConnector`) already written. It is currently **dead code** — the grid publish
  ships no `WebRtcVoice*` assembly at all (verified in slice 0.10a: 146 files, zero). Bringing it to life
  gives one process per grid that every region can reach, which is exactly the shape a session registry
  needs, and it is the only existing seam that is grid-scoped by construction.
- A new grid service. Cleaner sheet, more moving parts, and it duplicates what the dead connector was for.

**What stays per region:** the audio path. Spatial arming, the feeder, the epoch and the heartbeat remain a
region's own business, untouched by any of this.

---

## 4. What "leave" actually is (O-108)

There is no `leave` method on `ChatSessionRequest`. The viewer's full method set is `start conference`,
`invite`, `accept invitation`, `decline invitation`, `call`, `decline p2p voice`, `mute update`,
`session update`, `fetch history` (`llimview.cpp:585`, `:634`, `:674`, `:792`, `:3422`, `:3437`;
`fsfloaterim.cpp:2126`; `llvoicechannel.cpp:631`; `llspeakers.cpp:849`, `:877`, `:957`).

So departure must be inferred from four signals, and a session engine has to treat all four as equivalent:

| signal | what the sim sees |
|---|---|
| the viewer drops voice | a provision teardown body, already recognised (`A2AProvisionAdmission.cs:3`) |
| the viewer closes the IM session | IM-layer traffic; no voice call at all |
| the agent logs out or crashes | presence removal; the mixer sees a hangup (O-56) |
| the sim evicts | its own decision, which must persist as a refusal (O-112) |

A conference roster that waits for an explicit leave will accumulate ghosts — which is O-41's lesson in a
new place.

---

## 5. Consequences for the Phase 1 slice list

Part 1 changed three things: conference **text** IM does not exist at all (O-111) and must be built before
voice can hang off it; the reconciliation adds `ChatSessionAgentListUpdates` to slice 1.2 and idempotency
under retry/reconnect/region-crossing races to 1.1b; and the authority question is now answered, so slice
1.4 shrinks to one small mixer change while a new slice appears for the session service.

| # | slice | repos | deploy + live proof | estimate (min) |
|---|---|---|---|---|
| 1.0b | NPC-connector harness driver (`voice connector move`), instrument only | sim + harness | no | 120–155 |
| **1.1b** | `NonSpatialVoiceSession` engine: records, membership, derived ids, the 50-cap (O-109), **idempotency under retry, reconnect and region crossing**, departure from the four signals of §4 | sim | no | 80–100 |
| **1.1c** *(new)* | **Grid-scoped session registry** behind `WebRtcVoiceServerConnector`, with the region-side client; multi-host from the start (O-110) | sim (region + Robust) | **yes** | 80–100 + 65–70 = **145–170** |
| **1.2a** *(new, was hidden)* | **Ad-hoc conference TEXT session** on the Groups pattern: `ChatterBoxSessionStartReply` with the authoritative id, `ChatterBoxInvitation` cross-host via `IMessageTransferModule`, `IM_SESSION_SEND` fan-out (O-111) | sim | **yes** | 2 × 80–100 + 65–70 = **225–270** |
| **1.2b** | `start conference` / `invite` / `accept invitation` / `decline invitation` against the engine, **plus `ChatSessionAgentListUpdates`** on every membership change (O-107) | sim | **yes** | 80–100 + 65–70 + fix 45 = 190–215 |
| **1.3** | Voice on a conference session: `call` provisions `multiagent` with the session's room, admission through the engine | sim | **yes** | 80–100 + 65–70 + fix 45 = 190–215 |
| **1.4** | **Capability required for every sim-created room** (key the gate on creation, not on `vis_authority`), + harness scenario + board (O-105) | mixer | **yes** | 45–60 + 65–70 = **110–130** |
| 1.5 | Teleport continuity (depends on 1.0b and 1.1c) | sim + harness | **yes** | 185–225 |
| 1.6 | Migrate A2A/P2P onto the engine, LAST | sim | **yes** | 190–215 |
| 1.7 | Docs, coverage, release note | both (docs) | no | 40–55 |

**Total 1475–1755 min ≈ 24 h 35 m – 29 h 15 m** of CC time, plus John's in-world time for seven live proofs.
Without the optional 1.0b driver: **1355–1600 min ≈ 22 h 35 m – 26 h 40 m**.

Against plan v3's **3 h 25 m** for all of Phase 1 that is **seven to eight times larger**, and the recon's own
estimate (20–24 h) grew by about four hours once Q1 exposed O-111. The single biggest line is the one nobody
had costed: **a voice conference needs a text conference to hang off, and Tranquillity has never had one.**

**Order.** 1.4 first — it is the smallest slice, it closes a live exposure (O-105) rather than a future one,
and it is independent of everything else. Then 1.1b, 1.1c, 1.2a, 1.2b, 1.3. Teleport and the A2A migration
last, as carried in.

---

## 6. Scenarios added by this slice

`S40`, `S41`, `S42` in `tests/integration/scenarios.py`, all marked **CHARACTERISATION**: they record what
the mixer does today and pass as long as the measurement can be taken. They must not be "fixed" into
acceptance tests without changing this brief first. S40 is the reproduction of O-106 and should be re-run
against any future change to the arming or epoch rules.
