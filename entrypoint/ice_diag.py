#!/usr/bin/env python3
"""legion-voice ICE diagnostics (slice A.5, SC-126): one record per slvoice session, kept after the session ends.

The question it answers is "why did voice fail for that one user", after the fact and without Janus's raw log.

How it collects:
  - Janus's sample event handler (janus.eventhandler.sampleevh.jcfg, written by the entrypoint when
    JS_ICE_DIAG_HISTORY > 0) POSTs session, handle, WebRTC and plugin events to this collector on
    127.0.0.1:SLV_ICE_DIAG_PORT (default 14229). jsep (SDP) and media events are not subscribed.
  - The slvoice plugin sends a plugin event when a participant joins a room, leaves it, or is reaped for having no
    media (O-75). That is where the display (the agent UUID), the room and the reap reason come from.
  - Janus never sends an event for a remote candidate, so the collector reads remote candidate types, and the
    plugin's RTP counters, from the Admin API's handle_info every SLV_ICE_DIAG_POLL_S seconds (default 2) while a
    session is live. That needs JS_ADMIN_SECRET; without it the remote counts stay unobserved.

What a record holds: the agent, the room, the Janus session and handle ids, the ICE and DTLS states reached, the
selected candidate pair with both candidate types, local and remote candidate type counts, whether the path is relay
or direct as far as this side can tell (with the prflx caveat, always), the RTP counters last polled, a timeline,
and an outcome: PASS | WARN | FAIL | INCONCLUSIVE with the reason and the last state reached. Never SDP, candidate
lines, credentials or secrets: records copy named facts only, and every output passes through scrub().

Retention: live sessions are always kept; the newest JS_ICE_DIAG_HISTORY ended sessions are kept (default 200,
at most 10000). The store is written to /run/legion-voice/ice-diag.json (SLV_ICE_DIAG_FILE), reloaded when the
collector starts, and lost when the container is recreated. A session live when the collector stopped is reloaded
as ended, with its end marked as not observed.

Read it with `legion-voice-selfcheck --sessions` and `legion-voice-selfcheck --session <handle|agent>`; this module
also holds those views. Run directly, it is the collector (started by the entrypoint). The image's python3 is 3.6.
"""

import collections
import json
import os
import re
import socket
import socketserver
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

SCHEMA = 1
PLUGIN = "janus.plugin.slvoice"
PASS, FAIL, WARN, INCONCLUSIVE = "PASS", "FAIL", "WARN", "INCONCLUSIVE"
SEVERITY = {PASS: 0, WARN: 1, INCONCLUSIVE: 2, FAIL: 3}
EXIT_FOR = {PASS: 0, WARN: 0, INCONCLUSIVE: 2, FAIL: 1}

HISTORY_DEFAULT = 200
HISTORY_MAX = 10000
DEFAULT_FILE = "/run/legion-voice/ice-diag.json"
DEFAULT_PORT = 14229
TIMELINE_MAX = 40
EARLIER_MAX = 5
STATES_MAX = 20
IGNORED_MAX = 4096
GONE_AFTER_MISSES = 2
HEARTBEAT_S = 30.0
STALE_AFTER_S = 120.0
MAX_POST_BYTES = 8 * 1024 * 1024

# Janus event types and WebRTC subtypes (vendor/janus-gateway/src/events/eventhandler.h).
TYPE_SESSION, TYPE_HANDLE, TYPE_JSEP, TYPE_WEBRTC, TYPE_MEDIA, TYPE_PLUGIN = 1, 2, 8, 16, 32, 64
SUB_ICE, SUB_LCAND, SUB_RCAND, SUB_PAIR, SUB_DTLS, SUB_STATE = 1, 2, 3, 4, 5, 6

CANDIDATE_TYPES = ("host", "srflx", "relay", "prflx")
_CANDIDATE_TYPE = re.compile(r"\btyp (host|srflx|relay|prflx)\b")
# ice.c: "%s:%d [%s,%s] <-> %s:%d [%s,%s]" (address, port, candidate type, transport; local first).
_PAIR = re.compile(r"^(.+):(\d+) \[(\w+),(\w+)\] <-> (.+):(\d+) \[(\w+),(\w+)\]$")

# The ladder a session climbs. The last step reached is the "last state" of a failure.
LADDER = ("attached", "joined", "ice checking", "ice connected", "dtls connected", "media up")

PATH_CAVEAT = (
    "As far as this side can tell. Janus sees its own candidates, the candidates the peer signalled and the address "
    "the peer's packets arrive from, nothing more. On a published-port mixer a peer's packets, relayed or not, can "
    "arrive through the port publish and show as prflx, so Janus's selected pair alone never proves how the viewer "
    "reached it.")

# Keys never copied or printed, and string values that look like SDP.
_SENSITIVE_KEY = re.compile(r"(sdp|jsep|secret|passw|pwd|credential|token|ufrag|api_?key)", re.I)
_SDP_VALUE = re.compile(r"(^v=0)|(\r?\na=)|(a=ice-(pwd|ufrag))", re.I)


