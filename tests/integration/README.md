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
| `--no-restart` | off | skip S4 |
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
