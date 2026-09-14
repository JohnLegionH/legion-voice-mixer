# public-address.sh: public address discovery, nat_1_1_mapping verdict and change detection (slice A.1).
#
# Sourced by docker-entrypoint.sh and entrypoint/public-address-watch.sh. POSIX sh (the image's
# /bin/sh is dash). Every function returns 0 unless stated otherwise, so callers under `set -e` are
# safe.
#
# Test seams (not operator knobs):
#   SLV_ADDR_PROBE       the probe command (entrypoint/addr_probe.py; the tests use a stub)
#   SLV_ADDR_STATE_FILE  where the resolution JSON is written
# Operator knobs read here (defaults are set by docker-entrypoint.sh):
#   JS_STUN_SERVER, JS_PUBLIC_IP_DNS_RESOLVER

: "${SLV_LIB_DIR:=/usr/local/lib/legion-voice}"
: "${SLV_ADDR_PROBE:=$SLV_LIB_DIR/addr_probe.py}"
: "${SLV_ADDR_STATE_FILE:=/run/legion-voice/public-address.json}"

# 0 if $1 is a dotted-quad IPv4 literal with every octet <= 255.
addr_is_ipv4() {
	printf '%s' "$1" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' || return 1
	for _oct in $(printf '%s' "$1" | tr '.' ' '); do
		[ "$_oct" -le 255 ] || return 1
	done
	return 0
}

# The reachability class of an IPv4 literal:
#   public | private (RFC 1918) | loopback (127/8) | linklocal (169.254/16) | unspecified (0/8)
#   | cgnat (100.64.0.0/10, RFC 6598) | invalid
addr_class() {
	if ! addr_is_ipv4 "$1"; then echo invalid; return 0; fi
	_a=${1%%.*}; _rest=${1#*.}; _b=${_rest%%.*}
	case "$_a" in
		10)  echo private; return 0 ;;
		127) echo loopback; return 0 ;;
		0)   echo unspecified; return 0 ;;
		169) if [ "$_b" -eq 254 ]; then echo linklocal; return 0; fi ;;
		172) if [ "$_b" -ge 16 ] && [ "$_b" -le 31 ]; then echo private; return 0; fi ;;
		192) if [ "$_b" -eq 168 ]; then echo private; return 0; fi ;;
		100) if [ "$_b" -ge 64 ] && [ "$_b" -le 127 ]; then echo cgnat; return 0; fi ;;
	esac
	echo public
}

# Print comma list $1 with $2 appended unless already present.
addr_list_add() {
	case ",$1," in
		*",$2,"*) printf '%s' "$1" ;;
		*)        printf '%s' "${1:+$1,}$2" ;;
	esac
}

# Clear the discovery results (for a start with no hostname to discover).
addr_reset() {
	ADDR_SOURCES=""; ADDR_WINNER=""; ADDR_WINNER_SOURCE=""; ADDR_PASSED_OVER=""
	for _s in stun dns container; do
		eval "ADDR_${_s}_RESULT=''; ADDR_${_s}_STATUS=skipped"
	done
	ADDR_stun_VIA=${JS_STUN_SERVER:-}
	ADDR_dns_VIA="resolver ${JS_PUBLIC_IP_DNS_RESOLVER:-}"
	ADDR_container_VIA="container resolver"
	return 0
}

