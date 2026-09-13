#!/usr/bin/env bash
# docker-entrypoint.sh tests (O-55 WS default, O-65 fail-closed secrets, nat_1_1 RFC 1918
# guard, JS_NAT_EXTRA_IPS). No Docker: the entrypoint runs against a scratch config dir
# and a stub Janus binary via its JANUS_* path overrides.
#
#   bash tests/entrypoint_test.sh
#
# Env: ENTRYPOINT_UNDER_TEST (default: repo docker-entrypoint.sh); ENTRYPOINT_TEST_TPL
# (a dir holding stock janus.jcfg, janus.transport.http.jcfg, janus.transport.websockets.jcfg;
# default: /opt/janus/share/janus-templates if present, else the vendored Janus samples).
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
EP=${ENTRYPOINT_UNDER_TEST:-$REPO/docker-entrypoint.sh}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

TPL="$WORK/tpl"
mkdir -p "$TPL"
if [ -n "${ENTRYPOINT_TEST_TPL:-}" ]; then
	cp "$ENTRYPOINT_TEST_TPL"/janus.jcfg "$ENTRYPOINT_TEST_TPL"/janus.transport.http.jcfg \
		"$ENTRYPOINT_TEST_TPL"/janus.transport.websockets.jcfg "$TPL"/
elif [ -f /opt/janus/share/janus-templates/janus.jcfg ]; then
	cp /opt/janus/share/janus-templates/janus.jcfg /opt/janus/share/janus-templates/janus.transport.http.jcfg \
		/opt/janus/share/janus-templates/janus.transport.websockets.jcfg "$TPL"/
elif [ -f "$REPO/vendor/janus-gateway/conf/janus.jcfg.sample.in" ]; then
	tr -d '\r' < "$REPO/vendor/janus-gateway/conf/janus.jcfg.sample.in" > "$TPL/janus.jcfg"
	tr -d '\r' < "$REPO/vendor/janus-gateway/conf/janus.transport.http.jcfg.sample" > "$TPL/janus.transport.http.jcfg"
	tr -d '\r' < "$REPO/vendor/janus-gateway/conf/janus.transport.websockets.jcfg.sample" > "$TPL/janus.transport.websockets.jcfg"
else
	echo "entrypoint_test: no Janus jcfg templates found (set ENTRYPOINT_TEST_TPL)" >&2
	exit 2
fi

STUB="$WORK/janus-stub"
printf '#!/bin/sh\necho "STUB-JANUS-RAN $*"\n' > "$STUB"
chmod +x "$STUB"

pass=0; fail=0
OUT=""; RC=0; CONF=""

# run_ep VAR=value ... : run the entrypoint with a clean JS_* environment plus the given vars.
run_ep() {
	CONF="$WORK/conf.$((pass + fail))"
	rm -rf "$CONF"; mkdir -p "$CONF"
	OUT=$(env -i PATH="$PATH" \
		JANUS_CONF_DIR="$CONF" JANUS_TEMPLATE_DIR="$TPL" JANUS_OVERRIDE_DIR="$WORK/no-overrides" JANUS_BIN="$STUB" \
		JS_API_SECRET=test-api-secret JS_ADMIN_SECRET=test-admin-secret \
		"$@" sh "$EP" --stub-arg 2>&1)
	RC=$?
}

