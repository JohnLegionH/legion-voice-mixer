# Adapted from os-webrtc-janus-docker by Robert Adams (https://github.com/Misterblue/os-webrtc-janus-docker,
# commit 73d6410), which is licensed under the Mozilla Public License, v. 2.0. The upstream file carries no
# per-file notice of its own; its licence text is in LICENSES/MPL-2.0.txt.
#
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0. If a copy of the MPL
# was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Adapted 2026 by Legion Builds. Changed: Janus is built from a pinned submodule together with the slvoice plugin; the image's start-up scripts are added.
# Container for the Legion SLVoice mixer: Janus Gateway + janus.plugin.slvoice.
#
# This mirrors Misterblue/os-webrtc-janus-docker so it is a drop-in replacement
# in an existing OpenSimulator deployment (same base image, ports, volume, CMD,
# and env-var conventions). Intentional divergences are documented in
# docs/docker-notes.md. The most important one: Janus is built from the PINNED
# git submodule (vendor/janus-gateway @ v1.4.1), not a fresh clone of master at
# build time, so the Janus version is reproducible.

ARG ARCH=x86_64

# Same prebuilt Janus-core-dependencies base image the reference uses, so the
# dependency surface matches a known-good build.
FROM shivanshtalwar0/januscoredeps:${ARCH}

ARG BUILD_DATE=YYYYMMDD.HHMM
ARG BUILD_DAY=YYYYMMDD
ARG JANUS_BUILD_TARGET=Release

# Container labels
ARG IMAGE_OWNER=legion
ARG IMAGE_NAME=legion-voice-mixer
ARG IMAGE_VERSION=latest

LABEL description="Janus Gateway + janus.plugin.slvoice spatial voice mixer for OpenSimulator"
LABEL janus.version="v1.4.1"

# --- Build Janus from the pinned submodule source (NOT a git clone of master) ---
# The build context must include vendor/janus-gateway (the pinned submodule).
COPY vendor/janus-gateway /root/janus-gateway
# Normalise line endings: if the submodule was checked out on Windows the
# autotools/shell inputs arrive as CRLF, which breaks autogen.sh (e.g. it would
# `mkdir -p m4\r`) and any sed matching. Strip CR from the build inputs first.
# The stock *.jcfg.sample configs are included: they become the entrypoint's templates, and a
# Windows checkout would otherwise bake CRLF into the image a Linux checkout builds as LF
# (upstream commits them LF), breaking the byte-exact tests/golden/phase0-knobs-off in CI.
# Then drop the redundant `ACLOCAL_AMFLAGS = -I m4` (it conflicts with
# AC_CONFIG_MACRO_DIR under libtool >= 2.4.7, which is what the base image ships).
RUN cd /root/janus-gateway \
    && find . -type f \( -name '*.sh' -o -name '*.ac' -o -name '*.am' -o -name '*.m4' -o -name '*.in' -o -name '*.jcfg.sample' \) -exec sed -i 's/\r$//' {} + \
    && sed -i '/^ACLOCAL_AMFLAGS = -I m4$/d' Makefile.am \
    && sh autogen.sh \
    && ./configure \
        --prefix=/opt/janus \
        --disable-rabbitmq \
        --disable-mqtt \
        --disable-unix-sockets \
        --disable-nanomsg \
        --disable-plugin-audiobridge \
        --disable-plugin-echotest \
        --disable-plugin-nosip \
        --disable-plugin-recordplay \
        --disable-plugin-sip \
        --disable-plugin-streaming \
        --disable-plugin-textroom \
        --disable-plugin-videocall \
        --disable-plugin-videoroom \
    && make \
    && make install \
    && make configs \
    && test -z "$(ls /opt/janus/lib/janus/plugins)" \
    && test -z "$(ls /opt/janus/lib/janus/transports | grep -E 'nanomsg|pfunix')"
# The configure flags above leave no stock Janus plugin, so the image carries janus.plugin.slvoice only (the lua and
# duktape plugins are opt-in in Janus and stay off); the two `test -z` checks fail the build if one comes back.
# Transports: HTTP carries the sim's signalling and the admin API. WebSockets stays built because it is on by default
# (JS_WS_ENABLED=false switches it off). Nothing uses nanomsg or Unix-socket signalling, so they are not built (the
# line this replaces, --disable-linux-sockets, is not a Janus option, so the Unix-socket transport was built before).
# See docs/docker-notes.md, "Plugins and transports".

