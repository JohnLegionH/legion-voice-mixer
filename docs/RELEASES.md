# Release notes

Each release lists its **Behaviour changes on upgrade** and **One-time migrations**, even when a list is empty
(the compatibility rule in `docs/docker-notes.md`). The configuration knobs and their defaults are in the knob
register in `docs/docker-notes.md`.

## 1.2.0

The first public release since 0.7.0. Versions 1.0.0 and 1.1.0 were internal versions that were never published;
their changes are part of 1.2.0, and everything below is a change since 0.7.0.

**Image:** `ghcr.io/johnlegionh/legion-voice-mixer` (the name is unchanged; the release workflow pushes the tags
`1.2.0`, `latest`, `1.2.0-debug` and `debug`). `latest` moves from 0.7.0 to 1.2.0 for everyone who pulls it.

### What works with stock NGC voice and what needs this mixer

- **Stock voice needs nothing from this repository.** Region-server voice on the stock Janus AudioBridge plugin
  (`janus.plugin.audiobridge`, the default) does not use this mixer, and nothing here changes it.
- **These need this mixer** (`PluginName = janus.plugin.slvoice` on the region server): distance-based spatial audio,
  the per-listener visibility feed from the region server, sim-reported positions, the sim-issued join capability and
  visibility arming, the recorder and injector connectors, and parcel-level moderation mutes. On a stock plugin the
  region server's switches for the mixer-only features log a warning and turn themselves off.
- Parcel moderation also runs on the stock plugin, but there it mutes the participant rather than the parcel.
- The region-server side of the mixer-only features is being contributed to OpenSim-NGC as separate pull requests,
  each naming the mixer version it needs. It is not in NGC `develop` until those pull requests are merged.
- Every region-server feature that talks to this mixer is off until the operator turns it on.

### Behaviour changes on upgrade

Changes a 0.7.0 operator will see with the defaults:

- **The image carries `janus.plugin.slvoice` only.** It is built without Janus's stock plugins (audio bridge, video
  room, text room, echo test and the rest) and without the nanomsg and Unix-socket transports, and the image build
  fails if any of them reappears. A deployment that uses another stock plugin through this image must stay on the
  previous image for that. The HTTP, admin and WebSockets transports are unchanged.
- **A blank `JS_API_SECRET` or `JS_ADMIN_SECRET` refuses to start** (exit 1, a FATAL naming the keys; the
  development override is `ALLOW_INSECURE_DEV=true`). A secret shorter than 32 characters is named in a start-up
  WARNING (never its value or length); `JS_WEAK_SECRET_FATAL=true` makes that fatal too.
- **Rooms are spatial unless created with `spatial_audio=false`.** A room created without the key now applies
  distance culling, falloff and stereo panning from the geometry the viewer sends; 0.7.0 mixed such a room flat. A room
  created with `spatial_audio=false` is a flat mix (the region server creates avatar-to-avatar call rooms that way).
- **Empty rooms are removed** after `JS_EMPTY_ROOM_GRACE_S` seconds (default `60`; `0` keeps rooms until restart).
- **A participant whose PeerConnection never comes up is removed** `JS_JOIN_MEDIA_TIMEOUT_S` seconds after joining
  (default `30`; `0` restores the old behaviour).
- **The room limit is 50 participants by default** (it was 64 in 0.7.0). `JS_ROOM_SIZE` sets it up to 110, the most the
  plugin is built for. A join beyond the limit is refused as room full.
- **The Opus encoder complexity default is 5** (0.7.0 used 9, set in `src/janus_slvoice.c`). The encoder does less work
  per listener, so a tick takes about 35% less CPU at 50 to 80 participants (table below); the audio is encoded with a
  cheaper search at the same 64 kbit/s bitrate. It is a build-time setting (`-DSLV_OPUS_COMPLEXITY=N`), not a runtime one.
- **Quiet but audible mixes are sent.** Encode-skip now applies only to an empty or exactly-zero mix; there is no
  level floor, so a talker at speech level beyond about 33 m under the default curve is heard. Encode CPU rises a
  little for listeners whose only in-range talker used to fall below the old floor.
- **Ten-millisecond packets are mixed correctly**, and the SDP answer advertises `ptime` 20.
- **Voice dots follow what the listener hears.** A source that is muted for a listener, or culled by distance, reports
  no power in that listener's batch.
- **The recorder, and the injector with `RECORD=1`, refuse to start until `RECORDING_OPT_IN=yes`** is set, and a
  recording participant is marked as such in the room.
- **The full SDP offer and answer dumps are at verbose level** and carry no ICE credentials; each join still logs
  one INFO line.
