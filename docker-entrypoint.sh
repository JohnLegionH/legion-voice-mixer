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
# An IPv4 literal (used as-is) or, since A.1, a hostname (discovered, below).
: "${JS_PUBLIC_IP:=}"
# A DNS/DDNS hostname whose public address is discovered at start (slice A.1).
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
# Slice A.1: public address discovery and its periodic re-check (docs/docker-notes.md, "External access").
: "${JS_PUBLIC_IP_DISCOVERY:=auto}"
: "${JS_STUN_SERVER:=stun.l.google.com:19302}"
: "${JS_PUBLIC_IP_DNS_RESOLVER:=1.1.1.1}"
: "${JS_PUBLIC_IP_REFRESH_S:=300}"
: "${JS_PUBLIC_IP_CHANGE_ACTION:=warn}"
: "${JS_PUBLIC_IP_RESTART_MAX_WAIT_S:=900}"
# Slice A.2: the startup self-check (docs/docker-notes.md, "Startup self-check").
: "${JS_SELFCHECK:=on}"
: "${JS_SELFCHECK_TIMEOUT_S:=20}"
# Slice A.2b: hours an inbound receipt recorded by `legion-voice-selfcheck --listen` keeps C2b at PASS.
: "${JS_SELFCHECK_INBOUND_MAX_AGE_H:=168}"

# The address library and its probe ship beside this script (SLV_LIB_DIR is a test seam).
SLV_LIB_DIR=${SLV_LIB_DIR:-/usr/local/lib/legion-voice}
. "$SLV_LIB_DIR/public-address.sh"

is_true() {
	case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
		1|true|yes|on) return 0 ;;
		*)             return 1 ;;
	esac
}

# A non-negative integer knob, or its default with a WARNING.
uint_or_default() {
	case "$2" in
		''|*[!0-9]*)
			echo "[entrypoint] WARNING: $1='$2' is not a non-negative integer; using $3" >&2
			echo "$3" ;;
		*)  echo "$2" ;;
	esac
}

if is_true "$JS_WS_ENABLED"; then JS_WS_ENABLED=true; else JS_WS_ENABLED=false; fi
if is_true "$ALLOW_INSECURE_DEV"; then ALLOW_INSECURE_DEV=true; else ALLOW_INSECURE_DEV=false; fi
JS_PUBLIC_IP_DISCOVERY=$(printf '%s' "$JS_PUBLIC_IP_DISCOVERY" | tr '[:upper:]' '[:lower:]')
JS_PUBLIC_IP_CHANGE_ACTION=$(printf '%s' "$JS_PUBLIC_IP_CHANGE_ACTION" | tr '[:upper:]' '[:lower:]')
JS_PUBLIC_IP_REFRESH_S=$(uint_or_default JS_PUBLIC_IP_REFRESH_S "$JS_PUBLIC_IP_REFRESH_S" 300)
JS_PUBLIC_IP_RESTART_MAX_WAIT_S=$(uint_or_default JS_PUBLIC_IP_RESTART_MAX_WAIT_S "$JS_PUBLIC_IP_RESTART_MAX_WAIT_S" 900)
# The self-check is diagnostics: a bad value WARNs and falls back, never stops the start.
case "$(printf '%s' "$JS_SELFCHECK" | tr '[:upper:]' '[:lower:]')" in
	on|true|yes|1)  JS_SELFCHECK=on ;;
	off|false|no|0) JS_SELFCHECK=off ;;
	*)
		echo "[entrypoint] WARNING: JS_SELFCHECK='${JS_SELFCHECK}' is not on or off; using on" >&2
		JS_SELFCHECK=on ;;
esac
JS_SELFCHECK_TIMEOUT_S=$(uint_or_default JS_SELFCHECK_TIMEOUT_S "$JS_SELFCHECK_TIMEOUT_S" 20)
if [ "$JS_SELFCHECK_TIMEOUT_S" -eq 0 ]; then
	echo "[entrypoint] WARNING: JS_SELFCHECK_TIMEOUT_S=0 is not a usable bound; using 20" >&2
	JS_SELFCHECK_TIMEOUT_S=20
