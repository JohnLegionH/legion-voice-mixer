# Scaling Assessment — WebRTC Voice

**Status:** DRAFT. Not frozen. Records what three recons found, what is now settled,
and what remains open.
**Date:** 2026-08-18.
**Basis:** CC recons 2026-08-18 against `legion-voice-mixer @ b1669dc`,
`tranquillity-develop @ b68d86b66a`, and `D:\phoenix-firestorm` (read-only reference).

## Why this exists

Phase 3b slice one added per-pair spatial DSP to a mixer that had never been assessed
for occupancy. Before more DSP is built on that core, two questions needed answering:
what the target actually is, and whether the architecture can reach it.

## The target is ~110, not 500

Second Life's own per-region limits bound the problem:

| Region type | Limit |
|---|---|
| Full | 100, raised to 110 |
| Mainland | 40–44 |
| Homestead | 20–25 |
| Openspace | 10–12 |

Linden Lab's guidance states the maximum for a private region is 100 but that best
practice is to limit each region to 50, and recommends a multiple-locations strategy
for large events — noting a four-corners arrangement reaching roughly 200 avatars in
one place has drawbacks.

Sources: `https://wiki.secondlife.com/wiki/Limits`,
`https://community.secondlife.com/knowledgebase/english/managing-private-regions-r50`,
`https://community.secondlife.com/news/featured-news/improved-region-capacity-and-access-r566/`.

**SL does not put 500 avatars in a region either.** It caps and distributes. The
design target is ~110 concurrent, realistically ~50.

## SETTLED: per-listener server mixing is mandatory

An earlier recon recommended moving to selective forwarding — forward the K nearest or
loudest talkers and spatialize client-side — asserting this is how SL-scale voice
works. **That was an inference stated as fact, and it is wrong.** The viewer source
settles it.

**The viewer has no client-side spatializer.** Zero matches for HRTF, panning,
spatialization, or attenuation in `llvoicewebrtc.cpp`. The only client-side operation
on received audio is `setReceiveVolume` (`llwebrtc.cpp:1380`–`:1402`), a single scalar
applied across all received tracks. The audio processing module (`llwebrtc.cpp:189`–`:263`)
is echo-cancel, noise-suppress and AGC on the **capture** path, not the received stream.

**Every per-source control that affects what the user hears goes upstream.** Per-avatar
volume is serialized as `ug` and sent on the data channel (`llvoicewebrtc.cpp:2705`–`:2711`);
per-avatar mute as `m` (`:2721`–`:2727`); position and camera as `sp`/`sh`/`lp`/`lh`
(`:1241`–`:1268`). Only the master volume and the speaker-indicator UI are handled
locally. **A viewer that mixed locally would apply per-avatar volume to a per-avatar
track. This one sends it to the server, because there is no per-avatar track.**

**The forced stereo is the spatial field.** `stereo=1;sprop-stereo=1` is asserted on
the sender (`llwebrtc.cpp:1236`), the receiver (`:1251`), and re-asserted on the
inbound track in `OnAddTrack` (`:1436`). The viewer configures the inbound track as
stereo Opus and plays it — nothing interprets left from right. Since the viewer applies
no panning of its own, a stereo received stream can only carry a field rendered
upstream.

**Inference flagged as such:** no per-remote-participant track or receiver structure
was found, and the single-track offer plus the singular receive-volume model imply one
inbound mixed track — but no literal assertion of exactly one m-line was citable from
static source. Closing that to certainty would need a negotiated SDP answer from a live
session.

**Consequence.** A viewer speaking this protocol cannot be served by forwarding talker
streams; it has nothing to mix them with. The per-listener encode cost is **inherent to
the protocol shape, not self-inflicted.** LL faces the same wall, and their published
answer is the region cap above.

## SEPARATE AND OPEN: can this implementation hold 110?

That the wall is inherent does not make the current implementation adequate at the
target. LL presumably serves ~110 with a mixer that is not one thread per room.

**One tick thread per room** (`janus_slvoice.c:2330`), holding `room->mutex` for the
entire tick (`:2128`–`:2284`). A single busy region cannot use more than one core.

**Estimated 65–130 encodes fit a 20 ms budget** at complexity 9 stereo full-band
(`:2027`, `:2281`, ~150–300 µs each). The target is 110. **Full occupancy lands on the
wall on one core.**