# addr_discover HOST MODE
# Query the sources MODE selects, in this order: stun, dns (JS_PUBLIC_IP_DNS_RESOLVER), container
# (the container resolver).
#   auto: all three | stun: stun | dns: dns | static: container (the pre-A.1 behaviour)
# Every selected source is queried, so the log shows what each one returned.
# The winner is the first public answer in order, else the first IPv4 answer of any class.
# Sets:
#   ADDR_SOURCES
#   ADDR_<source>_RESULT / _STATUS (ok | no_answer | skipped) / _VIA
#   ADDR_WINNER, ADDR_WINNER_SOURCE
#   ADDR_PASSED_OVER: why earlier sources lost, e.g. "stun gave no answer"
addr_discover() {
	_host=$1
	addr_reset
	case "$2" in
		auto)   ADDR_SOURCES="stun dns container" ;;
		stun)   ADDR_SOURCES="stun" ;;
		dns)    ADDR_SOURCES="dns" ;;
		static) ADDR_SOURCES="container" ;;
		*)      ADDR_SOURCES="" ;;
	esac
	_any=""; _any_src=""
	for _s in $ADDR_SOURCES; do
		case "$_s" in
			stun)      _r=$("$SLV_ADDR_PROBE" stun "$JS_STUN_SERVER" 2>/dev/null) || _r="" ;;
			dns)       _r=$("$SLV_ADDR_PROBE" dns "$_host" "$JS_PUBLIC_IP_DNS_RESOLVER" 2>/dev/null) || _r="" ;;
			container) _r=$("$SLV_ADDR_PROBE" system "$_host" 2>/dev/null) || _r="" ;;
		esac
		if addr_is_ipv4 "$_r"; then
			_st=ok
			if [ -z "$ADDR_WINNER" ] && [ "$(addr_class "$_r")" = public ]; then
				ADDR_WINNER=$_r; ADDR_WINNER_SOURCE=$_s
			fi
			if [ -z "$_any" ]; then _any=$_r; _any_src=$_s; fi
		else
			_r=""; _st=no_answer
		fi
		eval "ADDR_${_s}_RESULT=\$_r; ADDR_${_s}_STATUS=\$_st"
	done
	if [ -z "$ADDR_WINNER" ] && [ -n "$_any" ]; then
		ADDR_WINNER=$_any; ADDR_WINNER_SOURCE=$_any_src
	fi
	for _s in $ADDR_SOURCES; do
		if [ "$_s" = "$ADDR_WINNER_SOURCE" ]; then break; fi
		eval "_r=\$ADDR_${_s}_RESULT"
		if [ -z "$_r" ]; then _why="$_s gave no answer"; else _why="$_s answered $_r ($(addr_class "$_r"))"; fi
		ADDR_PASSED_OVER="${ADDR_PASSED_OVER:+$ADDR_PASSED_OVER; }$_why"
	done
	return 0
}

# addr_log_sources PREFIX: one INFO line per source queried, then the winner.
addr_log_sources() {
	for _s in $ADDR_SOURCES; do
		eval "_r=\$ADDR_${_s}_RESULT; _via=\$ADDR_${_s}_VIA"
		if [ -n "$_r" ]; then
			echo "$1 INFO: discovery source ${_s} (${_via}) -> ${_r} ($(addr_class "$_r"))"
		else
			echo "$1 INFO: discovery source ${_s} (${_via}) -> no answer"
		fi
	done
	if [ -z "$ADDR_WINNER" ]; then
		echo "$1 ERROR: discovery got no IPv4 address from any source (${ADDR_SOURCES})" >&2
	elif [ -n "$ADDR_PASSED_OVER" ]; then
		echo "$1 INFO: discovery winner: ${ADDR_WINNER_SOURCE} -> ${ADDR_WINNER} (fallback: ${ADDR_PASSED_OVER})"
	else
		echo "$1 INFO: discovery winner: ${ADDR_WINNER_SOURCE} -> ${ADDR_WINNER}"
	fi
	return 0
}

# addr_verdict MAPPING: judge the FINAL nat_1_1_mapping. Sets:
#   ADDR_VERDICT: public (a public address is present) | cgnat (no public, a CGNAT address)
#                 | no_public | none (empty mapping)
#   ADDR_PUBLIC, ADDR_CGNAT: comma lists
addr_verdict() {
	ADDR_PUBLIC=""; ADDR_CGNAT=""
	for _ip in $(printf '%s' "$1" | tr ',' ' '); do
		case "$(addr_class "$_ip")" in
			public) ADDR_PUBLIC=$(addr_list_add "$ADDR_PUBLIC" "$_ip") ;;
			cgnat)  ADDR_CGNAT=$(addr_list_add "$ADDR_CGNAT" "$_ip") ;;
		esac
	done
	if [ -z "$1" ]; then ADDR_VERDICT=none
	elif [ -n "$ADDR_PUBLIC" ]; then ADDR_VERDICT=public
	elif [ -n "$ADDR_CGNAT" ]; then ADDR_VERDICT=cgnat
	else ADDR_VERDICT=no_public
	fi
	return 0
}

# addr_log_verdict PREFIX MAPPING RTP_RANGE: the verdict lines for addr_verdict's result.
addr_log_verdict() {
	case "$ADDR_VERDICT" in
		public)
			echo "$1 INFO: nat_1_1_mapping=$2 includes public address(es) ${ADDR_PUBLIC}: off-LAN viewers get a reachable candidate when UDP $3 is forwarded to this host" ;;
		no_public)
			echo "$1 ERROR: nat_1_1_mapping=$2 has no public address: off-LAN viewers will fail ICE (only viewers on this LAN get a reachable candidate)." >&2
			echo "$1 ERROR: remediation: set JS_PUBLIC_HOST (or JS_PUBLIC_IP) to this server's DNS/DDNS name with JS_PUBLIC_IP_DISCOVERY=auto so STUN finds the router's public address, or set JS_PUBLIC_IP / JS_NAT_EXTRA_IPS to that public IPv4; then forward UDP $3 to this host. See docs/docker-notes.md, \"External access\"." >&2 ;;
	esac
	for _ip in $(printf '%s' "$ADDR_CGNAT" | tr ',' ' '); do
		echo "$1 WARNING: nat_1_1_mapping address ${_ip} is in 100.64.0.0/10: this is carrier-grade NAT (CGNAT). The ISP shares that address, so direct paths from off-LAN viewers will fail and port forwarding cannot fix it; a TURN server is required." >&2
	done
	return 0
}

