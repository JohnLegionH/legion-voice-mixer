# legion-voice-mixer — `janus.plugin.slvoice`

A [Janus Gateway](https://janus.conf.meetecho.com/) plugin that will become a
**spatial voice mixer for OpenSimulator grids**, speaking the **Second Life
WebRTC voice protocol**. It is the server-side counterpart to the OpenSim
`os-webrtc-janus` addon, intended as a drop-in alternative to the stock
`janus.plugin.audiobridge` used today.

> **Status: Phase 0 — scaffold only.** The plugin implements the full Janus
> plugin vtable and loads/registers cleanly, but contains **no audio logic and
> no protocol logic beyond parsing and logging request verbs**. The mixer
> itself (per-region 20ms tick, spatial mixing) is Phase 1. See
> `src/mixer/mixer.h` for the model.

## Pinned Janus version

Janus Gateway is vendored as a git submodule at `vendor/janus-gateway`, pinned
to release tag **`v1.4.1`** (plugin API version **106**). All C is written
against that tag's `src/plugins/plugin.h`, which is the API authority — not
remembered signatures.

```
git clone <this repo>
cd legion-voice-mixer
git submodule update --init --recursive
```

## `docs/` is the authority

The `docs/` directory is the **protocol and feature authority** for this
project. Consult it before changing behaviour:

- `docs/webrtc-voice-spec.md` — the Second Life WebRTC voice protocol spec.
- `docs/parcel-voice-semantics.md` — OpenSim parcel/estate/presence voice
  semantics (sim-side survey).
- `docs/current-architecture.md` — the current OpenSim C# voice implementation
  (region + Robust side), including the **§3 message table** that fixes the
  wire shapes this plugin must accept.
- `docs/protocol-compat.md` — **compatibility constraint**: `handle_message`
  starts as a superset of `janus.plugin.audiobridge`'s join/leave/configure
  request shapes (same field names the C# side sends today) so the OpenSim side
  can A/B between audiobridge and slvoice by config during bring-up. Extensions
  go in new fields only. Includes the constraint's planned expiry.
- `docs/docker-notes.md` — how the container mirrors, and where it diverges
  from, `Misterblue/os-webrtc-janus-docker`.

> The first three files are surveys of the OpenSim C# side and are dropped into
> `docs/` separately; the last two are maintained in this repo.

## Layout

```
src/janus_slvoice.c     the plugin (full vtable; scaffold stubs)
src/mixer/mixer.h        Phase-1 per-region tick model (declarations only)
Makefile                 out-of-tree plugin build (pkg-config against Janus)
Dockerfile               Janus (from the pinned submodule) + this plugin
docker-compose.yml       single "janus" service, host networking
env / secrets.sample     runtime config (mirrors os-webrtc-janus-docker)
etc/janus/               Janus config incl. janus.plugin.slvoice.jcfg
*.sh                     build/run/stop/restart/updateConfiguration helpers
vendor/janus-gateway     Janus submodule, pinned @ v1.4.1
```

## Building

### The plugin, out-of-tree (against an installed Janus)

Janus ships `janus-gateway.pc`, so the plugin builds with a plain Makefile:

```
make JANUS_PREFIX=/opt/janus            # -> janus_slvoice.so
sudo make install JANUS_PREFIX=/opt/janus
```

The `.so` is a Janus plugin dlopen()ed by the core; its `janus_*` symbols are
resolved from the core at load time, so it links only glib + jansson.

### The container (Janus + plugin)

```
git submodule update --init --recursive
cp secrets.sample secrets      # then set real tokens (uuidgen)
./build-janus.sh               # builds Janus v1.4.1 from the submodule + the plugin
./run-janus.sh                 # applies env/secrets, starts the container
```

## Acceptance (Phase 0)

- Container builds clean.
- Janus starts.
- Startup log shows `janus.plugin.slvoice` registered.
- `janus_admin` lists the plugin.

No audio. No protocol handling beyond logging parsed message verbs. The
scaffold is deliberately boring — the interesting work is Phase 1.

## License

GPLv3 (matches Janus Gateway, which this plugin links against at the API level).