**The per-encode cost is a tunable, not a constant.** It is a function of
`OPUS_SET_COMPLEXITY(9)` (`janus_slvoice.c:1823`) — one below the maximum of 10 — and
`OPUS_SET_BITRATE(64000)` (`:1824`). Lowering complexity to ~6 could cut per-encode CPU
by roughly a third to a half (**estimate, unmeasured**), which may move the encode wall
past 110 on a single core with no architecture change. A complexity/bitrate sweep must
therefore be measured **before any locking restructure is considered**: a config knob
that reaches the target is worth far more than a thread-pool rewrite.

**Pass 2 is embarrassingly parallel — but not free to parallelize.** Each listener's
mix is independent: it reads shared, read-only, tick-owned decode buffers and geometry
snapshots and writes only its own session state under `s->mutex`. But the membership
freeze and the no-media-freed-mid-tick guarantee currently come from holding
`room->mutex` across the whole tick (`:2128`–`:2284`); distributing the work across
cores requires re-establishing **both** under concurrency — worker threads inside the
held region, or session refcounting to keep `sess[]` alive without the coarse lock —
which is bounded and well-defined but not trivial. Since the encode count cannot be
reduced without breaking the protocol, distributing it is the only *structural* lever,
but it should be reached for only after the non-structural knobs above are measured.
**This is the primary architectural question**, and it is unanswered.

## Mixer-side findings

**The hard cap.** `SLV_MAX_MIX = 64` (`:128`) truncates at `:2139` with a WARN. The
65th participant is not decoded, not mixed into anyone's output, and receives no mix,
while remaining joined and connected with no indication on their side.

**The truncation is non-deterministic.** Which 64 are served comes from
`g_hash_table` iteration order over `room->participants` (`:2137`–`:2138`), undefined
and shifting on rehash. **This is the more serious defect — worse than the cap
itself.** A deterministic cap with a clear rule would be defensible; an arbitrary one
is not.

**Cost terms.** Per-listener encode is O(N) and is the first wall. All-pairs spatial
setup (`:2180`–`:2246`) is O(N²) and dominates above it. No O(N³) term exists.

**What culling actually saves.** VAD-inactive sources skip decode (`:1942`–`:1946`)
and all-silent listeners skip encode (`:2023`–`:2025`). But distance-culled and
exclusion-muted sources are **still decoded** — the decode gate is liveness only — and
the O(N²) setup runs per pair before `audible[]` is consulted. The spec's culling
argument holds for decode and quiet rooms, and not for the two terms that break at
scale.

**Memory.** ~540 KB per participant, of which the 500 ms echo delay ring is 240 KB
(`:1830`) — allocated for every session though echo is a rarely-used diagnostic
override (`:1859`–`:1860`). Not the binding constraint, but ~45% of per-session memory
is unused in normal operation.

## Sim-side findings

**No asset, inventory, region-storage, or database access.** Confirmed by search across
the voice addon. The voice path reads presence positions, land, and estate settings —
all already resident — and talks HTTP to Janus.

**No frame-loop stall.** `VoiceStateFeeder` runs on its own Watchdog-registered thread
(`VoiceVisibilityService.cs:100`–`:107`), holds no long shared lock, takes
`m_landIDList` only briefly. Degradation would be CPU competition, not blocking.

**Off by default.** Both `[JanusWebRtcVoice] Enabled` and `VisibilityFeederEnabled`
default false (`WebRtcVoiceRegionModule.cs:68`, `:102`).

**The feeder pays full cost when nobody uses voice.** `SnapshotAgents` enumerates all
presences regardless of voice state (`FeederWorldFromScene.cs:41`) and `Tick()` runs
unconditionally (`VoiceVisibilityService.cs:150`); the emit gate skips only the send.
A populated region with the feeder enabled and zero voice users pays the full O(N²)
build every 250 ms. **Cost tracks population, not usage** — the most consequential
sim-side finding for a distribution shipped to other operators.

**Cost.** At the settled target of N≈110 the O(N²) build is roughly **4–5 ms/tick** on
its own thread in the common case — comfortable. For context, it is ~1.5 ms at N=64 and
~12–15 ms at N=200, rising to ~80–120 ms at high N when bans or a `SeeAVs=false` parcel
make the per-pair delegates do real ban-list and permission scans (all engineering
estimates, not measurements). Plus 2N closure allocations per tick, so continuous gen-0
GC pressure proportional to population. The finding is not that the cost is large at the
target — it is not — but that it is **paid whether or not anyone uses voice.**

## What to do

Ordered. None of 1–4 depend on the parallelism question.

1. **Fix the non-deterministic truncation.** Whatever the cap, which participants it
   serves must be deterministic and explainable, and excluded participants should know.
   A correctness defect independent of the cap's value.
