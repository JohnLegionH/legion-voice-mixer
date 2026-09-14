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

# Paths are overridable only so tests/entrypoint_test.sh can run this script
# without Docker against a scratch config dir and a stub Janus binary.
CONF_DIR=${JANUS_CONF_DIR:-/opt/janus/etc/janus}
TPL_DIR=${JANUS_TEMPLATE_DIR:-/opt/janus/share/janus-templates}
OVERRIDE_DIR=${JANUS_OVERRIDE_DIR:-/opt/janus/etc/janus.d}
JANUS_BIN=${JANUS_BIN:-/opt/janus/bin/janus}

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
# O-55 knobs. Defaults reproduce the behaviour before the knobs existed (WS on,
# admin published on all interfaces); narrowing is opt-in. See "Configuration
# compatibility rule" in docs/docker-notes.md.
: "${JS_WS_ENABLED:=true}"
# Host address docker-compose.yml publishes the admin port on. Compose passes it
# in; the entrypoint only reports it (Janus itself listens on all container addresses).
: "${JS_ADMIN_BIND:=0.0.0.0}"
: "${JS_RTP_PORT_RANGE:=10000-10200}"
: "${JS_PUBLIC_IP:=}"
: "${JS_PUBLIC_HOST:=}"
# Extra nat_1_1_mapping addresses (comma list), appended after the public address.
: "${JS_NAT_EXTRA_IPS:=}"
: "${JS_API_SECRET:=}"
: "${JS_ADMIN_SECRET:=}"
# O-65: dev-only escape hatch for starting with an empty secret.
: "${ALLOW_INSECURE_DEV:=false}"
# O-54: seconds a non-permanent mixer room may stay empty before the plugin destroys it
# (0 disables). The plugin reads it from the process environment, so it is exported here.
: "${JS_EMPTY_ROOM_GRACE_S:=60}"
export JS_EMPTY_ROOM_GRACE_S
# O-75: seconds a joined participant may go without its PeerConnection coming up before the plugin reaps
# it (0 disables). Read by the plugin from the process environment, so exported like the grace.
: "${JS_JOIN_MEDIA_TIMEOUT_S:=30}"
export JS_JOIN_MEDIA_TIMEOUT_S

is_true() {
	case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
		1|true|yes|on) return 0 ;;
		*)             return 1 ;;
	esac
}

is_ipv4() {
	printf '%s' "$1" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'
}

# RFC 1918 private ranges and loopback: addresses no off-LAN viewer can reach.
is_private_or_loopback() {
	case "$1" in
		10.*|127.*|192.168.*)                   return 0 ;;
		172.1[6-9].*|172.2[0-9].*|172.3[0-1].*) return 0 ;;
	esac
	return 1
}

if is_true "$JS_WS_ENABLED"; then JS_WS_ENABLED=true; else JS_WS_ENABLED=false; fi
if is_true "$ALLOW_INSECURE_DEV"; then ALLOW_INSECURE_DEV=true; else ALLOW_INSECURE_DEV=false; fi

secret_state() {
	if [ -z "$(printf '%s' "$1" | tr -d '[:space:]')" ]; then echo EMPTY; else echo set; fi
}

# ---- Effective values first (compatibility rule) --------------------------
# Every security- or connectivity-relevant knob, before anything can fail, so a
# wrong value is visible in `docker compose logs` before anyone logs in.
# Secrets are reported as set/EMPTY, never printed.
echo "[entrypoint] INFO: admin API bind=${JS_ADMIN_BIND} port=${JS_ADMIN_PORT} base_path=${JS_ADMIN_BASEPATH} (JS_ADMIN_BIND)"
case "$JS_ADMIN_BIND" in
	0.0.0.0|::|"[::]")
		echo "[entrypoint] WARNING: admin API is reachable on all interfaces; protected by JS_ADMIN_SECRET only — firewall the port or set JS_ADMIN_BIND to the address the regionserver uses." >&2 ;;