- **Replies advertise `vis_protocol` 3.** A sim that ignores the field is unaffected; the capability it announces is
  `peer_pos`, below.

Nothing else changes with the default configuration: with none of the new options set, the generated Janus
configuration has the same settings as the previous release's.

### Room size and CPU

Every room mixes on its own thread, and each 20 ms tick encodes one Opus frame per listener, so a tick's cost grows with
the number of participants. The room limit is therefore a setting (`JS_ROOM_SIZE`, default 50, 2 to 110) rather than a
fixed number.

Measured with the tick benchmark (`make bench_tick`, one thread, 300 timed ticks per run after 10 warm-up ticks, three runs
per cell; the table gives the median over the runs), built once per encoder complexity at the default 64 kbit/s bitrate.
Machine: Intel Core i7-12700KF, run in a Docker Desktop Linux container on Windows 11 with no CPU limit. The tick budget
is 20 ms. Every listener is encoded on every tick in this benchmark, so the one in ten case saves only the mixing work.

| Complexity | Participants | All talking, p50 | All talking, p95 | One in ten talking, p50 | One in ten talking, p95 |
|---|---|---|---|---|---|
| 9 (0.7.0) | 50 | 17.51 ms | 18.74 ms | 15.70 ms | 19.28 ms |
| 9 (0.7.0) | 64 | 23.10 ms | 24.72 ms | 20.15 ms | 24.21 ms |
| 9 (0.7.0) | 80 | 29.67 ms | 31.47 ms | 25.48 ms | 30.57 ms |
| 5 (default) | 50 | 11.27 ms | 11.91 ms | 9.39 ms | 12.04 ms |
| 5 (default) | 64 | 14.84 ms | 15.53 ms | 12.29 ms | 15.37 ms |
| 5 (default) | 80 | 19.78 ms | 21.39 ms | 15.73 ms | 19.82 ms |

At the default complexity of 5, 64 participants fit within the 20 ms tick with headroom (p95 15.53 ms all talking,
15.37 ms one in ten); 80 do not (p95 21.39 ms all talking; 19.82 ms with one in ten talking, which is only just inside). At
complexity 9, 50 was at the edge (p95 18.74 ms all talking) and 64 overran. The room size default stays at 50 to leave
room for cores slower than that desktop's. Check the tick histogram in the Admin API `handle_info` reply (`overruns`,
`ge20ms`) under real load before raising `JS_ROOM_SIZE`.

### Spatial audio

- Distance cull with hysteresis, distance attenuation and constant-power stereo panning from the geometry the viewer
  sends, with a camera-position leash. The five constants are settable in `etc/janus/janus.plugin.slvoice.jcfg`
  (all optional, validated together).
- Per-source mute and gain, a join-time room capacity check, and an echo delay ring allocated only when echo is used.
- Opus encoder tuning (complexity default 5) and the largest room size can be overridden at build time. A tick benchmark (`tests/bench_tick.c`)
  is included.

### Visibility and moderation

- The region server can send per-listener visibility (`peer_ctl_batch`): who must not hear whom, and moderation mutes,
  kept in a separate set so a full table cannot lose one. Entries for a listener who has not joined yet are held and
  replayed on join; an unknown room or request is answered with an error.
- A moderation-muted source stays in the roster, greyed. A joining listener is sent the participants already in the
  room, and an avatar-to-avatar room announces every removal.
- **Visibility authority (opt-in).** The sim arms every listener per room with an epoch and a policy generation and
  heartbeats the arming. With `JS_VIS_FAIL_CLOSED=1` a listener who is not armed, or whose arming is older than
  `JS_VIS_STALE_MS` (default `8000`), is silenced in rooms the sim created with `vis_authority`. The default `0`
  counts what it would do and changes nothing.
