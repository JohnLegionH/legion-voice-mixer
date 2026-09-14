# Docker notes — image, config precedence, and divergences

This project ships a **prebuilt container image** so operators deploy without
cloning, submodules, or build tools: download `docker-compose.yml` + `env.sample`,
set three values in `.env`, `docker compose up -d`. The image bundles Janus
**v1.4.1** and `janus.plugin.slvoice`.

It remains conceptually a drop-in for
[Misterblue/os-webrtc-janus-docker](https://github.com/Misterblue/os-webrtc-janus-docker)
(same ports, host networking, OpenSim wiring), but the config mechanism is
different: **config is generated inside the container from environment variables
at start**, rather than by editing mounted files. Reference snapshot compared
against: os-webrtc-janus-docker `VERSION` 1.0.6.

## Distribution

- Image: `ghcr.io/johnlegionh/legion-voice-mixer:{version}` and `:latest`.
- Published by `.github/workflows/release.yml` on every `v*` tag (linux/amd64;
  an arm64 matrix entry is present but commented). CI checks out the Janus
  submodule and builds the repo `Dockerfile` unchanged.
- `docker-compose.yml` pulls `:latest` by default; a commented `build:` block is
  provided for developers.

## Configuration precedence

The entrypoint (`docker-entrypoint.sh`, baked at `/usr/local/bin/`) resolves
config in this order, **lowest to highest**:

1. **Default** — the stock Janus `*.jcfg` produced by `make configs`, snapshotted
   into the image at `/opt/janus/share/janus-templates/`. The entrypoint restores
   the active config dir from this snapshot on every start, so results are
   deterministic across restarts.
2. **Environment** — the `JS_*` variables (from `.env`) are applied over the
   defaults with anchored `sed` edits (see the table below).
3. **Mounted override** — any `*.jcfg` bind-mounted into
   `/opt/janus/etc/janus.d/` is copied over the generated config **last**, so it
   wins. This is the escape hatch for advanced users who need full control of a
   config file; enable it via the commented `volumes:` block in
   `docker-compose.yml`.

So: **mounted file > env var > built-in default.** Operators never edit files
inside the container; they set `.env`. There is no `etc/janus` volume mounted
over the whole config dir by default (that was the old model).

### Environment variables → Janus config

| `.env` variable | Janus file → key | Default |
|---|---|---|
| `JS_PUBLIC_IP` | `janus.jcfg` → `nat_1_1_mapping`. An IPv4 literal is used as-is, with no lookup. Since A.1 a hostname here is discovered like `JS_PUBLIC_HOST` (when that is unset) | *(unset)* |
| `JS_PUBLIC_HOST` | a DNS/DDNS hostname. Its public address is discovered at start (`JS_PUBLIC_IP_DISCOVERY`) and goes first in `nat_1_1_mapping`, with a literal `JS_PUBLIC_IP` kept after it. Before A.1 it was resolved by the container resolver and *overrode* `JS_PUBLIC_IP` | *(unset)* |
| `JS_PUBLIC_IP_DISCOVERY` | no jcfg key: how a hostname becomes an address. `auto` = STUN, then DNS via `JS_PUBLIC_IP_DNS_RESOLVER`, then the container resolver; the first public answer wins. `stun` / `dns` = that source only. `static` = the container resolver only, at start, with no re-check (the pre-A.1 behaviour). Any other value is FATAL | `auto` |
| `JS_STUN_SERVER` | no jcfg key: the STUN server discovery asks (`host[:port]` or `stun:host:port`). Janus itself is not given it | `stun.l.google.com:19302` |
| `JS_PUBLIC_IP_DNS_RESOLVER` | no jcfg key: the external DNS resolver discovery asks directly (`host[:port]`), bypassing the container resolver | `1.1.1.1` |
| `JS_PUBLIC_IP_REFRESH_S` | no jcfg key: seconds between re-checks of a discovered address; `0` disables. A change counts after 2 consecutive agreeing checks | `300` |
| `JS_PUBLIC_IP_CHANGE_ACTION` | no jcfg key: what a confirmed change does. `warn` = WARN with the old and new address; `restart` = restart Janus once nobody is connected, which needs the compose restart policy (see "External access"). Any other value is FATAL | `warn` |
| `JS_PUBLIC_IP_RESTART_MAX_WAIT_S` | no jcfg key: with `restart`, the longest wait for zero participants before restarting anyway | `900` |
| `JS_SELFCHECK` | no jcfg key: `on` runs the reachability self-check in the background once Janus starts, printing one `[selfcheck]` block. `off` skips it. Any other value WARNs and means `on` | `on` |
| `JS_SELFCHECK_TIMEOUT_S` | no jcfg key: the self-check's time bound, at start and on demand. `0` or a non-integer WARNs and means `20` | `20` |
| `JS_KEEP_PRIVATE_HOST` | `janus.jcfg` → `keep_private_host` (only when a public address is set) | `true` when public set, else `false` |
| `JS_NAT_EXTRA_IPS` | comma list of IPv4 literals appended to `nat_1_1_mapping` after the discovered and literal addresses (duplicates dropped; a non-IPv4 entry is FATAL) | *(unset)* |
| `JS_API_SECRET` | `janus.jcfg` → `api_secret`. **Required**: empty → FATAL, exit 1 (O-65) | *(none)* |
| `JS_ADMIN_SECRET` | `janus.jcfg` → `admin_secret`. **Required**: empty → FATAL, exit 1 (O-65) | *(none)* |
| `ALLOW_INSECURE_DEV` | no jcfg key — `true` lets the container start with an empty secret. **Dev only** | `false` |
| `JS_RTP_PORT_RANGE` | `janus.jcfg` → `rtp_port_range` | `10000-10200` |
| `JS_SERVER_NAME` | `janus.jcfg` → `server_name` | `GridVoice` |
| `JS_HTTP_PORT` | `janus.transport.http.jcfg` → `port` | `14223` |
| `JS_HTTP_BASEPATH` | `janus.transport.http.jcfg` → `base_path` | `/voice` |
| `JS_ADMIN_PORT` | `janus.transport.http.jcfg` → `admin_port` | `14225` |
| `JS_ADMIN_BASEPATH` | `janus.transport.http.jcfg` → `admin_base_path` | `/voiceAdmin` |
| `JS_ADMIN_BIND` | no jcfg key: the host address `docker-compose.yml` publishes the admin port on (O-55). Unset = all interfaces, with a start-up WARNING | *(unset)* = all interfaces |
| `JS_WS_ENABLED` | `janus.transport.websockets.jcfg` → `ws`; `false` also sets `transports: { disable = "libjanus_websockets.so" }` (O-55) | `true` |
| `JS_WS_PORT` | `janus.transport.websockets.jcfg` → `ws_port` (used when `JS_WS_ENABLED=true`) | `8188` |
| `JS_EMPTY_ROOM_GRACE_S` | no jcfg key — exported by the entrypoint, read by the slvoice plugin at init: a non-permanent room empty this many seconds is destroyed (O-54); `0` disables; an invalid value is ignored with a WARN | `60` |
| `JS_JOIN_MEDIA_TIMEOUT_S` | no jcfg key — exported by the entrypoint, read by the slvoice plugin at init: a participant whose PeerConnection is not up this many seconds after joining is reaped from its room, as a hangup leaves (O-75); `0` disables; an invalid value is ignored with a WARN | `30` |

The entrypoint also forces `http = true` and `admin_http = true`, and sets `ws`
from `JS_WS_ENABLED`.
`sed` substitutions are anchored to line start so `http`/`port`/`base_path`
never collide with `admin_http`/`admin_port`/`admin_base_path`, and `ws`/`ws_port`
never collide with `wss`/`admin_ws`/`admin_ws_port`. The container's internal WS
port tracks `JS_WS_PORT` so the bridge port mapping stays symmetric (host ==
container), matching how `JS_HTTP_PORT`/`JS_ADMIN_PORT` behave.

### Binds, secrets and extra NAT addresses

Config hygiene from the 2026-09-09 audit (W-8, §8; ledger O-55, O-65, O-66).

- **Admin bind (O-55).** `docker-compose.yml` publishes the Admin API on
  `JS_ADMIN_BIND`. Unset (the default) publishes it on all interfaces, exactly as
  before the knob existed, and the entrypoint's first lines say so:
  `[entrypoint] INFO: admin API bind=0.0.0.0 port=… base_path=…`, then
  `[entrypoint] WARNING: admin API is reachable on all interfaces; protected by
  JS_ADMIN_SECRET only — firewall the port or set JS_ADMIN_BIND to the address the
  regionserver uses.` To narrow, set `JS_ADMIN_BIND` to the host address in the
  sim's `JanusGatewayAdminURI`. The sim uses that address even when it runs on the
  same host (Legion Grid: `http://192.168.1.225:24225/voiceAdmin` →
  `JS_ADMIN_BIND=192.168.1.225`), and on a production server it is a different host.
  So `127.0.0.1` cuts the sim off: its visibility/moderation `peer_ctl_batch` calls
  are refused. That was the slice-6m regression that hotfix 6m-1 reverted. The HTTP
  signalling port stays on all interfaces: the sim and the connectors use it, and
  `api_secret` guards it.
- **WebSockets (O-55).** `JS_WS_ENABLED` defaults to `true`: Janus loads the
  transport on `JS_WS_PORT` and compose publishes it, as before the knob existed.
  The entrypoint prints `[entrypoint] INFO: websockets transport enabled=<value>
  port=<port>`. The OpenSim sim uses HTTP only, so `JS_WS_ENABLED=false` is an
  opt-in narrowing: the entrypoint writes `ws = false` and adds the transport to
  `transports: { disable }`, so Janus does not load it. The compose port mapping
  stays but answers nothing; delete that line in your compose file to unpublish it.
- **Fail-closed secrets (O-65).** The container exits 1 at start when either secret
  is empty or whitespace, naming the keys and the override:
  `[entrypoint] FATAL: JS_API_SECRET and JS_ADMIN_SECRET empty; refusing to start.
  Set JS_API_SECRET=<the sim's APIToken> and JS_ADMIN_SECRET=<the sim's
  AdminAPIToken> in .env, or set ALLOW_INSECURE_DEV=true in .env to start without
  them (dev only: the API is then open).` A blank secret was always an open API: an
  empty `JS_API_SECRET` left the Janus API open, and an empty `JS_ADMIN_SECRET` kept
  the stock template's well-known `admin_secret`. So this is kept as a deliberate
  **behaviour change on upgrade** (see below). **`ALLOW_INSECURE_DEV=true` is a
  dev-only override**: it starts anyway, with a WARNING. Use it only on a throwaway
  local box that nothing else can reach, never on the grid host.
- **`nat_1_1_mapping` verdict and `JS_NAT_EXTRA_IPS` (A.1).** The entrypoint builds
  the mapping in this order:
  1. the address discovered for a hostname (see "External access" below);
  2. any literal `JS_PUBLIC_IP`;
  3. `JS_NAT_EXTRA_IPS`.

  It then judges the **final** mapping, not any one lookup:
  - **No public address:** two ERROR lines, `nat_1_1_mapping=<m> has no public address: off-LAN
    viewers will fail ICE`, then the remediation.
  - **A public address:** one INFO line naming the public addresses.
  - **An address in `100.64.0.0/10`:** a CGNAT WARNING. Direct paths fail, port forwarding cannot
    help, and a TURN server is required.

  Private addresses next to a public one (a LAN literal, or a hairpinned DNS answer)
  are expected with `keep_private_host`, and are no longer warned about one by one.
  Janus 1.x splits the list and advertises a host candidate per address, so LAN
  and off-LAN viewers both get a reachable one. `JS_NAT_EXTRA_IPS` still appends
  literals, but a hand-added router address goes stale when a dynamic IP changes;
  discovery exists so it is not needed.
- **Resolution record (A.1).** Every start writes one line, `[entrypoint] ADDRESS_RESOLUTION
  {json}`, and the same JSON to `/run/legion-voice/public-address.json` in the container.
  - **Fields:** `event`, `time`, `host`, `host_var`, `discovery`, `sources[]`
    (`name`/`via`/`status`/`address`/`class`), `winner`, `literal_ips`, `extra_ips`,
    `nat_1_1_mapping`, `keep_private_host` and `verdict` (`public` | `cgnat` | `no_public` | `none`).
  - **The re-check watcher** logs the same line with `event: change` on a confirmed change. It
    rewrites the file after every check (`event: refresh`) with `running_address`,
    `observed_address` and `agreeing_checks`.
  - **To read it:** `docker exec <container> cat /run/legion-voice/public-address.json`.
- **SDP logging (O-66).** The plugin's full offer/answer SDP dumps are at
  `LOG_VERB` (debug level 5+), with `a=ice-ufrag`, `a=ice-pwd` and the
  `a=fingerprint` value redacted. Each join still logs one INFO line:
  `Answer sent: audio Opus pt=<pt> ptime=20 maxptime=20; m=application answered=YES`.
- **Tests.** `bash tests/entrypoint_test.sh` runs the entrypoint without Docker, against:
  - a scratch config dir and a stub Janus (the `JANUS_CONF_DIR`, `JANUS_TEMPLATE_DIR`,
    `JANUS_OVERRIDE_DIR` and `JANUS_BIN` overrides exist only for it);
  - a stub address probe, so discovery, the verdict, the state file and the re-check watcher are
    tested with no network.

  `python3 tests/test_addr_probe.py` covers the STUN and DNS codecs, and runs both probes end to
  end against loopback UDP servers. The image build runs both suites against the baked scripts and
  templates; a failure fails the build.

### Startup self-check (A.2)

A.1 proves what the mixer advertises. The self-check tests what it can find out from inside the
container about reachability, for a home host behind consumer NAT and for a VPS with a public
address alike.

**When it runs.** Once Janus is started, in the background (`JS_SELFCHECK=on`, the default), within
`JS_SELFCHECK_TIMEOUT_S` (default 20 s). A `timeout` backstop stops it 5 s past that bound. It never
delays Janus.

**Output.** One block, every line prefixed `[selfcheck]`, from `===== BEGIN` to `===== END`:
```
docker compose logs janus | grep '^\[selfcheck\]'
```

**On demand.** `docker exec <container> legion-voice-selfcheck [--json] [--timeout S]`. On Git Bash, set
`MSYS_NO_PATHCONV=1`. Every run writes the JSON report to `/run/legion-voice/selfcheck.json`.

**Exit status**, for monitoring:

| Exit | Meaning |
|---|---|
| `0` | no FAIL and no INCONCLUSIVE: every check passed, or only WARN advice remains |
| `1` | any FAIL |
| `2` | any INCONCLUSIVE and no FAIL |
| `64` | usage error |

A standing WARN, such as C6 on a server with no TURN, does not make monitoring alarm.

**Report format.** Each check reports:
- an id and a title;
- `PASS` | `FAIL` | `WARN` | `INCONCLUSIVE`;
- what it observed;
- for anything not PASS, a remediation naming the knob, router forward or firewall rule.

INCONCLUSIVE means the check could not decide, and is never reported as PASS.

| Check | What it tests | Results |
|---|---|---|
| C1 advertised address is public | the A.1 state file's `nat_1_1_mapping` | PASS: a public address. **CGNAT (100.64.0.0/10) is its own FAIL**, remediation TURN or a public IPv4. No public address: FAIL naming `JS_PUBLIC_HOST` / `JS_PUBLIC_IP` / `JS_NAT_EXTRA_IPS`. A confirmed address change not yet applied: WARN. Unreadable state: INCONCLUSIVE |
| C2 media path | STUN Binding requests from sockets bound to the lowest, middle and highest port of `JS_RTP_PORT_RANGE` (the nearest free port if one is in use), to `JS_STUN_SERVER` | Every mapped port equals its local port: PASS. On a VPS whose public address is on the interface, the report says "no address translation" and gives no NAT warning. Any port remapped: FAIL "port-remapping or symmetric NAT: some users will have no direct path, TURN required". No answer while the same server answers an ephemeral port: FAIL "outbound UDP from the media range is blocked". No answer from any port: INCONCLUSIVE. **This proves the outbound mapping only.** A missing router forward or host firewall rule for inbound UDP can still pass C2; the external check is A.6's recipes |
| C3 RTP range | binds up to 9 sampled ports of the range, and compares `JS_RTP_PORT_RANGE` with `rtp_port_range` in the generated `janus.jcfg` | PASS: bindable (a port in use by Janus media is fine) and matching. FAIL: a port the container cannot bind, an invalid range, or a mismatch (a mounted override). INCONCLUSIVE: config unreadable, or every sampled port in use |
| C4 signalling | `GET http://127.0.0.1:<JS_HTTP_PORT><JS_HTTP_BASEPATH>/info`; at start it waits for Janus within the bound | PASS: Janus `server_info` with `janus.plugin.slvoice`. FAIL: HTTP error (base path), not Janus, plugin missing, or connection refused. INCONCLUSIVE: connected but no reply within the bound |
| C5 admin API | reports `JS_ADMIN_BIND`:`JS_ADMIN_PORT`, then connects to that port at each public address in the mapping | PASS: unreachable, **the desired result**. FAIL: the Janus admin API answers there. WARN: the port accepts but is not Janus. INCONCLUSIVE: no public address. It deliberately does not probe the LAN address, which the sim uses. From inside, a router that does not hairpin TCP also reads as unreachable, so confirm from outside (A.6) |
| C6 STUN/TURN | `stun_server` / `turn_server` / `turn_rest_api` in the nat section of `janus.jcfg` | PASS: TURN configured. WARN with no TURN, naming who has no path: viewers on UDP-blocking networks always. Behind a CGNAT server (C1), viewers behind symmetric NAT or CGNAT have no path at all. After outbound port remapping (C2), those viewers have a direct path only if inbound UDP reaches the server, which C2 cannot see. On a server C2 found directly addressed, the WARN names no NAT types. INCONCLUSIVE: config unreadable. The hook A.3/A.4 fill in |

**Inputs.** The self-check reads `/run/legion-voice/effective-config.json`, which the entrypoint writes
at start. It holds the effective, non-secret values of `JS_RTP_PORT_RANGE`, `JS_HTTP_PORT`,
`JS_HTTP_BASEPATH`, `JS_ADMIN_PORT`, `JS_ADMIN_BASEPATH`, `JS_ADMIN_BIND`, `JS_STUN_SERVER` and
`JS_SELFCHECK_TIMEOUT_S`, because a `docker exec` shell does not see the entrypoint's defaults.

**Side effects:**
- a few hundred milliseconds of UDP binds on sampled RTP ports;
- three STUN requests from the media range, plus one from an ephemeral port;
- one TCP connection per public address to the admin port.

## Configuration compatibility rule

Rebuilding or pulling a new image onto an existing `.env` must not silently change
what a running install does. Slice 6m broke this: its `JS_ADMIN_BIND=127.0.0.1`
default cut the regionserver off the Admin API, and `JS_WS_ENABLED=false` removed a
WebSocket transport that had been on and published. Hotfix 6m-1 reverted both
defaults and wrote down the rule:

- **A new knob's default reproduces the behaviour of the release before it
  existed.**
- **Narrowing is always opt-in**: binds, transports, TLS, allow-lists. The operator
  sets the narrower value; an upgrade never does.
- **The entrypoint prints the effective value of every security- or
  connectivity-relevant knob in its first lines** (before anything can fail), so a
  wrong value is visible in `docker compose logs janus` before anyone logs in.
  Secrets are shown as `set`/`EMPTY`, never printed.
- **Every release note lists "Behaviour changes on upgrade" and "One-time
  migrations"**, even when a list is empty. The release notes are
  `docs/RELEASES.md`; the two sections below summarise them for configuration.

A change that must break compatibility for safety (O-65 below) is allowed only if
it fails loud at start, names the exact keys to set and the override, and appears
under "Behaviour changes on upgrade".

### Knob register

Every operator knob, its default, what the install did before the knob existed, and
the commit that introduced it. `46335f5` (2026-08-12) is the first env-driven image;
its knobs have no "before".

| Variable | Default | Behaviour before the knob existed | Since |
|---|---|---|---|
| `JS_SERVER_NAME` | `GridVoice` | original knob | `46335f5` |
| `JS_HTTP_PORT` | `14223` | original knob | `46335f5` |
| `JS_HTTP_BASEPATH` | `/voice` | original knob | `46335f5` |
| `JS_ADMIN_PORT` | `14225` | original knob | `46335f5` |
| `JS_ADMIN_BASEPATH` | `/voiceAdmin` | original knob | `46335f5` |
| `JS_RTP_PORT_RANGE` | `10000-10200` | original knob | `46335f5` |
| `JS_PUBLIC_IP` | *(unset)*: no `nat_1_1_mapping` | original knob | `46335f5` |
| `JS_API_SECRET` | *(none; required)* | original knob; until `b96e7b3` a blank value started with the Janus API open | `46335f5`; required since `b96e7b3` |
| `JS_ADMIN_SECRET` | *(none; required)* | original knob; until `b96e7b3` a blank value kept the template's well-known `admin_secret` | `46335f5`; required since `b96e7b3` |
| `SLV_ECHO_AUTOSTART` | `false` | no echo | `e483799` |
| `JS_WS_PORT` | `8188` | WebSockets on the stock port 8188 (host networking) | `92d7d73` |
| `JS_PUBLIC_HOST` | *(unset)* | only `JS_PUBLIC_IP` could set the mapping | `fc39fb5` |
| `JS_KEEP_PRIVATE_HOST` | `true` when a public address is set, else `false` | Janus default `false`: the mapping replaced host candidates with the public address. **Pre-rule exception**: the `true` default added the private candidate on upgrade | `fc39fb5` |
| `JS_EMPTY_ROOM_GRACE_S` | `60` | rooms persisted until the mixer restarted. **Pre-rule exception, kept**: an empty non-permanent room is destroyed after 60 s, and the sim self-heals (a join to the destroyed room answers 485 → the sim forgets and re-creates it). `0` restores the old behaviour | `1859a7f` |
| `JS_ADMIN_BIND` | *(unset)*: all interfaces, with a start-up WARNING | admin API published on all interfaces | `b96e7b3` (default `127.0.0.1`; restored to all interfaces by hotfix 6m-1) |
| `JS_WS_ENABLED` | `true` | WebSockets transport loaded and published | `b96e7b3` (default `false`; restored to `true` by hotfix 6m-1) |
| `JS_NAT_EXTRA_IPS` | *(unset)* | `nat_1_1_mapping` held only the single public address | `b96e7b3` |
| `ALLOW_INSECURE_DEV` | `false` | no secret check: blank secrets started. The `false` default *is* the O-65 behaviour change | `b96e7b3` |
| `JS_JOIN_MEDIA_TIMEOUT_S` | `30` | a joined participant whose PeerConnection never came up stayed in the room — holding a mix slot and blocking the room's grace destroy — until its Janus session ended, which the sim's long-poll could postpone indefinitely. **Deliberate behaviour change (O-75)**: such a participant is now reaped after 30 s; `0` restores the old behaviour | `3618e9a` (released in 1.1.0) |
| `JS_PUBLIC_IP_DISCOVERY` | `auto` | `JS_PUBLIC_HOST` was resolved once by the container resolver and overrode `JS_PUBLIC_IP`; that is `static`. **Deliberate behaviour change (A.1)**: `auto` puts the STUN-discovered public address first, and keeps a literal `JS_PUBLIC_IP`. A hairpinned DDNS name then no longer yields a private-only mapping. `static` restores the old resolution, but keeps the literal | slice A.1 |
| `JS_STUN_SERVER` | `stun.l.google.com:19302` | no STUN query (the container made no outbound STUN request) | slice A.1 |
| `JS_PUBLIC_IP_DNS_RESOLVER` | `1.1.1.1` | no external DNS query (the container resolver only) | slice A.1 |
| `JS_PUBLIC_IP_REFRESH_S` | `300` | no re-check: a mid-run IP change went unnoticed. The default adds a background re-check that only logs; `0` restores the old behaviour | slice A.1 |
| `JS_PUBLIC_IP_CHANGE_ACTION` | `warn` | nothing acted on an IP change. `warn` only logs; `restart` is opt-in | slice A.1 |
| `JS_PUBLIC_IP_RESTART_MAX_WAIT_S` | `900` | n/a (used only with `JS_PUBLIC_IP_CHANGE_ACTION=restart`) | slice A.1 |
| `JS_SELFCHECK` | `on` | no self-check. The default adds a background, diagnostics-only run after start: one `[selfcheck]` log block, brief UDP binds and STUN requests from the RTP range, and a TCP probe of the admin port at the public address. Nothing is configured or blocked by it. `off` restores the old behaviour | slice A.2 |
| `JS_SELFCHECK_TIMEOUT_S` | `20` | n/a (the self-check's bound) | slice A.2 |

| `RECORDING_OPT_IN` (connector env: `connectors/recorder/recorder.env`, and `injector.env` when `RECORD=1`) | *(unset)*: off, so the peer refuses to start | the recorder started and recorded with no opt-in. **Deliberate behaviour change (SC-96)**: an existing recorder, or an injector with `RECORD=1`, now exits 1 at start until the operator sets `yes`. Printed as the peer's first start-up line (the connector's own entrypoint, not the janus container banner) | `ae159b0` |

`JANUS_CONF_DIR`, `JANUS_TEMPLATE_DIR`, `JANUS_OVERRIDE_DIR`, `JANUS_BIN`, `SLV_LIB_DIR`,
`SLV_ADDR_PROBE`, `SLV_ADDR_STATE_FILE` and the watcher's `SLV_ADDR_WATCH_MAX_CHECKS`,
`SLV_ADDR_SLEEP`, `SLV_ADDR_RESTART_CMD`, `SLV_ADDR_POLL_S`, `SLV_ADDR_PROBE_TIMEOUT_S`, and the
self-check's `SLV_EFFECTIVE_CONFIG`, `SLV_SELFCHECK_CMD`, `SLV_SELFCHECK_FILE`, `SLV_SELFCHECK_BIND` and
`SLV_SELFCHECK_CONNECT_MAP` are test seams for `tests/entrypoint_test.sh`, `tests/test_addr_probe.py`
and `tests/test_selfcheck.py`, not operator knobs.

## Behaviour changes on upgrade

- **Slice A.2: startup self-check**
  - **Every start** now runs `legion-voice-selfcheck` in the background (`JS_SELFCHECK=on`) and logs
    one `[selfcheck]` block. It only reports; it changes no configuration and never blocks or
    delays Janus.
  - **Traffic it adds:**
    - brief UDP binds on sampled RTP ports;
    - three outbound STUN requests from the media range, plus one from an ephemeral port;
    - one TCP connection per public address to the admin port.
  - **New files:** `/run/legion-voice/effective-config.json` (non-secret effective values) and
    `/run/legion-voice/selfcheck.json`.
  - **A.1 restart action:** a participant poll that fails, is unauthorised (wrong `JS_API_SECRET`) or
    returns anything but a count is now logged, `[address-watch] WARNING: participant poll failed
    (<reason>); not counted as zero participants`. It was already never read as zero; now the
    reason is visible.

- **Slice A.1: public address discovery**
  - **A DDNS hostname now yields the router's public address (deliberate).** A hostname in
    `JS_PUBLIC_HOST` (or `JS_PUBLIC_IP`) is discovered by STUN first, then an external resolver,
    then the container resolver. Before, the container resolver alone decided, and a hairpinning
    router answered it with the LAN address, so off-LAN viewers failed ICE. A literal `JS_PUBLIC_IP`
    is now kept next to the discovered address instead of being overridden.
    `JS_PUBLIC_IP_DISCOVERY=static` restores the old resolution.
  - **Outbound queries at start and every 300 s:** UDP to `JS_STUN_SERVER` (default
    `stun.l.google.com:19302`) and to `JS_PUBLIC_IP_DNS_RESOLVER` (default `1.1.1.1:53`). A firewall
    that blocks them leaves discovery on the next source, which is logged.
  - **The verdict judges the final `nat_1_1_mapping`.** The per-address "is private/loopback"
    WARNING is gone:
    - no public address: an ERROR with remediation;
    - a public address: an INFO line;
    - a `100.64.0.0/10` address: a CGNAT WARNING.
  - **New log lines:** the discovery chain, one `ADDRESS_RESOLUTION` JSON line, and `[address-watch]`
    lines when a re-check sees a different address. A new state file sits at
    `/run/legion-voice/public-address.json`.
  - **A hostname that no source can resolve still refuses to start.** The message is now
    `FATAL: could not discover an IPv4 address for JS_PUBLIC_HOST=…`.

- **Untagged V-2 to V-4 (2026-09-14): `34935a2` (O-80), `fc48ea6` (O-83), `4fbfaf4` (SC-87), `ae159b0` (SC-96)**
  - **A recorder, or an injector with `RECORD=1`, refuses to start until `RECORDING_OPT_IN=yes`**
    is set in its env file (SC-96). It logs `recorder: RECORDING_OPT_IN=<unset> (recording opt-in,
    default off)` and then a FATAL line. Set it only after the room has been told it is being
    recorded.
  - A room created with `spatial_audio=false` is a flat mix (O-80). A room created without the key
    stays spatial (O-83); V-2 alone briefly made it flat.
  - Voice dots: each listener receives `{p:0, v:false}` for sources it cannot hear (SC-87). This
    changes the protocol payload only; there is no config change.
- **`3618e9a` (O-75), released in 1.1.0**
  - **A participant whose PeerConnection never comes up is reaped** `JS_JOIN_MEDIA_TIMEOUT_S`
    (default 30 s) after joining, logging `[slvoice] <display> reaped from room <id>: no media <n>s
    after join`. Before, it held its roster row and mix slot, and kept the room from its grace destroy,
    for as long as its Janus session lived. This default deliberately changes behaviour: it is the fix.
    `JS_JOIN_MEDIA_TIMEOUT_S=0` restores the old behaviour. A participant that had media and lost it is
    unaffected (the O-56 hangup path).
- **`b96e7b3` (slice 6m) + hotfix 6m-1**
  - **Blank `JS_API_SECRET` or `JS_ADMIN_SECRET` now refuses to start (O-65).**
    `[entrypoint] FATAL: … empty; refusing to start. Set JS_API_SECRET=<the sim's
    APIToken> and JS_ADMIN_SECRET=<the sim's AdminAPIToken> in .env, or set
    ALLOW_INSECURE_DEV=true in .env …`. Set both secrets. `ALLOW_INSECURE_DEV=true`
    is for a throwaway dev box only.
  - The plugin's full offer/answer SDP dumps moved from INFO to VERB, with ICE
    credentials redacted (O-66). Log content only.
  - New start-up log lines: the effective-value INFO block, the "admin API is
    reachable on all interfaces" WARNING when `JS_ADMIN_BIND` is unset, and the
    private-only `nat_1_1_mapping` WARNING. Log content only.
  - Slice 6m alone also bound admin to `127.0.0.1` and turned WebSockets off.
    Hotfix 6m-1 reverted both, so upgrading straight to 6m-1 changes neither.
- **`1859a7f` (slice 5)**: empty non-permanent rooms are destroyed after
  `JS_EMPTY_ROOM_GRACE_S` (60 s); `0` restores rooms that live until restart.
- **`fc39fb5` (v0.3.1)**: `keep_private_host` defaults to `true` when a public
  address is set.

## One-time migrations

- **Slice A.1:** none required. An install that added its router's public IPv4 to
  `JS_NAT_EXTRA_IPS` only to work around a hairpinned DDNS name can remove it once the start log
  shows discovery finding that address (the static value goes stale when the IP changes).
- **Regionserver build 1.1.392+ (connectors):** connector NPC ids are now derived
  and stable. On the first restart after upgrading, each NPC's id changes once, so
  re-edit `DISPLAY` in `recorder.env` / `injector.env` one last time. See
  `connectors/README.md`.
- Mixer configuration: none for hotfix 6m-1. An install upgrading from before
  `b96e7b3` with a blank secret must set it (above).

### External access: public hostname and split-horizon ICE

For outside testers, the server must advertise a reachable public address in its
ICE candidates (`nat_1_1_mapping`). Two knobs support this:

- **`JS_PUBLIC_HOST`**: a DNS/DDNS hostname (e.g. `legiongrid.ddns.net`). `JS_PUBLIC_IP` may
  also hold one when `JS_PUBLIC_HOST` is unset. `nat_1_1_mapping` needs an **IP literal**, so the
  entrypoint **discovers the name's public address at start** (slice A.1).

  **Sources.** With `JS_PUBLIC_IP_DISCOVERY=auto` (the default) it asks three sources, in order,
  and logs what each returned:
  1. **STUN** (`JS_STUN_SERVER`): the server-reflexive address, which is what the internet sees
     this host's traffic come from. Behind a home router that is the router's public IPv4, the
     address off-LAN viewers must reach.
  2. **DNS against `JS_PUBLIC_IP_DNS_RESOLVER`** (default `1.1.1.1`), sent straight to that
     resolver.
  3. **The container resolver,** last. A router that hairpins its own DDNS name, or a
     split-horizon/hosts pin, answers it with the LAN address. Legion Grid: `legiongrid.ddns.net`
     → `192.168.1.225`, which is why the pre-A.1 resolution produced a private-only mapping.

  **Winner.** The first public answer wins. If no source gives a public answer, the first answer of
  any kind is used and the verdict says so. The start log reads:

  ```
  [entrypoint] INFO: discovery source stun (stun.l.google.com:19302) -> 174.82.163.190 (public)
  [entrypoint] INFO: discovery source dns (resolver 1.1.1.1) -> 174.82.163.190 (public)
  [entrypoint] INFO: discovery source container (container resolver) -> 192.168.1.225 (private)
  [entrypoint] INFO: discovery winner: stun -> 174.82.163.190
  ```

  **Other modes.** `stun` and `dns` use that source only. `static` uses the container resolver
  alone, once, as before A.1. If no source answers, the container **refuses to start** (`FATAL:
  could not discover an IPv4 address for JS_PUBLIC_HOST=…`) rather than coming up silently broken.
  A literal `JS_PUBLIC_IP` is kept after the discovered address (for LAN viewers), then
  `JS_NAT_EXTRA_IPS`.

  **Re-check.** Every `JS_PUBLIC_IP_REFRESH_S` (default 300; `0` disables; never with `static`) a
  background watcher runs the same chain.
  - **When a change counts:** only after **two consecutive checks agree** on the same new address.
  - **What never counts:** a failed lookup; a non-public answer while the running address is public
    (e.g. STUN down and DNS hairpinned). Both also break the streak.
  - **`JS_PUBLIC_IP_CHANGE_ACTION=warn`** (default): logs `[address-watch] WARNING: public address
    changed: <old> -> <new>` once per new address. Janus keeps advertising the old mapping until
    you restart it.
  - **`JS_PUBLIC_IP_CHANGE_ACTION=restart`:** the watcher polls the client API until no
    participant is connected in any room, then stops Janus (SIGTERM to PID 1) so the container
    restarts and re-discovers. It waits at most `JS_PUBLIC_IP_RESTART_MAX_WAIT_S` (default 900),
    then restarts anyway and logs that it took the outage.
  - **A failed poll never counts as zero.** The poll uses `JS_API_SECRET`. A poll that fails, is
    refused (403: wrong or missing secret) or returns no count is logged with its reason, and the
    wait goes on to the same bound.

  **Restart policy this requires:** `restart: unless-stopped` (as shipped in
  `docker-compose.yml`) or `restart: always`. Janus exits 0 on SIGTERM. So under `on-failure`, or
  with no restart policy, the container stays stopped and voice is down until someone starts it.
  The same policy also re-runs discovery after a host reboot or Docker restart.

- **`JS_KEEP_PRIVATE_HOST`** → Janus `keep_private_host`. When a public mapping
  is in effect, `nat_1_1_mapping` normally **rewrites** every host candidate to
  the public address. That breaks **LAN** viewers whose router lacks NAT
  hairpin/loopback (common on home routers): they'd be told to reach the server
  at its public IP and can't loop back to it. Setting `keep_private_host = true`
  makes Janus advertise **both** the private and the public host candidate, so:
  - **LAN viewers** pick the private candidate and connect directly — no hairpin
    needed;
  - **external viewers** pick the public candidate.

  This is why the default is **`true` whenever a public address is set**. The
  tradeoff: the container's private IP is included in candidates handed to
  external peers (minor information disclosure), and there are marginally more
  candidates to gather and connectivity-check. Set `JS_KEEP_PRIVATE_HOST=false`
  to advertise only the public candidate (e.g. a pure cloud host with no LAN
  viewers), accepting that same-LAN viewers then need working NAT loopback.
  `keep_private_host` is only written when a public address is set; with none it
  is left at the Janus default.

## Windows/Docker Desktop (networking)

`docker-compose.yml` uses **bridge networking with explicit `ports:` mappings**,
not `network_mode: host`. This is the portable default and the only mode that
works on **Docker Desktop for Windows/macOS**.

**Why host networking fails there.** On Docker Desktop the containers run inside
a Linux VM (WSL2 on Windows, LinuxKit on macOS). `network_mode: host` binds the
Janus ports on *that VM's* network namespace, not on the Windows/macOS host, so
`curl http://localhost:14223/voice/info` from the host gets no route. Verified on
this tree: with host networking the container binds and answers `/voice/info`
*from inside the container*, but the same request from the Windows host fails.
Bridge mode + `ports:` publishes the ports through Docker's proxy onto host
`localhost`, which does work.

**Ports published** (all driven by the `.env` values, so host == container):

| Purpose | Port(s) | Proto | Env var |
|---|---|---|---|
| HTTP signalling (`/voice`) | `14223` | tcp | `JS_HTTP_PORT` |
| Admin/monitor API (`/voiceAdmin`) | `14225` | tcp | `JS_ADMIN_PORT` |
| WebSockets signalling | `8188` | tcp | `JS_WS_PORT` |
| WebRTC media (RTP/RTCP) | `10000-10200` | udp | `JS_RTP_PORT_RANGE` |

**ICE / `nat_1_1_mapping` under bridge mode.** This is the one behavioural
gotcha. Under bridge networking Janus lives on the container's private network
(e.g. `172.17.0.x`) and, left alone, gathers ICE **host candidates** with that
private IP — unreachable from anything off the container, so media never flows
even though the UDP ports are published. The fix is the existing `JS_PUBLIC_IP`
path: it sets `nat_1_1_mapping` in `janus.jcfg`, which makes Janus **advertise
that address in its candidates instead** of the private one. So under bridge
mode `JS_PUBLIC_IP` is effectively **required for working media** — set it to the
host's reachable IPv4 (a LAN address for LAN clients, the public IPv4 for
internet clients). Note this is not a Windows-only concern: any NAT'd host needs
it, but bridge networking makes it mandatory even for same-host reachability.