esac
echo "[entrypoint] INFO: websockets transport enabled=${JS_WS_ENABLED} port=${JS_WS_PORT} (JS_WS_ENABLED)"
echo "[entrypoint] INFO: http port=${JS_HTTP_PORT} base_path=${JS_HTTP_BASEPATH} rtp=${JS_RTP_PORT_RANGE}"
echo "[entrypoint] INFO: secrets api_secret=$(secret_state "$JS_API_SECRET") admin_secret=$(secret_state "$JS_ADMIN_SECRET") allow_insecure_dev=${ALLOW_INSECURE_DEV}"
echo "[entrypoint] INFO: public address public_host=${JS_PUBLIC_HOST:-<none>} public_ip=${JS_PUBLIC_IP:-<none>} nat_extra_ips=${JS_NAT_EXTRA_IPS:-<none>} keep_private_host=${JS_KEEP_PRIVATE_HOST:-<auto>}"
echo "[entrypoint] INFO: empty_room_grace_s=${JS_EMPTY_ROOM_GRACE_S} join_media_timeout_s=${JS_JOIN_MEDIA_TIMEOUT_S}"

# ---- O-65: fail closed on empty secrets -----------------------------------
# A blank secret was always an open API: an empty JS_API_SECRET leaves the Janus
# API open to anyone who can reach the HTTP port, and an empty JS_ADMIN_SECRET
# leaves the stock template's well-known admin_secret in force. Kept as a
# deliberate behaviour change on upgrade (docs/docker-notes.md).
missing=""; set_hint=""
if [ "$(secret_state "$JS_API_SECRET")" = EMPTY ]; then
	missing="JS_API_SECRET"; set_hint="JS_API_SECRET=<the sim's APIToken>"
fi
if [ "$(secret_state "$JS_ADMIN_SECRET")" = EMPTY ]; then
	missing="${missing:+$missing and }JS_ADMIN_SECRET"
	set_hint="${set_hint:+$set_hint and }JS_ADMIN_SECRET=<the sim's AdminAPIToken>"
fi
if [ -n "$missing" ]; then
	if [ "$ALLOW_INSECURE_DEV" = true ]; then
		echo "[entrypoint] WARNING: ${missing} empty; starting anyway because ALLOW_INSECURE_DEV=true (DEV ONLY — never on a reachable host)" >&2
	else
		echo "[entrypoint] FATAL: ${missing} empty; refusing to start. Set ${set_hint} in .env, or set ALLOW_INSECURE_DEV=true in .env to start without them (dev only: the API is then open). See docs/docker-notes.md, \"Behaviour changes on upgrade\"." >&2
		exit 1
	fi
fi

# ---- Public address resolution --------------------------------------------
# Janus's nat_1_1_mapping needs an IPv4 *literal*, not a hostname. When
# JS_PUBLIC_HOST is set (e.g. a DDNS name like legiongrid.ddns.net), resolve it
# to an IPv4 now, at container start, and use that for nat_1_1_mapping —
# overriding JS_PUBLIC_IP. This is a resolve-once-at-start operation: if the
# dynamic IP changes while the container runs, external voice breaks until the
# container is restarted (`docker compose restart` re-resolves). See env.sample.
if [ -n "$JS_PUBLIC_HOST" ]; then
	resolved=$(getent ahostsv4 "$JS_PUBLIC_HOST" 2>/dev/null | awk '{print $1; exit}')
	if ! is_ipv4 "$resolved"; then
		echo "[entrypoint] FATAL: could not resolve JS_PUBLIC_HOST='${JS_PUBLIC_HOST}' to an IPv4 address; refusing to start" >&2
		exit 1
	fi
	echo "[entrypoint] resolved JS_PUBLIC_HOST='${JS_PUBLIC_HOST}' -> ${resolved} (used for nat_1_1_mapping)"
	JS_PUBLIC_IP="$resolved"
fi

# ---- nat_1_1_mapping: public address + JS_NAT_EXTRA_IPS -------------------
# Janus 1.x accepts a comma list and advertises a host candidate per address.
NAT_MAPPING="$JS_PUBLIC_IP"
for ip in $(printf '%s' "$JS_NAT_EXTRA_IPS" | tr ',' ' '); do
	if ! is_ipv4 "$ip"; then
		echo "[entrypoint] FATAL: JS_NAT_EXTRA_IPS entry '${ip}' is not an IPv4 literal; refusing to start" >&2
		exit 1
	fi
	case ",${NAT_MAPPING}," in
		*",${ip},"*) ;;
		*) NAT_MAPPING="${NAT_MAPPING:+$NAT_MAPPING,}${ip}" ;;
	esac
done