_addr_json_str() {
	if [ -n "$1" ]; then printf '"%s"' "$1"; else printf 'null'; fi
}

_addr_json_list() {
	_out="["; _sep=""
	for _x in $(printf '%s' "$1" | tr ',' ' '); do
		_out="${_out}${_sep}\"${_x}\""; _sep=","
	done
	printf '%s]' "$_out"
}

# addr_json EVENT HOST HOST_VAR MODE LITERALS EXTRAS MAPPING KEEP_PRIVATE [EXTRA_FIELDS]
# Print the resolution as one line of JSON. It uses the discovery and verdict globals set above.
# Every interpolated value is an IPv4 literal, a validated hostname or a fixed word, so no
# escaping is needed. EXTRA_FIELDS, if given, is a pre-built `"key":value,...` fragment.
addr_json() {
	_srcs="["; _sep=""
	for _s in $ADDR_SOURCES; do
		eval "_r=\$ADDR_${_s}_RESULT; _st=\$ADDR_${_s}_STATUS; _via=\$ADDR_${_s}_VIA"
		if [ -n "$_r" ]; then _cls=$(addr_class "$_r"); else _cls=""; fi
		_srcs="${_srcs}${_sep}{\"name\":\"${_s}\",\"via\":$(_addr_json_str "$_via"),\"status\":\"${_st}\",\"address\":$(_addr_json_str "$_r"),\"class\":$(_addr_json_str "$_cls")}"
		_sep=","
	done
	_srcs="${_srcs}]"
	if [ -n "$ADDR_WINNER" ]; then
		_win="{\"source\":\"${ADDR_WINNER_SOURCE}\",\"address\":\"${ADDR_WINNER}\"}"
	else
		_win=null
	fi
	if [ "$8" = true ]; then _kph=true; else _kph=false; fi
	printf '{"schema":1,"event":"%s","time":"%s","host":%s,"host_var":%s,"discovery":"%s","sources":%s,"winner":%s,"literal_ips":%s,"extra_ips":%s,"nat_1_1_mapping":%s,"keep_private_host":%s,"verdict":"%s"%s}\n' \
		"$1" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(_addr_json_str "$2")" "$(_addr_json_str "$3")" "$4" \
		"$_srcs" "$_win" "$(_addr_json_list "$5")" "$(_addr_json_list "$6")" "$(_addr_json_list "$7")" \
		"$_kph" "$ADDR_VERDICT" "${9:+,$9}"
}

# addr_write_state JSON PREFIX: write the state file atomically (tmp + rename). Failure only WARNs.
addr_write_state() {
	_dir=$(dirname "$SLV_ADDR_STATE_FILE")
	if mkdir -p "$_dir" 2>/dev/null \
		&& printf '%s\n' "$1" > "$SLV_ADDR_STATE_FILE.tmp" 2>/dev/null \
		&& mv -f "$SLV_ADDR_STATE_FILE.tmp" "$SLV_ADDR_STATE_FILE" 2>/dev/null; then
		return 0
	fi
	echo "$2 WARNING: could not write the address state file ${SLV_ADDR_STATE_FILE}" >&2
	return 0
}

# addr_change_step RUNNING PENDING COUNT OBSERVED -> prints "PENDING COUNT ACTION"
# PENDING is "-" when no candidate change is pending. ACTION is "change" once two consecutive
# checks agree on the same address different from RUNNING, and "none" otherwise.
# These observations are never a change, and they break the streak:
#   - an empty OBSERVED (a failed lookup);
#   - OBSERVED equal to RUNNING;
#   - a non-public OBSERVED while RUNNING is public: a STUN outage falling back to a hairpinned DNS
#     answer is a degraded lookup, not a new address.
addr_change_step() {
	_run=$1; _pend=$2; _cnt=$3; _obs=$4
	if [ -z "$_obs" ] || [ "$_obs" = "$_run" ]; then echo "- 0 none"; return 0; fi
	if [ "$(addr_class "$_run")" = public ] && [ "$(addr_class "$_obs")" != public ]; then
		echo "- 0 none"; return 0
	fi
	if [ "$_obs" = "$_pend" ]; then _cnt=$((_cnt + 1)); else _pend=$_obs; _cnt=1; fi
	if [ "$_cnt" -ge 2 ]; then echo "$_pend $_cnt change"; else echo "$_pend $_cnt none"; fi
	return 0
}
