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
WS_JCFG="$CONF_DIR/janus.transport.websockets.jcfg"

# ---- Defaults (env > default) ----
: "${JS_SERVER_NAME:=GridVoice}"
: "${JS_HTTP_PORT:=14223}"
: "${JS_HTTP_BASEPATH:=/voice}"
: "${JS_ADMIN_PORT:=14225}"
: "${JS_ADMIN_BASEPATH:=/voiceAdmin}"
: "${JS_WS_PORT:=8188}"
: "${JS_RTP_PORT_RANGE:=10000-10200}"
: "${JS_PUBLIC_IP:=}"
: "${JS_PUBLIC_HOST:=}"
: "${JS_API_SECRET:=}"
: "${JS_ADMIN_SECRET:=}"

# ---- Public address resolution --------------------------------------------
# Janus's nat_1_1_mapping needs an IPv4 *literal*, not a hostname. When
# JS_PUBLIC_HOST is set (e.g. a DDNS name like legiongrid.ddns.net), resolve it
# to an IPv4 now, at container start, and use that for nat_1_1_mapping —
# overriding JS_PUBLIC_IP. This is a resolve-once-at-start operation: if the
# dynamic IP changes while the container runs, external voice breaks until the
# container is restarted (`docker compose restart` re-resolves). See env.sample.
if [ -n "$JS_PUBLIC_HOST" ]; then
	resolved=$(getent ahostsv4 "$JS_PUBLIC_HOST" 2>/dev/null | awk '{print $1; exit}')
	if ! printf '%s' "$resolved" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
		echo "[entrypoint] FATAL: could not resolve JS_PUBLIC_HOST='${JS_PUBLIC_HOST}' to an IPv4 address; refusing to start" >&2
		exit 1
	fi
	echo "[entrypoint] resolved JS_PUBLIC_HOST='${JS_PUBLIC_HOST}' -> ${resolved} (used for nat_1_1_mapping)"
	JS_PUBLIC_IP="$resolved"
fi

# ---- keep_private_host default --------------------------------------------
# When a public mapping is in effect, default to advertising BOTH the private
# and public host candidates so LAN viewers keep working without NAT hairpin
# while external viewers use the public address. Override with
# JS_KEEP_PRIVATE_HOST; it only has an effect when a public address is set.
if [ -n "$JS_PUBLIC_IP" ]; then
	: "${JS_KEEP_PRIVATE_HOST:=true}"
else
	: "${JS_KEEP_PRIVATE_HOST:=false}"
fi
case "$(printf '%s' "$JS_KEEP_PRIVATE_HOST" | tr '[:upper:]' '[:lower:]')" in
	1|true|yes|on) JS_KEEP_PRIVATE_HOST=true ;;
	*)             JS_KEEP_PRIVATE_HOST=false ;;
esac

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

# Like set_kv, but GUARANTEES an uncommented `key = value` inside a named jcfg
# section. set_kv alone silently no-ops if the template ships no matching line
# (commented or not), which is how a critical key like nat_1_1_mapping can
# silently stay commented. This first tries the in-place uncomment/replace, and
# if that still leaves no uncommented line, injects one right after `section: {`.
ensure_kv_in_section() {
	file=$1; section=$2; key=$3; val=$4
	esc=$(printf '%s' "$val" | sed 's/[\\&|]/\\&/g')
	# 1. Uncomment/replace an existing line (commented or not).
	sed -i "s|^\([[:space:]]*\)#*[[:space:]]*${key} = .*|\1${key} = ${esc}|" "$file"
	# 2. If no UNcommented line resulted, inject one after the `section: {` opener.
	if ! grep -Eq "^[[:space:]]*${key}[[:space:]]*=" "$file"; then
		sed -i "s|^\([[:space:]]*\)${section}:[[:space:]]*{.*|&\n\t${key} = ${esc}|" "$file"
	fi
}