# Guard: a private/loopback mapping only works for viewers on that LAN.
if [ -n "$NAT_MAPPING" ]; then
	have_public=false
	for ip in $(printf '%s' "$NAT_MAPPING" | tr ',' ' '); do
		if is_private_or_loopback "$ip"; then
			echo "[entrypoint] WARNING: nat_1_1_mapping address ${ip} is private/loopback (RFC 1918 or 127/8)" >&2
		else
			have_public=true
		fi
	done
	if [ "$have_public" = false ]; then
		echo "[entrypoint] WARNING: no public address in nat_1_1_mapping=${NAT_MAPPING}: off-LAN viewers will fail ICE (only LAN viewers get a reachable candidate)." >&2
		echo "[entrypoint] WARNING: add the router's public IPv4 via JS_NAT_EXTRA_IPS, or make JS_PUBLIC_HOST resolve to it inside the container. See docs/docker-notes.md." >&2
	fi
fi

# ---- keep_private_host default --------------------------------------------
# When a public mapping is in effect, default to advertising BOTH the private
# and public host candidates so LAN viewers keep working without NAT hairpin
# while external viewers use the public address. Override with
# JS_KEEP_PRIVATE_HOST; it only has an effect when a public address is set.
if [ -n "$NAT_MAPPING" ]; then
	: "${JS_KEEP_PRIVATE_HOST:=true}"
else
	: "${JS_KEEP_PRIVATE_HOST:=false}"
fi
if is_true "$JS_KEEP_PRIVATE_HOST"; then JS_KEEP_PRIVATE_HOST=true; else JS_KEEP_PRIVATE_HOST=false; fi

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
if [ -n "$NAT_MAPPING" ]; then
	# nat_1_1_mapping/keep_private_host live in the nat:{} section, shipped
	# COMMENTED in the stock template — use the robust helper so they reliably
	# end up uncommented (see ensure_kv_in_section).
	ensure_kv_in_section "$JANUS_JCFG" nat nat_1_1_mapping   "\"${NAT_MAPPING}\""
	ensure_kv_in_section "$JANUS_JCFG" nat keep_private_host "${JS_KEEP_PRIVATE_HOST}"
	# Verify it actually landed, and state the applied value (fail loud if not).
	if grep -Eq "^[[:space:]]*nat_1_1_mapping[[:space:]]*=" "$JANUS_JCFG"; then
		echo "[entrypoint] nat_1_1_mapping = ${NAT_MAPPING}"
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

# WebSockets signalling transport (on by default, as before O-55; JS_WS_ENABLED=false
# narrows). `ws` anchors to line start so it never collides with `wss`/`admin_ws`/
# `admin_wss`; `ws_port` likewise never matches `admin_ws_port`. The container's
# internal WS port tracks JS_WS_PORT so the port mapping in docker-compose.yml
# stays symmetric.
if [ "$JS_WS_ENABLED" = true ]; then
	set_kv "$WS_JCFG" ws      true
	set_kv "$WS_JCFG" ws_port "${JS_WS_PORT}"
else
	set_kv "$WS_JCFG" ws false
	# Don't load the transport at all (a loaded one with no server logs an init
	# error). Scoped to transports:{} — plugins/loggers/events have `disable` too.
	sed -i '/^transports:[[:space:]]*{/,/^}/ s|^\([[:space:]]*\)#*[[:space:]]*disable = .*|\1disable = "libjanus_websockets.so"|' "$JANUS_JCFG"
fi

# ---- 3. Operator overrides (mounted file > env) ----
if [ -d "$OVERRIDE_DIR" ]; then
	for f in "$OVERRIDE_DIR"/*.jcfg; do
		[ -e "$f" ] || continue
		echo "[entrypoint] applying override $(basename "$f")"
		cp -f "$f" "$CONF_DIR/$(basename "$f")"
	done
fi

if [ "$JS_WS_ENABLED" = true ]; then ws_desc="${JS_WS_PORT}"; else ws_desc="off"; fi
echo "[entrypoint] starting Janus: server_name=${JS_SERVER_NAME} http=${JS_HTTP_PORT}${JS_HTTP_BASEPATH} admin=${JS_ADMIN_PORT}${JS_ADMIN_BASEPATH} ws=${ws_desc} rtp=${JS_RTP_PORT_RANGE} public_host=${JS_PUBLIC_HOST:-<none>} public_ip=${JS_PUBLIC_IP:-<none>} nat_1_1_mapping=${NAT_MAPPING:-<none>} keep_private_host=${JS_KEEP_PRIVATE_HOST} empty_room_grace_s=${JS_EMPTY_ROOM_GRACE_S} join_media_timeout_s=${JS_JOIN_MEDIA_TIMEOUT_S}"
exec "$JANUS_BIN" "$@"
