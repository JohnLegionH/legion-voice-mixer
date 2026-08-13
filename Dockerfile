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
RUN cd /root/janus-gateway \
    && sh autogen.sh \
    && ./configure \
        --prefix=/opt/janus \
        --disable-rabbitmq \
        --disable-mqtt \
        --disable-linux-sockets \
    && make \
    && make install \
    && make configs

# --- Build and install the slvoice plugin out-of-tree against installed Janus ---
COPY Makefile /root/slvoice/Makefile
COPY src /root/slvoice/src
RUN cd /root/slvoice \
    && make JANUS_PREFIX=/opt/janus \
    && make install JANUS_PREFIX=/opt/janus \
    && ls -l /opt/janus/lib/janus/plugins/janus_slvoice.so

# --- Install the plugin's config into the image (used when no volume is mounted) ---
COPY etc/janus/janus.plugin.slvoice.jcfg /opt/janus/etc/janus/janus.plugin.slvoice.jcfg

# The configuration directory mounted when run (same as the reference)
VOLUME /opt/janus/etc/janus/

# API connections (Janus HTTP/HTTPS/admin/websockets live in 14220-14229)
EXPOSE 14220-14229

# The WebRTC media streams created
EXPOSE 10000-10200/udp

CMD /opt/janus/bin/janus