fi
JS_SELFCHECK_INBOUND_MAX_AGE_H=$(uint_or_default JS_SELFCHECK_INBOUND_MAX_AGE_H "$JS_SELFCHECK_INBOUND_MAX_AGE_H" 168)
if [ "$JS_SELFCHECK_INBOUND_MAX_AGE_H" -eq 0 ]; then
	echo "[entrypoint] WARNING: JS_SELFCHECK_INBOUND_MAX_AGE_H=0 is not a usable age; using 168" >&2
	JS_SELFCHECK_INBOUND_MAX_AGE_H=168
fi

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
echo "[entrypoint] INFO: address discovery=${JS_PUBLIC_IP_DISCOVERY} stun_server=${JS_STUN_SERVER} dns_resolver=${JS_PUBLIC_IP_DNS_RESOLVER} refresh_s=${JS_PUBLIC_IP_REFRESH_S} change_action=${JS_PUBLIC_IP_CHANGE_ACTION} restart_max_wait_s=${JS_PUBLIC_IP_RESTART_MAX_WAIT_S}"
echo "[entrypoint] INFO: selfcheck=${JS_SELFCHECK} timeout_s=${JS_SELFCHECK_TIMEOUT_S} inbound_max_age_h=${JS_SELFCHECK_INBOUND_MAX_AGE_H} (JS_SELFCHECK; on demand: legion-voice-selfcheck [--json], inbound proof: legion-voice-selfcheck --listen)"
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

# ---- Public address: a literal, or discovered from a hostname (slice A.1) ----
# nat_1_1_mapping needs IPv4 literals.
# - A literal in JS_PUBLIC_IP (or JS_PUBLIC_HOST) is used as-is, with no lookup.
# - A hostname in JS_PUBLIC_HOST (or, when that is unset, in JS_PUBLIC_IP) is discovered according
#   to JS_PUBLIC_IP_DISCOVERY:
#     auto        STUN (JS_STUN_SERVER: the address the internet sees this host's traffic come from),
#                 then DNS against JS_PUBLIC_IP_DNS_RESOLVER, then the container resolver. The
#                 container resolver comes last because a hairpin or split-horizon setup answers
#                 it with the LAN address.
#     stun | dns  that source only
#     static      the container resolver only, at start, as before A.1 (no periodic re-check)
# Every source queried is logged with its answer, then the winner: the first public answer.
# See entrypoint/public-address.sh.
case "$JS_PUBLIC_IP_DISCOVERY" in
	auto|stun|dns|static) ;;
	*)
		echo "[entrypoint] FATAL: JS_PUBLIC_IP_DISCOVERY='${JS_PUBLIC_IP_DISCOVERY}' is not one of auto, stun, dns, static; refusing to start" >&2
		exit 1 ;;
esac
case "$JS_PUBLIC_IP_CHANGE_ACTION" in
	warn|restart) ;;
	*)
		echo "[entrypoint] FATAL: JS_PUBLIC_IP_CHANGE_ACTION='${JS_PUBLIC_IP_CHANGE_ACTION}' is not one of warn, restart; refusing to start" >&2
		exit 1 ;;
esac

ADDR_HOST=""; ADDR_HOST_VAR=""; LITERAL_IPS=""
addr_reset
if [ -n "$JS_PUBLIC_IP" ]; then
	if addr_is_ipv4 "$JS_PUBLIC_IP"; then
		LITERAL_IPS=$JS_PUBLIC_IP
	elif [ -z "$JS_PUBLIC_HOST" ]; then
		ADDR_HOST=$JS_PUBLIC_IP; ADDR_HOST_VAR=JS_PUBLIC_IP
	else
		echo "[entrypoint] WARNING: JS_PUBLIC_IP='${JS_PUBLIC_IP}' is not an IPv4 literal and JS_PUBLIC_HOST is set; ignoring JS_PUBLIC_IP" >&2
	fi
fi
if [ -n "$JS_PUBLIC_HOST" ]; then
	if addr_is_ipv4 "$JS_PUBLIC_HOST"; then
		LITERAL_IPS=$(addr_list_add "$LITERAL_IPS" "$JS_PUBLIC_HOST")
	else
		ADDR_HOST=$JS_PUBLIC_HOST; ADDR_HOST_VAR=JS_PUBLIC_HOST
	fi