# --- Build and install the slvoice plugin out-of-tree against installed Janus ---
# The SLData parser unit tests (`make test`) run here too, so a test failure
# fails the image build.
#
# SLV_EXTRA_CFLAGS is empty for the normal image and "-DSLV_DEBUG_MEDIA" for the
# :debug image (which logs packet-level media detail). The release workflow sets
# it via --build-arg; nothing else in the build depends on it, so the expensive
# Janus layer above is shared/cached across both variants.
ARG SLV_EXTRA_CFLAGS=""
COPY Makefile /root/slvoice/Makefile
COPY src /root/slvoice/src
COPY tests /root/slvoice/tests
RUN cd /root/slvoice \
    && make test JANUS_PREFIX=/opt/janus \
    && make JANUS_PREFIX=/opt/janus EXTRA_CFLAGS="${SLV_EXTRA_CFLAGS}" \
    && make install JANUS_PREFIX=/opt/janus \
    && ls -l /opt/janus/lib/janus/plugins/janus_slvoice.so

# --- Install the plugin's config into the image ---
COPY etc/janus/janus.plugin.slvoice.jcfg /opt/janus/etc/janus/janus.plugin.slvoice.jcfg

# --- Snapshot the stock config as pristine templates ---
# The entrypoint restores from here on every start, then applies env values, so
# config generation is deterministic across restarts and never depends on a
# volume. (`make configs` already populated /opt/janus/etc/janus with the stock
# set, incl. janus.plugin.audiobridge.jcfg for A/B bring-up.)
RUN mkdir -p /opt/janus/share/janus-templates \
    && cp /opt/janus/etc/janus/*.jcfg /opt/janus/share/janus-templates/

# --- Config-generating entrypoint (operators configure via env, not files) ---
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# The public address library, its re-check watcher and the probe they call.
COPY entrypoint/ /usr/local/lib/legion-voice/
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh /usr/local/lib/legion-voice/* \
    && chmod +x /usr/local/bin/docker-entrypoint.sh /usr/local/lib/legion-voice/addr_probe.py \
        /usr/local/lib/legion-voice/public-address-watch.sh /usr/local/lib/legion-voice/selfcheck.py \
        /usr/local/lib/legion-voice/ice_diag.py \
    && ln -sf /usr/local/lib/legion-voice/selfcheck.py /usr/local/bin/legion-voice-selfcheck
# Tests, all loopback or stubbed with no external network; a failure in any fails the image build:
#  - address probe: STUN/DNS codecs, the participant poll;
#  - self-check: every check's branches, the VPS shape, exit codes, the time bound;
#  - ICE diagnostics: retention, the ring bound, the relay/direct caveat, no secrets or SDP, the CLI views;
#  - entrypoint (the nat_1_1_mapping verdict, address discovery and re-check, self-check launch, diagnostics events and the
#    debug-level guard): the baked scripts against the image's own templates, in a scratch dir with a stub Janus
#    and a stub probe.
RUN ADDR_PROBE_DIR=/usr/local/lib/legion-voice python3 /root/slvoice/tests/test_addr_probe.py \
    && ADDR_PROBE_DIR=/usr/local/lib/legion-voice python3 /root/slvoice/tests/test_selfcheck.py \
    && ADDR_PROBE_DIR=/usr/local/lib/legion-voice python3 /root/slvoice/tests/test_ice_diag.py \
    && ENTRYPOINT_UNDER_TEST=/usr/local/bin/docker-entrypoint.sh ENTRYPOINT_LIB_UNDER_TEST=/usr/local/lib/legion-voice \
        bash /root/slvoice/tests/entrypoint_test.sh

# API connections (Janus HTTP/HTTPS/admin/websockets live in 14220-14229)
EXPOSE 14220-14229

# The WebRTC media streams created
EXPOSE 10000-10200/udp

# Advanced users may bind-mount complete *.jcfg files into /opt/janus/etc/janus.d
# to override env/defaults (no VOLUME is declared, to avoid dangling anonymous
# volumes; the entrypoint tolerates the directory being absent).

# Exec form so Janus is PID 1 and receives SIGTERM directly (clean `docker stop`).
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
