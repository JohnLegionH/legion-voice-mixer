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
PKGS := janus-gateway glib-2.0 jansson opus

PKG_CFLAGS := $(shell $(PKGCONFIG) --cflags $(PKGS) 2>/dev/null)
PKG_LIBS   := $(shell $(PKGCONFIG) --libs glib-2.0 jansson opus 2>/dev/null) -lm

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
SRCS := src/janus_slvoice.c src/sldata.c src/visbatch.c src/mixer/mix.c
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
TEST_ROSTER_BIN    := tests/test_roster
TEST_ROSTER_SRCS   := tests/test_roster.c
TEST_CFLAGS  := -std=gnu11 -Wall -Wextra -g $(shell $(PKGCONFIG) --cflags jansson 2>/dev/null)
TEST_LIBS    := $(shell $(PKGCONFIG) --libs jansson 2>/dev/null) -lm
# roster.h is glib-only (no jansson/Janus); its test links glib.
TEST_GLIB_CFLAGS := -std=gnu11 -Wall -Wextra -g $(shell $(PKGCONFIG) --cflags glib-2.0 2>/dev/null)
TEST_GLIB_LIBS   := $(shell $(PKGCONFIG) --libs glib-2.0 2>/dev/null)

.PHONY: all install clean test

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

# Build and run ALL unit tests. `make test` is a required gate: it is also run
# during the Docker image build (see Dockerfile), so a failure fails the image.
test: $(TEST_SLDATA_BIN) $(TEST_MIX_BIN) $(TEST_VISBATCH_BIN) $(TEST_ROSTER_BIN)
	./$(TEST_SLDATA_BIN)
	./$(TEST_MIX_BIN)
	./$(TEST_VISBATCH_BIN)
	./$(TEST_ROSTER_BIN)

$(TEST_SLDATA_BIN): $(TEST_SLDATA_SRCS)
	$(CC) $(TEST_CFLAGS) -o $@ $(TEST_SLDATA_SRCS) $(TEST_LIBS)

$(TEST_VISBATCH_BIN): $(TEST_VISBATCH_SRCS)
	$(CC) $(TEST_CFLAGS) -o $@ $(TEST_VISBATCH_SRCS) $(TEST_LIBS)

$(TEST_ROSTER_BIN): $(TEST_ROSTER_SRCS)
	$(CC) $(TEST_GLIB_CFLAGS) -o $@ $(TEST_ROSTER_SRCS) $(TEST_GLIB_LIBS)

$(TEST_MIX_BIN): $(TEST_MIX_SRCS)
	$(CC) -std=gnu11 -Wall -Wextra -g -o $@ $(TEST_MIX_SRCS) -lm

install: $(TARGET)
	install -d $(DESTDIR)$(PLUGINDIR)
	install -m 0644 $(TARGET) $(DESTDIR)$(PLUGINDIR)/$(TARGET)

clean:
	rm -f $(OBJS) $(TARGET) $(TEST_SLDATA_BIN) $(TEST_MIX_BIN) $(TEST_VISBATCH_BIN) $(TEST_ROSTER_BIN)
