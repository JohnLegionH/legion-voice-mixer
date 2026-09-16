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
# Phase 0 slice 0.3 (docs/voice/nonspatial-phase0-design.md §6.1): the visibility authority knobs, read by the plugin
# from the process environment. 0 is shadow mode: the plugin tracks and counts arming, epochs and staleness, and audio
# is unchanged. 1 silences unarmed or stale listeners in rooms the sim declared (vis_authority). Do not set 1 while
# ledger O-88 (connector and recorder arming) is open. JS_VIS_STALE_MS is the staleness window; the plugin raises a
# value below 7250 to 7250 (§5). Neither changes any generated config file.
: "${JS_VIS_FAIL_CLOSED:=0}"
export JS_VIS_FAIL_CLOSED
: "${JS_VIS_STALE_MS:=8000}"
export JS_VIS_STALE_MS
# Phase 0 slice 0.4 (docs/voice/nonspatial-phase0-design.md §11): the sim-issued join capability. 0 is shadow: a
# capability that arrives is verified and counted, and no join is ever refused for it. 1 requires a valid one to
# join a room the sim created with vis_authority, and nowhere else. JS_JOIN_CAP_SECRET is the HMAC key shared with
# the sim's [JanusWebRtcVoice] JoinCapabilitySecret, deliberately NOT JS_API_SECRET. Neither changes any generated
# config file. Do not set JS_JOIN_CAP_REQUIRED=1 while ledger O-88/O-46's connector question is open.
: "${JS_JOIN_CAP_REQUIRED:=0}"
export JS_JOIN_CAP_REQUIRED
: "${JS_JOIN_CAP_SECRET:=}"
export JS_JOIN_CAP_SECRET
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
# Slice A.3: TURN for the mixer itself (docs/docker-notes.md, "TURN for the mixer"). All unset = no TURN, and the
# generated janus.jcfg is byte-for-byte what it was before these knobs existed. Static credentials
# (JS_TURN_SERVER + JS_TURN_USER + JS_TURN_PWD) and REST credentials (JS_TURN_REST_API) are mutually exclusive.
: "${JS_TURN_SERVER:=}"
: "${JS_TURN_PORT:=}"
: "${JS_TURN_TYPE:=}"
: "${JS_TURN_USER:=}"
: "${JS_TURN_PWD:=}"
: "${JS_TURN_REST_API:=}"
: "${JS_TURN_REST_API_KEY:=}"
: "${JS_TURN_REST_API_METHOD:=}"
# Slice A.5: ICE diagnostics (docs/docker-notes.md, "ICE diagnostics"). How many ENDED slvoice sessions the collector
# keeps for `legion-voice-selfcheck --sessions`; live ones are always kept. 0 turns the collector off, and with it
# Janus's event broadcast, so janus.jcfg is then what it was before A.5.
: "${JS_ICE_DIAG_HISTORY:=200}"

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
JS_ICE_DIAG_HISTORY=$(uint_or_default JS_ICE_DIAG_HISTORY "$JS_ICE_DIAG_HISTORY" 200)
if [ "${#JS_ICE_DIAG_HISTORY}" -gt 5 ] || [ "$JS_ICE_DIAG_HISTORY" -gt 10000 ]; then
	echo "[entrypoint] WARNING: JS_ICE_DIAG_HISTORY=${JS_ICE_DIAG_HISTORY} is above 10000; using 10000" >&2
	JS_ICE_DIAG_HISTORY=10000
fi
# TURN mode (validated after the effective values are printed): none | static | rest | partial.
if [ -n "$JS_TURN_REST_API" ]; then TURN_MODE=rest
elif [ -n "$JS_TURN_SERVER" ]; then TURN_MODE=static
elif [ -n "${JS_TURN_PORT}${JS_TURN_TYPE}${JS_TURN_USER}${JS_TURN_PWD}${JS_TURN_REST_API_KEY}${JS_TURN_REST_API_METHOD}" ]; then TURN_MODE=partial
else TURN_MODE=none
fi
TURN_PORT=${JS_TURN_PORT:-3478}
TURN_TYPE=$(printf '%s' "${JS_TURN_TYPE:-udp}" | tr '[:upper:]' '[:lower:]')
TURN_REST_METHOD=$(printf '%s' "${JS_TURN_REST_API_METHOD:-POST}" | tr '[:lower:]' '[:upper:]')

