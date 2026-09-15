# legion-voice-mixer — `janus.plugin.slvoice`

A [Janus Gateway](https://janus.conf.meetecho.com/) plugin that is a **spatial
voice mixer for OpenSimulator grids**, speaking the **Second Life WebRTC voice
protocol**. It is the server-side counterpart to the OpenSim `os-webrtc-janus`
addon and a drop-in alternative to the stock `janus.plugin.audiobridge`.

> **Status: 1.1.0, in production on Legion Grid.** Each viewer holds one
> PeerConnection (Opus + the SLData **DataChannel**) per region room.
> - **Mixing:** every room runs a 20 ms tick that builds a **per-listener
>   N-minus-one stereo mix**.
> - **Spatialisation:** from the viewers' SLData geometry (camera leash, distance
>   cull with hysteresis, distance attenuation, constant-power azimuth panning),
>   honouring each listener's per-source mute and gain.
> - **Sim control:** the sim sends **visibility exclusions and moderation mutes**
>   as Admin API `peer_ctl_batch` messages; an excluded source disappears from
>   both audio and roster.
> - **Room lifecycle:** empty rooms are destroyed after `JS_EMPTY_ROOM_GRACE_S`.
> - **Not implemented:** HRTF/ITD.
> - **Diagnostic:** echo-to-self (`SLV_ECHO_AUTOSTART` or `{"echo":true}`).
>
> Release history and upgrade notes: [`docs/RELEASES.md`](docs/RELEASES.md).

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
curl http://localhost:14223/voice/info
```

You should see `"janus.plugin.slvoice":{"name":"Legion SLVoice mixer"...}` in the
`plugins` object. That's a working voice server.

> **Connecting OpenSim:** the defaults match the `os-webrtc-janus` convention,
> so a stock OpenSim `[JanusWebRtcVoice]` config connects with zero edits — point
> `os-webrtc-janus.ini` at `http://THIS_HOST:14223/voice` (and admin
> `:14225/voiceAdmin`) with the same secrets. For a generic Janus setup instead,
> set `JS_HTTP_BASEPATH=/janus` and `JS_ADMIN_BASEPATH=/admin` in `.env`.

> **Deploying for real:** follow one of the two recipes. Each lists the router forwards, firewall rules and
> every `.env` value for its target, and ends with the self-check board you should see:
> [`docs/recipe-home-hosted.md`](docs/recipe-home-hosted.md) (a home server behind consumer NAT with DDNS) or
> [`docs/recipe-vps.md`](docs/recipe-vps.md) (a VPS or colo box with a public IPv4).

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

### CI and releases

- **`.github/workflows/ci.yml`** runs on every push to `main` and every pull
  request.
  - It builds the image, which runs the C unit suites and
    `tests/entrypoint_test.sh`.
  - It starts the image through `docker-compose.yml` with a job-written `.env`
    (explicit knobs, random secrets).
  - It runs the two-peer integration harness (`tests/integration`, every scenario
    except S4). Any FAIL fails the job.
- **Pushing a `v*` tag** triggers `.github/workflows/release.yml`. It builds the
  image and pushes `ghcr.io/johnlegionh/legion-voice-mixer:{version}` and
  `:latest`, plus the `-DSLV_DEBUG_MEDIA` `:debug` variant (linux/amd64). That
  published image is what the Install section pulls.
- **Every release** is recorded in `docs/RELEASES.md` with its "Behaviour changes
  on upgrade" and "One-time migrations". This follows the compatibility rule in
  `docs/docker-notes.md`.

### Layout

```
src/janus_slvoice.c       the plugin: negotiation, audiobridge-superset protocol, rooms,
                          per-room mix tick, SLData, peer_ctl_batch, grace destroy, diagnostics
src/sldata.{c,h}          SLData data-channel parser and per-field merge (jansson-only)
src/visbatch.{c,h}        Admin API peer_ctl_batch parser (visibility exclusions, moderation mutes)
src/deferred.{c,h}        per-room store of batch columns for listeners not yet joined
src/roster.h              the single exclusion predicate shared by audio and roster
src/sdp_redact.h          ICE-credential redaction for the VERB SDP dumps
src/mixer/mix.{c,h}       N-minus-one summing, gain and RMS
src/mixer/vec3.h, azimuth.h, pan.h   geometry, azimuth and constant-power pan maths
src/mixer/mixer.h         design record of the tick model (realised in janus_slvoice.c)
tests/test_*.c            unit suites (`make test`; also run by the image build)
tests/entrypoint_test.sh  docker-entrypoint.sh tests (bash, no Docker; also run by the image build)
tests/integration/        two-peer aiortc harness against a live mixer (`make integration`; CI)
tests/bench_tick.c        tick-cost load harness (`make bench_tick`; not a test)
Makefile                  out-of-tree plugin build (pkg-config against Janus)
Dockerfile                Janus (from the pinned submodule) + plugin + entrypoint
docker-entrypoint.sh      generates Janus *.jcfg from env at container start
docker-compose.yml        operator deployment (pulls the published image)
env.sample                operator config template (copy to .env)
etc/janus/                janus.plugin.slvoice.jcfg (baked into the image)
build-janus.sh            developer local image build
connectors/               recorder and injector peers (profile-gated compose services)
.github/workflows/        ci.yml (push/PR: build + suites + harness), release.yml (tag: publish)
vendor/janus-gateway      Janus submodule, pinned @ v1.4.1
```

---

## `docs/` is the authority

- `docs/voice/webrtc-voice-spec.md` — the Second Life WebRTC voice protocol spec.
- `docs/voice/parcel-voice-semantics.md` — OpenSim parcel/estate/presence voice semantics.
- `docs/voice/current-architecture.md` — current OpenSim C# voice implementation
  (incl. the **§3 message table** that fixes the wire shapes this plugin accepts).
- `docs/protocol-compat.md` — the audiobridge-superset compatibility constraint
  and its expiry.
- `docs/docker-notes.md` — image/config-precedence details, the configuration
  compatibility rule and knob register, and divergences from
  `Misterblue/os-webrtc-janus-docker`.
- `docs/RELEASES.md` — every deployed release with its O-items, behaviour changes
  on upgrade and one-time migrations.
- `docs/sldata-extensions.md` — the data-channel SLData field set, per-source
  mute/gain, and the slvoice `echo` diagnostic extension.
- `docs/voice-mute-wiring.md` — how the moderation mute channel is wired.
- `docs/phase1-bringup.md` — the original in-world bring-up runbook (CHECK 1
  session holds, CHECK 2 echo, CHECK 3 two-party mix); still the reference for
  diagnosing a single viewer.
- `tests/integration/README.md` — the two-peer integration harness and its
  scenarios.

> The OpenSim C# surveys and the voice programme ledger live in `docs/voice/`
> (mirrored from the sim tree); the other docs are maintained here. The plugin
> was reconciled against them: message shapes/error codes against
> `current-architecture.md` §3, and the SLData field set / Opus fmtp against
> `webrtc-voice-spec.md` §9/§6/§4.2.

## License

GPLv3 (matches Janus Gateway, which this plugin links against at the API level).