def scrub(obj):
    """A copy of obj with sensitive keys dropped and SDP-looking strings replaced. Every output goes through this."""
    if isinstance(obj, dict):
        return dict((k, scrub(v)) for k, v in obj.items() if not _SENSITIVE_KEY.search(str(k)))
    if isinstance(obj, (list, tuple)):
        return [scrub(v) for v in obj]
    if isinstance(obj, str) and _SDP_VALUE.search(obj):
        return "[removed: looked like SDP]"
    return obj


def iso(epoch):
    if epoch is None:
        return None
    whole = int(epoch)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(whole)) + ".%03dZ" % int((epoch - whole) * 1000)


def _event_time(event, clock):
    stamp = event.get("timestamp")
    if isinstance(stamp, int) and stamp > 0:
        return stamp / 1e6   # janus_get_real_time(): microseconds
    return clock()


def count_types(lines):
    counts = dict((kind, 0) for kind in CANDIDATE_TYPES)
    for line in lines or []:
        match = _CANDIDATE_TYPE.search(str(line))
        if match:
            counts[match.group(1)] += 1
    return counts


def parse_pair(text):
    match = _PAIR.match(str(text or "").strip())
    if not match:
        return None
    g = match.groups()
    return {"local": {"address": g[0], "port": int(g[1]), "type": g[2], "transport": g[3]},
            "remote": {"address": g[4], "port": int(g[5]), "type": g[6], "transport": g[7]}}


def parse_history(text):
    """(history, warning or None). Diagnostics never stop a start: a bad value falls back with a warning."""
    text = "" if text is None else str(text).strip()
    if text == "":
        return HISTORY_DEFAULT, None
    if not text.isdigit():
        return HISTORY_DEFAULT, "JS_ICE_DIAG_HISTORY='%s' is not a non-negative integer; using %d" % (text, HISTORY_DEFAULT)
    value = int(text)
    if value > HISTORY_MAX:
        return HISTORY_MAX, "JS_ICE_DIAG_HISTORY=%d is above %d; using %d" % (value, HISTORY_MAX, HISTORY_MAX)
    return value, None


# ---- records -------------------------------------------------------------------------------------

def new_record(session_id, handle_id, when):
    return {
        "id": str(handle_id), "session_id": session_id, "handle_id": handle_id, "plugin": None,
        "display": None, "room": None, "recorder": False,
        "first_seen": iso(when), "last_activity": iso(when), "attached_at": None, "joined_at": None,
        "media_up_at": None, "ended_at": None, "live": True, "end": None, "end_observed": None,
        "attempt": 1, "reached": [], "ice_state": None, "ice_states": [], "dtls_state": None,
        "hangup_reason": None, "reaped": None, "selected_pair": None,
        "local_candidates": dict((kind, 0) for kind in CANDIDATE_TYPES),
        "remote_candidates": dict((kind, 0) for kind in CANDIDATE_TYPES),
        "remote_candidates_observed": False, "media": None, "timeline": [], "earlier": [],
    }


def meaningful(rec):
    """A participant session: it joined a room or had WebRTC activity. A control handle (create, list) is not."""
    return any(step in rec["reached"] for step in LADDER[1:]) or bool(rec["ice_states"]) or rec["dtls_state"] is not None \
        or rec["selected_pair"] is not None or rec["hangup_reason"] is not None


def last_state(rec):
    reached = [step for step in LADDER if step in rec["reached"]]
    return reached[-1] if reached else "none"


def _failure_word(reason):
    text = (reason or "").lower()
    return any(word in text for word in ("fail", "error", "timeout"))


def assess(rec):
    """(status, summary, hints) for one record."""
    media = "media up" in rec["reached"]
    reason = rec.get("hangup_reason")
    hints = []
    if not media and rec["remote_candidates_observed"] and not any(rec["remote_candidates"].values()):
        hints.append("Janus held no remote candidates for this handle when last polled (none in the offer and none "
                     "trickled)")
    elif not media and not rec["remote_candidates_observed"]:
        hints.append("remote candidates were not observed: no handle_info poll saw this session's ICE state (the poll "
                     "runs every 2 s while a session is live and needs JS_ADMIN_SECRET)")
    if rec.get("reaped") and not media:
        return FAIL, ("reaped by the mixer: no media %s s after join (JS_JOIN_MEDIA_TIMEOUT_S); the PeerConnection "
                      "never came up" % rec["reaped"].get("no_media_s", "?")), hints
    if not media:
        if rec["ice_state"] == "failed":
            return FAIL, "ICE failed before media came up: no candidate pair worked", hints
        if rec["dtls_state"] == "failed":
            return FAIL, "DTLS failed before media came up", hints
        if reason:
            return FAIL, "hung up before media came up: %s" % reason, hints
        if not rec["live"]:
            if rec.get("end_observed") is False:
                return INCONCLUSIVE, "media had not come up, and the end was not observed (%s)" % rec["end"], hints
            return FAIL, "ended before media came up (%s)" % rec["end"], hints
        return INCONCLUSIVE, "in progress: media not up yet", hints
    if _failure_word(reason) or rec["ice_state"] == "failed" or rec["dtls_state"] == "failed":
        return WARN, "media came up, then was lost: %s" % (reason or "ICE or DTLS failed"), []
    if not rec["live"]:
        if rec.get("end_observed") is False:
            return WARN, "media came up; the end was not observed (%s)" % rec["end"], []
        how = "hangup (%s), then %s" % (reason, rec["end"]) if reason else rec["end"]
        return PASS, "media came up; ended: %s" % how, []
    return PASS, "media up (live)", []


