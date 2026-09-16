# Out-of-tree build for the janus.plugin.slvoice shared object.
#
# Janus is autotools/Makefile-based and installs a pkg-config file
# (janus-gateway.pc) precisely so external plugins can build like this. There
# is no CMake in Janus, so a plain Makefile driven by pkg-config is the
# idiomatic out-of-tree path.
#
# The plugin is a shared object dlopen()ed by the Janus core. Undefined
# janus_* symbols (JANUS_LOG's janus_log, janus_plugin_result_new, the
# refcount helpers, ...) are resolved at load time from the core process, so
# we do NOT link against Janus here (janus-gateway.pc intentionally exports an
# empty Libs:). We only compile against its headers and link glib + jansson.
#
# Usage:
#   make                      # build janus_slvoice.so
#   make install              # install into $(PLUGINDIR)
#   make clean
#
# Point at a non-default Janus prefix with JANUS_PREFIX=/opt/janus (default).

PLUGIN   := janus_slvoice
TARGET   := $(PLUGIN).so

JANUS_PREFIX    ?= /opt/janus
PLUGINDIR       ?= $(JANUS_PREFIX)/lib/janus/plugins
PKG_CONFIG_PATH ?= $(JANUS_PREFIX)/lib/pkgconfig

# Inline PKG_CONFIG_PATH into the pkg-config call: an `export`ed make variable is
# not reliably visible to $(shell ...) at read-time, whereas this always is.
# PKG_CONFIG_PATH is *prepended* to pkg-config's default search path, so the
# system glib/jansson .pc files are still found.
PKGCONFIG := PKG_CONFIG_PATH="$(PKG_CONFIG_PATH)" pkg-config

# janus-gateway pulls in glib-2.0 and jansson via its Requires:; we name them
# explicitly too so a build still works if only their .pc files are present.
# Phase 1B echoes audio, so opus (decode/encode) is a dependency again.
# openssl: slice 0.4's join capability verifies an HMAC-SHA256 with a constant-time compare
# (src/joincap.c). Janus already links OpenSSL for DTLS, so this adds no new runtime dependency.
PKGS := janus-gateway glib-2.0 jansson opus openssl

PKG_CFLAGS := $(shell $(PKGCONFIG) --cflags $(PKGS) 2>/dev/null)
PKG_LIBS   := $(shell $(PKGCONFIG) --libs glib-2.0 jansson opus openssl 2>/dev/null) -lm

CFLAGS  ?= -O2 -g
# EXTRA_CFLAGS is an append-only hook for extra defines passed from the caller,
# e.g. `make EXTRA_CFLAGS=-DSLV_DEBUG_MEDIA` for the packet-level media logging
# build (the :debug image). It is appended (not overriding CFLAGS) so the base
# flags below always apply.
EXTRA_CFLAGS ?=
# -I$(JANUS_PREFIX)/include is an explicit fallback so <janus/plugins/plugin.h>
# resolves even if janus-gateway.pc is not on the pkg-config path.
CFLAGS  += -std=gnu11 -fPIC -Wall -Wextra -Wno-unused-parameter -I$(JANUS_PREFIX)/include $(PKG_CFLAGS) $(EXTRA_CFLAGS)
LDFLAGS += -shared
LDLIBS  += $(PKG_LIBS)

# Phase 2 adds the per-room N-minus-one mixer; the pure mixing math lives in
# src/mixer/mix.c (Janus/Opus-free) so it is shared with the unit test below.
SRCS := src/janus_slvoice.c src/sldata.c src/visbatch.c src/deferred.c src/joincap.c src/mixer/mix.c
OBJS := $(SRCS:.c=.o)

