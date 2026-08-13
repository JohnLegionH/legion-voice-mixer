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
| `JS_API_SECRET` | `janus.jcfg` → `api_secret` (only if set) | *(unset)* |
| `JS_ADMIN_SECRET` | `janus.jcfg` → `admin_secret` (only if set) | *(unset)* |
| `JS_RTP_PORT_RANGE` | `janus.jcfg` → `rtp_port_range` | `10000-10200` |
| `JS_SERVER_NAME` | `janus.jcfg` → `server_name` | `GridVoice` |
| `JS_HTTP_PORT` | `janus.transport.http.jcfg` → `port` | `14223` |
| `JS_HTTP_BASEPATH` | `janus.transport.http.jcfg` → `base_path` | `/voice` |
| `JS_ADMIN_PORT` | `janus.transport.http.jcfg` → `admin_port` | `14225` |
| `JS_ADMIN_BASEPATH` | `janus.transport.http.jcfg` → `admin_base_path` | `/voiceAdmin` |
| `JS_WS_PORT` | `janus.transport.websockets.jcfg` → `ws_port` | `8188` |

The entrypoint also forces `http = true`, `admin_http = true`, and `ws = true`.
`sed` substitutions are anchored to line start so `http`/`port`/`base_path`
never collide with `admin_http`/`admin_port`/`admin_base_path`, and `ws`/`ws_port`
never collide with `wss`/`admin_ws`/`admin_ws_port`. The container's internal WS
port tracks `JS_WS_PORT` so the bridge port mapping stays symmetric (host ==
container), matching how `JS_HTTP_PORT`/`JS_ADMIN_PORT` behave.

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
