# Release notes

Each release lists its **Behaviour changes on upgrade** and **One-time migrations**, even when a list is empty
(the compatibility rule in `docs/docker-notes.md`). The configuration knobs and their defaults are in the knob
register in `docs/docker-notes.md`.

## 1.2.0 (not yet released)

Draft. The plugin and image version strings are not changed until the release is tagged.

### Behaviour changes on upgrade

- **The image carries `janus.plugin.slvoice` only.** It is built without Janus's stock plugins (audio bridge, video
  room, text room, echo test and the rest) and without the nanomsg and Unix-socket transports, and the image build
  fails if any of them reappears. A deployment that uses another stock plugin through this image must stay on the
  previous image for that. The HTTP, admin and WebSockets transports are unchanged.
- **Replies advertise `vis_protocol` 3** (it was 2). A sim that ignores the field is unaffected; the new capability it
  announces is `peer_pos`, below.
- **A start-up WARNING about weak secrets.** A non-empty API secret, admin secret or join-capability secret shorter
  than 32 characters is named in the start-up log (never its value or length). The mixer still starts.

Nothing else changes with the default configuration: with none of the new options set, the generated Janus
configuration is byte-identical to the previous release's.

### Security

- Stock plugins and the unused transports are not in the image (above), so the image exposes less than it did.
- **TLS for the API and the admin API (opt-in).** `JS_TLS_CERT` and `JS_TLS_KEY` turn on https for both
  (`JS_HTTPS_PORT`, `JS_ADMIN_HTTPS_PORT`). A missing or unreadable file refuses to start rather than falling back to
  plain http. `JS_HTTP_PLAIN` and `JS_ADMIN_HTTP_PLAIN` (`true`, `loopback` or `false`) narrow the plain-http
  listeners. All are off by default.
- **Secret-strength check.** The WARNING above, and `JS_WEAK_SECRET_FATAL=true` to make it refuse to start
  (default off).

### Rooms

- An empty room is removed after `JS_EMPTY_ROOM_GRACE_S` seconds (default `60`; `0` keeps rooms until restart).
  This is unchanged from 1.0.0. Since then, a create that is answered 486 (the room already exists) restarts an
  empty room's grace period, so a room is not removed between a create and the join that follows it. A room created with `vis_authority` and left with no listeners is removed after the same grace
  and is armed again by the next join.

### Positions

- A sim can report where it holds each participant with the new admin request `peer_pos` (global centimetres, a speed
  allowance and a tolerance per participant). The mixer compares it every mix tick with the position the viewer
  claims in its SLData and **counts** what it would correct: `would_clamp`, `would_use_sim` and `no_sim`, per
  participant (`pos_observe`) and per room (`visibility.positions` in `handle_info`). It changes no audio.
- A room that never receives `peer_pos` is not observed at all, so a sim that does not send it behaves exactly as
  before. Counters and request limits are in `docs/docker-notes.md`.
- **Enforcing, off by default.** With `JS_POS_MODE=enforce` a room whose sim's latest `peer_pos` asks for enforce is
  corrected: a claim beyond the tolerance is replaced by the sim's point (as a speaker and as a listener), a viewer that
  sent no position is placed at the sim's point instead of being mixed flat, and `JS_POS_FAIL` / `JS_POS_JOIN_GRACE_MS`
  say what happens to a participant the sim has no usable point for. With `JS_POS_MODE` unset the mixer observes exactly
  as before. Counters, the knobs and the rules are in `docs/docker-notes.md`.

### Test harness

- New scenarios S55 to S62 enforce: a mixer left in observe ignores a sim's ask; a speaker's and a listener's false
  position are replaced by the sim's point; a fast mover inside its allowance is not corrected while a walker on the same
  claim is; a viewer that sent no position is placed by the sim; a missing or stale point fails open or closed; and the
  sim can step back to observe. They skip on a mixer that is not set the way they need.
- New scenarios S50 to S54 act as a sim that reports positions: matching claims are accepted and counted as nothing;
  a far claim is counted as would-clamp and the audio equals that of a room without positions; a viewer with no
  position is counted as would-use-sim; an old sim that never sends `peer_pos` leaves behaviour unchanged; and a
  full room of 110 takes one request of 110 entries whole while 129 entries are refused.
- S54 needs a wide RTP range (a scratch mixer with several hundred ports); on a mixer with a small range it skips
  and says why. See the sizing note in the deployment recipes.
- S45 samples the mix after the tick that follows the stale row; the assertion is unchanged.
- The CI workflow runs the unit suites and the harness on every push; the release workflow has a dry-run mode that
  builds both images without pushing (see `.github/workflows/release.yml`).

### What an operator must change

- **Nothing, with the defaults**, unless the deployment relies on a stock Janus plugin or transport that the image no
  longer contains (first item above).
- Recommended: set API, admin and join-capability secrets of at least 32 random characters. Consider `JS_TLS_CERT` /
  `JS_TLS_KEY` if the API or admin API crosses a network you do not control.
- Check the size of `JS_RTP_PORT_RANGE` against the busiest room you expect: one UDP port per participant in voice at
  once, plus headroom (see the sizing section of the deployment recipe).
- An image older than 1.1.0: read the compatibility table in `docs/docker-notes.md` first; it lists every knob that
  changed a default and how to restore the old behaviour.

### One-time migrations

None.