- **Join capability (opt-in).** The sim mints a short-lived capability bound to agent, session, room, epoch,
  generation, expiry and a one-use nonce; the mixer verifies it with `JS_JOIN_CAP_SECRET` (shared with the region
  server's `JoinCapabilitySecret`, and not the API secret). `JS_JOIN_CAP_REQUIRED=1` refuses a join into a room the sim
  created that has no valid capability (error 496 with a reason). The default `0` verifies and counts only.
  Capability values are never logged.

### Positions, observe only

- A sim can report where it holds each participant with the admin request `peer_pos` (global centimetres, a speed
  allowance and a tolerance per participant). The mixer compares it every mix tick with the position the viewer
  claims in its SLData and **counts** what it would correct: `would_clamp`, `would_use_sim` and `no_sim`, per
  participant (`pos_observe`) and per room (`visibility.positions` in `handle_info`). It changes no audio.
- A room that never receives `peer_pos` is not observed at all, so a sim that does not send it behaves exactly as
  before. Counters and request limits are in `docs/docker-notes.md`.

### Connectors

- A recorder peer and an injector peer (python, in `connectors/`) join a room as ordinary participants, fetch a
  sim-minted join capability before every join, rejoin on their own after a failed join or a mixer restart, and send a
  position so injected audio fades with distance. Recording needs `RECORDING_OPT_IN=yes`, and the room must be told.

### Deployment and operations

- **Public address discovery** (STUN, external DNS, container resolver) with a NAT mapping that follows a changing
  address, `JS_NAT_EXTRA_IPS`, and a WARNING when every `nat_1_1_mapping` address is private.
- **Start-up self-check** that prints one board after start (`JS_SELFCHECK`, on by default), and per-session ICE
  diagnostics kept after a session ends.
- **TURN settings** (`JS_TURN_*`), off unless set.
- **Binds:** `JS_ADMIN_BIND` and `JS_WS_ENABLED=false` narrow the admin and WebSocket listeners when set; the defaults
  are the previous release's (all interfaces, WebSockets on).
- **TLS for the API and the admin API (opt-in).** `JS_TLS_CERT` and `JS_TLS_KEY` turn on https for both
  (`JS_HTTPS_PORT`, `JS_ADMIN_HTTPS_PORT`). A missing or unreadable file refuses to start rather than falling back to
  plain http. `JS_HTTP_PLAIN` and `JS_ADMIN_HTTP_PLAIN` (`true`, `loopback` or `false`) narrow the plain-http
  listeners. All are off by default.
- Deployment recipes for a VPS and a home-hosted machine, including how to size `JS_RTP_PORT_RANGE`.
- The container log is capped in `docker-compose.yml` (see the README).

### Fixes

- A create answered 486 (the room already exists) restarts an empty room's grace period, so a room is not removed
  between a create and the join that follows it. A room created with `vis_authority` and left with no listeners is
  removed after the same grace and is armed again by the next join.
- An SLData message without geometry (a volume slider or a mute) no longer flattens the listener's spatial mix until
  the next move; a duplicate display name is detected at join and the dot batch is merged deterministically.
- A heartbeat built before a batch applied no longer disarms a listener, and a capability with no epoch is accepted and
  counted.
- The recorder wrote the decoder's whole 120 ms plane per 20 ms frame; segments now hold real-time audio.

### Test harness

- The integration harness (`tests/integration`) covers joins, leaves, crashes, mute persistence, restarts, room
  switches, spatial coverage, visibility arming, join capability, connectors and `peer_pos`. Scenarios S50 to S54 act as
  a sim that reports positions: matching claims are accepted and counted as nothing; a far claim is counted as
  would-clamp and the audio equals that of a room without positions; a viewer with no position is counted as
  would-use-sim; an old sim that never sends `peer_pos` leaves behaviour unchanged; and a full room of 110 takes one
  request of 110 entries whole while 129 entries are refused.
- S54 needs a wide RTP range (a scratch mixer with several hundred ports); on a mixer with a small range it skips
  and says why. See the sizing note in the deployment recipes.
- The CI workflow runs the unit suites and the harness on every push; the release workflow has a dry-run mode that
  builds both images without pushing (see `.github/workflows/release.yml`).

### What an operator must change

- **Nothing, with the defaults**, unless the deployment relies on a stock Janus plugin or transport that the image no
  longer contains (first item above), or runs a recorder or injector (set `RECORDING_OPT_IN=yes` once the room has been
  told).
- Set both `JS_API_SECRET` and `JS_ADMIN_SECRET` if either was blank, and use secrets of at least 32 random characters
  for the API, admin and join-capability secrets. Consider `JS_TLS_CERT` / `JS_TLS_KEY` if the API or admin API crosses a
  network you do not control.
- Check the size of `JS_RTP_PORT_RANGE` against the busiest room you expect: one UDP port per participant in voice at
  once, plus headroom (see the sizing section of the deployment recipe).
- Coming from 0.7.0: read the compatibility table in `docs/docker-notes.md` first; it lists every knob that changed a
  default and how to restore the old behaviour.

### One-time migrations

- A recorder or an injector with `RECORD=1`: add `RECORDING_OPT_IN=yes` to its env file and rebuild the connector image.
- Set both secrets in `.env` if either was blank. Nothing else: no file format, room number or on-disk layout changes.