2. **Skip the feeder build when no listener is provisioned**, so populated regions stop
   paying for unused voice.
3. **Hoist per-parcel ban and restrict results** instead of re-deriving per pair and
   allocating 2N closures per tick.
4. **Stop allocating the 240 KB echo ring** for sessions that never enable echo.
5. **Measure the tick's composition at N≈110**, not a single total against budget —
   encode ms vs. setup ms vs. decode ms — with synthetic load, and across a couple of
   `OPUS_SET_COMPLEXITY` values. Every timing in this document is an engineering
   estimate, not a measurement; the histogram already instruments the total (`ge20ms`,
   `overruns`, `max_ms`), but the decomposition is what decides whether the answer is
   the complexity knob, parallelism, or both.
6. **Then decide on pass-2 parallelism**, informed by 5 and only if the complexity
   sweep does not already reach the target, and raise `SLV_MAX_MIX` toward ~110–128 only
   once the tick can carry it.

**Do not build the deferred DSP first.** HRTF, ITD, distance tiers and azimuth binning
all add per-pair and per-listener cost, lowering the breaking N. They should wait until
the target is reachable.

## Open questions

- **Primary:** measure the tick composition at N≈110, sweep the encode complexity, and
  only then decide whether pass-2 parallelism is needed. Requires (5). The order
  matters — a complexity knob that reaches the target is cheaper than a locking
  restructure.
- **What is the measured cost curve, decomposed?** All timings here are estimates.
- **Exactly one inbound track?** High-confidence inference, not literally cited.
  Resolvable from a live negotiated SDP answer.

---

## AMENDMENT 1 — measured: the binding variable is audible listeners, not population (2026-08-18)

The body's cost figures were estimates. The `bench_tick` harness has now measured the tick directly, and the conclusion changes materially.

**Measurements**, 500 measured ticks each, `SLV_MAX_MIX=128`, complexity 9, on an i7-12700KF:

| Config | Encodes / N | p50 | vs 20 ms |
|---|---|---|---|
| N=110, A=4, clustered | 110 / 110 | 36.6 ms | over |
| N=110, A=4, spread | 24 / 110 | 7.4 ms | fits |
| N=110, A=16, spread | 92 / 110 | 21.4 ms | just over |
| N=50, A=4, spread | ~25 / 50 | 6.8 ms | fits |
| N=50, A=4, clustered | 50 / 50 † | 15.8 ms | fits |

† The N=50 clustered encode count is geometry-derived, not counter-measured: that row predates the harness encode counter, so only its p50 was printed. 50/50 is certain by construction — clustered means every listener is audible, hence every listener encodes — but the count itself was never emitted by the counter.

**The tick is encode-bound, confirmed two independent ways.** Under clustered geometry, cost is invariant to active-talker count — 36.6 / 36.3 / 37.1 ms at A = 4 / 8 / 16 — so decode is negligible. Under spread geometry, varying the audible count 24 → 92 scales the tick 7.4 → 21.4 ms, a slope of ~0.21 ms per encode.

**The binding variable is the audible-listener count, not N.** Break-even at complexity 9 is roughly **85 simultaneously-audible listeners**, essentially independent of population. A 110-avatar region of quiet spectators around a few talkers fits comfortably; a region where everyone can hear everyone does not.

**This supersedes the body's fit conclusion.** The body reasoned from an encode-per-listener wall and concluded ~110 was out of reach. That holds only for the pathological all-audible case. Under realistic spread geometry with single-digit talkers, 110 fits with large margin — because listeners whose entire audible set is culled produce a silent mix and skip encode entirely.

**Culling helps on two fronts, not one.** Clustered at 110 encodes costs 36.6 ms; spread extrapolated to 110 encodes is ~25 ms. The difference is the O(N²) spatial setup, which clustering also maximises because nothing culls. A spread region is therefore cheaper than its encode count alone predicts. The body acknowledged that all-silent listeners skip encode but underweighted how much geometry actually silences. Measured, the effect is large: realistic spread culls most listeners' entire audible set, so the encode count falls to a fraction of N.

**The complexity knob, measured.** c9 → c6 reduces the clustered N=110 tick by 25% (36.6 → 27.4 ms), still over. c9 → c3 reduces it by 57% (36.6 → 15.8 ms), bringing even the clustered worst case under budget — but at a large quality cost, and with max grazing 20.1 ms.

**Hardware caveat, unchanged and important.** All figures are from a fast desktop CPU. A commodity region host is typically 1.5–3× slower single-threaded. The favourable spread case at 7.4 ms would be roughly 22 ms at 3×, which is at the edge. **These numbers are a best case for deployment hardware, not a representative one.**