# ---- 2. Apply environment values ----
set_kv "$JANUS_JCFG" server_name "\"${JS_SERVER_NAME}\""
if [ -n "$JS_API_SECRET" ];    then set_kv "$JANUS_JCFG" api_secret   "\"${JS_API_SECRET}\""; fi
if [ -n "$JS_ADMIN_SECRET" ];  then set_kv "$JANUS_JCFG" admin_secret "\"${JS_ADMIN_SECRET}\""; fi
if [ -n "$JS_RTP_PORT_RANGE" ];then set_kv "$JANUS_JCFG" rtp_port_range "\"${JS_RTP_PORT_RANGE}\""; fi
if [ -n "$JS_PUBLIC_IP" ]; then
	# nat_1_1_mapping/keep_private_host live in the nat:{} section, shipped
	# COMMENTED in the stock template — use the robust helper so they reliably
	# end up uncommented (see ensure_kv_in_section).
	ensure_kv_in_section "$JANUS_JCFG" nat nat_1_1_mapping   "\"${JS_PUBLIC_IP}\""
	ensure_kv_in_section "$JANUS_JCFG" nat keep_private_host "${JS_KEEP_PRIVATE_HOST}"
	# Verify it actually landed, and state the applied value (fail loud if not).
	if grep -Eq "^[[:space:]]*nat_1_1_mapping[[:space:]]*=" "$JANUS_JCFG"; then
		echo "[entrypoint] nat_1_1_mapping = ${JS_PUBLIC_IP}"
		echo "[entrypoint] keep_private_host = ${JS_KEEP_PRIVATE_HOST}"
	else
		echo "[entrypoint] FATAL: could not set nat_1_1_mapping in janus.jcfg (Janus template changed?); refusing to start" >&2
		exit 1
	fi
else
	# Fail-loud: no public address under bridge networking guarantees media failure.
	echo "[entrypoint] WARNING: neither JS_PUBLIC_IP nor JS_PUBLIC_HOST is set." >&2
	echo "[entrypoint] WARNING: under bridge networking Janus advertises only its private container IP," >&2
	echo "[entrypoint] WARNING: so WebRTC MEDIA WILL FAIL for every viewer (signalling/ICE may still look ok)." >&2
	echo "[entrypoint] WARNING: set JS_PUBLIC_IP (LAN or public IPv4) or JS_PUBLIC_HOST in .env. See docs/docker-notes.md." >&2
fi

set_kv "$HTTP_JCFG" http            true
set_kv "$HTTP_JCFG" port            "${JS_HTTP_PORT}"
set_kv "$HTTP_JCFG" base_path       "\"${JS_HTTP_BASEPATH}\""
set_kv "$HTTP_JCFG" admin_http      true
set_kv "$HTTP_JCFG" admin_port      "${JS_ADMIN_PORT}"
set_kv "$HTTP_JCFG" admin_base_path "\"${JS_ADMIN_BASEPATH}\""

# WebSockets signalling transport. `ws` anchors to line start so it never
# collides with `wss`/`admin_ws`/`admin_wss`; `ws_port` likewise never matches
# `admin_ws_port`. The container's internal WS port tracks JS_WS_PORT so the
# bridge port mapping in docker-compose.yml stays symmetric (host == container).
set_kv "$WS_JCFG" ws      true
set_kv "$WS_JCFG" ws_port "${JS_WS_PORT}"

# ---- 3. Operator overrides (mounted file > env) ----
if [ -d "$OVERRIDE_DIR" ]; then
	for f in "$OVERRIDE_DIR"/*.jcfg; do
		[ -e "$f" ] || continue
		echo "[entrypoint] applying override $(basename "$f")"
		cp -f "$f" "$CONF_DIR/$(basename "$f")"
	done
fi

echo "[entrypoint] starting Janus: server_name=${JS_SERVER_NAME} http=${JS_HTTP_PORT}${JS_HTTP_BASEPATH} admin=${JS_ADMIN_PORT}${JS_ADMIN_BASEPATH} ws=${JS_WS_PORT} rtp=${JS_RTP_PORT_RANGE} public_host=${JS_PUBLIC_HOST:-<none>} public_ip=${JS_PUBLIC_IP:-<none>} keep_private_host=${JS_KEEP_PRIVATE_HOST}"
exec /opt/janus/bin/janus "$@"
