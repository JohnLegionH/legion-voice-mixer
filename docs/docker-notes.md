# Docker notes — divergences from os-webrtc-janus-docker

This container is designed as a **drop-in replacement** for
[Misterblue/os-webrtc-janus-docker](https://github.com/Misterblue/os-webrtc-janus-docker)
in an existing OpenSimulator voice deployment. It deliberately mirrors that
project's config surface, ports, volume, and env-var conventions so an operator
can swap the image with minimal change.

Reference snapshot mirrored: os-webrtc-janus-docker `VERSION` 1.0.6.

## What is identical (mirrored on purpose)

- **Env-var surface** (`env`): `JS_SERVER_NAME`, `JS_TRANSPORT_HTTP_*`,
  `JS_TRANSPORT_HTTPS_*`, `JS_TRANSPORT_HTTP_ADMIN*`, `JS_TRANSPORT_NANOMSG_ENABLE`,
  `JS_TRANSPORT_WEBSOCKETS_*` — same names, same defaults (HTTP `14223`
  base path `/voice`, admin `14225` base path `/voiceAdmin`, https/ws/nanomsg
  off).
- **Secrets** (`secrets`): `JS_API_TOKEN`, `JS_ADMIN_TOKEN`, applied to the
  config by `updateConfiguration.sh` with the same `sed` edits.
- **Ports**: `EXPOSE 14220-14229` (API) and `10000-10200/udp` (media).
- **Runtime shape**: `docker-compose.yml` runs a single `janus` service with
  `network_mode: host`, mounts `./etc/janus:/opt/janus/etc/janus`, and
  `restart: unless-stopped`. `CMD /opt/janus/bin/janus`.
- **Base image**: `shivanshtalwar0/januscoredeps:${ARCH}` — the same prebuilt
  Janus-core-dependencies image, so the dependency surface matches a known-good
  build (and it already carries the gcc/make/pkg-config needed to build the
  plugin out-of-tree).
- **Scripts**: `build-janus.sh`, `run-janus.sh`, `stop-janus.sh`,
  `restart-janus.sh`, `updateConfiguration.sh` keep the same roles and flow.

## Intentional divergences

1. **Janus is built from a pinned submodule, not a build-time clone of master.**
   os-webrtc-janus-docker runs `git clone -b master … janus-gateway` inside the
   Dockerfile, so the Janus version floats. Here the Dockerfile `COPY`s
   `vendor/janus-gateway` (a git submodule pinned to **v1.4.1**) and builds that.
   Consequence: builds are reproducible, and the plugin is compiled against the
   exact headers it was written for. Run `git submodule update --init --recursive`
   before `./build-janus.sh` (the script checks and errors if the submodule is
   empty).

2. **Adds `janus.plugin.slvoice`.** The Dockerfile builds the plugin out-of-tree
   (`make` against the installed Janus via `pkg-config`) and installs
   `janus_slvoice.so` into `/opt/janus/lib/janus/plugins/`, plus
   `janus.plugin.slvoice.jcfg` into the config dir. os-webrtc-janus-docker ships
   only stock plugins.

3. **Reduced `etc/janus` config set.** os-webrtc-janus-docker vendors the full
   stock set of Janus config files. This repo ships only what a spatial-voice
   server needs and what `updateConfiguration.sh` edits:
   `janus.jcfg`, `janus.transport.http.jcfg`, `janus.transport.websockets.jcfg`,
   `janus.transport.nanomsg.jcfg`, `janus.plugin.audiobridge.jcfg` (kept for A/B
   bring-up — see `protocol-compat.md`), and `janus.plugin.slvoice.jcfg`.
   Because `docker-compose.yml` mounts `./etc/janus` **over** the image's config
   dir, this mounted set is what Janus actually reads. The image still installs
   the full stock config set via `make configs` (used only if you run the image
   without the volume mount). Other stock plugins remain installed in the image;
   with no matching `.jcfg` in the mounted dir they load with defaults and are
   harmless. If you want a leaner runtime, disable them in `janus.jcfg`'s
   `plugins: { disable = "..." }`.
   - The four transport/core files were copied verbatim from
     os-webrtc-janus-docker so the `sed` substitutions line up exactly.

4. **`secrets` is git-ignored; `secrets.sample` is committed.** The reference
   repo commits a `secrets` file with placeholder tokens. Here `secrets` is in
   `.gitignore` and the template is `secrets.sample`; copy it to `secrets` and
   set real tokens (`uuidgen`). `run-janus.sh` refuses to start without `secrets`.

5. **Image name.** Default image/tag is `legion-voice-mixer:latest`
   (`JS_IMAGE` in `env`) instead of `os-webrtc-janus-docker:latest`.

6. **`build-janus.sh` drops the `JANUS_GIT_*` build args** (they no longer apply,
   since Janus comes from the submodule) and keeps only `ARCH` and image naming.

7. **Build-time source normalization + autotools fix** (in the Dockerfile, before
   `autogen.sh`). Needed because the submodule may be checked out on Windows and
   because the base image ships a newer libtool than Janus v1.4.1's build files
   assume:
   - Strip CR from the autotools/shell inputs (`*.sh *.ac *.am *.m4 *.in`). A
     CRLF `autogen.sh` otherwise runs `mkdir -p m4\r` (wrong dir) and every line
     fails as `: not found`.
   - Delete the redundant `ACLOCAL_AMFLAGS = -I m4` from `Makefile.am`; under
     libtool ≥ 2.4.7 it conflicts with `AC_CONFIG_MACRO_DIR([m4])` and aborts
     `autoreconf`. Keeping `AC_CONFIG_MACRO_DIR` alone is sufficient.
   These touch only the in-image copy; the vendored submodule stays pristine.

8. **`CMD` uses exec (JSON) form** — `CMD ["/opt/janus/bin/janus"]` rather than the
   reference's shell form — so Janus is PID 1 and gets SIGTERM directly for a
   clean `docker stop` / `compose down` (no 10s SIGKILL wait).

9. **Plugin Makefile robustness** (not a container divergence, but relevant to the
   build): the out-of-tree `make` inlines `PKG_CONFIG_PATH=$(JANUS_PREFIX)/lib/pkgconfig`
   into the `pkg-config` call (an `export`ed make var is not reliably visible to
   `$(shell)` at read-time) and adds an explicit `-I$(JANUS_PREFIX)/include`
   fallback so `<janus/plugins/plugin.h>` resolves even if `janus-gateway.pc`
   isn't on the pkg-config path.

## Verified

Built and smoke-tested with Docker on this tree:
- image builds clean;
- `janus_slvoice.so` installs to `/opt/janus/lib/janus/plugins/`;
- Janus starts and logs `Loading plugin 'janus_slvoice.so'` →
  `Legion SLVoice mixer initialized! (API v106, ...)`;
- `GET /janus/info` (the same plugin list the Admin API returns) includes
  `"janus.plugin.slvoice":{"name":"Legion SLVoice mixer"}`.

## OpenSim side (unchanged)

Point the OpenSim `os-webrtc-janus.ini` at this server exactly as for
os-webrtc-janus-docker: same `JanusGatewayURI` (`…:14223/voice`),
`JanusGatewayAdminURI` (`…:14225/voiceAdmin`), and matching `APIToken` /
`AdminAPIToken`. To A/B against audiobridge, no container change is needed —
the C# side chooses which plugin package it attaches to.
