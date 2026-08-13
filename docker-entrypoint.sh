#!/bin/sh
# Legion SLVoice mixer container entrypoint.
#
# Generates the Janus *.jcfg configuration from environment variables at
# container start, so operators configure everything via .env and never edit
# files inside the container.
#
# Configuration precedence (lowest to highest):
#   1. built-in defaults   — the stock jcfg templates baked into the image
#   2. environment vars    — the JS_* variables below (from .env)
#   3. mounted override    — any *.jcfg dropped into /opt/janus/etc/janus.d
# i.e. mounted file > env > default. See docs/docker-notes.md.
set -eu

CONF_DIR=/opt/janus/etc/janus
TPL_DIR=/opt/janus/share/janus-templates
OVERRIDE_DIR=/opt/janus/etc/janus.d

JANUS_JCFG="$CONF_DIR/janus.jcfg"
HTTP_JCFG="$CONF_DIR/janus.transport.http.jcfg"

# ---- Defaults (env > default) ----
: "${JS_SERVER_NAME:=GridVoice}"
: "${JS_HTTP_PORT:=14223}"
: "${JS_HTTP_BASEPATH:=/janus}"
: "${JS_ADMIN_PORT:=14225}"
: "${JS_ADMIN_BASEPATH:=/admin}"
: "${JS_RTP_PORT_RANGE:=10000-10200}"
: "${JS_PUBLIC_IP:=}"
: "${JS_API_SECRET:=}"
: "${JS_ADMIN_SECRET:=}"

# ---- 1. Restore pristine templates so generation is deterministic every start ----
if [ -d "$TPL_DIR" ]; then
	for f in "$TPL_DIR"/*.jcfg; do
		[ -e "$f" ] || continue
		cp -f "$f" "$CONF_DIR/$(basename "$f")"
	done
fi

# Uncomment (if needed) and set `key = value`, anchored to line start so that
# e.g. `http`/`port`/`base_path` never accidentally match `admin_http`/
# `admin_port`/`admin_base_path`. The replacement is escaped for sed.
set_kv() {
	file=$1; key=$2; val=$3
	esc=$(printf '%s' "$val" | sed 's/[\\&|]/\\&/g')
	sed -i "s|^\([[:space:]]*\)#*[[:space:]]*${key} = .*|\1${key} = ${esc}|" "$file"
}

# ---- 2. Apply environment values ----
set_kv "$JANUS_JCFG" server_name "\"${JS_SERVER_NAME}\""
if [ -n "$JS_API_SECRET" ];    then set_kv "$JANUS_JCFG" api_secret   "\"${JS_API_SECRET}\""; fi
if [ -n "$JS_ADMIN_SECRET" ];  then set_kv "$JANUS_JCFG" admin_secret "\"${JS_ADMIN_SECRET}\""; fi
if [ -n "$JS_RTP_PORT_RANGE" ];then set_kv "$JANUS_JCFG" rtp_port_range "\"${JS_RTP_PORT_RANGE}\""; fi
if [ -n "$JS_PUBLIC_IP" ];     then set_kv "$JANUS_JCFG" nat_1_1_mapping "\"${JS_PUBLIC_IP}\""; fi

set_kv "$HTTP_JCFG" http            true
set_kv "$HTTP_JCFG" port            "${JS_HTTP_PORT}"
set_kv "$HTTP_JCFG" base_path       "\"${JS_HTTP_BASEPATH}\""
set_kv "$HTTP_JCFG" admin_http      true
set_kv "$HTTP_JCFG" admin_port      "${JS_ADMIN_PORT}"
set_kv "$HTTP_JCFG" admin_base_path "\"${JS_ADMIN_BASEPATH}\""

# ---- 3. Operator overrides (mounted file > env) ----
if [ -d "$OVERRIDE_DIR" ]; then
	for f in "$OVERRIDE_DIR"/*.jcfg; do
		[ -e "$f" ] || continue
		echo "[entrypoint] applying override $(basename "$f")"
		cp -f "$f" "$CONF_DIR/$(basename "$f")"
	done
fi

echo "[entrypoint] starting Janus: server_name=${JS_SERVER_NAME} http=${JS_HTTP_PORT}${JS_HTTP_BASEPATH} admin=${JS_ADMIN_PORT}${JS_ADMIN_BASEPATH} rtp=${JS_RTP_PORT_RANGE} public_ip=${JS_PUBLIC_IP:-<none>}"
exec /opt/janus/bin/janus "$@"
