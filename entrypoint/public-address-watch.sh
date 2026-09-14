#!/bin/sh
# public-address-watch.sh: periodic re-check of the discovered public address (slice A.1).
#
# docker-entrypoint.sh starts this in the background just before it execs Janus, and only when:
#   - a hostname was discovered;
#   - JS_PUBLIC_IP_DISCOVERY is not static;
#   - JS_PUBLIC_IP_REFRESH_S > 0.
# It inherits the entrypoint's environment, including the running state:
#   SLV_ADDR_HOST, SLV_ADDR_HOST_VAR  the hostname being discovered and the variable it came from
#   SLV_ADDR_RUNNING                  the discovered address baked into nat_1_1_mapping
#   SLV_ADDR_LITERALS, SLV_ADDR_EXTRAS, SLV_ADDR_KEEP_PRIVATE  the other mapping inputs
#
# Every JS_PUBLIC_IP_REFRESH_S it runs the discovery chain again. A change counts only after two
# consecutive checks agree on the same new address; a failed lookup is never a change (see
# addr_change_step). On a confirmed change:
#   JS_PUBLIC_IP_CHANGE_ACTION=warn     WARN with the old and new address, once per new address
#   JS_PUBLIC_IP_CHANGE_ACTION=restart  wait for zero participants across all rooms, bounded by
#                                       JS_PUBLIC_IP_RESTART_MAX_WAIT_S, then stop Janus (PID 1).
#                                       Restarting after that is the compose restart policy's job, so
#                                       it must be `unless-stopped` or `always`. With `on-failure` or
#                                       `no`, voice stays down: Janus exits 0 on SIGTERM.
#
# Test seams (not operator knobs):
#   SLV_ADDR_WATCH_MAX_CHECKS  stop after N checks; 0 = forever
#   SLV_ADDR_SLEEP             the sleep command
#   SLV_ADDR_RESTART_CMD       how Janus is stopped
#   SLV_ADDR_POLL_S            the participant poll interval while waiting to restart
set -u
. "${SLV_LIB_DIR:-/usr/local/lib/legion-voice}/public-address.sh"

P="[address-watch]"
: "${SLV_ADDR_WATCH_MAX_CHECKS:=0}"
: "${SLV_ADDR_SLEEP:=sleep}"
: "${SLV_ADDR_RESTART_CMD:=kill -TERM 1}"
: "${SLV_ADDR_POLL_S:=15}"
: "${SLV_ADDR_LITERALS:=}"
: "${SLV_ADDR_EXTRAS:=}"
: "${SLV_ADDR_KEEP_PRIVATE:=true}"

# Participants across all rooms, or empty when the poll failed. A failed, unauthorised or malformed poll
# is logged with its reason and is never read as zero participants: restart_janus then keeps waiting,
# still bounded by JS_PUBLIC_IP_RESTART_MAX_WAIT_S.
participants() {
	_perr=$(mktemp 2>/dev/null) || _perr="/tmp/slv-participants.$$"
	_pn=$("$SLV_ADDR_PROBE" participants "http://127.0.0.1:${JS_HTTP_PORT}${JS_HTTP_BASEPATH}" 2>"$_perr") || _pn=""
	case "$_pn" in
		''|*[!0-9]*)
			_why=$(tr '\n' ' ' < "$_perr" 2>/dev/null)
			echo "$P WARNING: participant poll failed (${_why:-no reason given}${_pn:+; reply '$_pn'}); not counted as zero participants" >&2
			_pn="" ;;
	esac
	rm -f "$_perr"
	printf '%s' "$_pn"
}

# mapping_for ADDRESS: the nat_1_1_mapping the entrypoint would build with ADDRESS discovered.
mapping_for() {
	_m=$1
	for _ip in $(printf '%s' "$SLV_ADDR_LITERALS,$SLV_ADDR_EXTRAS" | tr ',' ' '); do
		_m=$(addr_list_add "$_m" "$_ip")
	done
	printf '%s' "$_m"
}

restart_janus() {
	echo "$P WARNING: JS_PUBLIC_IP_CHANGE_ACTION=restart: waiting for zero participants across all rooms before restarting Janus to advertise $1 (at most ${JS_PUBLIC_IP_RESTART_MAX_WAIT_S} s)" >&2
	_waited=0
	while :; do
		_n=$(participants)
		if [ "$_n" = 0 ]; then
			echo "$P WARNING: restarting Janus now to advertise $1: zero participants" >&2
			break
		fi
		if [ "$_waited" -ge "$JS_PUBLIC_IP_RESTART_MAX_WAIT_S" ]; then
			echo "$P WARNING: restarting Janus now to advertise $1 with ${_n:-an unknown number of} participant(s) still connected after ${_waited} s: taking the outage (JS_PUBLIC_IP_RESTART_MAX_WAIT_S=${JS_PUBLIC_IP_RESTART_MAX_WAIT_S})" >&2
			break
		fi
		$SLV_ADDR_SLEEP "$SLV_ADDR_POLL_S"
		_waited=$((_waited + SLV_ADDR_POLL_S))
	done
	sh -c "$SLV_ADDR_RESTART_CMD"
}