Signalling is unaffected: `GET /voice/info` (the verify step) returns correctly
with `JS_PUBLIC_IP` unset, because that path is plain published TCP and does not
depend on ICE. Only the WebRTC media leg needs the mapping.

**RTP range cost.** Docker publishes each UDP port in the range via a separate
`docker-proxy`, so a wide `JS_RTP_PORT_RANGE` means many proxies and slower
`compose up`. The default 201-port range is fine; keep it modest. On a native
**Linux** host you can instead uncomment `network_mode: host` in
`docker-compose.yml` — it skips the proxy entirely and lets Janus read the host
interfaces directly for ICE. That block is kept commented for exactly this case.

## Divergences from os-webrtc-janus-docker

1. **Prebuilt image, env-driven config.** The reference expects operators to
   clone the repo, edit mounted `etc/janus` files (via `updateConfiguration.sh`),
   and `docker build` locally. Here operators pull a published image and set
   `.env`; the in-container entrypoint generates the config. No `.sh` scripts,
   no build tools, no git on the operator path.

2. **Janus is built from a pinned submodule**, not a build-time `git clone` of
   `master`. `vendor/janus-gateway` is pinned to **v1.4.1**, so builds are
   reproducible and the plugin is compiled against the headers it was written for.

3. **Adds `janus.plugin.slvoice`** — built out-of-tree and installed into
   `/opt/janus/lib/janus/plugins/`, with `janus.plugin.slvoice.jcfg` in the
   config dir. `janus.plugin.audiobridge` remains available (from `make configs`)
   for A/B bring-up — see `protocol-compat.md`.