def classify_path(rec):
    """Relay, direct, undetermined or none, as far as this side can tell. Never decided from a prflx pair."""
    pair = rec.get("selected_pair")
    notes = []
    offered = rec["remote_candidates"].get("relay", 0)
    if not pair:
        verdict, basis = "none", "no candidate pair was selected, so there is no path to classify"
    else:
        lt, rt = pair["local"]["type"], pair["remote"]["type"]
        # The peer's side decides (A.6, measured): a published port DNATs Janus's own address, so Janus's local side reads
        # prflx even when the peer's real address arrives untouched. The peer's side reads prflx only when its source
        # address was rewritten on the way (Docker Desktop's port proxy) or learned from checks.
        if lt == "relay":
            verdict, basis = "relay", "Janus's side of the selected pair is a relay candidate: Janus sends through its own TURN server"
        elif rt == "relay":
            verdict, basis = "relay", "the peer's side of the selected pair is a relay candidate the peer signalled"
        elif rt == "prflx":
            verdict = "undetermined"
            basis = ("the peer's side of the selected pair is prflx (local %s): an address learned from connectivity "
                     "checks, not one the peer signalled. A source address rewritten on the way (Docker Desktop's port "
                     "proxy shows its gateway) produces this for relayed and direct peers alike" % lt)
        elif rt in ("host", "srflx"):
            verdict, basis = "direct", ("the peer's side of the selected pair is a %s candidate the peer signalled (local "
                                        "%s), so its own address reached Janus and it is not relaying through a TURN "
                                        "server of its own" % (rt, lt))
        else:
            verdict, basis = "undetermined", "unrecognised candidate types in the selected pair (local %s, remote %s)" % (lt, rt)
    if offered and verdict != "relay":
        notes.append("the peer signalled %d relay candidate(s), so it may be relaying" % offered)
    return {"verdict": verdict, "basis": basis, "caveat": PATH_CAVEAT, "notes": notes}


def view(rec):
    """The public form of a record: its facts plus outcome and path, scrubbed."""
    status, summary, hints = assess(rec)
    out = dict(rec)
    out["outcome"] = {"status": status, "summary": summary, "last_state": last_state(rec), "hints": hints}
    out["path"] = classify_path(rec)
    return scrub(out)


def record_from_view(item):
    rec = new_record(item.get("session_id"), item.get("handle_id"), time.time())
    for key in rec:
        if key in item:
            rec[key] = item[key]
    return rec


# ---- the store -----------------------------------------------------------------------------------

