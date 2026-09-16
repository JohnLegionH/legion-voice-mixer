# Two-peer integration harness (O-73)

Automated churn checks for the slvoice mixer. Every live check before this needed a person with
two viewers; from now on **each mixer slice is verified by running this harness against the
deployed mixer** before its ledger rows say VERIFIED.

The harness drives real WebRTC peers (aiortc) through the Janus client API exactly as the sim and
viewer do, and reads the result from the Admin API. It reuses `connectors/common` —
`janus.py` for the HTTP transport and `peer.py` (`ConnectorPeer`) for join + PeerConnection +
SLData data channel + teardown — and adds what a viewer does that a connector does not.

## Run

From the repo root, against a running mixer (`docker compose up -d janus`):

```sh
python -m venv .venv-it && . .venv-it/bin/activate      # Windows: .venv-it\Scripts\activate
pip install --only-binary=:all: -r tests/integration/requirements.txt
python -m tests.integration.run                          # all scenarios, grace 60 s
python -m tests.integration.run --only S3 --no-restart   # one scenario, never restart the mixer
make integration INTEGRATION_ARGS="--only S1,S7"        # same, through make (PYTHON=... to pick the venv)
```

It is safe while the grid is up: test rooms are numbered from **900 000 000** (a fresh block per
run), far from any room `CalcRoomNumber` produces, and every display is a random UUID. **S4 is the
exception** — it restarts the mixer, which drops every live voice session for ~25 s until the sim
self-heals; pass `--no-restart` when people are talking.

| flag | default | meaning |
|---|---|---|
| `--janus-url` | `http://localhost:24223/voice` | client API base |
| `--admin-url` | `http://localhost:24225/voiceAdmin` | Admin API base (the oracle) |
| `--env` | `<repo>/.env` | where `JS_API_SECRET` / `JS_ADMIN_SECRET` are read from |
| `--api-secret`, `--admin-secret` | from `--env` | override the secrets |
| `--compose-file` | `<repo>/docker-compose.yml` | S4 `restart janus`, S5 and S10 `logs janus` |
| `--only S3` / `--only S1,S7` | all | run a subset (repeatable) |
| `--grace N` | `60` | the mixer's `JS_EMPTY_ROOM_GRACE_S`; S5 waits it out, so a short grace (set it in `.env`, `docker compose up -d janus`) makes a full run fast |
| `--join-timeout N` | `30` | the mixer's `JS_JOIN_MEDIA_TIMEOUT_S`; S10 waits it out (a short value in `.env` makes a full run faster) |
| `--no-restart` | off | skip S4 and S20 |
| `--container NAME` | none | a mixer started with `docker run` (the 0.3/0.4 scratch mixers): S17 reads `docker logs NAME`, S20 runs `docker restart NAME` |
| `--join-cap-secret S` | none | slice 0.4: the mixer's `JS_JOIN_CAP_SECRET`, so the harness can mint join capabilities as the sim does. Without it S22 and S23 skip |
| `--stale-ms-started-with N` | none | slice 0.5: the `JS_VIS_STALE_MS` this mixer was **started** with, when that is below the §5 minimum (`scratch.sh up-clamp` uses 1000). Without it S31 skips, because a correctly configured mixer cannot show the clamp |
| `--prove-fail` | off | slice 0.5: report a `SKIP` as a `FAIL`. Only for the "behaviour absent" proof runs against an older image, where a scenario that merely skips proves nothing. **Never for a reporting run** |
| `-v` | off | peer and harness logs, tracebacks |

Output is one `PASS` / `FAIL` / `SKIP` line per scenario — a FAIL carries the expectation and the
observed oracle values — then a summary with the wall-clock and the room-id block used. The exit
code is non-zero on any FAIL. **A FAIL is a finding about the mixer** unless the harness can be
shown wrong; report it, do not bend the scenario to pass.

## How it works

- **Peers** (`harness.TestPeer`, a `ConnectorPeer`) send a real Opus stream — a 440 Hz tone from a
  `MediaStreamTrack` — so `rtp_in_count` climbs and `last_rms` is non-trivial, and send SLData on
  the data channel in the viewer's shapes: `sp`/`lp` as `{x,y,z}` and `sh`/`lh` as `{x,y,z,w}`, int
  ×100 (`llvoicewebrtc.cpp:1239-1266`), and `ug`/`m` as one `{uuid: value}` per message.
  `crash()` closes the PeerConnection and sends Janus **nothing** more (no leave, detach or
  destroy, even when Janus reports the hangup), the way a crashed viewer disappears.
