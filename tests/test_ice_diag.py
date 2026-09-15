#!/usr/bin/env python3
"""Unit tests for entrypoint/ice_diag.py and the --sessions / --session views of selfcheck.py (slice A.5, SC-126):
  - a failed session is retained and reportable after it ends (reaped, ICE failed, hung up or ended before media);
  - the ring buffer keeps the newest JS_ICE_DIAG_HISTORY ended sessions and never evicts a live one;
  - relay vs direct carries the prflx caveat and is never decided from a prflx pair;
  - no credentials, secrets, SDP or candidate lines in the store, the file or any output;
  - the collector's HTTP ingestion and its handle_info poll;
  - --json shapes and exit codes, usage errors included.
Loopback only (tests/fakes.py), so the image build runs it with no network:

    python3 tests/test_ice_diag.py

ADDR_PROBE_DIR points at the directory holding ice_diag.py and selfcheck.py (default: ../entrypoint).
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ.get("ADDR_PROBE_DIR", os.path.join(HERE, "..", "entrypoint")))

import fakes  # noqa: E402
import ice_diag  # noqa: E402
import selfcheck  # noqa: E402
from ice_diag import FAIL, INCONCLUSIVE, PASS, PLUGIN, WARN  # noqa: E402

AGENT_A = "4f1c2a9e-1111-4222-8333-444455556666"
AGENT_B = "9b0d7e21-aaaa-4bbb-8ccc-dddddddddddd"
SID = 7001
ROOM = 904110900
SECRETS = ("S3CRET-api", "S3CRET-admin", "S3CRET-turn-user", "S3CRET-turn-pwd", "S3CRET-ufrag", "S3CRET-icepwd",
           "S3CRET-token", "S3CRET-opaque")


class Clock(object):
    def __init__(self, start=None):
        self.t = time.time() - 600 if start is None else start

    def __call__(self):
        return self.t

    def tick(self, seconds=1.0):
        self.t += seconds


def ev(etype, hid, body, subtype=None, sid=SID):
    event = {"type": etype, "session_id": sid, "handle_id": hid, "event": body}
    if subtype is not None:
        event["subtype"] = subtype
    return event


class Feed(object):
    """Janus-shaped events for one handle, in the order Janus and the plugin send them."""

    def __init__(self, store, clock, hid, agent=AGENT_A, room=ROOM, sid=SID):
        self.store, self.clock, self.hid, self.agent, self.room, self.sid = store, clock, hid, agent, room, sid

    def send(self, etype, body, subtype=None):
        self.clock.tick(0.5)
        self.store.ingest(ev(etype, self.hid, body, subtype, self.sid))
        return self

    def attach(self, plugin=PLUGIN):
        return self.send(2, {"name": "attached", "plugin": plugin, "opaque_id": "S3CRET-opaque", "token": "S3CRET-token"})

    def joined(self):
        return self.send(64, {"plugin": PLUGIN, "data": {"event": "joined", "room": self.room, "display": self.agent,
                                                          "recorder": False}})

    def ice(self, state):
        return self.send(16, {"ice": state, "stream_id": 1, "component_id": 1}, 1)

    def lcand(self, typ):
        return self.send(16, {"local-candidate": "1 1 udp 2015363327 172.23.0.2 10097 typ %s" % typ}, 2)

    def pair(self, lt, rt):
        return self.send(16, {"selected-pair": "174.82.163.190:10097 [%s,udp] <-> 172.23.0.1:57573 [%s,udp]" % (lt, rt)}, 4)

    def dtls(self, state):
        return self.send(16, {"dtls": state, "retransmissions": 0}, 5)

    def up(self):
        return self.send(16, {"connection": "webrtcup"}, 6)

    def hangup(self, reason):
        return self.send(16, {"connection": "hangup", "reason": reason}, 6)

    def left(self):
        return self.send(64, {"plugin": PLUGIN, "data": {"event": "left", "room": self.room, "display": self.agent}})

    def reaped(self, seconds=30):
        return self.send(64, {"plugin": PLUGIN, "data": {"event": "reaped", "room": self.room, "display": self.agent,
                                                          "no_media_s": seconds}})

    def detached(self):
        return self.send(2, {"name": "detached", "plugin": PLUGIN})

    def completed(self, lt="host", rt="srflx", reason="DTLS alert", end=True):
        self.attach().joined().ice("gathering").lcand("host").lcand("host").lcand("srflx").ice("connecting")
        self.ice("connected").pair(lt, rt).dtls("trying").dtls("connected").up()
        if end:
            self.hangup(reason).left().detached()
        return self

    def reaped_session(self, ice=True):
        self.attach().joined()
        if ice:
            self.ice("gathering").lcand("host")
        return self.reaped().left().detached()


def status_of(store, hid):
    rec = store.ended.get(hid) or store.live.get(hid)
    return ice_diag.view(rec)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = ice_diag.Store(200, self.clock)

    def feed(self, hid, agent=AGENT_A, room=ROOM):
        return Feed(self.store, self.clock, hid, agent, room)

    def test_reaped_session_is_retained_and_failed_after_it_ends(self):
        self.feed(11).reaped_session(ice=True)
        self.feed(12).reaped_session(ice=False)
        self.assertNotIn(11, self.store.live)
        v = status_of(self.store, 11)
        self.assertEqual((v["live"], v["end"], v["display"], v["room"]), (False, "handle detached", AGENT_A, ROOM))
        self.assertEqual(v["outcome"]["status"], FAIL)
        self.assertIn("reaped by the mixer: no media 30 s after join", v["outcome"]["summary"])
        self.assertEqual(v["outcome"]["last_state"], "ice checking")
        self.assertEqual(status_of(self.store, 12)["outcome"]["last_state"], "joined")

    def test_ice_failed_before_media(self):
        self.feed(21).attach().joined().ice("gathering").ice("connecting").ice("failed").hangup("ICE failed").detached()
        v = status_of(self.store, 21)
        self.assertEqual((v["outcome"]["status"], v["outcome"]["last_state"], v["hangup_reason"]),
                         (FAIL, "ice checking", "ICE failed"))
        self.assertIn("ICE failed before media came up", v["outcome"]["summary"])
        self.assertEqual(v["ice_states"], ["gathering", "connecting", "failed"])

    def test_hung_up_or_ended_before_media(self):
        self.feed(22).attach().joined().ice("gathering").hangup("Close PC").detached()
        self.assertEqual(status_of(self.store, 22)["outcome"]["summary"], "hung up before media came up: Close PC")
        self.feed(23).attach().joined().detached()
        v = status_of(self.store, 23)
        self.assertEqual((v["outcome"]["status"], v["outcome"]["summary"], v["outcome"]["last_state"]),
                         (FAIL, "ended before media came up (handle detached)", "joined"))

    def test_completed_session_passes_with_both_candidate_types(self):
        self.feed(31).completed()
        v = status_of(self.store, 31)
        self.assertEqual(v["outcome"]["status"], PASS)
        self.assertEqual(v["outcome"]["summary"], "media came up; ended: hangup (DTLS alert), then handle detached")
        self.assertEqual((v["outcome"]["last_state"], v["dtls_state"], v["ice_state"]), ("media up", "connected", "connected"))
        self.assertEqual((v["selected_pair"]["local"]["type"], v["selected_pair"]["remote"]["type"]), ("host", "srflx"))
        self.assertEqual(v["local_candidates"], {"host": 2, "srflx": 1, "relay": 0, "prflx": 0})
        self.assertIsNotNone(v["media_up_at"])

    def test_media_lost_is_warn_and_live_states(self):
        self.feed(32).completed(reason="ICE failed")
        self.assertEqual(status_of(self.store, 32)["outcome"]["status"], WARN)
        self.feed(33).attach().joined().ice("gathering")
        self.assertEqual(status_of(self.store, 33)["outcome"]["status"], INCONCLUSIVE)
        self.feed(34).completed(end=False)
        self.assertEqual(status_of(self.store, 34)["outcome"]["summary"], "media up (live)")

    def test_session_destroyed_ends_its_live_records(self):
        self.feed(41).attach().joined()
        self.store.ingest({"type": 1, "session_id": SID, "event": {"name": "destroyed"}})
        self.assertEqual(status_of(self.store, 41)["end"], "Janus session destroyed")

    def test_control_handles_and_other_plugins_are_not_recorded(self):
        self.feed(51).attach().detached()
        self.feed(52).attach()
        Feed(self.store, self.clock, 53).attach("janus.plugin.echotest").ice("connecting").up().detached()
        self.assertNotIn(51, self.store.ended)
        self.assertNotIn(53, self.store.ended)
        self.assertNotIn(53, self.store.live)
        snap = self.store.snapshot({"pid": os.getpid()})
        self.assertEqual((snap["live"], snap["ended"]), ([], []))

    def test_rejoin_on_the_same_handle_archives_the_failed_attempt(self):
        f = self.feed(61).attach().joined().reaped().left()
        f.joined().ice("connecting").ice("connected").pair("host", "host").dtls("connected").up()
        v = status_of(self.store, 61)
        self.assertEqual((v["attempt"], v["outcome"]["status"]), (2, PASS))
        self.assertEqual((v["earlier"][0]["status"], v["earlier"][0]["attempt"]), (FAIL, 1))
        self.assertIsNone(v["reaped"])

    def test_ring_buffer_keeps_the_newest_ended_and_every_live_session(self):
        store = ice_diag.Store(5, self.clock)
        for i in range(12):
            Feed(store, self.clock, 100 + i).completed()
        for i in range(3):
            Feed(store, self.clock, 200 + i).attach().joined()
        self.assertEqual(list(store.ended), [107, 108, 109, 110, 111])
        self.assertEqual((store.evicted, len(store.live)), (7, 3))
        snap = store.snapshot({"pid": os.getpid()})
        self.assertEqual([v["id"] for v in snap["ended"]], ["111", "110", "109", "108", "107"])
        self.assertEqual(len(snap["live"]), 3)
        self.assertEqual(snap["collector"]["evicted"], 7)

    def test_reload_ends_live_sessions_as_not_observed_and_keeps_the_bound(self):
        self.feed(71).completed(end=False)
        self.feed(72).attach().joined()
        self.feed(73).reaped_session()
        doc = json.loads(json.dumps(self.store.snapshot({"pid": 1})))
        fresh = ice_diag.Store(200, self.clock)
        fresh.load(doc)
        self.assertEqual(fresh.live, {})
        a, b, c = (ice_diag.view(fresh.ended[h]) for h in (71, 72, 73))
        self.assertEqual((a["end_observed"], a["outcome"]["status"]), (False, WARN))
        self.assertEqual((b["end_observed"], b["outcome"]["status"]), (False, INCONCLUSIVE))
        self.assertEqual(c["outcome"]["status"], FAIL)
        small = ice_diag.Store(1, self.clock)
        small.load(doc)
        self.assertEqual(len(small.ended), 1)

    def test_a_handle_the_admin_api_reports_gone_twice_is_ended(self):
        self.feed(81).attach().joined()
        self.store.handle_missing(81)
        self.assertIn(81, self.store.live)
        self.store.handle_missing(81)
        self.assertIn("handle gone per the Admin API", status_of(self.store, 81)["end"])

    def test_a_polled_ice_state_without_remote_candidates_is_observed_as_none(self):
        """Janus omits remote-candidates when it holds none (S10's offer had no candidates): that is an observation,
        not a missing poll."""
        self.feed(82).attach().joined().ice("gathering")
        self.assertIn("were not observed", status_of(self.store, 82)["outcome"]["hints"][0])
        self.store.apply_handle_info(82, {"plugin": PLUGIN, "webrtc": {"ice": {"local-candidates": [
            "1 1 udp 1 172.23.0.2 10097 typ host"]}}, "plugin_specific": {"rtp_in_count": 0}})
        self.feed(82).reaped().left().detached()
        v = status_of(self.store, 82)
        self.assertEqual((v["remote_candidates_observed"], v["remote_candidates"]["host"]), (True, 0))
        self.assertEqual(v["outcome"]["hints"], ["Janus held no remote candidates for this handle when last polled "
                                                 "(none in the offer and none trickled)"])
        self.store.apply_handle_info(83, {"plugin": PLUGIN})   # not live: ignored

    def test_parse_history(self):
        self.assertEqual(ice_diag.parse_history(""), (200, None))
        self.assertEqual(ice_diag.parse_history("0"), (0, None))
        self.assertEqual(ice_diag.parse_history("150"), (150, None))
        self.assertEqual(ice_diag.parse_history("abc")[0], 200)
        self.assertEqual(ice_diag.parse_history("20000")[0], 10000)


class PathTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = ice_diag.Store(200, self.clock)

    def path(self, hid, lt, rt, remote_relay=0):
        Feed(self.store, self.clock, hid).completed(lt, rt, end=False)
        if remote_relay:
            lines = ["candidate:%d 1 udp 1 203.0.113.9 %d typ relay raddr 0.0.0.0 rport 0" % (i, 50000 + i)
                     for i in range(remote_relay)]
            self.store.apply_handle_info(hid, {"plugin": PLUGIN, "webrtc": {"ice": {"remote-candidates": lines}}})
        return ice_diag.view(self.store.live[hid])["path"]

    def test_a_prflx_pair_is_undetermined_never_relay_or_direct(self):
        for n, (lt, rt) in enumerate((("prflx", "prflx"), ("host", "prflx"), ("prflx", "srflx"))):
            p = self.path(300 + n, lt, rt)
            self.assertEqual(p["verdict"], "undetermined", (lt, rt))
            self.assertEqual(p["caveat"], ice_diag.PATH_CAVEAT)
            self.assertIn("published-port", p["caveat"])

    def test_relay_candidates_offered_with_a_prflx_pair_is_a_note_not_a_verdict(self):
        p = self.path(310, "prflx", "prflx", remote_relay=2)
        self.assertEqual(p["verdict"], "undetermined")
        self.assertEqual(p["notes"], ["the peer signalled 2 relay candidate(s), so it may be relaying"])

    def test_relay_on_either_side_of_the_pair(self):
        self.assertEqual(self.path(320, "relay", "prflx")["verdict"], "relay")
        self.assertEqual(self.path(321, "host", "relay")["verdict"], "relay")

    def test_host_and_srflx_pair_is_direct_with_the_caveat(self):
        p = self.path(330, "host", "srflx")
        self.assertEqual((p["verdict"], p["caveat"]), ("direct", ice_diag.PATH_CAVEAT))

    def test_no_pair_is_none(self):
        Feed(self.store, self.clock, 340).attach().joined()
        self.assertEqual(ice_diag.view(self.store.live[340])["path"]["verdict"], "none")

    def test_the_detail_view_states_the_caveat_for_every_verdict(self):
        for n, (lt, rt) in enumerate((("prflx", "prflx"), ("relay", "host"), ("host", "host"))):
            Feed(self.store, self.clock, 350 + n).completed(lt, rt, end=False)
            v = ice_diag.view(self.store.live[350 + n])
            text = ice_diag.render_detail(v, [], [], 0)
            self.assertIn("as far as this side can tell", text)
            self.assertIn("caveat: " + ice_diag.PATH_CAVEAT, text)


class CliBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="icediag-")
        self.path = os.path.join(self.dir, "ice-diag.json")
        self.clock = Clock()
        self.store = ice_diag.Store(200, self.clock)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, pid=None):
        self.clock.t = time.time()
        ice_diag.write_json(self.path, self.store.snapshot({"pid": os.getpid() if pid is None else pid,
                                                            "listen": "127.0.0.1:14229", "poll": "every 2 s"}))

    def run_main(self, *argv, **kw):
        out = io.StringIO()
        environ = {"SLV_ICE_DIAG_FILE": kw.get("path", self.path),
                   "SLV_EFFECTIVE_CONFIG": os.path.join(self.dir, "no-effective-config.json")}
        code = selfcheck.main(list(argv), environ=environ, out=out, runner=lambda *a: [], collector=lambda *a: {})
        return code, out.getvalue()


class CliTests(CliBase):
    def setUp(self):
        CliBase.setUp(self)
        Feed(self.store, self.clock, 12, AGENT_B, ROOM + 1).completed()          # oldest: B, PASS
        Feed(self.store, self.clock, 11, AGENT_A).reaped_session()               # A, FAIL
        Feed(self.store, self.clock, 13, AGENT_A).completed("prflx", "prflx")    # newest: A, PASS
        self.write()

    def test_list_json_shape_and_worst_status_exit(self):
        code, text = self.run_main("--sessions", "--json")
        data = json.loads(text)
        self.assertEqual(code, 1)
        for key in ("schema", "available", "file", "written", "collector", "problems", "filters", "total_recorded",
                    "sessions", "verdict", "exit_code"):
            self.assertIn(key, data)
        self.assertEqual((data["verdict"], data["exit_code"], data["total_recorded"], data["problems"]), (FAIL, 1, 3, []))
        self.assertEqual([s["id"] for s in data["sessions"]], ["13", "11", "12"])
        for s in data["sessions"]:
            for key in ("display", "room", "session_id", "handle_id", "ice_state", "dtls_state", "selected_pair",
                        "local_candidates", "remote_candidates", "live", "ended_at", "timeline"):
                self.assertIn(key, s)
            self.assertEqual(set(s["outcome"]), {"status", "summary", "last_state", "hints"})
            self.assertEqual(set(s["path"]), {"verdict", "basis", "caveat", "notes"})

    def test_list_text_and_filters(self):
        code, text = self.run_main("--sessions")
        self.assertEqual(code, 1)
        self.assertIn("[ice-diag] ===== legion-voice ICE diagnostics: 3 of 3 recorded session(s)", text)
        self.assertIn("reaped by the mixer", text)
        self.assertIn("===== worst FAIL; exit 1;", text)
        self.assertEqual(self.run_main("--sessions", "--agent", AGENT_B[:8])[0], 0)
        data = json.loads(self.run_main("--sessions", "--failed", "--json")[1])
        self.assertEqual([s["id"] for s in data["sessions"]], ["11"])
        data = json.loads(self.run_main("--sessions", "--room", str(ROOM + 1), "--json")[1])
        self.assertEqual([s["id"] for s in data["sessions"]], ["12"])
        data = json.loads(self.run_main("--sessions", "--limit", "1", "--json")[1])
        self.assertEqual((len(data["sessions"]), data["exit_code"]), (1, 0))

    def test_detail_by_handle_and_by_agent(self):
        code, text = self.run_main("--session", "11")
        self.assertEqual(code, 1)
        for part in ("[ice-diag] ===== session 11: FAIL =====", "reaped by the mixer: no media 30 s after join",
                     "last state:  ice checking", "ended ", "(handle detached)", "caveat: "):
            self.assertIn(part, text)
        code, text = self.run_main("--session", AGENT_A[:8], "--json")
        data = json.loads(text)
        self.assertEqual((code, data["found"], data["session"]["id"], data["older_sessions_for_agent"], data["verdict"]),
                         (0, True, "13", ["11"], PASS))
        self.assertEqual(data["session"]["path"]["verdict"], "undetermined")

    def test_not_found_short_prefix_and_ambiguous_exit_2(self):
        self.assertEqual(self.run_main("--session", "999999")[0], 2)
        self.assertEqual(self.run_main("--session", "4f")[0], 2)
        data = json.loads(self.run_main("--session", "zzzz", "--json")[1])
        self.assertEqual((data["found"], data["exit_code"]), (False, 2))

    def test_unavailable_file_exit_2(self):
        missing = os.path.join(self.dir, "missing.json")
        code, text = self.run_main("--sessions", "--json", path=missing)
        data = json.loads(text)
        self.assertEqual((code, data["available"], data["exit_code"]), (2, False, 2))
        self.assertIn("JS_ICE_DIAG_HISTORY=0", data["reason"])
        self.assertEqual(self.run_main("--session", "11", path=missing)[0], 2)

    def test_a_stopped_collector_raises_a_clean_exit_to_2_and_keeps_fail(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.write(pid=dead.pid)
        code, text = self.run_main("--sessions", "--agent", AGENT_B[:8])
        self.assertEqual(code, 2)
        self.assertIn("WARNING: the collector (pid %d) is not running" % dead.pid, text)
        self.assertEqual(self.run_main("--sessions")[0], 1)

    def test_empty_store_exit_0(self):
        self.store = ice_diag.Store(200, self.clock)
        self.write()
        code, text = self.run_main("--sessions")
        self.assertEqual(code, 0)
        self.assertIn("no sessions match", text)

    def test_usage_errors(self):
        for argv in (("--agent", "abcd"), ("--session",), ("--sessions", "--limit", "abc"),
                     ("--sessions", "--session", "11"), ("--session", "11", "--failed"), ("--failed",),
                     ("--sessions", "--candidates")):
            self.assertEqual(self.run_main(*argv)[0], 64, argv)


class SecretTests(CliBase):
    def test_scrub_drops_sensitive_keys_and_sdp(self):
        dirty = {"sdp": "x", "jsep": {"type": "offer"}, "admin_secret": "a", "turn_pwd": "b", "credential": "c",
                 "token": "t", "ufrag": "u", "api_key": "k", "password": "p",
                 "ok": "v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\na=ice-pwd:zzz", "nested": [{"passwd": "p", "keep": 1}]}
        self.assertEqual(ice_diag.scrub(dirty), {"ok": "[removed: looked like SDP]", "nested": [{"keep": 1}]})

    def test_no_secrets_sdp_or_candidate_lines_in_any_output(self):
        sdp = ("v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\na=ice-ufrag:S3CRET-ufrag\r\na=ice-pwd:S3CRET-icepwd\r\n"
               "a=candidate:1 1 udp 1 192.0.2.1 5000 typ host\r\n")
        f = Feed(self.store, self.clock, 91)
        f.attach()
        f.send(8, {"owner": "remote", "jsep": {"type": "offer", "sdp": sdp}})
        f.send(64, {"plugin": PLUGIN, "data": {"event": "joined", "room": ROOM, "display": AGENT_A,
                                               "admin_secret": "S3CRET-admin", "password": "S3CRET-turn-pwd"}})
        f.send(16, {"ice": "connecting", "turn_pwd": "S3CRET-turn-pwd", "credential": "S3CRET-turn-user"}, 1)
        f.send(32, {"media": "audio", "secret": "S3CRET-api"})
        f.send(256, {"code": 1, "info": {"username": "S3CRET-turn-user", "password": "S3CRET-turn-pwd"}})
        self.store.apply_handle_info(91, {
            "plugin": PLUGIN, "sdps": {"local": sdp, "remote": sdp}, "token": "S3CRET-token",
            "plugin_specific": {"display": AGENT_A, "room": ROOM, "rtp_in_count": 5, "api_secret": "S3CRET-api"},
            "webrtc": {"ice": {"remote-candidates": ["candidate:1 1 udp 1 198.51.100.4 6000 typ srflx raddr 0.0.0.0 "
                                                      "rport 0 generation 0 ufrag S3CRET-ufrag network-id 1"],
                               "local-candidates": ["1 1 udp 1 172.23.0.2 10097 typ host"]},
                       "dtls": {"fingerprint": "S3CRET-icepwd"}}})
        f.pair("host", "srflx").dtls("connected").up()
        live_view = ice_diag.view(self.store.live[91])
        f.hangup("DTLS alert").detached()
        self.write()
        with open(self.path) as handle:
            written = handle.read()
        outputs = {"live view": json.dumps(live_view), "snapshot": json.dumps(self.store.snapshot({"pid": 1})),
                   "file": written}
        for name, argv in (("list", ("--sessions",)), ("list json", ("--sessions", "--json")),
                           ("detail", ("--session", "91")), ("detail json", ("--session", "91", "--json"))):
            outputs[name] = self.run_main(*argv)[1]
        for name, text in outputs.items():
            for secret in SECRETS:
                self.assertNotIn(secret, text, "%s carries %s" % (name, secret))
            for marker in ("v=0", "a=ice", "a=candidate", "typ host", "typ srflx"):
                self.assertNotIn(marker, text, "%s carries %r" % (name, marker))
        data = json.loads(outputs["detail json"])["session"]
        self.assertEqual(data["remote_candidates"]["srflx"], 1)
        self.assertEqual(data["media"]["rtp_in"], 5)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = ice_diag.Store(200, self.clock)

    def test_http_ingestion_of_grouped_events_and_bad_bodies(self):
        server = ice_diag.make_server(self.store, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = "http://127.0.0.1:%d/events" % server.server_address[1]
            events = [ev(2, 501, {"name": "attached", "plugin": PLUGIN}),
                      ev(64, 501, {"plugin": PLUGIN, "data": {"event": "joined", "room": ROOM, "display": AGENT_A}}),
                      ev(16, 501, {"ice": "gathering"}, 1)]
            request = urllib.request.Request(url, data=json.dumps(events).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=5) as reply:
                self.assertEqual(reply.status, 200)
            self.assertEqual((self.store.live[501]["display"], self.store.live[501]["ice_state"]), (AGENT_A, "gathering"))
            self.assertEqual(self.store.events_received, 3)
            bad = urllib.request.Request(url, data=b"not json", headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(bad, timeout=5)
            self.assertEqual(caught.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()

    def test_poll_reads_remote_candidates_drops_other_plugins_and_ends_gone_handles(self):
        Feed(self.store, self.clock, 601).attach().joined()
        self.store.ingest(ev(16, 602, {"ice": "connecting"}, 1))   # attach unseen: plugin unknown until polled
        admin = fakes.FakeJanusAdmin({SID: {
            601: {"plugin": PLUGIN, "plugin_specific": {"display": AGENT_A, "room": ROOM, "rtp_in_count": 12,
                                                        "rtp_out_count": 9, "datachannel_open": True},
                  "webrtc": {"ice": {"remote-candidates": ["candidate:1 1 udp 1 203.0.113.9 50000 typ relay",
                                                           "candidate:2 1 udp 1 192.168.1.5 50001 typ host"],
                                     "selected-pair": "172.23.0.2:10097 [host,udp] <-> 192.168.1.5:50001 [host,udp]"}}},
            602: {"plugin": "janus.plugin.echotest"}}})
        try:
            ice_diag.poll_once(self.store, admin.url, admin.secret)
            rec = self.store.live[601]
            self.assertEqual(rec["remote_candidates"], {"host": 1, "srflx": 0, "relay": 1, "prflx": 0})
            self.assertEqual((rec["media"]["rtp_in"], rec["media"]["datachannel_open"]), (12, True))
            self.assertEqual(rec["selected_pair"]["remote"]["type"], "host")
            self.assertNotIn(602, self.store.live)
            admin.handles = {SID: {}}
            ice_diag.poll_once(self.store, admin.url, admin.secret)
            ice_diag.poll_once(self.store, admin.url, admin.secret)
            self.assertIn("handle gone per the Admin API", self.store.ended[601]["end"])
        finally:
            admin.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