# Unit tests. Both are plain C binaries with NO Janus link:
#  - test_sldata: the SLData parser (needs jansson).
#  - test_mix:    the N-minus-one mixing math (libm only).
TEST_SLDATA_BIN  := tests/test_sldata
TEST_SLDATA_SRCS := tests/test_sldata.c src/sldata.c
TEST_MIX_BIN     := tests/test_mix
TEST_MIX_SRCS    := tests/test_mix.c src/mixer/mix.c
TEST_VISBATCH_BIN  := tests/test_visbatch
TEST_VISBATCH_SRCS := tests/test_visbatch.c src/visbatch.c
# test_deferred: the per-room deferred visibility/mute store (libc only, no Janus/glib/jansson).
TEST_DEFERRED_BIN  := tests/test_deferred
TEST_DEFERRED_SRCS := tests/test_deferred.c src/deferred.c
TEST_ROSTER_BIN    := tests/test_roster
TEST_ROSTER_SRCS   := tests/test_roster.c
TEST_AZIMUTH_BIN   := tests/test_azimuth
TEST_AZIMUTH_SRCS  := tests/test_azimuth.c
TEST_PAN_BIN       := tests/test_pan
TEST_PAN_SRCS      := tests/test_pan.c
# test_sdp_redact: O-66 SDP log redaction (header-only sdp_redact.h; libc only).
TEST_REDACT_BIN    := tests/test_sdp_redact
TEST_REDACT_SRCS   := tests/test_sdp_redact.c
# bench_tick is a load HARNESS, not a unit test — deliberately NOT in `test:`.
# bench_tick.c #includes janus_slvoice.c to reach the static tick, so the plugin
# .c is NOT listed here (it would double-define); the other pure .c deps are.
BENCH_TICK_BIN     := tests/bench_tick
BENCH_TICK_SRCS    := tests/bench_tick.c src/sldata.c src/visbatch.c src/deferred.c src/mixer/mix.c
BENCH_CFLAGS       := $(CFLAGS) -std=gnu11 -Wall -Wextra -Wno-unused-parameter -Wno-unused-function -I$(JANUS_PREFIX)/include $(PKG_CFLAGS) $(EXTRA_CFLAGS)
# --unresolved-symbols=ignore-all: janus_slvoice.c compiles in init (janus_config_*)
# and negotiate (janus_sdp_*), which the harness NEVER calls; leaving them undefined
# avoids 14 fragile signature-matched stubs. Safe because those paths are never
# invoked at runtime — a bad assumption would segfault the run immediately.
BENCH_LIBS         := $(PKG_LIBS) -pthread -Wl,--unresolved-symbols=ignore-all
# test_room_lifecycle: slice-5 room lifecycle (O-68 reset_room_state, O-54 grace destroy, O-56
# hangup leaves, O-67 shared teardown). It reaches the plugin's statics by #including
# janus_slvoice.c exactly like bench_tick, so it builds with the bench flags and links -- but
# unlike bench_tick it IS a unit test and runs in `make test`.
TEST_LIFECYCLE_BIN  := tests/test_room_lifecycle
TEST_LIFECYCLE_SRCS := tests/test_room_lifecycle.c src/sldata.c src/visbatch.c src/deferred.c src/mixer/mix.c
TEST_VISAUTH_BIN  := tests/test_visauth
TEST_VISAUTH_SRCS := tests/test_visauth.c src/sldata.c src/visbatch.c src/deferred.c src/joincap.c src/mixer/mix.c
# test_joincap: slice 0.4's join capability (src/joincap.c). Links OpenSSL and libc only.
TEST_JOINCAP_BIN  := tests/test_joincap
TEST_JOINCAP_SRCS := tests/test_joincap.c src/joincap.c
TEST_SSL_CFLAGS   := -std=gnu11 -Wall -Wextra -g $(shell $(PKGCONFIG) --cflags openssl 2>/dev/null)
TEST_SSL_LIBS     := $(shell $(PKGCONFIG) --libs openssl 2>/dev/null)
TEST_CFLAGS  := -std=gnu11 -Wall -Wextra -g $(shell $(PKGCONFIG) --cflags jansson 2>/dev/null)
TEST_LIBS    := $(shell $(PKGCONFIG) --libs jansson 2>/dev/null) -lm
# roster.h is glib-only (no jansson/Janus); its test links glib.
TEST_GLIB_CFLAGS := -std=gnu11 -Wall -Wextra -g $(shell $(PKGCONFIG) --cflags glib-2.0 2>/dev/null)
TEST_GLIB_LIBS   := $(shell $(PKGCONFIG) --libs glib-2.0 2>/dev/null)

.PHONY: all install clean test bench_tick integration
# `integration` sits above `all`; without this a bare `make` (the Dockerfile's plugin build) ran it.
.DEFAULT_GOAL := all

# Two-peer integration harness (O-73, tests/integration/README.md). Runs against a LIVE mixer and
# is NOT part of `test`: the image build never runs it. Needs Python 3.12 with
# tests/integration/requirements.txt; pass flags through, e.g. INTEGRATION_ARGS="--only S3 --no-restart".
PYTHON           ?= python3
INTEGRATION_ARGS ?=
integration:
	$(PYTHON) -m tests.integration.run $(INTEGRATION_ARGS)

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

# Build and run ALL unit tests. `make test` is a required gate: it is also run
# during the Docker image build (see Dockerfile), so a failure fails the image.
test: $(TEST_SLDATA_BIN) $(TEST_MIX_BIN) $(TEST_VISBATCH_BIN) $(TEST_DEFERRED_BIN) $(TEST_ROSTER_BIN) $(TEST_AZIMUTH_BIN) $(TEST_PAN_BIN) $(TEST_LIFECYCLE_BIN) $(TEST_REDACT_BIN) $(TEST_VISAUTH_BIN) $(TEST_JOINCAP_BIN)
	./$(TEST_SLDATA_BIN)
	./$(TEST_MIX_BIN)
	./$(TEST_VISBATCH_BIN)
	./$(TEST_DEFERRED_BIN)
	./$(TEST_ROSTER_BIN)
	./$(TEST_AZIMUTH_BIN)
	./$(TEST_PAN_BIN)
	./$(TEST_LIFECYCLE_BIN)
	./$(TEST_REDACT_BIN)
	./$(TEST_VISAUTH_BIN)
	./$(TEST_JOINCAP_BIN)