fi
if [ -n "$ADDR_HOST" ]; then
	if ! printf '%s' "$ADDR_HOST" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'; then
		echo "[entrypoint] FATAL: ${ADDR_HOST_VAR}='${ADDR_HOST}' is neither an IPv4 literal nor a hostname; refusing to start" >&2
		exit 1
	fi
	echo "[entrypoint] INFO: discovering the public address of '${ADDR_HOST}' (${ADDR_HOST_VAR}) with JS_PUBLIC_IP_DISCOVERY=${JS_PUBLIC_IP_DISCOVERY}"
	addr_discover "$ADDR_HOST" "$JS_PUBLIC_IP_DISCOVERY"
	addr_log_sources "[entrypoint]"
	if [ -z "$ADDR_WINNER" ]; then
		echo "[entrypoint] FATAL: could not discover an IPv4 address for ${ADDR_HOST_VAR}='${ADDR_HOST}' from any source (${ADDR_SOURCES}); refusing to start" >&2
		exit 1
	fi
fi

# ---- nat_1_1_mapping: discovered address, literals, then JS_NAT_EXTRA_IPS ----
# Janus 1.x accepts a comma list and advertises a host candidate per address.
NAT_MAPPING=$ADDR_WINNER
for ip in $(printf '%s' "$LITERAL_IPS" | tr ',' ' '); do
	NAT_MAPPING=$(addr_list_add "$NAT_MAPPING" "$ip")
done
EXTRA_IPS=""
for ip in $(printf '%s' "$JS_NAT_EXTRA_IPS" | tr ',' ' '); do
	if ! addr_is_ipv4 "$ip"; then
		echo "[entrypoint] FATAL: JS_NAT_EXTRA_IPS entry '${ip}' is not an IPv4 literal; refusing to start" >&2
		exit 1
	fi
	EXTRA_IPS=$(addr_list_add "$EXTRA_IPS" "$ip")
	NAT_MAPPING=$(addr_list_add "$NAT_MAPPING" "$ip")
done

# Verdict on the FINAL mapping, not on any single lookup:
# - no public address: ERROR with remediation;
# - a public address: INFO listing them;
# - any 100.64.0.0/10 address: a CGNAT warning (TURN is required).
addr_verdict "$NAT_MAPPING"
if [ -n "$NAT_MAPPING" ]; then
	addr_log_verdict "[entrypoint]" "$NAT_MAPPING" "$JS_RTP_PORT_RANGE"
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

# ---- Resolution record (slice A.1) ----
# The same one-line JSON goes to the log (grep ADDRESS_RESOLUTION) and to the state file
# /run/legion-voice/public-address.json inside the container, for tooling to read. The fields are
# the sources, values, winner, mapping and verdict.
ADDR_JSON=$(addr_json start "$ADDR_HOST" "$ADDR_HOST_VAR" "$JS_PUBLIC_IP_DISCOVERY" "$LITERAL_IPS" "$EXTRA_IPS" "$NAT_MAPPING" "$JS_KEEP_PRIVATE_HOST")
echo "[entrypoint] ADDRESS_RESOLUTION ${ADDR_JSON}"
addr_write_state "$ADDR_JSON" "[entrypoint]"

# ---- Effective configuration for the self-check (slice A.2) ----
# legion-voice-selfcheck reads the values this start actually used. A `docker exec` shell has the .env
# values but not the defaults assigned above. No secrets go in this file.
: "${SLV_EFFECTIVE_CONFIG:=/run/legion-voice/effective-config.json}"
slv_json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
EFFECTIVE_JSON=$(printf '{"JS_RTP_PORT_RANGE":"%s","JS_HTTP_PORT":"%s","JS_HTTP_BASEPATH":"%s","JS_ADMIN_PORT":"%s","JS_ADMIN_BASEPATH":"%s","JS_ADMIN_BIND":"%s","JS_STUN_SERVER":"%s","JS_SELFCHECK_TIMEOUT_S":"%s","JS_SELFCHECK_INBOUND_MAX_AGE_H":"%s"}' \
	"$(slv_json_escape "$JS_RTP_PORT_RANGE")" "$(slv_json_escape "$JS_HTTP_PORT")" "$(slv_json_escape "$JS_HTTP_BASEPATH")" \
	"$(slv_json_escape "$JS_ADMIN_PORT")" "$(slv_json_escape "$JS_ADMIN_BASEPATH")" "$(slv_json_escape "$JS_ADMIN_BIND")" \
	"$(slv_json_escape "$JS_STUN_SERVER")" "$(slv_json_escape "$JS_SELFCHECK_TIMEOUT_S")" \
	"$(slv_json_escape "$JS_SELFCHECK_INBOUND_MAX_AGE_H")")