class Store(object):
    def __init__(self, history=HISTORY_DEFAULT, clock=time.time):
        self.history, self.clock = history, clock
        self.lock = threading.RLock()
        self.live = collections.OrderedDict()     # handle id -> record
        self.ended = collections.OrderedDict()    # handle id -> record, oldest first
        self.ignored = collections.OrderedDict()  # handles attached to another plugin
        self.misses = {}
        self.events_received = 0
        self.last_event = None
        self.evicted = 0
        self.dirty = False

    # -- helpers --
    def _note(self, rec, when, what):
        rec["timeline"].append({"t": iso(when), "what": what})
        del rec["timeline"][:-TIMELINE_MAX]
        rec["last_activity"] = iso(when)

    @staticmethod
    def _reach(rec, step):
        if step not in rec["reached"]:
            rec["reached"].append(step)

    def _live(self, session_id, handle_id, when):
        rec = self.live.get(handle_id)
        if rec is None:
            rec = new_record(session_id, handle_id, when)
            self.live[handle_id] = rec
        elif rec["session_id"] is None:
            rec["session_id"] = session_id
        return rec

    def _end(self, rec, how, when, observed=True):
        self.live.pop(rec["handle_id"], None)
        self.misses.pop(rec["handle_id"], None)
        if not meaningful(rec):
            return
        rec["live"], rec["ended_at"], rec["end"], rec["end_observed"] = False, iso(when), how, observed
        self._note(rec, when, "ended: %s" % how)
        self.ended[rec["handle_id"]] = rec
        while len(self.ended) > self.history:
            self.ended.popitem(last=False)
            self.evicted += 1

    def _archive_attempt(self, rec):
        status, summary, _ = assess(rec)
        rec["earlier"].append({"attempt": rec["attempt"], "room": rec["room"], "status": status, "summary": summary,
                               "last_state": last_state(rec), "joined_at": rec["joined_at"]})
        del rec["earlier"][:-EARLIER_MAX]
        rec["attempt"] += 1
        fresh = new_record(rec["session_id"], rec["handle_id"], self.clock())
        for key in ("joined_at", "media_up_at", "reached", "ice_state", "ice_states", "dtls_state", "hangup_reason",
                    "reaped", "selected_pair", "local_candidates", "remote_candidates", "remote_candidates_observed",
                    "media"):
            rec[key] = fresh[key]
        if rec["attached_at"]:
            rec["reached"].append("attached")

    # -- ingestion --
    def ingest(self, event):
        if not isinstance(event, dict):
            return
        with self.lock:
            self.events_received += 1
            self.last_event = iso(self.clock())
            self.dirty = True
            etype, body = event.get("type"), event.get("event") or {}
            if not isinstance(body, dict):
                return
            sid, hid = event.get("session_id"), event.get("handle_id")
            when = _event_time(event, self.clock)
            if etype == TYPE_SESSION:
                if body.get("name") in ("destroyed", "timeout"):
                    for rec in [r for r in self.live.values() if r["session_id"] == sid]:
                        self._end(rec, "Janus session %s" % body.get("name"), when)
                return
            if not isinstance(hid, int) or hid <= 0:
                return
            if etype == TYPE_HANDLE:
                self._handle(sid, hid, body, when)
            elif hid in self.ignored:
                return
            elif etype == TYPE_WEBRTC:
                self._webrtc(sid, hid, event.get("subtype"), body, when)
            elif etype == TYPE_PLUGIN and body.get("plugin") == PLUGIN and isinstance(body.get("data"), dict):
                self._plugin(sid, hid, body["data"], when)
            # jsep, media and every other type are ignored even if a handler sends them.

    def _handle(self, sid, hid, body, when):
        name = body.get("name")
        if name == "attached":
            if body.get("plugin") != PLUGIN:
                self.ignored[hid] = True
                while len(self.ignored) > IGNORED_MAX:
                    self.ignored.popitem(last=False)
                return
            rec = self._live(sid, hid, when)
            rec["plugin"], rec["attached_at"] = PLUGIN, iso(when)
            self._reach(rec, "attached")
            self._note(rec, when, "attached to %s" % PLUGIN)
        elif name == "detached":
            self.ignored.pop(hid, None)
            rec = self.live.get(hid)
            if rec is not None:
                self._end(rec, "handle detached", when)

    def _webrtc(self, sid, hid, subtype, body, when):
        rec = self._live(sid, hid, when)
        if subtype == SUB_ICE and isinstance(body.get("ice"), str):
            state = body["ice"]
            rec["ice_state"] = state
            if not rec["ice_states"] or rec["ice_states"][-1] != state:
                rec["ice_states"].append(state)
                del rec["ice_states"][:-STATES_MAX]
            if state in ("gathering", "connecting"):
                self._reach(rec, "ice checking")
            elif state in ("connected", "ready"):
                self._reach(rec, "ice checking")
                self._reach(rec, "ice connected")
            self._note(rec, when, "ICE %s" % state)
        elif subtype == SUB_LCAND:
            kind = count_types([body.get("local-candidate")])
            for key in CANDIDATE_TYPES:
                rec["local_candidates"][key] += kind[key]
        elif subtype == SUB_RCAND:
            kind = count_types([body.get("remote-candidate")])
            for key in CANDIDATE_TYPES:
                rec["remote_candidates"][key] += kind[key]
            rec["remote_candidates_observed"] = True
        elif subtype == SUB_PAIR:
            pair = parse_pair(body.get("selected-pair"))
            if pair:
                rec["selected_pair"] = pair
                self._note(rec, when, "selected pair: local %s %s, remote %s %s" % (
                    pair["local"]["type"], pair["local"]["transport"], pair["remote"]["type"], pair["remote"]["transport"]))
        elif subtype == SUB_DTLS and isinstance(body.get("dtls"), str):
            rec["dtls_state"] = body["dtls"]
            if body["dtls"] == "connected":
                self._reach(rec, "dtls connected")
            self._note(rec, when, "DTLS %s" % body["dtls"])
        elif subtype == SUB_STATE:
            if body.get("connection") == "webrtcup":
                for step in ("ice checking", "ice connected", "dtls connected", "media up"):
                    self._reach(rec, step)
                rec["media_up_at"] = iso(when)
                self._note(rec, when, "media up (webrtcup)")
            elif body.get("connection") == "hangup":
                rec["hangup_reason"] = str(body.get("reason") or "no reason given")
                self._note(rec, when, "hangup: %s" % rec["hangup_reason"])

    def _plugin(self, sid, hid, data, when):
        rec = self._live(sid, hid, when)
        rec["plugin"] = PLUGIN
        what = data.get("event")
        room = data.get("room") if isinstance(data.get("room"), int) else None
        display = data.get("display") if isinstance(data.get("display"), str) and data.get("display") else None
        if what == "joined":
            if "joined" in rec["reached"]:
                self._archive_attempt(rec)
            rec["room"], rec["joined_at"] = room, iso(when)
            rec["display"] = display or rec["display"]
            rec["recorder"] = data.get("recorder") is True
            self._reach(rec, "joined")
            self._note(rec, when, "joined room %s" % room)
        elif what == "left":
            self._note(rec, when, "left room %s" % room)
        elif what == "reaped":
            seconds = data.get("no_media_s") if isinstance(data.get("no_media_s"), int) else None
            rec["reaped"] = {"room": room, "no_media_s": seconds}
            self._note(rec, when, "reaped: no media %s s after join" % seconds)

    # -- the handle_info poll --
    def to_poll(self):
        with self.lock:
            return [(r["session_id"], r["handle_id"]) for r in self.live.values()
                    if r["session_id"] is not None and (meaningful(r) or r["plugin"] is None)]

    def apply_handle_info(self, hid, info):
        with self.lock:
            rec = self.live.get(hid)
            if rec is None or not isinstance(info, dict):
                return
            self.misses.pop(hid, None)
            plugin = info.get("plugin")
            if plugin and plugin != PLUGIN:
                self.live.pop(hid, None)
                self.ignored[hid] = True
                return
            rec["plugin"] = plugin or rec["plugin"]
            specific = info.get("plugin_specific") if isinstance(info.get("plugin_specific"), dict) else {}
            if isinstance(specific.get("display"), str) and specific["display"] and not rec["display"]:
                rec["display"] = specific["display"]
            if isinstance(specific.get("room"), int) and rec["room"] is None:
                rec["room"] = specific["room"]
            if "rtp_in_count" in specific or "rtp_out_count" in specific:
                rec["media"] = {"rtp_in": specific.get("rtp_in_count"), "rtp_out": specific.get("rtp_out_count"),
                                "datachannel_open": specific.get("datachannel_open") is True,
                                "polled_at": iso(self.clock())}
            ice = (info.get("webrtc") or {}).get("ice") if isinstance(info.get("webrtc"), dict) else None
            if isinstance(ice, dict):
                if isinstance(ice.get("remote-candidates"), list):
                    rec["remote_candidates"] = count_types(ice["remote-candidates"])
                    rec["remote_candidates_observed"] = True
                elif not rec["remote_candidates_observed"]:
                    # janus.c lists remote-candidates only when it holds some (`if(pc->remote_candidates)`), so a polled
                    # ice object without the key means none: none in the offer and none trickled so far.
                    rec["remote_candidates_observed"] = True
                if isinstance(ice.get("local-candidates"), list):
                    polled = count_types(ice["local-candidates"])
                    if sum(polled.values()) >= sum(rec["local_candidates"].values()):
                        rec["local_candidates"] = polled
                if rec["selected_pair"] is None and ice.get("selected-pair"):
                    rec["selected_pair"] = parse_pair(ice["selected-pair"])
            self.dirty = True

    def handle_missing(self, hid):
        """The Admin API says the handle is gone. Twice in a row, and its detach event never came: end it."""
        with self.lock:
            rec = self.live.get(hid)
            if rec is None:
                return
            self.misses[hid] = self.misses.get(hid, 0) + 1
            if self.misses[hid] >= GONE_AFTER_MISSES:
                self._end(rec, "handle gone per the Admin API (its detach event was not received)", self.clock())
                self.dirty = True

    # -- persistence --
    def snapshot(self, meta):
        with self.lock:
            live = [view(r) for r in reversed(list(self.live.values())) if meaningful(r)]
            ended = [view(r) for r in reversed(list(self.ended.values()))]
            collector = dict(meta, history=self.history, events_received=self.events_received,
                             last_event=self.last_event, evicted=self.evicted)
            self.dirty = False
        return scrub({"schema": SCHEMA, "written": iso(self.clock()), "collector": collector, "live": live,
                      "ended": ended})

    def load(self, doc):
        """Reload a previous collector's file. Its live sessions end now, with the end marked as not observed."""
        if not isinstance(doc, dict):
            return
        with self.lock:
            for item in reversed(doc.get("ended") or []):
                if isinstance(item, dict) and isinstance(item.get("handle_id"), int):
                    rec = record_from_view(item)
                    self.ended[rec["handle_id"]] = rec
            when = self.clock()
            for item in reversed(doc.get("live") or []):
                if isinstance(item, dict) and isinstance(item.get("handle_id"), int):
                    rec = record_from_view(item)
                    self._end(rec, "the collector restarted (a mixer restart?) while this session was live", when,
                              observed=False)
            while len(self.ended) > self.history:
                self.ended.popitem(last=False)
                self.evicted += 1
            self.dirty = True


