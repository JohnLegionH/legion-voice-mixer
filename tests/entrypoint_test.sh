#!/usr/bin/env bash
# Copyright (c) 2026 Legion Builds
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GNU General Public License, version 3 only; see the LICENSE file in the repository root.
# docker-entrypoint.sh tests:
#   - O-55 WS default, O-65 fail-closed secrets, the nat_1_1_mapping verdict, JS_NAT_EXTRA_IPS;
#   - A.1 address discovery, state file and periodic re-check;
#   - A.5 Janus events for the ICE diagnostics collector, and the debug-level warning for TURN REST credentials.
# No Docker: the entrypoint runs against a scratch config dir and a stub Janus binary via its JANUS_*
# path overrides, and address discovery runs against a stub probe (no network).
#
#   bash tests/entrypoint_test.sh
#
# Env:
#   ENTRYPOINT_UNDER_TEST      default: the repo's docker-entrypoint.sh
#   ENTRYPOINT_LIB_UNDER_TEST  the directory holding public-address.sh; default: the repo's entrypoint/
#   ENTRYPOINT_TEST_TPL        a dir holding the stock janus.jcfg, janus.transport.http.jcfg and
#                              janus.transport.websockets.jcfg; default: /opt/janus/share/janus-templates
#                              if present, else the vendored Janus samples
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
EP=${ENTRYPOINT_UNDER_TEST:-$REPO/docker-entrypoint.sh}
LIB=${ENTRYPOINT_LIB_UNDER_TEST:-$REPO/entrypoint}

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

# Stub address probe. Answers come from STUB_STUN / STUB_DNS / STUB_CONTAINER / STUB_PARTICIPANTS,
# where empty means no answer. STUB_STUN_FILE, when set, supplies one STUN answer per call; a line
# "-" means no answer. Every call is appended to STUB_CALLS.
PROBE="$WORK/probe-stub"
cat > "$PROBE" <<'EOF'
#!/bin/sh
if [ -n "${STUB_CALLS:-}" ]; then echo "$*" >> "$STUB_CALLS"; fi
case "$1" in
	stun)
		if [ -n "${STUB_STUN_FILE:-}" ]; then
			v=$(head -n 1 "$STUB_STUN_FILE")
			tail -n +2 "$STUB_STUN_FILE" > "$STUB_STUN_FILE.next" && mv "$STUB_STUN_FILE.next" "$STUB_STUN_FILE"
			if [ "$v" = - ]; then v=""; fi
		else
			v=${STUB_STUN:-}
		fi ;;
	dns)          v=${STUB_DNS:-} ;;
	system)       v=${STUB_CONTAINER:-} ;;
	participants)
		v=${STUB_PARTICIPANTS:-}
		if [ -z "$v" ] && [ -n "${STUB_PARTICIPANTS_ERR:-}" ]; then echo "$STUB_PARTICIPANTS_ERR" >&2; exit 1; fi ;;
	*)            exit 2 ;;
esac
[ -n "$v" ] || exit 1
echo "$v"
EOF
chmod +x "$PROBE"

pass=0; fail=0
OUT=""; RC=0; CONF=""

# run_ep VAR=value ... : run the entrypoint with a clean JS_* environment plus the given vars.
# The periodic re-check is off unless a test turns it on (a background watcher sleeping 300 s would
# hold the output capture open).
run_ep() {
	CONF="$WORK/conf.$((pass + fail))"
	rm -rf "$CONF"; mkdir -p "$CONF"
	OUT=$(env -i PATH="$PATH" \
		JANUS_CONF_DIR="$CONF" JANUS_TEMPLATE_DIR="$TPL" JANUS_OVERRIDE_DIR="$WORK/no-overrides" JANUS_BIN="$STUB" \
		SLV_LIB_DIR="$LIB" SLV_ADDR_PROBE="$PROBE" SLV_ADDR_STATE_FILE="$CONF/state/public-address.json" \
		JS_PUBLIC_IP_REFRESH_S=0 JS_SELFCHECK=off SLV_EFFECTIVE_CONFIG="$CONF/state/effective-config.json" \
		JS_API_SECRET=test-api-secret JS_ADMIN_SECRET=test-admin-secret JS_ICE_DIAG_HISTORY=0 \
		"$@" sh "$EP" --stub-arg ${EP_ARGS:-} 2>&1)
	RC=$?
}
# EP_ARGS: extra Janus arguments for the next run_ep (A.5's debug-level guard reads -d / --debug-level).
EP_ARGS=""

# run_watch VAR=value ... : run the re-check watcher in the foreground with the stub probe, no sleeping,
# and a recorded restart. Running state: 'voice.example.test' discovered as 203.0.113.7.
run_watch() {
	rm -f "$WORK/watch-state.json"
	OUT=$(env -i PATH="$PATH" \
		SLV_LIB_DIR="$LIB" SLV_ADDR_PROBE="$PROBE" SLV_ADDR_STATE_FILE="$WORK/watch-state.json" \
		SLV_ADDR_SLEEP=true SLV_ADDR_POLL_S=15 SLV_ADDR_RESTART_CMD="echo RESTART-CALLED" \
		JS_STUN_SERVER=stun.example.test:3478 JS_PUBLIC_IP_DNS_RESOLVER=1.1.1.1 JS_PUBLIC_IP_DISCOVERY=auto \
		JS_PUBLIC_IP_REFRESH_S=300 JS_PUBLIC_IP_CHANGE_ACTION=warn JS_PUBLIC_IP_RESTART_MAX_WAIT_S=900 \
		JS_HTTP_PORT=14223 JS_HTTP_BASEPATH=/voice \
		SLV_ADDR_HOST=voice.example.test SLV_ADDR_HOST_VAR=JS_PUBLIC_HOST SLV_ADDR_RUNNING=203.0.113.7 \
		"$@" sh "$LIB/public-address-watch.sh" 2>&1)
	RC=$?
}