- **Oracle**: Admin API `handle_info` → `plugin_specific` (`room`, `display`, `id`,
  `datachannel_open`, `rtp_in_count`, `peer_ctl_entries`, `peer_ctl_full_drops`,
  `mod_muted_entries`, `excluded_entries`, `last_data_fields_seen`, `last_msg_fields_seen`,
  `room_participants`), plus the plugin's `list` / `listparticipants` through a per-scenario
  control handle for the room-level view.
- **Moderation feed**: `peer_ctl_batch` over Admin `message_plugin` with `admin_secret` in the body,
  in the sim's shape (`PeerCtlBatchSerializer.BuildRequest` + the sink's `room` stamp):
  `{"request":"peer_ctl_batch","op":"replace","excl":{},"mute":{"<listener>":["<source>"]},"room":R}`;
  a listener key with an empty array clears it. `BatchResender` repeats it every 250 ms as the
  feeder does.
- **Timing**: every expectation polls the oracle every 100 ms for up to 5 s (S4 allows 10 s for
  RTP after the restart, S5 polls `list` once a second through the grace). Nothing sleeps blindly.
- **Isolation**: each scenario has its own control handle and fresh room ids, and a `finally`
  tears down its background senders, its peers (leave + detach + destroy; a crashed peer's
  session is destroyed directly), and its rooms, so a failure never leaks into the next one.

## Scenarios