echo "$P INFO: re-checking the public address of '${SLV_ADDR_HOST}' every ${JS_PUBLIC_IP_REFRESH_S} s (discovery=${JS_PUBLIC_IP_DISCOVERY}, change_action=${JS_PUBLIC_IP_CHANGE_ACTION}); running address ${SLV_ADDR_RUNNING}; a change counts after 2 consecutive agreeing checks"

pending=-; count=0; announced=""; checks=0
while :; do
	if [ "$SLV_ADDR_WATCH_MAX_CHECKS" -gt 0 ] && [ "$checks" -ge "$SLV_ADDR_WATCH_MAX_CHECKS" ]; then
		exit 0
	fi
	$SLV_ADDR_SLEEP "$JS_PUBLIC_IP_REFRESH_S"
	checks=$((checks + 1))

	addr_discover "$SLV_ADDR_HOST" "$JS_PUBLIC_IP_DISCOVERY"
	# shellcheck disable=SC2046  # three space-separated words by construction
	set -- $(addr_change_step "$SLV_ADDR_RUNNING" "$pending" "$count" "$ADDR_WINNER")
	pending=$1; count=$2; action=$3

	if [ -z "$ADDR_WINNER" ]; then
		echo "$P WARNING: check ${checks}: no source answered for '${SLV_ADDR_HOST}'; a failed lookup is not a change (running address ${SLV_ADDR_RUNNING})" >&2
	elif [ "$ADDR_WINNER" != "$SLV_ADDR_RUNNING" ]; then
		addr_log_sources "$P"
		if [ "$pending" = - ]; then
			echo "$P WARNING: check ${checks}: observed ${ADDR_WINNER} ($(addr_class "$ADDR_WINNER")) while running ${SLV_ADDR_RUNNING} (public): treated as a degraded lookup, not a change" >&2
		else
			echo "$P INFO: check ${checks}: observed ${ADDR_WINNER} from ${ADDR_WINNER_SOURCE}, running ${SLV_ADDR_RUNNING}: ${count} of 2 agreeing checks"
		fi
	elif [ -n "$announced" ]; then
		echo "$P INFO: check ${checks}: the public address is back to ${SLV_ADDR_RUNNING}, which the running nat_1_1_mapping advertises"
		announced=""
	fi

	observed_mapping=$(mapping_for "${ADDR_WINNER:-$SLV_ADDR_RUNNING}")
	addr_verdict "$observed_mapping"
	extra="\"running_address\":\"${SLV_ADDR_RUNNING}\",\"observed_address\":$(_addr_json_str "$ADDR_WINNER"),\"agreeing_checks\":${count},\"running_nat_1_1_mapping\":$(_addr_json_list "$(mapping_for "$SLV_ADDR_RUNNING")")"

	if [ "$action" = change ] && [ "$pending" != "$announced" ]; then
		json=$(addr_json change "$SLV_ADDR_HOST" "$SLV_ADDR_HOST_VAR" "$JS_PUBLIC_IP_DISCOVERY" "$SLV_ADDR_LITERALS" "$SLV_ADDR_EXTRAS" "$observed_mapping" "$SLV_ADDR_KEEP_PRIVATE" "$extra")
		echo "$P ADDRESS_RESOLUTION ${json}"
		addr_write_state "$json" "$P"
		echo "$P WARNING: public address changed: ${SLV_ADDR_RUNNING} -> ${pending} (confirmed by 2 consecutive checks). Janus still advertises nat_1_1_mapping=$(mapping_for "$SLV_ADDR_RUNNING"), so off-LAN viewers will fail ICE until Janus restarts." >&2
		announced=$pending
		if [ "$JS_PUBLIC_IP_CHANGE_ACTION" = restart ]; then
			restart_janus "$pending"
			exit 0
		fi
		echo "$P WARNING: JS_PUBLIC_IP_CHANGE_ACTION=warn: not restarting. Run 'docker compose restart janus' when voice is quiet, or set JS_PUBLIC_IP_CHANGE_ACTION=restart." >&2
	else
		addr_write_state "$(addr_json refresh "$SLV_ADDR_HOST" "$SLV_ADDR_HOST_VAR" "$JS_PUBLIC_IP_DISCOVERY" "$SLV_ADDR_LITERALS" "$SLV_ADDR_EXTRAS" "$observed_mapping" "$SLV_ADDR_KEEP_PRIVATE" "$extra")" "$P"
	fi
done