4. **Secrets/config via `.env`**, not a committed `secrets` file. Variables are
   `JS_API_SECRET` / `JS_ADMIN_SECRET` (mapping to OpenSim `APIToken` /
   `AdminAPIToken`). `.env` is git-ignored; `env.sample` is the template.

5. **Default base paths are the OpenSim-native `/voice` and `/voiceAdmin`**
   (matching os-webrtc-janus-docker), so a stock OpenSim `[JanusWebRtcVoice]`
   config connects with zero edits and the verify step is `curl .../voice/info`.
   For a generic Janus setup, set `JS_HTTP_BASEPATH=/janus` and
   `JS_ADMIN_BASEPATH=/admin` to restore the Janus stock paths.

6. **Build-time source normalization + autotools fix** (Dockerfile, before
   `autogen.sh`): strip CR from autotools/shell inputs (a Windows checkout of the
   submodule is CRLF, which breaks `autogen.sh`), and delete the redundant
   `ACLOCAL_AMFLAGS = -I m4` (conflicts with `AC_CONFIG_MACRO_DIR` under
   libtool ≥ 2.4.7, which the base image ships). Touches only the in-image copy.

7. **`ENTRYPOINT` (exec form)** runs `docker-entrypoint.sh`, which `exec`s Janus
   as PID 1 so it receives SIGTERM directly for a clean `docker stop`.