| id | what it does | expects | proves |
|---|---|---|---|
| S1 | A and B join R; A leaves; A rejoins | both `datachannel_open`; exactly one `listparticipants` row per display | presence churn leaves no duplicate rows |
| S2 | B's PeerConnection dies with no leave | within 5 s B's row is gone and A sees `room_participants` 1; C joins; B rejoins with a new session, one row | **O-56** hangup leaves the room and frees the slot |
| S3 | batch mutes B for listener A, re-sent every 250 ms; A leaves and rejoins; then a clearing batch | A `mod_muted_entries` 1; the rejoined session reads 0 mod/excluded before any batch, then 1 within 1 s; 0 after clearing | **O-49** mute set, **O-68** no carried room state |
| S4 | `docker compose restart janus` mid-session; sessions re-created, R rejoined | R re-created with both peers; `rtp_in_count` > 0 and climbing within 10 s | restart self-heal (skipped with `--no-restart`) |
| S5 | A and B move R1 → R2 (leave + join) | R2 holds both; R1 leaves `list` no earlier than the grace, the log shows `[slvoice] room <R1> destroyed after <n>s empty`; a later join re-creates R1 | **O-54** empty-room grace destroy |
| S6 | A sends `ug` for 33 distinct sources, then a mute batch for a 34th | `peer_ctl_entries` 32, `peer_ctl_full_drops` 1, then `mod_muted_entries` 1 | **O-49** a full `peer_ctl` cannot eat moderation |
| S7 | B sends `sp/sh/lp/lh`, then a `ug`-only message | `last_msg_fields_seen` = `ug`, `last_data_fields_seen` still has `sp`, `lp` | **O-64** geometry persists |
| S8 | B rejoins with the same display while its old handle is still up (two rows, the mixer's duplicate-display WARN), then the old PeerConnection dies with no leave | within 5 s one row for that display, and it is the new handle; the old handle is out of the room | **O-56** / **O-13** duplicate-display residue |
| S10 | G creates its Janus session and joins R with an offer stripped of ICE candidates, then never brings a PeerConnection up (it only long-polls, as the sim does) | G listed right after the join; gone no earlier than `--join-timeout` − 1 s and within `--join-timeout` + 5 s; the log shows `[slvoice] <G> reaped from room <R>: no media <n>s after join`; a fresh join by G's display is admitted and gets media | **O-75** join-media reap |
| S11 | A, B and C join R. B's tone lights A's dot. A batch moderation-mutes B for listener A only, re-sent every 250 ms | once A's `mod_muted_entries` is 1, all of the next 10 power batches A receives show B at `p` 0, `v` false; C's next 10 still show B lit | **SC-87** a listener's dots follow what it hears |
| S12 | A and B join R, created without `spatial_audio` so it is spatial by default. Both send geometry: B 10 m to A's side, then 110 m away, then 20 m | at 10 m A's `last_mix_rms` > 0.01 and one of `last_mix_rms_l`/`_r` is under a tenth of the other (hard-panned); at 110 m `last_mix_rms` is exactly 0 (culled); back at 20 m it is > 0.01 again (re-added) | the spatial path (cull, pan) runs, and the **O-83** default is spatial |
| S13 | A joins R as a relay-only peer: its ICE gathering is restricted to relay candidates through the TURN server in `--turn-uri`, so its offer carries only `typ relay`. B joins R normally. **Skipped** without `--turn-uri`; with the `turn-test` compose profile up, pass `--turn-uri 'turn:127.0.0.1:3478?transport=tcp' --turn-secret turn-test-secret` | A's offer holds relay candidates only; A's own ICE agent nominated pairs whose local candidate is its relay, and A has no non-relay socket left (they are closed after gathering: aioice's RELAY policy alone still sends checks from host sockets); B's `last_mix_rms` > 0.01 (A's tone crossed the relay); A receives more than 25 RTP packets back. The mixer's selected pair is printed as information only: on a published-port mixer the relay's packets arrive through the port publish, so Janus reports a gateway prflx pair either way | the relay path carries media both ways, exercised rather than assumed (**A.3**) |
| S14 | A joins R and brings media up. The harness compares the remote end of Janus's selected pair with the addresses A itself holds (its offer's candidates), then ends A's session and reads its record with `docker compose exec janus legion-voice-selfcheck --session <handle> --json` | a rewritten source (the remote end is not one of A's addresses) reads path `undetermined`; a preserved source reads `direct`. The info line says which branch ran: a Docker Desktop mixer can only run the rewritten one, a Linux mixer the preserved one | the diagnostics never overclaim or underclaim relay/direct against measured ground truth (**A.6**, SC-126) |
| S15 | **Fail-closed off only** (skips otherwise). A and B join R, created with `vis_authority`. No arming; then an unstamped batch; then the harness acts as the 0.2 sim: an arming replace in E1, a matching heartbeat, a new-epoch heartbeat E2, an exclusion replace in E2 | never armed: A hears B, `would_silence_listeners` 2 and `would_silence_pairs` 2, A's `vis_row` 1. Every reply carries `vis_protocol` 2 and one `mixer_instance`; the unstamped reply has no `status`. Armed: both counters 0, `vis_row` 4, still audible. After E2: counted again and A **keeps hearing B**; the exclusion still silences B | **0.3 shadow mode**: the full protocol changes nothing audible (§9 1, 2, 25) |
| S16 | **Fail-closed on only.** A and B join R (`vis_authority`). A armed alone, then B; a `Heartbeater` every 1 s; heartbeats paused past the window, resumed; a new-epoch heartbeat; arming in E2 | row 1: A's mix exactly 0, and 10 power batches carry nothing for B and no presence. Pair rule: armed A stays silent for 1.5 s. Row 4: audible and B's `j` arrives. Heartbeats alone hold it audible for 10 s. Audible until `stale_ms` − 1 s after the last heartbeat, silent (`vis_row` 3) by `stale_ms` + 1 s, with B's `l`. Resumed heartbeats restore it. E2 lists A unarmed and silences it; arming in E2 restores it | **0.3 decision table** rows 1, 3, 4 and the pair rule, heard (§9 4, 5, 7-12). The ±1 s margins are polling limits; `test_visauth` checks −500 ms / +100 ms exactly |
| S17 | **Fail-closed on only.** A and B join R created **without** `vis_authority`; nothing arms or heartbeats | A hears B for 2 s with nothing counted; `vis_authority` and `enforced` false; the log holds `fail-closed enabled but room <R> has no vis_authority: NOT enforced` exactly once | **0.3** an undeclared room is unaffected (§9 3) |
| S18 | **Fail-closed on only.** Armed in E2 with heartbeats. A replace in E1; an add with a wrong `base`, a re-arm, an add and a remove with the right base; a heartbeat omitting A; a stopping heartbeat; arming in E1 | E1 refused `stale_epoch` with nothing changed; the wrong base lists A stale and silences it (entry not applied); the right base applies (`excluded_entries` 1, generation 4) and the remove restores; omission silences A (`vis_row` 1); the stop silences within 2 s; E1 then takes over at once and is audible | **0.3** epoch adoption, `base`, omission, graceful stop, takeover (§9 13, 15, 16, 22) |
| S19 | **Fail-closed on only.** A armed and audible; A2 joins with A's display; a wrong generation, a replace; A leaves; an exclusion; A2 leaves and A3 rejoins; A4 joins another declared room | A2 audible at once; both silenced and restored together; A2 stays audible after A leaves; A3 comes in with `excluded_entries` 1 and never hears B; A4 silent until armed in its room | **0.3 keying per room by avatar**: reconnect, fan-out, rejoin columns (§9 19, 20) |
| S20 | **Fail-closed on only**; skipped with `--no-restart`. Armed; the mixer restarts (`--container` or the compose service); R re-created and rejoined | after the restart the room's `authority_epoch` is all zeros and A is silent; the next reply's `mixer_instance` differs from the one before; re-arming restores audio | **0.3** a restart is visible to the sim (§9 21) |
| S21 | **A pre-0.3 image only** (skips when replies carry `vis_protocol`). A and B join R created with `vis_authority`; stamped add and replace (the second with a lower epoch); a heartbeat | the room works; stamped batches apply exactly as unstamped (mute 1, then 0), with no Phase 0 reply keys; the heartbeat is `unknown_request` | **0.3 skew**: a new sim against an old mixer degrades to today's behaviour (§9 26) |
| S22 | **`JS_JOIN_CAP_REQUIRED=0` only**, and needs `--join-cap-secret`. A joins a declared room with a valid capability; B with one bound to another agent; C with none; D joins an undeclared room with none | all four join. `seen` and `accepted` climb; `refused.cap_wrong_agent` and `refused.cap_missing` each climb by one; `enforced_refusals` stays 0; the undeclared room counts no missing capability | **0.4 shadow**: every capability is verified and counted, and no join is refused (§11.4) |
| S23 | **`JS_JOIN_CAP_REQUIRED=1` only.** A valid capability joins a declared room; then nine joins that must each be refused: a replay of the same capability (same agent and session, new Janus session), none at all, malformed, wrong key, expired, wrong agent, wrong session, wrong room, and one minted under another epoch. Finally a capability-less join into an undeclared room | the valid one is admitted; each refusal answers `error_code` 496 with its own `reason` (`cap_replayed`, `cap_missing`, `cap_malformed`, `cap_bad_signature`, `cap_expired`, `cap_wrong_agent`, `cap_wrong_session`, `cap_wrong_room`, `cap_stale_generation`); the undeclared room still admits; `enforced_refusals` reaches 9 | **0.4 O-46**: the join is gated, and every refusal says which check failed (§11.4) |
| S24 | **A pre-0.4 image only** (skips when the mixer reports `join_cap` state). A joins with `join_cap` and `session_id` set, B joins normally | both join and A hears B: the old mixer ignores both keys | **0.4 skew**: a minting sim against an old mixer degrades to today's behaviour (§11.8) |
| S25 | **Fail-closed on only.** S and T join R (`vis_authority`). One replace arms L with S excluded, and arms S and T. **Then** L joins, so the mixer builds L's roster after the arming | L joins already at `vis_row` 4 with `excluded_entries` 1; S is **absent** from the roster the joined event carries and T is present; L hears T; L gets no dot and no presence for S | **§9 6** an excluded source is invisible as well as inaudible. `query_session` has no roster field, so the joined event is the only place that view exists |
| S26 | **Fail-closed on only.** L, M and SRC armed and audible; generations 2 and 3 applied in turn; then one heartbeat naming **L alone** at generation 99 | each reply echoes the highest applied `policy_generation`; L goes silent at `vis_row` 3 while **M stays audible for 2 s**; the heartbeat reply lists L in `stale_listeners` and not M; a replace for L restores it | **§9 14** staleness is per listener, not per room, and **§9 25**'s last clause |
| S27 | **Fail-closed on only.** Armed at generation 10, then a delayed `add` at generation 9. Then, in an **undeclared** room, an exclusion replace carrying no epoch fields at all | the late add is refused `stale_generation`, A's set is unchanged for 1.5 s, `stale_generation_rejects` climbs, and the log names both generations; the unstamped exclusion still silences | **§9 17** out-of-order rejection, and **§9 1**'s remaining half |
| S28 | **Fail-closed on only.** An **empty** arming replace for a display not yet in the room, then that display joins. No heartbeat is ever sent | L joins straight to `vis_row` 4 and is audible, with no heartbeat round trip | **§9 18** pre-join arming survives deferral — empty columns are what makes it hard (design §2) |
| S29 | **Fail-closed on only.** A declared room is emptied and left with no heartbeat for `--grace`; then re-created, rejoined and armed | destroyed no earlier than its grace, with the `room <R> destroyed after <n>s empty` line; a fresh join plus an arming replace is audible again | **§9 23** a declared room with no listeners behaves exactly as today |
| S30 | **Fail-closed on only.** SRC joins R (`vis_authority`); REC joins with `"recorder": true`, then is armed | `handle_info` reports `recorder` true; REC is silent at `vis_row` 1 and stays silent for 2 s unarmed; armed, it hears the room | **§9 24** a recording tap is gated like any participant (open question 3, ledger **O-88**) |
| S31 | **A mixer started below the §5 minimum only** (`scratch.sh up-clamp`, `JS_VIS_STALE_MS=1000`); needs `--stale-ms-started-with` | the reported `stale_ms` is **7250**, the §5 constraint, not the 1000 it was given, and the startup log holds `JS_VIS_STALE_MS=1000 is below the minimum 7250 ms` | **§9 27** the window clamp is enforced and visible, not just computed (`test_visauth` covers the arithmetic) |
| S32 | **Fail-closed on only.** Armed in E2 with heartbeats; a lower E1 while E2 is fresh; then heartbeats stop, the window passes, and the same E1 is sent again | while E2 is fresh E1 is `stale_epoch`; once the window passes A goes silent and E1 is **adopted** (`authority_epoch` E1) with the takeover logged and records disarmed; re-arming in E1 restores audio | **§9 13**'s second half — the takeover path the design specifies, which S18 reaches only through a graceful stop |

**Phase 0 runs (0.3).** S15 runs against the deployed mixer (fail-closed off). S16-S20 need a mixer with
`JS_VIS_FAIL_CLOSED=1`, which must never be the live grid's: start a scratch container on other ports and pass
`--janus-url`, `--admin-url`, its throwaway secrets and `--container <name>`. S21 needs the pre-0.3 image, also as
a scratch container. `--container NAME` makes S17 read `docker logs NAME` and S20 run `docker restart NAME`.

**Phase 0 runs (0.5).** S25-S30 and S32 need `JS_VIS_FAIL_CLOSED=1` and a normal window; S31 needs a mixer
**started** below the §5 minimum. Both are scratch containers, never the live grid's — `scratch.sh up-fc`
(ports 47223/47225, `JS_EMPTY_ROOM_GRACE_S=15`, so pass `--grace 15` for S29) and `scratch.sh up-clamp`
(ports 46223/46225, `JS_VIS_STALE_MS=1000`, so pass `--stale-ms-started-with 1000`). When overriding a knob
that `COMMON` already pins, put the `-e` **after** `$COMMON`: the last one wins, and getting that backwards
is why the clamp mixer first came up in shadow mode.

**Proving a new scenario is worth having.** A scenario counts only once it has been shown to fail when the
thing it asserts is absent. Two proofs, because the first alone is weak:
1. **Behaviour absent** — run it against `legion-voice-mixer:rollback-pre-03` (no `vis_protocol`, no
   `peer_ctl_heartbeat`, no `vis_authority`, no `stale_generation`, no takeover) with `--prove-fail`, which
   reports the skip as the failure it really is. This proves the image lacks the authority, and no more: all
   eight 0.5 scenarios fail there with the *same* message.
2. **Mutation** — invert the one condition the scenario is about and confirm it fails with *its own*
   assertion text. This is the proof that the scenario tests what it claims; the first proof cannot show
   that, which is how S13 once passed while its "relay-only" peer was still sending checks from host
   sockets.

**Source-address probe (`source_probe.py`, A.6).** Not a scenario: one peer, one question, for any
mixer you can reach. `python -m tests.integration.source_probe [--janus-url URL] [--admin-url URL]
[--env PATH] [--label TEXT] [--json]` joins a test room at 990 000 000 and up, brings media up, and
prints the peer's own candidates beside Janus's selected pair with a verdict: `PRESERVED` (the pair's
remote end is one of the peer's addresses) or `REWRITTEN`. It prints the handle for `legion-voice-selfcheck
--session`. Exit 0 when a pair was selected, 2 when media never came up. `docs/docker-notes.md` →
"Source addresses (A.6)" records what it measured.

**Why S8 joins before it crashes.** The brief's order — drop the PeerConnection, then rejoin at once —
never overlaps on a local mixer: Janus processes the old PeerConnection's DTLS close before the
rejoin's join is handled, even when the rejoin's offer is ready at the moment of the crash (measured
2026-09-13, no duplicate-display WARN either way). Joining first makes the two-rows window real, and
the mixer log shows `join: display … already in room … duplicate display` for every S8 run.

**Known limits.** S3's O-68 check reads a *new* session after a leave; a same-handle leave-and-rejoin
would need a second offer on one handle, which the harness does not do (the unit test
`tests/test_room_lifecycle.c` covers the reset itself). O-67's failure branches (a thread that
cannot start) have no live trigger. Peers run on the host and reach the container's RTP ports
through the compose port mapping, so a host that cannot reach its own mapped UDP range will fail
every `rtp_in_count` expectation — that is an environment problem, visible as S1 failing first.
