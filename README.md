# legion-voice-mixer — `janus.plugin.slvoice`

A [Janus Gateway](https://janus.conf.meetecho.com/) plugin that will become a
**spatial voice mixer for OpenSimulator grids**, speaking the **Second Life
WebRTC voice protocol**. It is the server-side counterpart to the OpenSim
`os-webrtc-janus` addon, intended as a drop-in alternative to the stock
`janus.plugin.audiobridge`.

> **Status: Phase 0 — scaffold only.** The plugin loads and registers cleanly
> but contains no audio logic yet (the mixer is Phase 1). It is, however,
> already deployable as a running Janus voice server.

The published container image bundles Janus **v1.4.1** and this plugin, so
operators deploy it **without cloning, submodules, or build tools**.

---

## Install

You need a host with **Docker** and two files from this repo. Nothing else — no
git, no build tools.

> **Windows:** install **Docker Desktop with the WSL2 backend**. Janus is
> Linux-native; it only runs in a Linux container. Everything below is the same
> once Docker Desktop is running.

**1. Download the two files** (into an empty directory):

```sh
curl -LO https://raw.githubusercontent.com/JohnLegionH/legion-voice-mixer/main/docker-compose.yml
curl -LO https://raw.githubusercontent.com/JohnLegionH/legion-voice-mixer/main/env.sample
```

**2. Create your `.env` and edit the three required values:**

```sh
cp env.sample .env
```

Then edit `.env` and set:

| Variable | What to put |
|---|---|
| `JS_PUBLIC_IP` | This server's public IPv4 address (advertised to voice clients). |
| `JS_API_SECRET` | A shared secret; must match the OpenSim `os-webrtc-janus` `APIToken`. |
| `JS_ADMIN_SECRET` | Admin secret; must match `AdminAPIToken`. |

(The remaining values have sensible defaults — see the comments in the file.)

**3. Start it:**

```sh
docker compose up -d
```

Docker pulls `ghcr.io/johnlegionh/legion-voice-mixer:latest` and starts Janus.
The container generates its Janus config from your `.env` at startup — you never
edit files inside the container.

**4. Verify** — one request to the Janus info endpoint should list the plugin:

```sh
curl http://localhost:14223/janus/info
```

You should see `"janus.plugin.slvoice":{"name":"Legion SLVoice mixer"...}` in the
`plugins` object. That's a working voice server.

> **Connecting OpenSim:** point `os-webrtc-janus.ini` at
> `http://THIS_HOST:14223/janus` (and admin `:14225/admin`) with the same
> secrets. The `os-webrtc-janus` project conventionally uses the base paths
> `/voice` and `/voiceAdmin`; to match it, set `JS_HTTP_BASEPATH=/voice` and
> `JS_ADMIN_BASEPATH=/voiceAdmin` in `.env`.

To stop / update:

```sh
docker compose down
docker compose pull && docker compose up -d   # upgrade to a newer image
```

---

## Development

Everything below is for **building the image or the plugin from source**.
Operators do not need any of it.

### Prerequisites

```sh
git clone https://github.com/JohnLegionH/legion-voice-mixer.git
cd legion-voice-mixer
git submodule update --init --recursive     # pulls Janus v1.4.1 into vendor/
```

Janus Gateway is vendored as a git submodule at `vendor/janus-gateway`, pinned
to release tag **`v1.4.1`** (plugin API version **106**). All C is written
against that tag's `src/plugins/plugin.h`, the API authority.

### Build the container locally

```sh
./build-janus.sh          # builds Janus v1.4.1 from the submodule + the plugin
cp env.sample .env        # set values as in Install
docker compose up -d      # uses the local image (same tag), no pull
```

`build-janus.sh` tags the image with the same name `docker-compose.yml`
expects, so `docker compose up -d` picks up your local build. Alternatively,
uncomment the `build:` block in `docker-compose.yml`.

### Build just the plugin (against an installed Janus)

Janus ships `janus-gateway.pc`, so the plugin builds with a plain Makefile:

```sh
make JANUS_PREFIX=/opt/janus            # -> janus_slvoice.so
sudo make install JANUS_PREFIX=/opt/janus
```

The `.so` is dlopen()ed by the Janus core; its `janus_*` symbols resolve from
the core at load time, so it links only glib + jansson.

### Release / publishing

Pushing a `v*` tag triggers `.github/workflows/release.yml`, which builds the
image and pushes `ghcr.io/johnlegionh/legion-voice-mixer:{version}` and
`:latest` (linux/amd64). That published image is what the Install section pulls.

### Layout

```
src/janus_slvoice.c      the plugin (full vtable; scaffold stubs)
src/mixer/mixer.h         Phase-1 per-region tick model (declarations only)
Makefile                  out-of-tree plugin build (pkg-config against Janus)
Dockerfile                Janus (from the pinned submodule) + plugin + entrypoint
docker-entrypoint.sh      generates Janus *.jcfg from env at container start
docker-compose.yml        operator deployment (pulls the published image)
env.sample                operator config template (copy to .env)
etc/janus/                janus.plugin.slvoice.jcfg (baked into the image)
build-janus.sh            developer local image build
.github/workflows/        release.yml — tag-triggered image publish
vendor/janus-gateway      Janus submodule, pinned @ v1.4.1
```

---

## `docs/` is the authority

- `docs/webrtc-voice-spec.md` — the Second Life WebRTC voice protocol spec.
- `docs/parcel-voice-semantics.md` — OpenSim parcel/estate/presence voice semantics.
- `docs/current-architecture.md` — current OpenSim C# voice implementation
  (incl. the **§3 message table** that fixes the wire shapes this plugin accepts).
- `docs/protocol-compat.md` — the audiobridge-superset compatibility constraint
  and its expiry.
- `docs/docker-notes.md` — image/config-precedence details and divergences from
  `Misterblue/os-webrtc-janus-docker`.

> The first three are surveys of the OpenSim C# side, dropped into `docs/`
> separately; the last two are maintained in this repo.

## License

GPLv3 (matches Janus Gateway, which this plugin links against at the API level).