# A URL without user info, query or fragment, for the log: a TURN REST API URL can carry a key.
redact_url() {
	printf '%s' "$1" | sed -E 's#^([A-Za-z][A-Za-z0-9+.-]*://)([^/@?#]*@)?([^/?#]*)([^?#]*).*$#\1\3\4#'
}

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
if [ "$JS_ICE_DIAG_HISTORY" -gt 0 ]; then
	echo "[entrypoint] INFO: ice_diag=on history=${JS_ICE_DIAG_HISTORY} (JS_ICE_DIAG_HISTORY, 0 = off; on demand: legion-voice-selfcheck --sessions, --session <handle|agent>)"
else
	echo "[entrypoint] INFO: ice_diag=off (JS_ICE_DIAG_HISTORY=0): no session diagnostics, and Janus event broadcast stays off"
fi
# TURN credentials are reported as set/EMPTY, like the API secrets; a REST URL without its query.
case "$TURN_MODE" in
	none) echo "[entrypoint] INFO: turn=none (JS_TURN_SERVER and JS_TURN_REST_API unset)" ;;
	rest) echo "[entrypoint] INFO: turn=rest rest_api=$(redact_url "$JS_TURN_REST_API") rest_api_key=$(secret_state "$JS_TURN_REST_API_KEY") rest_api_method=${TURN_REST_METHOD}" ;;
	*)    echo "[entrypoint] INFO: turn=${TURN_MODE} server=${JS_TURN_SERVER:-<none>} port=${TURN_PORT} type=${TURN_TYPE} user=$(secret_state "$JS_TURN_USER") pwd=$(secret_state "$JS_TURN_PWD")" ;;
esac
echo "[entrypoint] INFO: empty_room_grace_s=${JS_EMPTY_ROOM_GRACE_S} join_media_timeout_s=${JS_JOIN_MEDIA_TIMEOUT_S}"
case "$(printf '%s' "$JS_VIS_FAIL_CLOSED" | tr '[:upper:]' '[:lower:]')" in
	1|true|yes|on) vis_mode="fail-closed ENABLED" ;;
	*)             vis_mode="fail-closed DISABLED (shadow mode)" ;;
esac
echo "[entrypoint] INFO: vis_fail_closed=${JS_VIS_FAIL_CLOSED} vis_stale_ms=${JS_VIS_STALE_MS}: ${vis_mode} (JS_VIS_FAIL_CLOSED, JS_VIS_STALE_MS; the plugin logs the effective values)"
case "$(printf '%s' "$JS_JOIN_CAP_REQUIRED" | tr '[:upper:]' '[:lower:]')" in
	1|true|yes|on) cap_mode="join capability REQUIRED in rooms with vis_authority" ;;
	*)             cap_mode="join capability shadow (verified and counted, never refused)" ;;
esac
echo "[entrypoint] INFO: join_cap_required=${JS_JOIN_CAP_REQUIRED} join_cap_secret=$(secret_state "$JS_JOIN_CAP_SECRET"): ${cap_mode} (JS_JOIN_CAP_REQUIRED, JS_JOIN_CAP_SECRET)"
# Never enforce a security control with no key: with the requirement on and no secret the plugin could verify
# nothing, so every join to a declared room would be refused for no security gain. Fail closed on the config
# instead, the way O-65 does for the API secrets.
case "$(printf '%s' "$JS_JOIN_CAP_REQUIRED" | tr '[:upper:]' '[:lower:]')" in
	1|true|yes|on)
		if [ "$(secret_state "$JS_JOIN_CAP_SECRET")" = EMPTY ]; then
			echo "[entrypoint] FATAL: JS_JOIN_CAP_REQUIRED=1 with an empty JS_JOIN_CAP_SECRET; refusing to start. Set JS_JOIN_CAP_SECRET=<the sim's [JanusWebRtcVoice] JoinCapabilitySecret> in .env, or set JS_JOIN_CAP_REQUIRED=0. See docs/docker-notes.md, \"Join capability\"." >&2
			exit 1
		fi ;;