slv_write_file "$SLV_EFFECTIVE_CONFIG" "$EFFECTIVE_JSON" "[entrypoint]"

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

# ---- 4. Periodic public address re-check (slice A.1) ----
# Runs in the background beside Janus; see entrypoint/public-address-watch.sh. The JS_* values it
# needs are exported, because a default assigned above is not in the environment otherwise.
if [ -n "$ADDR_HOST" ] && [ "$JS_PUBLIC_IP_DISCOVERY" != static ] && [ "$JS_PUBLIC_IP_REFRESH_S" -gt 0 ]; then
	export JS_PUBLIC_IP_DISCOVERY JS_STUN_SERVER JS_PUBLIC_IP_DNS_RESOLVER JS_PUBLIC_IP_REFRESH_S \
		JS_PUBLIC_IP_CHANGE_ACTION JS_PUBLIC_IP_RESTART_MAX_WAIT_S JS_HTTP_PORT JS_HTTP_BASEPATH SLV_LIB_DIR
	SLV_ADDR_HOST=$ADDR_HOST SLV_ADDR_HOST_VAR=$ADDR_HOST_VAR SLV_ADDR_RUNNING=$ADDR_WINNER \
		SLV_ADDR_LITERALS=$LITERAL_IPS SLV_ADDR_EXTRAS=$EXTRA_IPS SLV_ADDR_KEEP_PRIVATE=$JS_KEEP_PRIVATE_HOST \
		sh "$SLV_LIB_DIR/public-address-watch.sh" &
elif [ -n "$ADDR_HOST" ]; then
	echo "[entrypoint] INFO: periodic public address re-check off (JS_PUBLIC_IP_REFRESH_S=${JS_PUBLIC_IP_REFRESH_S}, JS_PUBLIC_IP_DISCOVERY=${JS_PUBLIC_IP_DISCOVERY})"
fi

# ---- 5. Startup self-check (slice A.2) ----
# Runs in the background, so Janus starts at once. The self-check waits for Janus's HTTP transport itself,
# stays inside JS_SELFCHECK_TIMEOUT_S, and prints one [selfcheck] block. `timeout` is a backstop 5 s past
# that bound, in case the check itself hangs. SLV_SELFCHECK_CMD is a test seam.
: "${SLV_SELFCHECK_CMD:=/usr/local/bin/legion-voice-selfcheck}"
if [ "$JS_SELFCHECK" = on ]; then
	JANUS_CONF_DIR=$CONF_DIR
	export SLV_EFFECTIVE_CONFIG SLV_ADDR_STATE_FILE JANUS_CONF_DIR
	(
		rc=0
		timeout -k 2 "$((JS_SELFCHECK_TIMEOUT_S + 5))" "$SLV_SELFCHECK_CMD" --startup || rc=$?
		case "$rc" in
			124|137) echo "[selfcheck] ===== legion-voice self-check was stopped after $((JS_SELFCHECK_TIMEOUT_S + 5)) s (JS_SELFCHECK_TIMEOUT_S=${JS_SELFCHECK_TIMEOUT_S} plus 5 s): no results =====" ;;
			126|127) echo "[selfcheck] ===== legion-voice self-check could not run (${SLV_SELFCHECK_CMD}, exit ${rc}) =====" ;;
		esac
	) &
fi

if [ "$JS_WS_ENABLED" = true ]; then ws_desc="${JS_WS_PORT}"; else ws_desc="off"; fi
echo "[entrypoint] starting Janus: server_name=${JS_SERVER_NAME} http=${JS_HTTP_PORT}${JS_HTTP_BASEPATH} admin=${JS_ADMIN_PORT}${JS_ADMIN_BASEPATH} ws=${ws_desc} rtp=${JS_RTP_PORT_RANGE} public_host=${JS_PUBLIC_HOST:-<none>} public_ip=${JS_PUBLIC_IP:-<none>} nat_1_1_mapping=${NAT_MAPPING:-<none>} keep_private_host=${JS_KEEP_PRIVATE_HOST} empty_room_grace_s=${JS_EMPTY_ROOM_GRACE_S} join_media_timeout_s=${JS_JOIN_MEDIA_TIMEOUT_S}"
exec "$JANUS_BIN" "$@"
