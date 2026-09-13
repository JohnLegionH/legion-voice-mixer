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
| `JS_PUBLIC_IP` | `janus.jcfg` → `nat_1_1_mapping` (only if set) | *(unset)* |
| `JS_PUBLIC_HOST` | resolved to IPv4 at start → `nat_1_1_mapping` (overrides `JS_PUBLIC_IP`) | *(unset)* |
| `JS_KEEP_PRIVATE_HOST` | `janus.jcfg` → `keep_private_host` (only when a public address is set) | `true` when public set, else `false` |
| `JS_NAT_EXTRA_IPS` | comma list of IPv4 literals appended to `nat_1_1_mapping` after the public address (duplicates dropped; a non-IPv4 entry is FATAL) | *(unset)* |
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
- **`nat_1_1_mapping` guard and `JS_NAT_EXTRA_IPS`.** After resolving
  `JS_PUBLIC_HOST` (or taking `JS_PUBLIC_IP`), the entrypoint WARNs for each mapped
  address that is RFC 1918 (`10/8`, `172.16/12`, `192.168/16`) or loopback
  (`127/8`). When *no* mapped address is public it adds
  `off-LAN viewers will fail ICE`: they receive only unreachable candidates. This
  happens when a hosts-file or split-horizon DNS pin makes the DDNS name resolve to
  the LAN IP inside the container (Legion Grid today: `legiongrid.ddns.net` →
  `192.168.1.225`). `JS_NAT_EXTRA_IPS` (comma list) appends addresses, e.g.
  `JS_NAT_EXTRA_IPS=<router public IPv4>` gives `nat_1_1_mapping =
  "192.168.1.225,<public>"`. Janus 1.x splits the list and advertises a host
  candidate per address, so LAN and off-LAN viewers both get a reachable one. A
  static public IP goes stale when a dynamic address changes; restart the container
  with the new value. With a public mapping and no extras, the generated config is
  unchanged.
- **SDP logging (O-66).** The plugin's full offer/answer SDP dumps are at
  `LOG_VERB` (debug level 5+), with `a=ice-ufrag`, `a=ice-pwd` and the
  `a=fingerprint` value redacted. Each join still logs one INFO line:
  `Answer sent: audio Opus pt=<pt> ptime=20 maxptime=20; m=application answered=YES`.
- **Tests.** `bash tests/entrypoint_test.sh` runs the entrypoint without Docker
  against a scratch config dir and a stub Janus (the `JANUS_CONF_DIR`,
  `JANUS_TEMPLATE_DIR`, `JANUS_OVERRIDE_DIR` and `JANUS_BIN` overrides exist only for
  it). The image build runs it against the baked script and templates.

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
  migrations"**, even when a list is empty.

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

`JANUS_CONF_DIR`, `JANUS_TEMPLATE_DIR`, `JANUS_OVERRIDE_DIR` and `JANUS_BIN` are test
seams for `tests/entrypoint_test.sh`, not operator knobs.

## Behaviour changes on upgrade

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

- **Regionserver build 1.1.392+ (connectors):** connector NPC ids are now derived
  and stable. On the first restart after upgrading, each NPC's id changes once, so
  re-edit `DISPLAY` in `recorder.env` / `injector.env` one last time. See
  `connectors/README.md`.
- Mixer configuration: none for hotfix 6m-1. An install upgrading from before
  `b96e7b3` with a blank secret must set it (above).

### External access: public hostname and split-horizon ICE

For outside testers, the server must advertise a reachable public address in its
ICE candidates (`nat_1_1_mapping`). Two knobs support this:

- **`JS_PUBLIC_HOST`** — a DNS/DDNS hostname (e.g. `legiongrid.ddns.net`).
  `nat_1_1_mapping` requires an **IP literal**, not a hostname, so the entrypoint
  **resolves the name to an IPv4 once at container start** (`getent ahostsv4`,
  first A record) and uses that, **overriding `JS_PUBLIC_IP`**. If the name
  fails to resolve the container **refuses to start** with a loud
  `[entrypoint] FATAL: could not resolve JS_PUBLIC_HOST=…` message, rather than
  coming up silently broken.

  Because resolution happens **only at start**, a dynamic IP that changes while
  the container runs leaves the old address baked into `nat_1_1_mapping` and
  external voice breaks until you re-resolve: `docker compose restart` (or
  `docker compose up -d` after the change). The `restart: unless-stopped` policy
  in `docker-compose.yml` covers the host-reboot / Docker-restart case — the
  container comes back and re-resolves automatically — but it does **not** react
  to a mid-run IP change on its own. For frequently-changing IPs, pair this with
  an external "restart on IP change" hook (e.g. your DDNS updater) if needed.

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