def write_json(path, data):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w") as handle:
        json.dump(data, handle, indent=1)
        handle.write("\n")
    os.replace(tmp, path)


# ---- the collector -------------------------------------------------------------------------------

def admin_post(url, body, secret, timeout):
    request = urllib.request.Request(url, data=json.dumps(dict(body, transaction="icediag-" + os.urandom(4).hex(),
                                                               admin_secret=secret)).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.loads(reply.read().decode("utf-8"))


def poll_once(store, admin_url, secret, timeout=2.0):
    base = admin_url.rstrip("/")
    for sid, hid in store.to_poll():
        try:
            reply = admin_post("%s/%s/%s" % (base, sid, hid), {"janus": "handle_info"}, secret, timeout)
        except (OSError, ValueError):
            continue   # the Admin API is busy or down: try again next round
        if reply.get("janus") == "error":
            code = (reply.get("error") or {}).get("code")
            if code in (458, 459):   # no such session / no such handle
                store.handle_missing(hid)
            continue
        store.apply_handle_info(hid, reply.get("info"))


class _Server(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(store, host="127.0.0.1", port=DEFAULT_PORT):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, code, text):
            body = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_POST_BYTES:
                return self._reply(400, '{"error":"bad length"}')
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._reply(400, '{"error":"not JSON"}')
            for event in payload if isinstance(payload, list) else [payload]:
                store.ingest(event)
            self._reply(200, "{}")

    return _Server((host, port), Handler)


def run_collector(environ=None, out=None):
    environ = os.environ if environ is None else environ
    out = sys.stdout if out is None else out
    history, warning = parse_history(environ.get("JS_ICE_DIAG_HISTORY"))
    path = environ.get("SLV_ICE_DIAG_FILE") or DEFAULT_FILE
    port = int(environ.get("SLV_ICE_DIAG_PORT") or DEFAULT_PORT)
    poll_s = float(environ.get("SLV_ICE_DIAG_POLL_S") or 2.0)
    secret = environ.get("JS_ADMIN_SECRET") or ""
    admin_url = "http://127.0.0.1:%s%s" % (environ.get("JS_ADMIN_PORT") or "14225",
                                           environ.get("JS_ADMIN_BASEPATH") or "/voiceAdmin")
    if warning:
        out.write("[ice-diag] WARNING: %s\n" % warning)
    store = Store(history)
    try:
        with open(path) as handle:
            store.load(json.load(handle))
    except (OSError, ValueError):
        pass
    try:
        server = make_server(store, "127.0.0.1", port)
    except OSError as err:
        out.write("[ice-diag] ERROR: cannot listen on 127.0.0.1:%d (%s); no session diagnostics this run\n" % (port, err))
        out.flush()
        return 1
    poll_text = "every %g s" % poll_s if secret else "off: JS_ADMIN_SECRET is not set, so remote candidates are not observed"
    meta = {"pid": os.getpid(), "started": iso(time.time()), "listen": "127.0.0.1:%d" % port, "poll": poll_text}
    out.write("[ice-diag] INFO: collecting slvoice session diagnostics on 127.0.0.1:%d; history=%d ended sessions; "
              "file %s; handle_info poll %s\n" % (port, history, path, poll_text))
    out.flush()

    def writer():
        last = 0.0
        while True:
            time.sleep(0.5)
            if store.dirty or time.monotonic() - last >= HEARTBEAT_S:
                try:
                    write_json(path, store.snapshot(meta))
                    last = time.monotonic()
                except OSError as err:
                    out.write("[ice-diag] WARNING: could not write %s (%s)\n" % (path, err))
                    out.flush()
                    time.sleep(5)

    def poller():
        while True:
            time.sleep(poll_s)
            poll_once(store, admin_url, secret)

    threading.Thread(target=writer, daemon=True).start()
    if secret:
        threading.Thread(target=poller, daemon=True).start()
    server.serve_forever()
    return 0


# ---- the operator views (legion-voice-selfcheck --sessions / --session) -------------------------

def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, TypeError, ValueError):
        return True
    return True


