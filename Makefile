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
PKGS := janus-gateway glib-2.0 jansson

PKG_CFLAGS := $(shell $(PKGCONFIG) --cflags $(PKGS) 2>/dev/null)
PKG_LIBS   := $(shell $(PKGCONFIG) --libs glib-2.0 jansson 2>/dev/null)

CFLAGS  ?= -O2 -g
# -I$(JANUS_PREFIX)/include is an explicit fallback so <janus/plugins/plugin.h>
# resolves even if janus-gateway.pc is not on the pkg-config path.
CFLAGS  += -std=gnu11 -fPIC -Wall -Wextra -Wno-unused-parameter -I$(JANUS_PREFIX)/include $(PKG_CFLAGS)
LDFLAGS += -shared
LDLIBS  += $(PKG_LIBS)

SRCS := src/janus_slvoice.c
OBJS := $(SRCS:.c=.o)

.PHONY: all install clean

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

install: $(TARGET)
	install -d $(DESTDIR)$(PLUGINDIR)
	install -m 0644 $(TARGET) $(DESTDIR)$(PLUGINDIR)/$(TARGET)

clean:
	rm -f $(OBJS) $(TARGET)
