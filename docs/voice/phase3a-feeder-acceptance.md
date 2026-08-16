# Phase 3a — VoiceStateFeeder adapter & wiring: acceptance notes

Scope of this commit: the `FeederWorldFromScene` Scene adapter, the `VoiceVisibilityService`
(per-region owner + tick thread + event wiring), and the `WebRtcVoiceRegionModule` hooks. The pure
matrix engine (`Addons/os-webrtc-janus/Visibility`) landed earlier. No Janus emission yet.

## Config knobs (`[WebRtcVoice]`)

| Key | Default | Meaning |
|---|---|---|
| `Enabled` | false | module master switch (pre-existing) |
| `VisibilityFeederEnabled` | **false** | start the per-listener visibility feeder per region |
| `VisibilityTickMs` | 250 | feeder tick cadence |

The feeder is **off by default** — no consumer emits its output to Janus yet, so production regions
should leave it off until the sender lands. Enable it for the smoke check below.

## Automated tests (all green)

- Engine — `Tests/WebRtcJanusService.Tests` (36): rules, matrix, delta, snapshot, fan-out, hardening,
  thread-capture. Includes the two-parcel symmetric-exclusion end-to-end via the `FakeWorld`.
- Adapter — `Tests/WebRtcVoiceRegionModule.Tests` (12):
  - `LandBanTests` (8): the TaxFree-bypass ban scan against real `LandData`/`LandAccessEntry`
    (permanent/live/expired bans, `UseBanList` gate, admin / EM-or-owner / parcel-owner exemptions).
  - `FeederWorldFromSceneTests` (4): real-`Scene` ban delegate reads live `LandObject` state,
    `SeeAVs`/`AllowVoiceChat` flag mapping, benign unknown-parcel, and event→dirty wiring.

## Tick-thread single-thread invariant — validate in a DEBUG session

`VoiceStateFeeder` asserts (via `Debug.Assert` in `RecordAndCheckTickThread`) that the matrix is
mutated only on one tick thread. **`Debug.Assert` is compiled out in Release**, so this invariant is
*not* checked in a Release build. It must be validated **in a DEBUG build session**:

1. Build the addon **Debug**.
2. Set `[WebRtcVoice] Enabled = true`, `VisibilityFeederEnabled = true` (optionally lower
   `VisibilityTickMs`).
3. Run a region; connect an avatar (or two) and move across parcel boundaries; edit parcel
   flags / bans; change estate voice settings.
4. **Pass:** no assertion failure fires, the dedicated thread `VoiceVisibilityFeeder:<region>` runs,
   and the log shows `[VOICE VISIBILITY] <region>: +N listeners / -M listeners` as avatars move.
   On region close the thread stops and joins within the 2s timeout.

The dedicated named background thread (never a `ThreadPool` timer) is what keeps the guard quiet;
event handlers only flip the dirty flag on sim threads and never touch the matrix.

## Known test-harness limitation

The real-`Scene` tests deliberately create **no `ScenePresence`s**: this tree's
`ScenePresence.Finalize()` throws an NRE during GC and crashes the test host (a pre-existing harness
fragility, not adapter code). The full presence→matrix path (symmetric exclusion across avatars) is
therefore covered by the deterministic engine `BanScenario` test and by the in-world DEBUG smoke
check above, rather than an automated real-`Scene` presence test.

## Decisions in force (see `parcel-voice-semantics.md` §E/§F)

- SeeAVs hiding is **symmetric, pending SL verification**.
- The parcel-ban TaxFree void is **fixed, ban-only**; access-**restriction** keeps the sim's TaxFree
  self-nullify (deliberate divergence, commented in `FeederWorldFromScene`).

## Deploy check — predicted room numbers (Legion Grid)

Computed from `JanusAudioBridge.CalcRoomNumber(regionId, "local", REGION_ROOM_ID=-999, "")` for the
three loaded regions. Note the hash consumes `-999` via the `Add(float)` overload (there is no
`Add(int)`), i.e. the 4 bytes of `float -999.0f`. These are the room numbers the sink must land on;
compare against the mixer's `handle_info.room` and `SelectRoom` log lines.

| Region | RegionUUID | Predicted room (estate channel) |
|---|---|---|
| Ebony | `c44606b1-43e1-45fb-8ae8-201545dc2f6a` | **226001844** |
| Transylvania | `0e99ab97-d710-4714-9230-ddd50b722000` | **1578726032** |
| Elm | `806332b8-7294-4101-842d-e6e2d5385e55` | **1967062692** |

A baseline capture (2026-08-16) confirmed 226001844 (Ebony) and 1578726032 (Transylvania) live; both
avatars occupied **both** rooms via the neighbour-region child agent (see `parcel-voice-semantics.md`
§G). Elm was not adjacent, so 1967062692 was absent.

## Instrument notes — reading the mixer's `handle_info` per session

The mixer's `janus.plugin.slvoice` per-session `handle_info` (Admin API) exposes the fields to verify
a batch landed. Read them precisely:

- **`excluded_entries`** — the **ban/visibility signal**. This is the size of the per-listener
  exclusion set the mix loop and roster both consult; a per-listener ban shows up here as a non-zero
  count on the banned listener's session. Baseline (no sender) = `0`.
- **`peer_ctl_entries`** — the **viewer-sourced mute/gain set** (the `m`/`ug` data-channel state a
  viewer applies to individual peers). This is **NOT** a visibility exclusion — it is a parallel,
  viewer-driven structure and moves independently of the sim-derived matrix. Do not read a non-zero
  `peer_ctl_entries` as a ban.
- **`visibility.joins_since_snapshot`** — the **roster-ahead-of-feed gap**: how many room joins have
  occurred since the last `replace` (snapshot) batch was applied. It reads how far the mixer roster
  leads the visibility feed; a persistently non-zero value with `have_batch=false`/`epoch=0` means no
  sender is (yet) driving the room.