esac

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

# ---- TURN for the mixer: validate (slice A.3) ----
# Two styles, never both: static (JS_TURN_SERVER, JS_TURN_PORT, JS_TURN_TYPE, JS_TURN_USER, JS_TURN_PWD) or REST
# (JS_TURN_REST_API, JS_TURN_REST_API_KEY, JS_TURN_REST_API_METHOD). A partial configuration is FATAL, not a
# silent start without TURN. Messages name knobs, never credential values.
turn_fatal() {
	echo "[entrypoint] FATAL: $1; refusing to start. See docs/docker-notes.md, \"TURN for the mixer\"." >&2
	exit 1
}
turn_static_set=""
for knob in JS_TURN_SERVER JS_TURN_PORT JS_TURN_TYPE JS_TURN_USER JS_TURN_PWD; do
	eval "knob_value=\${$knob}"
	if [ -n "$knob_value" ]; then turn_static_set="${turn_static_set:+$turn_static_set, }$knob"; fi
done
if [ -n "$JS_TURN_REST_API" ] && [ -n "$turn_static_set" ]; then
	turn_fatal "JS_TURN_REST_API (REST credentials) and ${turn_static_set} (static TURN settings) are both set; the two styles are mutually exclusive, so set one"
fi
if [ -z "$JS_TURN_REST_API" ] && { [ -n "$JS_TURN_REST_API_KEY" ] || [ -n "$JS_TURN_REST_API_METHOD" ]; }; then
	turn_fatal "JS_TURN_REST_API_KEY or JS_TURN_REST_API_METHOD is set without JS_TURN_REST_API: a partial TURN REST configuration"
fi
if [ -z "$JS_TURN_REST_API" ] && [ -z "$JS_TURN_SERVER" ] && [ -n "$turn_static_set" ]; then
	turn_fatal "${turn_static_set} set without JS_TURN_SERVER: a partial TURN configuration"
fi
if [ "$TURN_MODE" = static ] && { [ -z "$JS_TURN_USER" ] || [ -z "$JS_TURN_PWD" ]; }; then
	turn_fatal "JS_TURN_SERVER is set without credentials: set both JS_TURN_USER and JS_TURN_PWD, or use JS_TURN_REST_API instead"
fi
if [ "$TURN_MODE" = static ]; then
	case "$TURN_TYPE" in
		udp|tcp|tls) ;;
		*) turn_fatal "JS_TURN_TYPE='${JS_TURN_TYPE}' is not one of udp, tcp, tls" ;;
	esac
	case "$TURN_PORT" in
		''|*[!0-9]*) turn_fatal "JS_TURN_PORT='${JS_TURN_PORT}' is not a port number" ;;
	esac
	if [ "$TURN_PORT" -lt 1 ] || [ "$TURN_PORT" -gt 65535 ]; then
		turn_fatal "JS_TURN_PORT=${JS_TURN_PORT} is not a port number"
	fi
fi
if [ "$TURN_MODE" = rest ]; then
	case "$JS_TURN_REST_API" in
		http://*|https://*) ;;
		*) turn_fatal "JS_TURN_REST_API must be an http:// or https:// URL" ;;
	esac
	case "$TURN_REST_METHOD" in
		GET|POST) ;;
		*) turn_fatal "JS_TURN_REST_API_METHOD='${JS_TURN_REST_API_METHOD}' is not GET or POST" ;;
	esac
fi
for knob in JS_TURN_SERVER JS_TURN_USER JS_TURN_PWD JS_TURN_REST_API JS_TURN_REST_API_KEY; do
	eval "knob_value=\${$knob}"
	case "$knob_value" in
		*'"'*|*'\'*) turn_fatal "${knob} contains a double quote or a backslash, which janus.jcfg cannot hold as written" ;;
	esac
done

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