ok()   { pass=$((pass + 1)); echo "ok   $1"; }
bad()  { fail=$((fail + 1)); echo "FAIL $1"; echo "$OUT" | sed 's/^/     | /'; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

has()     { printf '%s\n' "$OUT" | grep -Fq -- "$1"; }
count()   { printf '%s\n' "$OUT" | grep -cF -- "$1"; }
first_line() { printf '%s\n' "$OUT" | grep -nF -- "$1" | head -1 | cut -d: -f1; }
cfg()     { grep -E "^[[:space:]]*$2[[:space:]]*=" "$CONF/$1" | head -1 | sed 's/^[[:space:]]*//'; }
# sed range, as in the entrypoint (the image's awk is mawk, which lacks [[:space:]]).
section() { sed -n "/^$2:[[:space:]]*{/,/^}/p" "$CONF/$1"; }
hasstate() { grep -Fq -- "$1" "$CONF/state/public-address.json" 2>/dev/null; }
# The state file is valid JSON (checked only where python3 exists, as in the image).
json_ok() { if command -v python3 >/dev/null 2>&1; then python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$1"; else [ -s "$1" ]; fi; }

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

# ---- nat_1_1_mapping verdict (A.1: judged on the FINAL mapping) ----
run_ep JS_PUBLIC_IP=192.168.1.225
check "RFC1918 only -> ERROR no public address, with remediation" 'has "ERROR: nat_1_1_mapping=192.168.1.225 has no public address: off-LAN viewers will fail ICE" && has "ERROR: remediation:"'
check "RFC1918 only -> no per-address private WARNING" '! has "is private/loopback"'
check "RFC1918 -> still starts, mapping unchanged" '[ "$RC" -eq 0 ] && [ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"192.168.1.225\"" ]'

for ip in 10.1.2.3 172.16.0.1 172.31.255.254 127.0.0.1 169.254.1.1; do
	run_ep JS_PUBLIC_IP=$ip
	check "private/loopback/link-local $ip -> ERROR verdict" 'has "ERROR: nat_1_1_mapping=$ip has no public address" && hasstate "\"verdict\":\"no_public\""'
done

for ip in 203.0.113.7 172.32.0.1 172.15.0.1 11.0.0.1; do
	run_ep JS_PUBLIC_IP=$ip
	check "public $ip -> INFO verdict, mapping + keep_private_host as before" \
		'[ "$RC" -eq 0 ] && has "INFO: nat_1_1_mapping=$ip includes public address(es) $ip" && ! has "ERROR:" && [ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"$ip\"" ] && [ "$(cfg janus.jcfg keep_private_host)" = "keep_private_host = true" ]'
done

# ---- JS_NAT_EXTRA_IPS ----
run_ep JS_PUBLIC_IP=192.168.1.225 JS_NAT_EXTRA_IPS=203.0.113.7
check "extra IP -> mapping has both addresses" '[ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"192.168.1.225,203.0.113.7\"" ]'
check "extra public IP -> no private WARNING, no ERROR, INFO names the public address" '! has "is private/loopback" && ! has "ERROR:" && has "includes public address(es) 203.0.113.7"'

run_ep JS_PUBLIC_IP=192.168.1.225 "JS_NAT_EXTRA_IPS= 203.0.113.7 , 198.51.100.9,203.0.113.7,192.168.1.225"
check "extra list -> spaces trimmed, duplicates dropped" '[ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"192.168.1.225,203.0.113.7,198.51.100.9\"" ]'

run_ep JS_NAT_EXTRA_IPS=203.0.113.7
check "extra IP without public address -> mapping is the extra alone" '[ "$RC" -eq 0 ] && [ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"203.0.113.7\"" ] && ! has "neither JS_PUBLIC_IP"'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_NAT_EXTRA_IPS=legiongrid.example
check "non-IPv4 extra -> FATAL exit 1" '[ "$RC" -eq 1 ] && has "FATAL: JS_NAT_EXTRA_IPS entry" && ! has STUB-JANUS-RAN'

# ---- A.1: address discovery ----
run_ep JS_PUBLIC_IP=203.0.113.7 STUB_CALLS="$WORK/calls.literal" STUB_STUN=198.51.100.1
check "A.1 literal JS_PUBLIC_IP -> passes through untouched" '[ "$RC" -eq 0 ] && [ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"203.0.113.7\"" ]'
check "A.1 literal JS_PUBLIC_IP -> no lookup at all" '[ ! -e "$WORK/calls.literal" ] && ! has "discovery source" && ! has "discovering the public address"'
check "A.1 literal -> state file: no sources, public verdict" 'hasstate "\"sources\":[]" && hasstate "\"literal_ips\":[\"203.0.113.7\"]" && hasstate "\"verdict\":\"public\""'

run_ep JS_PUBLIC_HOST=voice.example.test STUB_STUN=203.0.113.7 STUB_DNS=192.168.1.225 STUB_CONTAINER=192.168.1.225
check "A.1 hostname -> every source logged with its answer" 'has "INFO: discovery source stun (stun.l.google.com:19302) -> 203.0.113.7 (public)" && has "INFO: discovery source dns (resolver 1.1.1.1) -> 192.168.1.225 (private)" && has "INFO: discovery source container (container resolver) -> 192.168.1.225 (private)"'
check "A.1 STUN public + DNS private -> public wins" 'has "INFO: discovery winner: stun -> 203.0.113.7" && [ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"203.0.113.7\"" ] && hasstate "\"winner\":{\"source\":\"stun\",\"address\":\"203.0.113.7\"}"'
check "A.1 STUN public + DNS private -> no ERROR, no private WARNING" '! has "ERROR:" && ! has "is private/loopback"'

# The Legion Grid shape: a literal LAN address plus a DDNS name that hairpins to it.
run_ep JS_PUBLIC_IP=192.168.1.225 JS_PUBLIC_HOST=voice.example.test STUB_STUN=203.0.113.7 STUB_DNS=192.168.1.225 STUB_CONTAINER=192.168.1.225
check "A.1 literal + hostname -> discovered public first, literal kept" '[ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"203.0.113.7,192.168.1.225\"" ] && has "INFO: nat_1_1_mapping=203.0.113.7,192.168.1.225 includes public address(es) 203.0.113.7"'
check "A.1 one ADDRESS_RESOLUTION log line, and the state file is valid JSON" '[ "$(count "ADDRESS_RESOLUTION {")" -eq 1 ] && json_ok "$CONF/state/public-address.json" && hasstate "\"nat_1_1_mapping\":[\"203.0.113.7\",\"192.168.1.225\"]" && hasstate "\"event\":\"start\""'

run_ep JS_PUBLIC_HOST=voice.example.test STUB_STUN= STUB_DNS=203.0.113.9 STUB_CONTAINER=192.168.1.225
check "A.1 STUN unavailable -> logged as no answer" 'has "INFO: discovery source stun (stun.l.google.com:19302) -> no answer" && hasstate "{\"name\":\"stun\",\"via\":\"stun.l.google.com:19302\",\"status\":\"no_answer\",\"address\":null,\"class\":null}"'
check "A.1 STUN unavailable -> DNS fallback used, logged as such" 'has "INFO: discovery winner: dns -> 203.0.113.9 (fallback: stun gave no answer)" && [ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"203.0.113.9\"" ]'

run_ep JS_PUBLIC_HOST=voice.example.test STUB_STUN=10.0.0.5 STUB_DNS=192.168.1.225 STUB_CONTAINER=192.168.1.225
check "A.1 every source private -> ERROR verdict, still starts" '[ "$RC" -eq 0 ] && has "ERROR: nat_1_1_mapping=10.0.0.5 has no public address" && has "ERROR: remediation:" && hasstate "\"verdict\":\"no_public\""'

run_ep JS_PUBLIC_HOST=voice.example.test STUB_STUN=100.64.12.34 STUB_DNS= STUB_CONTAINER=192.168.1.225
check "A.1 CGNAT address -> CGNAT verdict and TURN warning" 'has "WARNING: nat_1_1_mapping address 100.64.12.34 is in 100.64.0.0/10: this is carrier-grade NAT (CGNAT)" && has "a TURN server is required" && hasstate "\"verdict\":\"cgnat\"" && ! has "has no public address"'
run_ep JS_PUBLIC_IP=100.127.255.254
check "A.1 literal 100.127.255.254 -> CGNAT verdict" 'hasstate "\"verdict\":\"cgnat\"" && has "carrier-grade NAT"'
for ip in 100.63.255.255 100.128.0.1; do
	run_ep JS_PUBLIC_IP=$ip
	check "A.1 $ip is outside 100.64.0.0/10 -> public verdict" 'hasstate "\"verdict\":\"public\"" && ! has "carrier-grade"'
done

run_ep JS_PUBLIC_HOST=voice.example.test JS_PUBLIC_IP_DISCOVERY=static STUB_CALLS="$WORK/calls.static" STUB_STUN=203.0.113.7 STUB_CONTAINER=127.0.0.1
check "A.1 static -> the container resolver only" '[ "$(cat "$WORK/calls.static")" = "system voice.example.test" ] && has "discovery winner: container -> 127.0.0.1" && has "has no public address"'
check "A.1 static -> no periodic re-check" 'has "periodic public address re-check off" && ! has "[address-watch]"'

run_ep JS_PUBLIC_HOST=voice.example.test JS_PUBLIC_IP_DISCOVERY=dns STUB_CALLS="$WORK/calls.dns" STUB_STUN=203.0.113.7 STUB_DNS=198.51.100.20
check "A.1 dns -> only the external resolver is asked" '[ "$(cat "$WORK/calls.dns")" = "dns voice.example.test 1.1.1.1" ] && has "discovery winner: dns -> 198.51.100.20"'

run_ep JS_PUBLIC_IP=voice.example.test STUB_STUN=203.0.113.7
check "A.1 hostname in JS_PUBLIC_IP -> discovered" 'has "(JS_PUBLIC_IP) with JS_PUBLIC_IP_DISCOVERY=auto" && [ "$(cfg janus.jcfg nat_1_1_mapping)" = "nat_1_1_mapping = \"203.0.113.7\"" ]'

run_ep JS_PUBLIC_HOST=voice.example.test
check "A.1 no source answers -> FATAL exit 1" '[ "$RC" -eq 1 ] && has "FATAL: could not discover an IPv4 address for JS_PUBLIC_HOST=" && ! has STUB-JANUS-RAN'

run_ep JS_PUBLIC_HOST="bad host;name"
check "A.1 not a hostname -> FATAL exit 1" '[ "$RC" -eq 1 ] && has "is neither an IPv4 literal nor a hostname"'

run_ep JS_PUBLIC_IP_DISCOVERY=bogus
check "A.1 bad JS_PUBLIC_IP_DISCOVERY -> FATAL after the effective values" '[ "$RC" -eq 1 ] && has "FATAL: JS_PUBLIC_IP_DISCOVERY=" && [ "$(first_line "INFO: address discovery=bogus")" -lt "$(first_line "FATAL:")" ]'
run_ep JS_PUBLIC_IP_CHANGE_ACTION=reboot
check "A.1 bad JS_PUBLIC_IP_CHANGE_ACTION -> FATAL" '[ "$RC" -eq 1 ] && has "FATAL: JS_PUBLIC_IP_CHANGE_ACTION="'
run_ep JS_PUBLIC_IP_REFRESH_S=5m
check "A.1 non-integer JS_PUBLIC_IP_REFRESH_S -> WARNING and default" 'has "WARNING: JS_PUBLIC_IP_REFRESH_S='\''5m'\'' is not a non-negative integer; using 300" && has "refresh_s=300"'
run_ep JS_PUBLIC_IP_REFRESH_S=
check "A.1 defaults printed with the effective values" 'has "INFO: address discovery=auto stun_server=stun.l.google.com:19302 dns_resolver=1.1.1.1 refresh_s=300 change_action=warn restart_max_wait_s=900"'

run_ep JS_PUBLIC_HOST=voice.example.test STUB_STUN=203.0.113.7 JS_PUBLIC_IP_REFRESH_S=300 SLV_ADDR_WATCH_MAX_CHECKS=1 SLV_ADDR_SLEEP=true
check "A.1 hostname + refresh -> re-check watcher started" 'has "[address-watch] INFO: re-checking the public address of '\''voice.example.test'\'' every 300 s" && has STUB-JANUS-RAN'

# ---- A.1: change detection ----
. "$LIB/public-address.sh"
check "A.1 change seen once -> no action" '[ "$(addr_change_step 203.0.113.7 - 0 198.51.100.4)" = "198.51.100.4 1 none" ]'
check "A.1 change seen twice -> action" '[ "$(addr_change_step 203.0.113.7 198.51.100.4 1 198.51.100.4)" = "198.51.100.4 2 change" ]'
check "A.1 failed lookup -> never a change, streak broken" '[ "$(addr_change_step 203.0.113.7 198.51.100.4 1 "")" = "- 0 none" ]'
check "A.1 two different new addresses -> no action" '[ "$(addr_change_step 203.0.113.7 198.51.100.4 1 198.51.100.5)" = "198.51.100.5 1 none" ]'
check "A.1 private answer while running public -> not a change" '[ "$(addr_change_step 203.0.113.7 - 0 192.168.1.225)" = "- 0 none" ]'
check "A.1 same address as running -> no action" '[ "$(addr_change_step 203.0.113.7 198.51.100.4 1 203.0.113.7)" = "- 0 none" ]'

printf '198.51.100.4\n' > "$WORK/stun.seq"
run_watch SLV_ADDR_WATCH_MAX_CHECKS=1 STUB_STUN_FILE="$WORK/stun.seq"
check "A.1 watcher: change detected once -> no action" '[ "$RC" -eq 0 ] && has "1 of 2 agreeing checks" && ! has "public address changed" && ! has RESTART-CALLED'

printf '198.51.100.4\n198.51.100.4\n198.51.100.4\n' > "$WORK/stun.seq"
run_watch SLV_ADDR_WATCH_MAX_CHECKS=3 STUB_STUN_FILE="$WORK/stun.seq"
check "A.1 watcher: change detected twice -> WARN with old and new, once" '[ "$(count "public address changed: 203.0.113.7 -> 198.51.100.4")" -eq 1 ] && has "JS_PUBLIC_IP_CHANGE_ACTION=warn: not restarting" && ! has RESTART-CALLED'
check "A.1 watcher: change -> ADDRESS_RESOLUTION change event and state file" 'has "\"event\":\"change\"" && grep -Fq "\"observed_address\":\"198.51.100.4\"" "$WORK/watch-state.json" && json_ok "$WORK/watch-state.json"'

printf -- '-\n198.51.100.4\n' > "$WORK/stun.seq"
run_watch SLV_ADDR_WATCH_MAX_CHECKS=2 STUB_STUN_FILE="$WORK/stun.seq"
check "A.1 watcher: failed lookup then new address -> no action" 'has "a failed lookup is not a change" && ! has "public address changed"'

printf '198.51.100.4\n198.51.100.4\n' > "$WORK/stun.seq"
run_watch SLV_ADDR_WATCH_MAX_CHECKS=2 STUB_STUN_FILE="$WORK/stun.seq" JS_PUBLIC_IP_CHANGE_ACTION=restart STUB_PARTICIPANTS=0
check "A.1 watcher restart: zero participants -> restarts at once" 'has "restarting Janus now to advertise 198.51.100.4: zero participants" && has RESTART-CALLED'

printf '198.51.100.4\n198.51.100.4\n' > "$WORK/stun.seq"
run_watch SLV_ADDR_WATCH_MAX_CHECKS=2 STUB_STUN_FILE="$WORK/stun.seq" JS_PUBLIC_IP_CHANGE_ACTION=restart STUB_PARTICIPANTS=3 JS_PUBLIC_IP_RESTART_MAX_WAIT_S=30
check "A.1 watcher restart: participants stay -> restarts after the max wait, logging the outage" 'has "with 3 participant(s) still connected after 30 s: taking the outage" && has RESTART-CALLED'

# ---- A.3: TURN for the mixer ----
TURN_USER_V="turn-user-SECRET-u1"
TURN_PWD_V="turn-pwd-SECRET-p1"
TURN_KEY_V="turn-key-SECRET-k1"
no_secrets() { ! has "$TURN_USER_V" && ! has "$TURN_PWD_V" && ! has "$TURN_KEY_V"; }
natkey() { section janus.jcfg nat | grep -Eq "^[[:space:]]*$1 = $2\$"; }
GOLDEN="$HERE/golden/janus.jcfg.pre-turn"

run_ep JS_PUBLIC_IP=203.0.113.7
NO_TURN_SUM=$(sha256sum "$CONF/janus.jcfg" | cut -d' ' -f1)
check "A.3 no TURN -> no turn keys in the nat section, turn=none reported" '[ "$RC" -eq 0 ] && ! section janus.jcfg nat | grep -Eq "^[[:space:]]*turn_" && has "INFO: turn=none"'
if [ -f /opt/janus/share/janus-templates/janus.jcfg ] && [ -z "${ENTRYPOINT_TEST_TPL:-}" ] && [ -f "$GOLDEN" ]; then
	check "A.3 no TURN -> janus.jcfg byte-identical to the pre-A.3 entrypoint's (tests/golden)" 'cmp -s "$CONF/janus.jcfg" "$GOLDEN"'
else
	echo "skip golden janus.jcfg comparison (needs the image's own templates)"
fi
run_ep JS_PUBLIC_IP=203.0.113.7 JS_TURN_SERVER= JS_TURN_PORT= JS_TURN_TYPE= JS_TURN_USER= JS_TURN_PWD= JS_TURN_REST_API= JS_TURN_REST_API_KEY= JS_TURN_REST_API_METHOD=
check "A.3 no TURN, knobs present but empty -> the same janus.jcfg" '[ "$(sha256sum "$CONF/janus.jcfg" | cut -d" " -f1)" = "$NO_TURN_SUM" ]'

# ---- 0.3: visibility authority knobs. Environment only: they must not change any generated config. ----
# tests/golden/phase0-knobs-off holds what the pre-0.3 image's own entrypoint generated (image c7b57f567f48, run with
# JS_PUBLIC_IP=203.0.113.7 and this file's run_ep environment), plus that image's janus.plugin.slvoice.jcfg.
GOLDEN0="$HERE/golden/phase0-knobs-off"
VISSTUB="$WORK/janus-stub-vis"
printf '#!/bin/sh\necho "STUB-JANUS-RAN $*"\necho "STUB-ENV JS_VIS_FAIL_CLOSED=${JS_VIS_FAIL_CLOSED-<unset>} JS_VIS_STALE_MS=${JS_VIS_STALE_MS-<unset>}"\n' > "$VISSTUB"
chmod +x "$VISSTUB"
golden0() {
	cmp -s "$CONF/janus.jcfg" "$GOLDEN0/janus.jcfg" \
		&& cmp -s "$CONF/janus.transport.http.jcfg" "$GOLDEN0/janus.transport.http.jcfg" \
		&& cmp -s "$CONF/janus.transport.websockets.jcfg" "$GOLDEN0/janus.transport.websockets.jcfg" \
		&& cmp -s "$CONF/state/effective-config.json" "$GOLDEN0/effective-config.json"
}
IMAGE_TPL=no
if [ -f /opt/janus/share/janus-templates/janus.jcfg ] && [ -z "${ENTRYPOINT_TEST_TPL:-}" ]; then IMAGE_TPL=yes; fi

run_ep JS_PUBLIC_IP=203.0.113.7 JANUS_BIN="$VISSTUB"
check "0.3 knobs unset -> Janus runs with JS_VIS_FAIL_CLOSED=0 and JS_VIS_STALE_MS=8000 exported" '[ "$RC" -eq 0 ] && has "STUB-ENV JS_VIS_FAIL_CLOSED=0 JS_VIS_STALE_MS=8000"'
check "0.3 knobs unset -> the banner says fail-closed DISABLED (shadow mode)" 'has "INFO: vis_fail_closed=0 vis_stale_ms=8000: fail-closed DISABLED (shadow mode)"'
if [ "$IMAGE_TPL" = yes ]; then
	check "0.3 knobs unset -> janus.jcfg, both transport configs and effective-config.json byte-identical to the pre-0.3 entrypoint's (tests/golden/phase0-knobs-off)" 'golden0'
	check "0.3 the image's janus.plugin.slvoice.jcfg is byte-identical to the pre-0.3 image's" 'cmp -s /opt/janus/share/janus-templates/janus.plugin.slvoice.jcfg "$GOLDEN0/janus.plugin.slvoice.jcfg"'
else
	echo "skip 0.3 golden config comparison (needs the image's own templates)"
fi
run_ep JS_PUBLIC_IP=203.0.113.7 JANUS_BIN="$VISSTUB" JS_VIS_FAIL_CLOSED=0 JS_VIS_STALE_MS=8000
check "0.3 knobs set to their defaults -> the same export and banner" '[ "$RC" -eq 0 ] && has "STUB-ENV JS_VIS_FAIL_CLOSED=0 JS_VIS_STALE_MS=8000" && has "fail-closed DISABLED (shadow mode)"'
if [ "$IMAGE_TPL" = yes ]; then
	check "0.3 knobs set to their defaults -> the generated config is still the golden" 'golden0'
fi
run_ep JS_PUBLIC_IP=203.0.113.7 JANUS_BIN="$VISSTUB" JS_VIS_FAIL_CLOSED=1 JS_VIS_STALE_MS=9000
check "0.3 fail-closed on -> exported as given, and the banner says ENABLED" '[ "$RC" -eq 0 ] && has "STUB-ENV JS_VIS_FAIL_CLOSED=1 JS_VIS_STALE_MS=9000" && has "INFO: vis_fail_closed=1 vis_stale_ms=9000: fail-closed ENABLED" && ! has "shadow mode"'
if [ "$IMAGE_TPL" = yes ]; then
	check "0.3 fail-closed on -> the generated config is still the golden (the knobs are environment only)" 'golden0'
fi
run_ep JS_PUBLIC_IP=203.0.113.7 JANUS_BIN="$VISSTUB" JS_VIS_FAIL_CLOSED=True
check "0.3 JS_VIS_FAIL_CLOSED=True -> the banner agrees with the plugin (case-insensitive): ENABLED" 'has "fail-closed ENABLED"'

# ---- 0.4: join capability knobs. Environment only, and the secret is never printed. ----
CAPSTUB="$WORK/janus-stub-cap"
# The stub reports only whether the secret is set: ${VAR:-unset} would print the VALUE, which is exactly what the
# "never printed" check below must not see from the harness itself.
printf '#!/bin/sh\necho "STUB-JANUS-RAN $*"\nif [ -n "${JS_JOIN_CAP_SECRET:-}" ]; then cap_s=set; else cap_s=unset; fi\necho "STUB-ENV JS_JOIN_CAP_REQUIRED=${JS_JOIN_CAP_REQUIRED-<unset>} JS_JOIN_CAP_SECRET=$cap_s"\n' > "$CAPSTUB"
chmod +x "$CAPSTUB"
CAP_SECRET_V="join-cap-SECRET-c1"

run_ep JS_PUBLIC_IP=203.0.113.7 JANUS_BIN="$CAPSTUB"
check "0.4 knobs unset -> Janus runs with JS_JOIN_CAP_REQUIRED=0 and no secret" '[ "$RC" -eq 0 ] && has "STUB-ENV JS_JOIN_CAP_REQUIRED=0 JS_JOIN_CAP_SECRET=unset"'
check "0.4 knobs unset -> the banner says shadow" 'has "INFO: join_cap_required=0 join_cap_secret=EMPTY: join capability shadow"'
if [ "$IMAGE_TPL" = yes ]; then
	check "0.4 knobs unset -> the generated config is still the pre-0.3 golden" 'golden0'
fi

run_ep JS_PUBLIC_IP=203.0.113.7 JANUS_BIN="$CAPSTUB" JS_JOIN_CAP_REQUIRED=1 JS_JOIN_CAP_SECRET="$CAP_SECRET_V"
check "0.4 required with a secret -> starts, banner REQUIRED, secret reaches Janus" '[ "$RC" -eq 0 ] && has "INFO: join_cap_required=1 join_cap_secret=set: join capability REQUIRED in rooms with vis_authority" && has "STUB-ENV JS_JOIN_CAP_REQUIRED=1 JS_JOIN_CAP_SECRET=set"'
check "0.4 required with a secret -> the secret value is never printed" '! has "$CAP_SECRET_V"'
if [ "$IMAGE_TPL" = yes ]; then
	check "0.4 required -> the generated config is still the golden (the knobs are environment only)" 'golden0'
fi

run_ep JS_PUBLIC_IP=203.0.113.7 JANUS_BIN="$CAPSTUB" JS_JOIN_CAP_REQUIRED=1
check "0.4 required with an empty secret -> FATAL exit 1, Janus not started" '[ "$RC" -eq 1 ] && has "FATAL: JS_JOIN_CAP_REQUIRED=1 with an empty JS_JOIN_CAP_SECRET" && ! has STUB-JANUS-RAN'

run_ep JS_PUBLIC_IP=203.0.113.7 JANUS_BIN="$CAPSTUB" JS_JOIN_CAP_SECRET="$CAP_SECRET_V"
check "0.4 a secret with the requirement off -> shadow, still no secret in the output" '[ "$RC" -eq 0 ] && has "join_cap_required=0 join_cap_secret=set: join capability shadow" && ! has "$CAP_SECRET_V"'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_TURN_SERVER=turn.example.test JS_TURN_PORT=5349 JS_TURN_TYPE=TLS JS_TURN_USER="$TURN_USER_V" JS_TURN_PWD="$TURN_PWD_V"
check "A.3 static TURN -> starts" '[ "$RC" -eq 0 ] && has STUB-JANUS-RAN'
check "A.3 static TURN -> each key written in the nat section" 'natkey turn_server "\"turn.example.test\"" && natkey turn_port 5349 && natkey turn_type "\"tls\"" && natkey turn_user "\"$TURN_USER_V\"" && natkey turn_pwd "\"$TURN_PWD_V\"" && ! section janus.jcfg nat | grep -Eq "^[[:space:]]*turn_rest_api"'
check "A.3 static TURN -> reported with credentials as set, never their values" 'has "INFO: turn=static server=turn.example.test port=5349 type=tls user=set pwd=set" && has "turn_server = turn.example.test:5349 (tls), static credentials user=set pwd=set" && no_secrets'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_TURN_SERVER=turn.example.test JS_TURN_USER="$TURN_USER_V" JS_TURN_PWD="$TURN_PWD_V"
check "A.3 static TURN defaults -> port 3478, type udp" 'natkey turn_port 3478 && natkey turn_type "\"udp\"" && no_secrets'

run_ep JS_PUBLIC_IP=203.0.113.7 "JS_TURN_REST_API=https://rest.example.test/turn?tenant=hidden-tenant" JS_TURN_REST_API_KEY="$TURN_KEY_V" JS_TURN_REST_API_METHOD=get
check "A.3 REST TURN -> each key written in the nat section" '[ "$RC" -eq 0 ] && natkey turn_rest_api "\"https://rest.example.test/turn\\?tenant=hidden-tenant\"" && natkey turn_rest_api_key "\"$TURN_KEY_V\"" && natkey turn_rest_api_method "\"GET\"" && ! section janus.jcfg nat | grep -Eq "^[[:space:]]*turn_(server|user|pwd) ="'
check "A.3 REST TURN -> URL logged without its query, key as set" 'has "INFO: turn=rest rest_api=https://rest.example.test/turn rest_api_key=set rest_api_method=GET" && ! has "hidden-tenant" && no_secrets'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_TURN_REST_API=http://rest.example.test/turn
check "A.3 REST TURN without a key -> no key line, method POST" 'natkey turn_rest_api_method "\"POST\"" && ! section janus.jcfg nat | grep -Eq "^[[:space:]]*turn_rest_api_key"'

run_ep JS_TURN_SERVER=turn.example.test JS_TURN_USER="$TURN_USER_V" JS_TURN_PWD="$TURN_PWD_V" JS_TURN_REST_API=https://rest.example.test/turn JS_TURN_REST_API_KEY="$TURN_KEY_V"
check "A.3 static and REST both configured -> FATAL naming both, no values" '[ "$RC" -eq 1 ] && has "FATAL: JS_TURN_REST_API (REST credentials) and JS_TURN_SERVER, JS_TURN_USER, JS_TURN_PWD (static TURN settings) are both set" && ! has STUB-JANUS-RAN && no_secrets'
run_ep JS_TURN_SERVER=turn.example.test
check "A.3 server with no credentials -> FATAL" '[ "$RC" -eq 1 ] && has "FATAL: JS_TURN_SERVER is set without credentials" && ! has STUB-JANUS-RAN'
run_ep JS_TURN_SERVER=turn.example.test JS_TURN_USER="$TURN_USER_V"
check "A.3 server with a user but no password -> FATAL" '[ "$RC" -eq 1 ] && has "FATAL: JS_TURN_SERVER is set without credentials" && no_secrets'
run_ep JS_TURN_USER="$TURN_USER_V" JS_TURN_PWD="$TURN_PWD_V"
check "A.3 credentials without a server -> FATAL (partial)" '[ "$RC" -eq 1 ] && has "FATAL: JS_TURN_USER, JS_TURN_PWD set without JS_TURN_SERVER: a partial TURN configuration" && no_secrets'
run_ep JS_TURN_REST_API_KEY="$TURN_KEY_V"
check "A.3 REST key without the API URL -> FATAL (partial)" '[ "$RC" -eq 1 ] && has "set without JS_TURN_REST_API: a partial TURN REST configuration" && no_secrets'
run_ep JS_TURN_SERVER=turn.example.test JS_TURN_USER="$TURN_USER_V" JS_TURN_PWD="$TURN_PWD_V" JS_TURN_TYPE=sctp
check "A.3 bad JS_TURN_TYPE -> FATAL" '[ "$RC" -eq 1 ] && has "FATAL: JS_TURN_TYPE='\''sctp'\'' is not one of udp, tcp, tls" && no_secrets'
run_ep JS_TURN_SERVER=turn.example.test JS_TURN_USER="$TURN_USER_V" JS_TURN_PWD="$TURN_PWD_V" JS_TURN_PORT=70000
check "A.3 bad JS_TURN_PORT -> FATAL" '[ "$RC" -eq 1 ] && has "FATAL: JS_TURN_PORT=70000 is not a port number"'
run_ep JS_TURN_REST_API=https://rest.example.test/turn JS_TURN_REST_API_METHOD=PUT
check "A.3 bad JS_TURN_REST_API_METHOD -> FATAL" '[ "$RC" -eq 1 ] && has "JS_TURN_REST_API_METHOD='\''PUT'\'' is not GET or POST"'
run_ep JS_TURN_REST_API=ftp://rest.example.test/turn
check "A.3 non-http JS_TURN_REST_API -> FATAL" '[ "$RC" -eq 1 ] && has "JS_TURN_REST_API must be an http:// or https:// URL"'
run_ep JS_TURN_SERVER=turn.example.test JS_TURN_USER="$TURN_USER_V" 'JS_TURN_PWD=pw"quote-SECRET'
check "A.3 a double quote in a credential -> FATAL naming the knob only" '[ "$RC" -eq 1 ] && has "FATAL: JS_TURN_PWD contains a double quote or a backslash" && ! has "quote-SECRET"'

# ---- A.2: an unauthorised or malformed participant poll is logged and never read as zero ----
printf '198.51.100.4\n198.51.100.4\n' > "$WORK/stun.seq"
run_watch SLV_ADDR_WATCH_MAX_CHECKS=2 STUB_STUN_FILE="$WORK/stun.seq" JS_PUBLIC_IP_CHANGE_ACTION=restart JS_PUBLIC_IP_RESTART_MAX_WAIT_S=30 \
	STUB_PARTICIPANTS_ERR="addr_probe: participants: unauthorized: Unauthorized request (wrong or missing secret/token) (check JS_API_SECRET)"
check "A.2 watcher restart: unauthorised poll logged with its reason" 'has "WARNING: participant poll failed (addr_probe: participants: unauthorized: Unauthorized request" && has "not counted as zero participants"'
check "A.2 watcher restart: unauthorised poll is not zero; bounded wait, then the logged outage" '! has "advertise 198.51.100.4: zero participants" && has "an unknown number of participant(s) still connected after 30 s: taking the outage" && has RESTART-CALLED'

printf '198.51.100.4\n198.51.100.4\n' > "$WORK/stun.seq"
run_watch SLV_ADDR_WATCH_MAX_CHECKS=2 STUB_STUN_FILE="$WORK/stun.seq" JS_PUBLIC_IP_CHANGE_ACTION=restart JS_PUBLIC_IP_RESTART_MAX_WAIT_S=15 STUB_PARTICIPANTS=garbage
check "A.2 watcher restart: a non-numeric poll reply is not zero" 'has "participant poll failed (no reason given; reply '\''garbage'\'')" && ! has "advertise 198.51.100.4: zero participants" && has RESTART-CALLED'

# ---- A.2: startup self-check launch and the effective config it reads ----
SCSTUB="$WORK/selfcheck-stub"
printf '#!/bin/sh\necho "SELFCHECK-STUB $*"\n' > "$SCSTUB"; chmod +x "$SCSTUB"
SCSLOW="$WORK/selfcheck-slow"
printf '#!/bin/sh\nexec sleep 30\n' > "$SCSLOW"; chmod +x "$SCSLOW"

run_ep JS_SELFCHECK= SLV_SELFCHECK_CMD="$SCSTUB"
check "A.2 self-check on by default, bound 20 s, in the effective values" 'has "INFO: selfcheck=on timeout_s=20"'
check "A.2 self-check started in the background with --startup" 'has "SELFCHECK-STUB --startup" && has STUB-JANUS-RAN'
check "A.2 effective config written for the self-check, without secrets" 'grep -Fq "\"JS_RTP_PORT_RANGE\":\"10000-10200\"" "$CONF/state/effective-config.json" && grep -Fq "\"JS_HTTP_PORT\":\"14223\"" "$CONF/state/effective-config.json" && grep -Fq "\"JS_STUN_SERVER\":\"stun.l.google.com:19302\"" "$CONF/state/effective-config.json" && ! grep -Fq "test-api-secret" "$CONF/state/effective-config.json" && json_ok "$CONF/state/effective-config.json"'

check "A.2b effective config and banner carry JS_SELFCHECK_INBOUND_MAX_AGE_H (default 168)" 'grep -Fq "\"JS_SELFCHECK_INBOUND_MAX_AGE_H\":\"168\"" "$CONF/state/effective-config.json" && has "inbound_max_age_h=168" && has "inbound proof: legion-voice-selfcheck --listen"'

run_ep JS_SELFCHECK_INBOUND_MAX_AGE_H=0
check "A.2b JS_SELFCHECK_INBOUND_MAX_AGE_H=0 -> WARNING, and 168" 'has "JS_SELFCHECK_INBOUND_MAX_AGE_H=0 is not a usable age; using 168" && grep -Fq "\"JS_SELFCHECK_INBOUND_MAX_AGE_H\":\"168\"" "$CONF/state/effective-config.json"'
run_ep JS_SELFCHECK_INBOUND_MAX_AGE_H=24
check "A.2b JS_SELFCHECK_INBOUND_MAX_AGE_H=24 -> passed to the self-check" 'grep -Fq "\"JS_SELFCHECK_INBOUND_MAX_AGE_H\":\"24\"" "$CONF/state/effective-config.json"'

run_ep JS_SELFCHECK=off SLV_SELFCHECK_CMD="$SCSTUB"
check "A.2 JS_SELFCHECK=off -> not started" '! has "SELFCHECK-STUB" && has "INFO: selfcheck=off"'

run_ep JS_SELFCHECK=maybe SLV_SELFCHECK_CMD="$SCSTUB"
check "A.2 bad JS_SELFCHECK -> WARNING, and on" 'has "WARNING: JS_SELFCHECK='\''maybe'\'' is not on or off; using on" && has "SELFCHECK-STUB --startup"'

run_ep JS_SELFCHECK=on JS_SELFCHECK_TIMEOUT_S=0 SLV_SELFCHECK_CMD="$SCSTUB"
check "A.2 JS_SELFCHECK_TIMEOUT_S=0 -> WARNING, and 20" 'has "JS_SELFCHECK_TIMEOUT_S=0 is not a usable bound; using 20" && has "timeout_s=20"'

run_ep JS_SELFCHECK=on JS_SELFCHECK_TIMEOUT_S=1 SLV_SELFCHECK_CMD="$SCSLOW"
check "A.2 a self-check that overruns is stopped by the backstop (bound + 5 s)" 'has "[selfcheck] ===== legion-voice self-check was stopped after 6 s" && has STUB-JANUS-RAN'

# ---- O-55: defaults reproduce pre-6m behaviour; narrowing is opt-in ----
run_ep
check "no JS_ADMIN_BIND -> 0.0.0.0 + WARN" 'has "INFO: admin API bind=0.0.0.0 port=14225" && has "WARNING: admin API is reachable on all interfaces; protected by JS_ADMIN_SECRET only"'
check "no JS_WS_ENABLED -> ws on" '[ "$(cfg janus.transport.websockets.jcfg ws)" = "ws = true" ] && [ "$(cfg janus.transport.websockets.jcfg ws_port)" = "ws_port = 8188" ] && ! section janus.jcfg transports | grep -Eq "^[[:space:]]*disable" && has "INFO: websockets transport enabled=true port=8188" && has " ws=8188 "'
check "defaults -> admin bind and WS are the first lines" '[ "$(first_line "INFO: admin API bind=")" -eq 1 ] && [ "$(first_line "INFO: websockets transport")" -le 3 ]'
check "no JS_JOIN_MEDIA_TIMEOUT_S -> 30 printed (O-75)" 'has "INFO: empty_room_grace_s=60 join_media_timeout_s=30" && has " join_media_timeout_s=30"'

run_ep JS_JOIN_MEDIA_TIMEOUT_S=10
check "JS_JOIN_MEDIA_TIMEOUT_S=10 -> 10 printed" 'has "join_media_timeout_s=10"'

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

# ---- A.5: ICE diagnostics (Janus events to the collector) and the debug-level guard ----
DIAGSTUB="$WORK/icediag-stub"
printf '#!/bin/sh\necho "ICEDIAG-STUB history=$JS_ICE_DIAG_HISTORY port=$SLV_ICE_DIAG_PORT admin=$JS_ADMIN_PORT$JS_ADMIN_BASEPATH"\n' > "$DIAGSTUB"
chmod +x "$DIAGSTUB"
EVH=janus.eventhandler.sampleevh.jcfg

run_ep JS_PUBLIC_IP=203.0.113.7 SLV_ICE_DIAG_CMD="$DIAGSTUB" SLV_ICE_DIAG_ONCE=1
check "A.5 JS_ICE_DIAG_HISTORY=0 -> off: no broadcast, no event handler file, no collector, janus.jcfg unchanged" '[ "$RC" -eq 0 ] && has "INFO: ice_diag=off" && ! section janus.jcfg events | grep -Eq "^[[:space:]]*broadcast" && [ ! -f "$CONF/$EVH" ] && ! has ICEDIAG-STUB && [ "$(sha256sum "$CONF/janus.jcfg" | cut -d" " -f1)" = "$NO_TURN_SUM" ]'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_ICE_DIAG_HISTORY= SLV_ICE_DIAG_CMD="$DIAGSTUB" SLV_ICE_DIAG_ONCE=1
check "A.5 JS_ICE_DIAG_HISTORY unset -> on with 200; the collector gets the history, its port and the admin API" '[ "$RC" -eq 0 ] && has "INFO: ice_diag=on history=200" && has "ICEDIAG-STUB history=200 port=14229 admin=14225/voiceAdmin" && has STUB-JANUS-RAN'
check "A.5 on -> broadcast = true in the events section" 'section janus.jcfg events | grep -Eq "^[[:space:]]*broadcast = true$"'
check "A.5 on -> the unused event handlers are disabled in events, and plugins/transports/loggers disable untouched" 'section janus.jcfg events | grep -Eq "^[[:space:]]*disable = \"libjanus_wsevh.so,libjanus_nanomsgevh.so,libjanus_rabbitmqevh.so,libjanus_gelfevh.so,libjanus_mqttevh.so\"$" && ! section janus.jcfg events | grep -q sampleevh && ! section janus.jcfg plugins | grep -Eq "^[[:space:]]*disable" && ! section janus.jcfg transports | grep -Eq "^[[:space:]]*disable" && ! section janus.jcfg loggers | grep -Eq "^[[:space:]]*disable"'
check "A.5 on -> sample event handler enabled, loopback backend, sessions/handles/webrtc/plugins only (no jsep, no media)" '[ "$(cfg $EVH enabled)" = "enabled = true" ] && [ "$(cfg $EVH backend)" = "backend = \"http://127.0.0.1:14229/events\"" ] && [ "$(cfg $EVH events)" = "events = \"sessions,handles,webrtc,plugins\"" ]'
if [ -f /opt/janus/share/janus-templates/janus.jcfg ] && [ -z "${ENTRYPOINT_TEST_TPL:-}" ] && [ -f "$GOLDEN" ]; then
	check "A.5 on -> janus.jcfg is the pre-A.3 golden with only the events broadcast and disable lines changed" 'sed -e "s/^\([[:space:]]*\)#broadcast = true/\1broadcast = true/" -e "/^events:[[:space:]]*{/,/^}/ s|^\([[:space:]]*\)#disable = .*|\1disable = \"libjanus_wsevh.so,libjanus_nanomsgevh.so,libjanus_rabbitmqevh.so,libjanus_gelfevh.so,libjanus_mqttevh.so\"|" "$GOLDEN" | cmp -s - "$CONF/janus.jcfg"'
else
	echo "skip golden janus.jcfg comparison with diagnostics on (needs the image's own templates)"
fi

run_ep JS_ICE_DIAG_HISTORY=lots SLV_ICE_DIAG_CMD="$DIAGSTUB" SLV_ICE_DIAG_ONCE=1
check "A.5 bad JS_ICE_DIAG_HISTORY -> WARNING, and 200" 'has "WARNING: JS_ICE_DIAG_HISTORY='\''lots'\'' is not a non-negative integer; using 200" && has "ice_diag=on history=200"'
run_ep JS_ICE_DIAG_HISTORY=99999999999999999999 SLV_ICE_DIAG_CMD="$DIAGSTUB" SLV_ICE_DIAG_ONCE=1
check "A.5 JS_ICE_DIAG_HISTORY above 10000 -> WARNING, and 10000" 'has "is above 10000; using 10000" && has "ice_diag=on history=10000"'

GUARD="Janus WILL PRINT TURN REST CREDENTIALS INTO THIS LOG"
REST_URL=https://rest.example.test/turn
EP_ARGS="-d 5"; run_ep JS_PUBLIC_IP=203.0.113.7 JS_TURN_REST_API="$REST_URL" JS_TURN_REST_API_KEY="$TURN_KEY_V"; EP_ARGS=""
check "A.5 TURN REST + janus -d 5 -> the credentials warning, which names no key" '[ "$RC" -eq 0 ] && has "$GUARD" && has "WARNING: Janus debug level is 5 and TURN REST is configured" && has " debug_level=5" && has STUB-JANUS-RAN && no_secrets'
EP_ARGS="--debug-level=7"; run_ep JS_PUBLIC_IP=203.0.113.7 JS_TURN_REST_API="$REST_URL"; EP_ARGS=""
check "A.5 TURN REST + --debug-level=7 -> warning" 'has "Janus debug level is 7 and TURN REST is configured"'
EP_ARGS="-d6"; run_ep JS_PUBLIC_IP=203.0.113.7 JS_TURN_REST_API="$REST_URL"; EP_ARGS=""
check "A.5 TURN REST + -d6 -> warning" 'has "Janus debug level is 6 and TURN REST is configured"'
run_ep JS_PUBLIC_IP=203.0.113.7 JS_TURN_REST_API="$REST_URL" JS_TURN_REST_API_KEY="$TURN_KEY_V"
check "A.5 TURN REST at the default level 4 -> no warning" '[ "$RC" -eq 0 ] && ! has "$GUARD" && has " debug_level=4"'
EP_ARGS="-d 4"; run_ep JS_PUBLIC_IP=203.0.113.7 JS_TURN_REST_API="$REST_URL"; EP_ARGS=""
check "A.5 TURN REST + -d 4 -> no warning" '[ "$RC" -eq 0 ] && ! has "$GUARD"'
EP_ARGS="-d 7"; run_ep JS_PUBLIC_IP=203.0.113.7 JS_TURN_SERVER=turn.example.test JS_TURN_USER="$TURN_USER_V" JS_TURN_PWD="$TURN_PWD_V"; EP_ARGS=""
check "A.5 static TURN + -d 7 -> no TURN REST warning" '[ "$RC" -eq 0 ] && ! has "$GUARD"'
EP_ARGS="-d 7"; run_ep JS_PUBLIC_IP=203.0.113.7; EP_ARGS=""
check "A.5 no TURN + -d 7 -> no warning" '[ "$RC" -eq 0 ] && ! has "$GUARD"'
OVR_DBG="$WORK/ovr-debug-rest"; mkdir -p "$OVR_DBG"
sed -e 's/^\([[:space:]]*\)debug_level = 4/\1debug_level = 5/' \
	-e 's|^\([[:space:]]*\)#turn_rest_api = .*|\1turn_rest_api = "http://rest.example.test/turn"|' "$TPL/janus.jcfg" > "$OVR_DBG/janus.jcfg"
run_ep JS_PUBLIC_IP=203.0.113.7 JANUS_OVERRIDE_DIR="$OVR_DBG"
check "A.5 mounted janus.jcfg with debug_level 5 and turn_rest_api (no JS_TURN_*) -> warning" '[ "$RC" -eq 0 ] && has "applying override janus.jcfg" && has "Janus debug level is 5 and TURN REST is configured" && has " debug_level=5"'
OVR_DBG2="$WORK/ovr-debug-only"; mkdir -p "$OVR_DBG2"
sed -e 's/^\([[:space:]]*\)debug_level = 4/\1debug_level = 6/' "$TPL/janus.jcfg" > "$OVR_DBG2/janus.jcfg"
run_ep JS_PUBLIC_IP=203.0.113.7 JANUS_OVERRIDE_DIR="$OVR_DBG2"
check "A.5 mounted janus.jcfg with debug_level 6 and no TURN REST -> no warning" '[ "$RC" -eq 0 ] && has " debug_level=6" && ! has "$GUARD"'

# ---- Secret strength: a non-empty API, admin or join-capability secret under 32 characters is warned about ----
# Fixed fake values of exact lengths. The WARNING names the knob, never the value and never the length.
S31="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
S32="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
S32B="cccccccccccccccccccccccccccccccc"
S32C="dddddddddddddddddddddddddddddddd"
check "secret strength: the fixtures are 31 and 32 characters" '[ "${#S31}" -eq 31 ] && [ "${#S32}" -eq 32 ] && [ "${#S32B}" -eq 32 ] && [ "${#S32C}" -eq 32 ]'

run_ep JS_PUBLIC_IP=203.0.113.7
check "secret strength: short API and admin secrets -> a WARNING naming both, and the start goes on" '[ "$RC" -eq 0 ] && has "WARNING: JS_API_SECRET, JS_ADMIN_SECRET shorter than 32 characters" && has STUB-JANUS-RAN'
check "secret strength: the warning does not name the join-capability secret when it is empty" '! has "JS_ADMIN_SECRET, JS_JOIN_CAP_SECRET" && ! has "JS_JOIN_CAP_SECRET shorter"'
check "secret strength: the warning prints no secret value and no length" '! has "test-api-secret" && ! has "test-admin-secret" && ! printf "%s\n" "$OUT" | grep "shorter than 32" | sed "s/32//g" | grep -q "[0-9]"'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_API_SECRET="$S32" JS_ADMIN_SECRET="$S32B" JS_JOIN_CAP_SECRET="$S32C"
check "secret strength: three 32-character secrets pass silently" '[ "$RC" -eq 0 ] && ! has "shorter than 32" && ! has "JS_WEAK_SECRET_FATAL=true" && has STUB-JANUS-RAN'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_API_SECRET="$S32" JS_ADMIN_SECRET="$S32B" JS_JOIN_CAP_SECRET="$S31"
check "secret strength: a 31-character join-capability secret alone is named, the 32-character ones are not" '[ "$RC" -eq 0 ] && has "WARNING: JS_JOIN_CAP_SECRET shorter than 32 characters" && ! has "JS_API_SECRET shorter" && ! has "JS_ADMIN_SECRET shorter" && ! has "$S31"'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_API_SECRET="$S31" JS_ADMIN_SECRET="$S32B"
check "secret strength: a 31-character API secret alone is named" '[ "$RC" -eq 0 ] && has "WARNING: JS_API_SECRET shorter than 32 characters" && ! has "JS_ADMIN_SECRET shorter"'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_API_SECRET="$S32" JS_ADMIN_SECRET="$S32B" JS_WEAK_SECRET_FATAL=true
check "secret strength: JS_WEAK_SECRET_FATAL=true with 32-character secrets and no join-capability secret -> starts, silent" '[ "$RC" -eq 0 ] && ! has "shorter than 32" && has STUB-JANUS-RAN'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_API_SECRET="$S32" JS_ADMIN_SECRET="$S31" JS_JOIN_CAP_SECRET="$S31" JS_WEAK_SECRET_FATAL=true
check "secret strength: JS_WEAK_SECRET_FATAL=true with short secrets -> FATAL exit 1 naming them, Janus not started" '[ "$RC" -eq 1 ] && has "FATAL: JS_ADMIN_SECRET, JS_JOIN_CAP_SECRET shorter than 32 characters" && ! has STUB-JANUS-RAN && ! has "$S31" && ! has "JS_API_SECRET shorter"'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_WEAK_SECRET_FATAL=yes
check "secret strength: JS_WEAK_SECRET_FATAL accepts yes (the other knobs' true spellings)" '[ "$RC" -eq 1 ] && has "FATAL: JS_API_SECRET, JS_ADMIN_SECRET shorter than 32 characters"'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_WEAK_SECRET_FATAL=false
check "secret strength: JS_WEAK_SECRET_FATAL=false is the default behaviour (warn, start)" '[ "$RC" -eq 0 ] && has "WARNING: JS_API_SECRET, JS_ADMIN_SECRET shorter than 32 characters"'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_API_SECRET="$S32" JS_ADMIN_SECRET="$S32B" JS_WEAK_SECRET_FATAL=true JS_JOIN_CAP_SECRET=
check "secret strength: an empty join-capability secret is not a weak one" '[ "$RC" -eq 0 ] && ! has "shorter than 32"'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_API_SECRET= JS_ADMIN_SECRET= JS_WEAK_SECRET_FATAL=true
check "secret strength: empty API and admin secrets still hit the empty-secret FATAL, not the strength one" '[ "$RC" -eq 1 ] && has "FATAL: JS_API_SECRET and JS_ADMIN_SECRET empty" && ! has "shorter than 32"'

if [ "$IMAGE_TPL" = yes ]; then
	run_ep JS_PUBLIC_IP=203.0.113.7
	check "secret strength: the warning changes no generated config (golden)" 'golden0'
fi

# ---- TLS for the API and admin API ----
TLSDIR="$WORK/tls"; mkdir -p "$TLSDIR"
TLS_CERT="$TLSDIR/cert.pem"; TLS_KEY="$TLSDIR/key.pem"
printf 'stand-in certificate\n' > "$TLS_CERT"; printf 'stand-in key\n' > "$TLS_KEY"
http_cfg() { cfg janus.transport.http.jcfg "$1" | sed 's/[[:space:]]*#.*$//'; }

run_ep JS_PUBLIC_IP=203.0.113.7
check "TLS: knobs unset -> tls=off reported, https stays false in both listeners" '[ "$RC" -eq 0 ] && has "INFO: tls=off plain_http=true admin_plain_http=true" && [ "$(http_cfg https)" = "https = false" ] && [ "$(http_cfg admin_https)" = "admin_https = false" ] && ! has "tls: https on"'
if [ "$IMAGE_TPL" = yes ]; then
	check "TLS: knobs unset -> janus.jcfg, both transport configs and effective-config.json are the golden" 'golden0'
fi
run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT= JS_TLS_KEY= JS_HTTPS_PORT=14224 JS_ADMIN_HTTPS_PORT=14226 JS_HTTP_PLAIN=true JS_ADMIN_HTTP_PLAIN=true
check "TLS: knobs present at their defaults -> tls=off and Janus starts" '[ "$RC" -eq 0 ] && has "INFO: tls=off" && has STUB-JANUS-RAN'
if [ "$IMAGE_TPL" = yes ]; then
	check "TLS: knobs present at their defaults -> the generated config is still the golden" 'golden0'
fi

run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLS_CERT" JS_TLS_KEY="$TLS_KEY"
check "TLS: certificate and key set -> https on both listeners, cert and key written, plain http left on" '[ "$RC" -eq 0 ] && has STUB-JANUS-RAN && [ "$(http_cfg https)" = "https = true" ] && [ "$(http_cfg secure_port)" = "secure_port = 14224" ] && [ "$(http_cfg admin_https)" = "admin_https = true" ] && [ "$(http_cfg admin_secure_port)" = "admin_secure_port = 14226" ] && [ "$(http_cfg cert_pem)" = "cert_pem = \"$TLS_CERT\"" ] && [ "$(http_cfg cert_key)" = "cert_key = \"$TLS_KEY\"" ] && [ "$(http_cfg http)" = "http = true" ] && [ "$(http_cfg admin_http)" = "admin_http = true" ] && [ "$(http_cfg port)" = "port = 14223" ] && [ "$(http_cfg admin_port)" = "admin_port = 14225" ]'
check "TLS: on -> reported at start, with the file contents never printed" 'has "INFO: tls=on https_port=14224 admin_https_port=14226" && has "tls: https on port 14224/voice, admin https on port 14226/voiceAdmin, plain http=true admin plain http=true" && ! has "stand-in"'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLS_CERT" JS_TLS_KEY="$TLS_KEY" JS_HTTPS_PORT=24224 JS_ADMIN_HTTPS_PORT=24226
check "TLS: the https ports follow JS_HTTPS_PORT and JS_ADMIN_HTTPS_PORT" '[ "$RC" -eq 0 ] && [ "$(http_cfg secure_port)" = "secure_port = 24224" ] && [ "$(http_cfg admin_secure_port)" = "admin_secure_port = 24226" ]'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLS_CERT" JS_TLS_KEY="$TLS_KEY" JS_HTTP_PLAIN=false JS_ADMIN_HTTP_PLAIN=false
check "TLS: plain http off for both -> http = false and admin_http = false, https stays on" '[ "$RC" -eq 0 ] && [ "$(http_cfg http)" = "http = false" ] && [ "$(http_cfg admin_http)" = "admin_http = false" ] && [ "$(http_cfg https)" = "https = true" ] && [ "$(http_cfg admin_https)" = "admin_https = true" ]'
check "TLS: plain http off with the self-check off and diagnostics off -> no loopback-tools warning" '! has "a plain http listener is off"'
run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLS_CERT" JS_TLS_KEY="$TLS_KEY" JS_HTTP_PLAIN=false JS_ADMIN_HTTP_PLAIN=false JS_SELFCHECK=on
check "TLS: plain http off with the self-check on -> a WARNING that the loopback tools lose Janus" 'has "WARNING: a plain http listener is off, and the startup self-check and the ICE diagnostics reach Janus over plain http on loopback"'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLS_CERT" JS_TLS_KEY="$TLS_KEY" JS_HTTP_PLAIN=loopback JS_ADMIN_HTTP_PLAIN=loopback JS_SELFCHECK=on
check "TLS: plain http on loopback only -> ip and admin_ip are 127.0.0.1, http stays on, no warning" '[ "$RC" -eq 0 ] && [ "$(http_cfg ip)" = "ip = \"127.0.0.1\"" ] && [ "$(http_cfg admin_ip)" = "admin_ip = \"127.0.0.1\"" ] && [ "$(http_cfg http)" = "http = true" ] && [ "$(http_cfg admin_http)" = "admin_http = true" ] && ! has "a plain http listener is off"'
run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLS_CERT" JS_TLS_KEY="$TLS_KEY" JS_HTTP_PLAIN=TRUE
check "TLS: JS_HTTP_PLAIN is case-insensitive" '[ "$RC" -eq 0 ] && has "plain_http=true"'

run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLSDIR/missing.pem" JS_TLS_KEY="$TLS_KEY"
check "TLS: a missing certificate -> FATAL naming JS_TLS_CERT, exit 1, Janus not started" '[ "$RC" -eq 1 ] && has "FATAL: JS_TLS_CERT=" && has "does not exist or is not a file; refusing to start" && ! has STUB-JANUS-RAN'
run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLS_CERT" JS_TLS_KEY="$TLSDIR/missing.pem"
check "TLS: a missing key -> FATAL naming JS_TLS_KEY, exit 1, Janus not started" '[ "$RC" -eq 1 ] && has "FATAL: JS_TLS_KEY=" && has "does not exist or is not a file; refusing to start" && ! has STUB-JANUS-RAN'
run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLSDIR" JS_TLS_KEY="$TLS_KEY"
check "TLS: a certificate path that is a directory -> FATAL" '[ "$RC" -eq 1 ] && has "FATAL: JS_TLS_CERT=" && ! has STUB-JANUS-RAN'
UNREADABLE="$TLSDIR/unreadable.pem"; printf 'x\n' > "$UNREADABLE"; chmod 000 "$UNREADABLE" 2>/dev/null
if [ -r "$UNREADABLE" ]; then
	echo "skip TLS unreadable-file test (this user can read a mode 000 file: root, or a filesystem without modes)"
else
	run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLS_CERT" JS_TLS_KEY="$UNREADABLE"
	check "TLS: an unreadable key -> FATAL naming JS_TLS_KEY, exit 1, Janus not started" '[ "$RC" -eq 1 ] && has "FATAL: JS_TLS_KEY=" && has "is not readable by the mixer; refusing to start" && ! has STUB-JANUS-RAN'
fi
chmod 600 "$UNREADABLE" 2>/dev/null
run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLS_CERT"
check "TLS: only the certificate set -> FATAL, exit 1, Janus not started" '[ "$RC" -eq 1 ] && has "FATAL: JS_TLS_CERT is set without JS_TLS_KEY" && ! has STUB-JANUS-RAN'
run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_KEY="$TLS_KEY"
check "TLS: only the key set -> FATAL, exit 1, Janus not started" '[ "$RC" -eq 1 ] && has "FATAL: JS_TLS_KEY is set without JS_TLS_CERT" && ! has STUB-JANUS-RAN'
run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT='/tls/ce"rt.pem' JS_TLS_KEY="$TLS_KEY"
check "TLS: a path with a double quote -> FATAL (the config cannot hold it as written)" '[ "$RC" -eq 1 ] && has "contains a double quote or a backslash" && ! has STUB-JANUS-RAN'
run_ep JS_PUBLIC_IP=203.0.113.7 JS_HTTP_PLAIN=false
check "TLS: plain http off while TLS is off -> FATAL (no way left to reach the API)" '[ "$RC" -eq 1 ] && has "FATAL: JS_HTTP_PLAIN or JS_ADMIN_HTTP_PLAIN is false but TLS is off" && ! has STUB-JANUS-RAN'
run_ep JS_PUBLIC_IP=203.0.113.7 JS_ADMIN_HTTP_PLAIN=false
check "TLS: admin plain http off while TLS is off -> FATAL" '[ "$RC" -eq 1 ] && has "TLS is off" && ! has STUB-JANUS-RAN'
run_ep JS_PUBLIC_IP=203.0.113.7 JS_HTTP_PLAIN=maybe
check "TLS: an unknown JS_HTTP_PLAIN value -> FATAL" '[ "$RC" -eq 1 ] && has "is not one of true, loopback, false" && has "JS_HTTP_PLAIN=" && ! has STUB-JANUS-RAN'
run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLS_CERT" JS_TLS_KEY="$TLS_KEY" JS_HTTPS_PORT=notaport
check "TLS: a non-numeric https port -> FATAL" '[ "$RC" -eq 1 ] && has "JS_HTTPS_PORT=" && has "is not a port number" && ! has STUB-JANUS-RAN'
run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLS_CERT" JS_TLS_KEY="$TLS_KEY" JS_HTTPS_PORT=14223
check "TLS: an https port equal to the plain http port -> FATAL" '[ "$RC" -eq 1 ] && has "equals a plain http port" && ! has STUB-JANUS-RAN'
run_ep JS_PUBLIC_IP=203.0.113.7 JS_TLS_CERT="$TLS_CERT" JS_TLS_KEY="$TLS_KEY" JS_HTTPS_PORT=14230 JS_ADMIN_HTTPS_PORT=14230
check "TLS: the two https ports equal -> FATAL" '[ "$RC" -eq 1 ] && has "JS_HTTPS_PORT and JS_ADMIN_HTTPS_PORT are both 14230" && ! has STUB-JANUS-RAN'

echo "entrypoint_test: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
