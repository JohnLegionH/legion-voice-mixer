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
| `JS_API_SECRET` | `janus.jcfg` → `api_secret` (only if set) | *(unset)* |
| `JS_ADMIN_SECRET` | `janus.jcfg` → `admin_secret` (only if set) | *(unset)* |
| `JS_RTP_PORT_RANGE` | `janus.jcfg` → `rtp_port_range` | `10000-10200` |
| `JS_SERVER_NAME` | `janus.jcfg` → `server_name` | `GridVoice` |
| `JS_HTTP_PORT` | `janus.transport.http.jcfg` → `port` | `14223` |
| `JS_HTTP_BASEPATH` | `janus.transport.http.jcfg` → `base_path` | `/voice` |
| `JS_ADMIN_PORT` | `janus.transport.http.jcfg` → `admin_port` | `14225` |
| `JS_ADMIN_BASEPATH` | `janus.transport.http.jcfg` → `admin_base_path` | `/voiceAdmin` |

The entrypoint also forces `http = true` and `admin_http = true`. `sed`
substitutions are anchored to line start so `http`/`port`/`base_path` never
collide with `admin_http`/`admin_port`/`admin_base_path`.

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