# ---- TURN keys in the nat section (slice A.3) ----
# Written only when TURN is configured, so a start without TURN generates exactly the config it did before these
# knobs existed. The stock template ships each key commented; ensure_kv_in_section uncomments it in place.
# Credentials go into the file, never into the log.
if [ "$TURN_MODE" = static ]; then
	ensure_kv_in_section "$JANUS_JCFG" nat turn_server "\"${JS_TURN_SERVER}\""
	ensure_kv_in_section "$JANUS_JCFG" nat turn_port "${TURN_PORT}"
	ensure_kv_in_section "$JANUS_JCFG" nat turn_type "\"${TURN_TYPE}\""
	ensure_kv_in_section "$JANUS_JCFG" nat turn_user "\"${JS_TURN_USER}\""
	ensure_kv_in_section "$JANUS_JCFG" nat turn_pwd "\"${JS_TURN_PWD}\""
	echo "[entrypoint] turn_server = ${JS_TURN_SERVER}:${TURN_PORT} (${TURN_TYPE}), static credentials user=$(secret_state "$JS_TURN_USER") pwd=$(secret_state "$JS_TURN_PWD")"
elif [ "$TURN_MODE" = rest ]; then
	ensure_kv_in_section "$JANUS_JCFG" nat turn_rest_api "\"${JS_TURN_REST_API}\""
	if [ -n "$JS_TURN_REST_API_KEY" ]; then
		ensure_kv_in_section "$JANUS_JCFG" nat turn_rest_api_key "\"${JS_TURN_REST_API_KEY}\""
	fi
	ensure_kv_in_section "$JANUS_JCFG" nat turn_rest_api_method "\"${TURN_REST_METHOD}\""
	echo "[entrypoint] turn_rest_api = $(redact_url "$JS_TURN_REST_API") method=${TURN_REST_METHOD} key=$(secret_state "$JS_TURN_REST_API_KEY")"
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

# ---- ICE diagnostics: Janus events to the collector (slice A.5) ----
# Written only when the collector runs, so JS_ICE_DIAG_HISTORY=0 generates exactly the config from before A.5. The
# sample event handler POSTs to the collector on loopback; jsep (SDP) and media events are not subscribed.
: "${SLV_ICE_DIAG_PORT:=14229}"
if [ "$JS_ICE_DIAG_HISTORY" -gt 0 ]; then
	ensure_kv_in_section "$JANUS_JCFG" events broadcast true
	# With broadcast on, Janus loads every event handler it has, and the unused ones each log a start-up WARN (GELF a
	# FATAL "giving up"). Only the sample handler is used. Scoped to events:{}: plugins/transports/loggers have `disable` too.
	sed -i '/^events:[[:space:]]*{/,/^}/ s|^\([[:space:]]*\)#*[[:space:]]*disable = .*|\1disable = "libjanus_wsevh.so,libjanus_nanomsgevh.so,libjanus_rabbitmqevh.so,libjanus_gelfevh.so,libjanus_mqttevh.so"|' "$JANUS_JCFG"
	cat > "$CONF_DIR/janus.eventhandler.sampleevh.jcfg" <<EOF
# Written by the legion-voice entrypoint (slice A.5): Janus session, handle, WebRTC and plugin events for the ICE
# diagnostics collector on loopback. No jsep (SDP) and no media events. JS_ICE_DIAG_HISTORY=0 turns this off.
general: {
	enabled = true
	events = "sessions,handles,webrtc,plugins"
	grouping = true
	json = "compact"
	backend = "http://127.0.0.1:${SLV_ICE_DIAG_PORT}/events"
	max_retransmissions = 3
	retransmissions_backoff = 100
}
EOF
fi