8. **Plugin Makefile robustness**: the out-of-tree `make` inlines
   `PKG_CONFIG_PATH` into the `pkg-config` call (an `export`ed make var isn't
   reliably visible to `$(shell)` at read-time) and adds an explicit
   `-I$(JANUS_PREFIX)/include` fallback.

## Verified

Built and smoke-tested with Docker on this tree:
- image builds clean;
- `janus_slvoice.so` installs to `/opt/janus/lib/janus/plugins/`;
- with only `docker-compose.yml` + `.env`, `docker compose up -d` starts Janus,
  the entrypoint generates config from `.env`, and
  `GET http://localhost:14223/voice/info` lists
  `"janus.plugin.slvoice":{"name":"Legion SLVoice mixer"}`.

## OpenSim side

Point `os-webrtc-janus.ini` at this server: `JanusGatewayURI` =
`http://THIS_HOST:14223/<JS_HTTP_BASEPATH>`, `JanusGatewayAdminURI` =
`http://THIS_HOST:14225/<JS_ADMIN_BASEPATH>`, with `APIToken` / `AdminAPIToken`
equal to `JS_API_SECRET` / `JS_ADMIN_SECRET`. The defaults (`/voice`,
`/voiceAdmin`) already match the os-webrtc-janus convention, so a stock OpenSim
`[JanusWebRtcVoice]` config connects with zero edits. A/B against audiobridge
needs no container change — the C# side chooses the plugin package it attaches to.