**What this means for the open questions:**

- **Pass-2 parallelism** is no longer the primary blocker for realistic load. It remains relevant for the all-audible case and for slower hardware, and stays open rather than closed.
- **The practical capacity rule** is "fewer than ~85 audible listeners per tick at c9," which is more useful than a participant cap and should inform whatever replaces the non-deterministic truncation.
- **The deferred DSP** still adds per-pair and per-listener cost, but the margin under realistic load is larger than the body assumed. That changes the weight of the argument for waiting, not its direction.

**Measurement caveats.** The harness is single-threaded with no lock contention and no transport, so real cost is higher; the synthetic tone makes borderline listeners flicker across the silence floor, which is partly real behaviour and partly artifact. See `tests/bench_tick.c` for the full list of differences from the real tick.

---

## AMENDMENT 2 — item 3 (§What to do) targets the wrong cost and describes a cache that cannot exist (2026-08-20)

Item 3 of the body — "Hoist per-parcel ban and restrict results instead of re-deriving per pair and allocating 2N closures per tick" — is wrong on three counts. Recon in the tranquillity tree (`feature/voice-visibility-matrix @ 01888744b3`) established the following against the live sim-side code.

**The 2N closure allocations it names are not the cost.** They are O(N) short-lived gen-0 churn: one `ParcelView` is resolved per agent, allocating its two ban/restrict closures once each (`ResolveParcel` called N times at `VisibilityMatrix.cs:46`–`:48`). The real expense is the **O(N²) delegate *invocations*** in the all-pairs loop (`VisibilityMatrix.cs:51`–`:62`), each of which runs a `ParcelAccessList` linear scan plus `IsAdministrator` and `IsEstateManagerOrOwner` lookups (`LandObject.cs:809`–`:838` on the non-TaxFree path; `LandBan.cs:34`–`:50` on the TaxFree path). Item 3 counted the O(N) allocation and missed the O(N²) work.

**"Per-parcel ban and restrict results" describes a value that cannot exist.** The delegates take the *tested avatar* as their argument (`ParcelView.ExcludesByBan(avatar)` at `FeederWorld.cs:67`–`:69`, invoked with the other party's id at `VisibilityRules.cs:34`), so the result is a function of **(parcel, avatar)** — a parcel bans some avatars and not others. There is no per-parcel scalar to hoist. Any cache is a **two-dimensional (parcel, avatar) memo**, not a per-parcel value, and it pays back only when agents cluster onto few parcels (few distinct parcels ⇒ the memo collapses the repeated per-avatar scans; one-agent-per-parcel ⇒ nothing to collapse). This is the same clustering dependence Amendment 1 measured on the encode side.

**The item missed a real quadratic allocation on the path it wasn't looking at.** Under a **TaxFree** estate the `banned` closure allocates **two further delegates on every invocation** — the admin and estate-manager lambdas passed into `LandBan.IsBannedIgnoringTaxFree` (`FeederWorldFromScene.cs:102`–`:103`, inside the closure at `:98`–`:106`). Because invocation is O(N²), allocation on the TaxFree path is **O(N²)**, not the O(N) the item described. The genuine allocation problem was quadratic and on a path the item never named.

**Fix taken: the inner-delegate hoist only.** Build those two admin/EM delegates once (they capture only the scene and estate, both stable across a tick) and reuse them, instead of reallocating them per invocation. This removes the O(N²) TaxFree allocation with zero semantic change and no new cache or invalidation surface.

**Deliberately not taken: the (parcel, avatar) memo.** It pays only under clustering (see above), and it would change the documented contract that **each delegate call reads live `LandData`** — the property the feeder's mid-scan hardening relies on (`FeederWorldFromScene.cs:7`–`:9`; `VoiceStateFeeder.cs:13`–`:17`). A per-tick memo would freeze the first (parcel, avatar) result for the rest of the build — more internally consistent, but a behavioural change to weigh, not a free hoist. Deferred until a measured tick-time problem at real N with real clustering justifies it.

**Meta-note.** Item 3 as written targeted the wrong cost — it named the O(N) allocation and the fix ("per-parcel result") was for a value that isn't well-defined. The recon that surfaced this was prompted by asking whether the item was worth doing **at all**, not how to do it. Framing the question as "is this worth it" rather than "how do I implement it" is what exposed that the item's cost model was wrong; an implementation-first pass would have built a per-parcel cache that silently degrades to a (parcel, avatar) memo and quietly changes the live-read contract.