# ---- 3. Operator overrides (mounted file > env) ----
if [ -d "$OVERRIDE_DIR" ]; then
	for f in "$OVERRIDE_DIR"/*.jcfg; do
		[ -e "$f" ] || continue
		echo "[entrypoint] applying override $(basename "$f")"
		cp -f "$f" "$CONF_DIR/$(basename "$f")"
	done
fi

# ---- Janus debug level vs TURN REST credentials (slice A.5) ----
# At debug level 5 Janus prints the TURN REST request URI with the API key (turnrest.c:166) and the REST response with
# the TURN username and password (turnrest.c:194), and at 6 the credentials again (ice.c:3657-3658). Said loudly at
# every start, not only in the docs. The level is Janus's -d/--debug-level argument when given, else debug_level in
# the final janus.jcfg (mounted overrides included), else Janus's default 4. TURN REST counts as configured from
# JS_TURN_REST_API or from an uncommented turn_rest_api in that janus.jcfg.
jcfg_value() {
	sed -n "/^$2:[[:space:]]*{/,/^}/p" "$1" 2>/dev/null | grep -E "^[[:space:]]*$3[[:space:]]*=" | head -n 1 \
		| sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]*#.*$//; s/^"//; s/"$//'
}
janus_arg_debug_level() {
	level=""; take=""
	for arg in "$@"; do
		if [ -n "$take" ]; then level=$arg; take=""; continue; fi
		case "$arg" in
			-d|--debug-level) take=1 ;;
			--debug-level=*)  level=${arg#--debug-level=} ;;
			-d[0-9]*)         level=${arg#-d} ;;
		esac
	done
	printf '%s' "$level"
}
JANUS_DEBUG_LEVEL=$(janus_arg_debug_level "$@")
[ -n "$JANUS_DEBUG_LEVEL" ] || JANUS_DEBUG_LEVEL=$(jcfg_value "$JANUS_JCFG" general debug_level)
case "$JANUS_DEBUG_LEVEL" in ''|*[!0-9]*) JANUS_DEBUG_LEVEL=4 ;; esac
if [ -n "$JS_TURN_REST_API" ] || [ -n "$(jcfg_value "$JANUS_JCFG" nat turn_rest_api)" ]; then
	if [ "$JANUS_DEBUG_LEVEL" -ge 5 ]; then
		for line in \
			"=====================================================================================================" \
			"Janus debug level is ${JANUS_DEBUG_LEVEL} and TURN REST is configured (turn_rest_api)." \
			"At debug level 5 and above Janus WILL PRINT TURN REST CREDENTIALS INTO THIS LOG: the REST request URI" \
			"with the API key, and the REST response with the TURN username and password (at 6, the credentials again)." \
			"Anyone who can read this log can use your TURN server. Set debug_level back to 4 (Janus's default), or" \
			"stop using TURN REST while debugging, and treat any log written at this level as secret." \
			"====================================================================================================="; do
			echo "[entrypoint] WARNING: $line" >&2
		done
	fi
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

# ---- 4b. ICE diagnostics collector (slice A.5) ----
# Receives Janus's events (configured above) and keeps session records for `legion-voice-selfcheck --sessions`. It is
# restarted if it exits, and reloads its file, so records survive. SLV_ICE_DIAG_CMD and SLV_ICE_DIAG_ONCE are test seams.
: "${SLV_ICE_DIAG_CMD:=$SLV_LIB_DIR/ice_diag.py}"
: "${SLV_ICE_DIAG_ONCE:=}"
if [ "$JS_ICE_DIAG_HISTORY" -gt 0 ]; then
	export JS_ICE_DIAG_HISTORY JS_ADMIN_PORT JS_ADMIN_BASEPATH SLV_ICE_DIAG_PORT
	(
		while :; do
			rc=0
			"$SLV_ICE_DIAG_CMD" || rc=$?
			[ -z "$SLV_ICE_DIAG_ONCE" ] || break
			echo "[ice-diag] WARNING: the ICE diagnostics collector exited (${rc}); restarting it in 5 s" >&2
			sleep 5
		done
	) &
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
echo "[entrypoint] starting Janus: server_name=${JS_SERVER_NAME} http=${JS_HTTP_PORT}${JS_HTTP_BASEPATH} admin=${JS_ADMIN_PORT}${JS_ADMIN_BASEPATH} ws=${ws_desc} rtp=${JS_RTP_PORT_RANGE} public_host=${JS_PUBLIC_HOST:-<none>} public_ip=${JS_PUBLIC_IP:-<none>} nat_1_1_mapping=${NAT_MAPPING:-<none>} keep_private_host=${JS_KEEP_PRIVATE_HOST} empty_room_grace_s=${JS_EMPTY_ROOM_GRACE_S} join_media_timeout_s=${JS_JOIN_MEDIA_TIMEOUT_S} debug_level=${JANUS_DEBUG_LEVEL}"
exec "$JANUS_BIN" "$@"