ok()   { pass=$((pass + 1)); echo "ok   $1"; }
bad()  { fail=$((fail + 1)); echo "FAIL $1"; echo "$OUT" | sed 's/^/     | /'; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

has()     { printf '%s\n' "$OUT" | grep -Fq -- "$1"; }
first_line() { printf '%s\n' "$OUT" | grep -nF -- "$1" | head -1 | cut -d: -f1; }
cfg()     { grep -E "^[[:space:]]*$2[[:space:]]*=" "$CONF/$1" | head -1 | sed 's/^[[:space:]]*//'; }
# sed range, as in the entrypoint (the image's awk is mawk, which lacks [[:space:]]).
section() { sed -n "/^$2:[[:space:]]*{/,/^}/p" "$CONF/$1"; }

# ---- O-65: fail closed on empty secrets ----
run_ep JS_API_SECRET= JS_ADMIN_SECRET=
check "empty secrets -> exit 1" '[ "$RC" -eq 1 ]'
check "empty secrets -> FATAL names both" 'has "FATAL: JS_API_SECRET and JS_ADMIN_SECRET empty"'
check "empty secrets -> Janus not started" '! has STUB-JANUS-RAN'
check "empty secrets -> FATAL names the keys to set and the override" 'has "Set JS_API_SECRET=" && has "and JS_ADMIN_SECRET=" && has "or set ALLOW_INSECURE_DEV=true in .env"'
check "empty secrets -> effective values printed before the FATAL" '[ "$(first_line "INFO: admin API bind=")" -lt "$(first_line "FATAL:")" ] && has "INFO: secrets api_secret=EMPTY admin_secret=EMPTY allow_insecure_dev=false"'

run_ep JS_ADMIN_SECRET=
check "empty admin secret only -> exit 1" '[ "$RC" -eq 1 ] && has "FATAL: JS_ADMIN_SECRET empty"'
check "empty admin secret only -> names only that key" 'has "Set JS_ADMIN_SECRET=" && ! has "Set JS_API_SECRET="'

run_ep JS_API_SECRET="   "
check "whitespace-only api secret -> exit 1" '[ "$RC" -eq 1 ] && has "FATAL: JS_API_SECRET empty"'

run_ep JS_API_SECRET= JS_ADMIN_SECRET= ALLOW_INSECURE_DEV=false
check "ALLOW_INSECURE_DEV=false -> still exit 1" '[ "$RC" -eq 1 ]'

run_ep JS_API_SECRET= JS_ADMIN_SECRET= ALLOW_INSECURE_DEV=true
check "ALLOW_INSECURE_DEV=true -> proceeds (exit 0, Janus exec'd)" '[ "$RC" -eq 0 ] && has "STUB-JANUS-RAN --stub-arg"'
check "ALLOW_INSECURE_DEV=true -> dev-only WARNING" 'has "starting anyway because ALLOW_INSECURE_DEV=true"'

run_ep
check "secrets set -> proceeds" '[ "$RC" -eq 0 ] && has "STUB-JANUS-RAN --stub-arg"'
check "secrets set -> api_secret written" '[ "$(cfg janus.jcfg api_secret)" = "api_secret = \"test-api-secret\"" ]'
check "secrets set -> admin_secret written" '[ "$(cfg janus.jcfg admin_secret)" = "admin_secret = \"test-admin-secret\"" ]'

# ---- nat_1_1 guard ----
run_ep JS_PUBLIC_IP=192.168.1.225
check "RFC1918 192.168/16 -> WARN names it" 'has "WARNING: nat_1_1_mapping address 192.168.1.225 is private/loopback"'
check "RFC1918 only -> off-LAN consequence WARN" 'has "off-LAN viewers will fail ICE"'
check "RFC1918 -> still starts, mapping unchanged" '[ "$RC" -eq 0 ] && [ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"192.168.1.225\"" ]'

for ip in 10.1.2.3 172.16.0.1 172.31.255.254 127.0.0.1; do
	run_ep JS_PUBLIC_IP=$ip
	check "private/loopback $ip -> WARN" 'has "nat_1_1_mapping address $ip is private/loopback"'
done

for ip in 203.0.113.7 172.32.0.1 172.15.0.1 11.0.0.1; do
	run_ep JS_PUBLIC_IP=$ip
	check "public $ip -> no WARN, mapping + keep_private_host as before" \
		'[ "$RC" -eq 0 ] && ! has "private/loopback" && ! has "off-LAN" && [ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"$ip\"" ] && [ "$(cfg janus.jcfg keep_private_host)" = "keep_private_host = true" ]'
done

if getent ahostsv4 localhost >/dev/null 2>&1; then
	run_ep JS_PUBLIC_HOST=localhost
	check "resolved JS_PUBLIC_HOST loopback -> WARN names resolved address" 'has "resolved JS_PUBLIC_HOST=" && has "is private/loopback" && has "off-LAN viewers will fail ICE"'
else
	echo "skip resolved JS_PUBLIC_HOST case (getent ahostsv4 unavailable here)"
fi

# ---- JS_NAT_EXTRA_IPS ----
run_ep JS_PUBLIC_IP=192.168.1.225 JS_NAT_EXTRA_IPS=203.0.113.7
check "extra IP -> mapping has both addresses" '[ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"192.168.1.225,203.0.113.7\"" ]'
check "extra public IP -> private WARN kept, no off-LAN consequence" 'has "192.168.1.225 is private/loopback" && ! has "off-LAN"'

run_ep JS_PUBLIC_IP=192.168.1.225 "JS_NAT_EXTRA_IPS= 203.0.113.7 , 198.51.100.9,203.0.113.7,192.168.1.225"
check "extra list -> spaces trimmed, duplicates dropped" '[ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"192.168.1.225,203.0.113.7,198.51.100.9\"" ]'

run_ep JS_NAT_EXTRA_IPS=203.0.113.7
check "extra IP without public address -> mapping is the extra alone" '[ "$RC" -eq 0 ] && [ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"203.0.113.7\"" ] && ! has "neither JS_PUBLIC_IP"'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_NAT_EXTRA_IPS=legiongrid.example
check "non-IPv4 extra -> FATAL exit 1" '[ "$RC" -eq 1 ] && has "FATAL: JS_NAT_EXTRA_IPS entry" && ! has STUB-JANUS-RAN'

# ---- O-55: defaults reproduce pre-6m behaviour; narrowing is opt-in ----
run_ep
check "no JS_ADMIN_BIND -> 0.0.0.0 + WARN" 'has "INFO: admin API bind=0.0.0.0 port=14225" && has "WARNING: admin API is reachable on all interfaces; protected by JS_ADMIN_SECRET only"'
check "no JS_WS_ENABLED -> ws on" '[ "$(cfg janus.transport.websockets.jcfg ws)" = "ws = true" ] && [ "$(cfg janus.transport.websockets.jcfg ws_port)" = "ws_port = 8188" ] && ! section janus.jcfg transports | grep -Eq "^[[:space:]]*disable" && has "INFO: websockets transport enabled=true port=8188" && has " ws=8188 "'
check "defaults -> admin bind and WS are the first lines" '[ "$(first_line "INFO: admin API bind=")" -eq 1 ] && [ "$(first_line "INFO: websockets transport")" -le 3 ]'

run_ep JS_ADMIN_BIND=192.168.1.225
check "JS_ADMIN_BIND=192.168.1.225 -> INFO names it, no WARN" 'has "INFO: admin API bind=192.168.1.225 port=14225" && ! has "reachable on all interfaces"'

run_ep JS_WS_ENABLED=false
check "JS_WS_ENABLED=false -> ws = false" '[ "$(cfg janus.transport.websockets.jcfg ws)" = "ws = false" ] && has "INFO: websockets transport enabled=false"'
check "JS_WS_ENABLED=false -> transports disable websockets" 'section janus.jcfg transports | grep -Eq "^[[:space:]]*disable = \"libjanus_websockets.so\""'
check "JS_WS_ENABLED=false -> plugins/loggers/events disable untouched" '! section janus.jcfg plugins | grep -Eq "^[[:space:]]*disable" && ! section janus.jcfg loggers | grep -Eq "^[[:space:]]*disable" && ! section janus.jcfg events | grep -Eq "^[[:space:]]*disable"'
check "JS_WS_ENABLED=false -> start line says ws=off" 'has " ws=off "'

run_ep JS_WS_ENABLED=true JS_WS_PORT=24288
check "JS_WS_ENABLED=true -> ws = true on JS_WS_PORT" '[ "$(cfg janus.transport.websockets.jcfg ws)" = "ws = true" ] && [ "$(cfg janus.transport.websockets.jcfg ws_port)" = "ws_port = 24288" ]'
check "JS_WS_ENABLED=true -> transport not disabled" '! section janus.jcfg transports | grep -Eq "^[[:space:]]*disable"'

echo "entrypoint_test: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