$(TEST_JOINCAP_BIN): $(TEST_JOINCAP_SRCS) src/joincap.h
	$(CC) $(TEST_SSL_CFLAGS) -o $@ $(TEST_JOINCAP_SRCS) $(TEST_SSL_LIBS)

# test_visauth: Phase 0 slice 0.3 (keying, the decision table, staleness, replies, shadow counters). Like
# test_room_lifecycle it #includes janus_slvoice.c and builds with the bench flags.
$(TEST_VISAUTH_BIN): $(TEST_VISAUTH_SRCS) src/janus_slvoice.c src/visauth.h
	$(CC) $(BENCH_CFLAGS) -o $@ $(TEST_VISAUTH_SRCS) $(BENCH_LIBS)

$(TEST_REDACT_BIN): $(TEST_REDACT_SRCS) src/sdp_redact.h
	$(CC) -std=gnu11 -Wall -Wextra -g -o $@ $(TEST_REDACT_SRCS)

# test_room_lifecycle compiles the plugin source into the test binary (see the variable block above).
$(TEST_LIFECYCLE_BIN): $(TEST_LIFECYCLE_SRCS) src/janus_slvoice.c
	$(CC) $(BENCH_CFLAGS) -o $@ $(TEST_LIFECYCLE_SRCS) $(BENCH_LIBS)

$(TEST_SLDATA_BIN): $(TEST_SLDATA_SRCS)
	$(CC) $(TEST_CFLAGS) -o $@ $(TEST_SLDATA_SRCS) $(TEST_LIBS)

$(TEST_VISBATCH_BIN): $(TEST_VISBATCH_SRCS)
	$(CC) $(TEST_CFLAGS) -o $@ $(TEST_VISBATCH_SRCS) $(TEST_LIBS)

# test_deferred: libc only (deferred.c pulls no jansson/glib), like test_mix.
$(TEST_DEFERRED_BIN): $(TEST_DEFERRED_SRCS)
	$(CC) -std=gnu11 -Wall -Wextra -g -o $@ $(TEST_DEFERRED_SRCS)

$(TEST_ROSTER_BIN): $(TEST_ROSTER_SRCS)
	$(CC) $(TEST_GLIB_CFLAGS) -o $@ $(TEST_ROSTER_SRCS) $(TEST_GLIB_LIBS)

$(TEST_MIX_BIN): $(TEST_MIX_SRCS)
	$(CC) -std=gnu11 -Wall -Wextra -g -o $@ $(TEST_MIX_SRCS) -lm

# test_azimuth: the horizontal azimuth maths (header-only azimuth.h; libm only).
$(TEST_AZIMUTH_BIN): $(TEST_AZIMUTH_SRCS)
	$(CC) -std=gnu11 -Wall -Wextra -g -o $@ $(TEST_AZIMUTH_SRCS) -lm

# test_pan: the constant-power stereo pan law (header-only pan.h; libm only).
$(TEST_PAN_BIN): $(TEST_PAN_SRCS)
	$(CC) -std=gnu11 -Wall -Wextra -g -o $@ $(TEST_PAN_SRCS) -lm

# bench_tick: tick-cost load harness (docs/voice/scaling-assessment.md). Built ONLY
# on demand (`make bench_tick`), never by `make test`. Sweep tuning via EXTRA_CFLAGS,
# e.g. `make bench_tick EXTRA_CFLAGS='-DSLV_MAX_MIX=128 -DSLV_OPUS_COMPLEXITY=6'`.
bench_tick: $(BENCH_TICK_BIN)
$(BENCH_TICK_BIN): $(BENCH_TICK_SRCS) src/janus_slvoice.c
	$(CC) $(BENCH_CFLAGS) -o $@ $(BENCH_TICK_SRCS) $(BENCH_LIBS)

install: $(TARGET)
	install -d $(DESTDIR)$(PLUGINDIR)
	install -m 0644 $(TARGET) $(DESTDIR)$(PLUGINDIR)/$(TARGET)

clean:
	rm -f $(OBJS) $(TARGET) $(TEST_SLDATA_BIN) $(TEST_MIX_BIN) $(TEST_VISBATCH_BIN) $(TEST_DEFERRED_BIN) $(TEST_ROSTER_BIN) $(TEST_AZIMUTH_BIN) $(TEST_PAN_BIN) $(TEST_LIFECYCLE_BIN) $(TEST_REDACT_BIN) $(TEST_VISAUTH_BIN) $(TEST_JOINCAP_BIN) $(BENCH_TICK_BIN)