def load_report(path, clock=time.time, pid_alive=_pid_alive):
    """(doc, problems, reason). doc is None with a reason when there is nothing to read."""
    try:
        with open(path) as handle:
            doc = json.load(handle)
    except OSError:
        return None, [], ("no diagnostics file at %s: the ICE diagnostics collector has not run in this container "
                          "(JS_ICE_DIAG_HISTORY=0 turns it off)" % path)
    except ValueError as err:
        return None, [], "the diagnostics file %s is not valid JSON (%s)" % (path, err)
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return None, [], "the diagnostics file %s has an unknown schema" % path
    problems = []
    collector = doc.get("collector") or {}
    if not pid_alive(collector.get("pid")):
        problems.append("the collector (pid %s) is not running: nothing after %s is recorded, and sessions shown as "
                        "live may have ended" % (collector.get("pid"), doc.get("written")))
    else:
        written = _parse_iso(doc.get("written"))
        if written is not None and clock() - written > STALE_AFTER_S:
            problems.append("the collector has not written for %d s: records may be stale" % (clock() - written))
    return doc, problems, None


def _parse_iso(text):
    try:
        return time.mktime(time.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except (ValueError, TypeError):
        return None


def all_sessions(doc):
    items = [v for v in (doc.get("live") or []) + (doc.get("ended") or []) if isinstance(v, dict)]
    return sorted(items, key=lambda v: str(v.get("ended_at") or v.get("last_activity") or ""), reverse=True)


def select(doc, agent=None, room=None, failed=False, limit=20):
    items = all_sessions(doc)
    if agent:
        items = [v for v in items if str(v.get("display") or "").lower().startswith(agent.lower())]
    if room is not None:
        items = [v for v in items if v.get("room") == room]
    if failed:
        items = [v for v in items if (v.get("outcome") or {}).get("status") in (FAIL, WARN)]
    return items[:limit] if limit else items


def find(doc, key):
    """(view, other matching ids, error). key: a handle id, or an agent UUID or its prefix (newest session wins)."""
    key = str(key or "").strip()
    items = all_sessions(doc)
    exact = [v for v in items if v.get("id") == key]
    if exact:
        return exact[0], [], None
    if len(key) < 4:
        return None, [], "'%s' matches no handle id; an agent prefix needs at least 4 characters" % key
    by_agent = [v for v in items if str(v.get("display") or "").lower().startswith(key.lower())]
    if not by_agent:
        return None, [], "no recorded session has handle id '%s' or an agent starting with it" % key
    agents = sorted(set(v.get("display") for v in by_agent))
    if len(agents) > 1:
        return None, [v.get("id") for v in by_agent], ("'%s' matches %d agents (%s): give more of the UUID"
                                                        % (key, len(agents), ", ".join(agents)))
    return by_agent[0], [v.get("id") for v in by_agent[1:]], None


def worst(views):
    statuses = [(v.get("outcome") or {}).get("status", INCONCLUSIVE) for v in views]
    return max(statuses, key=lambda s: SEVERITY.get(s, 2)) if statuses else PASS


def exit_code(status, problems):
    code = EXIT_FOR.get(status, 2)
    if problems and code == 0:
        code = 2
    return code


def _dash(value):
    return "-" if value in (None, "", []) else str(value)


def _counts(counts):
    counts = counts or {}
    return ", ".join("%s %d" % (kind, counts.get(kind, 0)) for kind in CANDIDATE_TYPES)


def _pair_text(pair):
    if not pair:
        return "-"
    l, r = pair["local"], pair["remote"]
    return "local %s:%s [%s,%s] <-> remote %s:%s [%s,%s]" % (l["address"], l["port"], l["type"], l["transport"],
                                                             r["address"], r["port"], r["type"], r["transport"])


def render_list(views, doc, path, problems, total, code):
    collector = doc.get("collector") or {}
    lines = ["[ice-diag] ===== legion-voice ICE diagnostics: %d of %d recorded session(s), newest first "
             "(%d live, %d ended; history %s; file %s) =====" % (len(views), total, len(doc.get("live") or []),
                                                                  len(doc.get("ended") or []), collector.get("history"), path)]
    for problem in problems:
        lines.append("[ice-diag] WARNING: %s" % problem)
    if not views:
        lines.append("[ice-diag] no sessions match")
    else:
        lines.append("[ice-diag] %-30s  %-12s  %-8s  %-10s  %-18s  %-12s  %s" % (
            "ENDED / LAST (UTC)", "STATUS", "AGENT", "ROOM", "HANDLE", "PATH", "OUTCOME"))
        for v in views:
            outcome = v.get("outcome") or {}
            when = v.get("ended_at") or ("live, %s" % v.get("last_activity"))
            lines.append("[ice-diag] %-30s  %-12s  %-8s  %-10s  %-18s  %-12s  %s" % (
                when, outcome.get("status"), str(v.get("display") or "?")[:8], _dash(v.get("room")), v.get("id"),
                (v.get("path") or {}).get("verdict"), outcome.get("summary")))
    lines.append("[ice-diag] ===== worst %s; exit %d; detail: legion-voice-selfcheck --session <HANDLE or agent UUID> "
                 "[--json] =====" % (worst(views), code))
    return "\n".join(lines) + "\n"


def render_detail(v, others, problems, code):
    outcome, path = v.get("outcome") or {}, v.get("path") or {}
    media = v.get("media") or {}
    lines = ["[ice-diag] ===== session %s: %s =====" % (v.get("id"), outcome.get("status"))]
    for problem in problems:
        lines.append("[ice-diag] WARNING: %s" % problem)
    lines += [
        "[ice-diag] agent:       %s (recorder: %s)" % (_dash(v.get("display")), "yes" if v.get("recorder") else "no"),
        "[ice-diag] room:        %s" % _dash(v.get("room")),
        "[ice-diag] janus:       session %s, handle %s%s" % (v.get("session_id"), v.get("handle_id"),
                                                             ", attempt %s on this handle" % v["attempt"] if (v.get("attempt") or 1) > 1 else ""),
        "[ice-diag] times (UTC): attached %s; joined %s; media up %s; %s" % (
            _dash(v.get("attached_at")), _dash(v.get("joined_at")), _dash(v.get("media_up_at")),
            "ended %s (%s)" % (v.get("ended_at"), v.get("end")) if not v.get("live") else "live, last activity %s" % v.get("last_activity")),
        "[ice-diag] outcome:     %s: %s" % (outcome.get("status"), outcome.get("summary")),
        "[ice-diag] last state:  %s" % outcome.get("last_state"),
    ]
    for hint in outcome.get("hints") or []:
        lines.append("[ice-diag] observed:    %s" % hint)
    lines += [
        "[ice-diag] ICE:         %s (states seen: %s)" % (_dash(v.get("ice_state")), ", ".join(v.get("ice_states") or []) or "none"),
        "[ice-diag] DTLS:        %s" % _dash(v.get("dtls_state")),
        "[ice-diag] hangup:      %s" % _dash(v.get("hangup_reason")),
        "[ice-diag] pair:        %s" % _pair_text(v.get("selected_pair")),
        "[ice-diag] candidates:  local (Janus) %s" % _counts(v.get("local_candidates")),
        "[ice-diag]              remote (peer) %s%s" % (_counts(v.get("remote_candidates")),
                                                        "" if v.get("remote_candidates_observed") else " (not observed)"),
        "[ice-diag] path:        %s, as far as this side can tell: %s" % (path.get("verdict"), path.get("basis")),
    ]
    for note in path.get("notes") or []:
        lines.append("[ice-diag]              note: %s" % note)
    lines.append("[ice-diag]              caveat: %s" % path.get("caveat"))
    if media:
        lines.append("[ice-diag] media:       rtp in %s, rtp out %s, data channel %s (polled %s)" % (
            _dash(media.get("rtp_in")), _dash(media.get("rtp_out")),
            "open" if media.get("datachannel_open") else "not open", media.get("polled_at")))
    else:
        lines.append("[ice-diag] media:       not polled")
    lines.append("[ice-diag] timeline:")
    for entry in v.get("timeline") or []:
        lines.append("[ice-diag]   %s  %s" % (entry.get("t"), entry.get("what")))
    for entry in v.get("earlier") or []:
        lines.append("[ice-diag] earlier attempt %s: %s, %s (room %s, last state %s)" % (
            entry.get("attempt"), entry.get("status"), entry.get("summary"), _dash(entry.get("room")), entry.get("last_state")))
    if others:
        lines.append("[ice-diag] older sessions for this agent: %s" % ", ".join(str(o) for o in others))
    lines.append("[ice-diag] ===== exit %d =====" % code)
    return "\n".join(lines) + "\n"


def cli_list(path, out, as_json=False, agent=None, room=None, failed=False, limit=20, clock=time.time,
             pid_alive=_pid_alive):
    doc, problems, reason = load_report(path, clock, pid_alive)
    if doc is None:
        return _unavailable(out, as_json, path, reason)
    views = select(doc, agent, room, failed, limit)
    total = len(all_sessions(doc))
    status = worst(views)
    code = exit_code(status, problems)
    if as_json:
        out.write(json.dumps(scrub({"schema": SCHEMA, "available": True, "file": path, "written": doc.get("written"),
                                    "collector": doc.get("collector"), "problems": problems,
                                    "filters": {"agent": agent, "room": room, "failed": failed, "limit": limit},
                                    "total_recorded": total, "sessions": views, "verdict": status, "exit_code": code}),
                             indent=2) + "\n")
    else:
        out.write(scrub(render_list(views, doc, path, problems, total, code)))
    out.flush()
    return code


def cli_detail(path, out, key, as_json=False, clock=time.time, pid_alive=_pid_alive):
    doc, problems, reason = load_report(path, clock, pid_alive)
    if doc is None:
        return _unavailable(out, as_json, path, reason)
    v, others, error = find(doc, key)
    if v is None:
        if as_json:
            out.write(json.dumps(scrub({"schema": SCHEMA, "available": True, "file": path, "found": False,
                                        "reason": error, "matches": others, "exit_code": 2}), indent=2) + "\n")
        else:
            out.write("[ice-diag] %s\n" % scrub(error))
        out.flush()
        return 2
    status = (v.get("outcome") or {}).get("status", INCONCLUSIVE)
    code = exit_code(status, problems)
    if as_json:
        out.write(json.dumps(scrub({"schema": SCHEMA, "available": True, "file": path, "found": True,
                                    "written": doc.get("written"), "collector": doc.get("collector"),
                                    "problems": problems, "session": v, "older_sessions_for_agent": others,
                                    "verdict": status, "exit_code": code}), indent=2) + "\n")
    else:
        out.write(scrub(render_detail(v, others, problems, code)))
    out.flush()
    return code


def _unavailable(out, as_json, path, reason):
    if as_json:
        out.write(json.dumps({"schema": SCHEMA, "available": False, "file": path, "reason": reason, "exit_code": 2},
                             indent=2) + "\n")
    else:
        out.write("[ice-diag] unavailable: %s\n" % reason)
    out.flush()
    return 2


if __name__ == "__main__":
    sys.exit(run_collector())
