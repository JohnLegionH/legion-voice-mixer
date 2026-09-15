#!/usr/bin/env python3
"""Unit tests for entrypoint/selfcheck.py (slices A.2, A.2b). They cover:
  - every check's PASS, FAIL (or WARN) and INCONCLUSIVE branch;
  - C2a preserved, remapped and blocked;
  - C2b with no, fresh and stale receipts, including the age boundary;
  - the --listen receipt;
  - exit codes, the worst-status verdict and the time bound;
  - the correct forwarded deployment and the VPS shape.
Loopback fakes only (tests/fakes.py), so the image build runs this with no network:

    python3 tests/test_selfcheck.py

ADDR_PROBE_DIR points at the directory holding selfcheck.py and addr_probe.py (default: ../entrypoint).
"""

import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ.get("ADDR_PROBE_DIR", os.path.join(HERE, "..", "entrypoint")))

import fakes  # noqa: E402
import selfcheck  # noqa: E402
from selfcheck import FAIL, INCONCLUSIVE, PASS, WARN  # noqa: E402

NAT_WORDS = ("NAT", "symmetric", "remapp", "carrier")
REMAP_TEXT = ("container-initiated UDP is source-port remapped; this is normal for a published-port container and "
              "does not by itself break a forwarded server")


def read_json(path):
    with open(path) as handle:
        return json.load(handle)


def free_udp_range(width, start=21000, stop=31000):
    """A 127.0.0.1 UDP range below the Linux ephemeral range, every port bindable right now."""
    for lo in range(start, stop, width + 9):
        socks = []
        try:
            for port in range(lo, lo + width):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                socks.append(sock)
                sock.bind(("127.0.0.1", port))
            return lo, lo + width - 1
        except OSError:
            continue
        finally:
            for sock in socks:
                sock.close()
    raise unittest.SkipTest("no free UDP range")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.closers = []

    def tearDown(self):
        for close in reversed(self.closers):
            close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def track(self, obj):
        self.closers.append(obj.close)
        return obj

    def hold_udp(self, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", port))
        self.closers.append(sock.close)

    def state(self, mapping, **extra):
        path = os.path.join(self.dir, "public-address.json")
        data = {"nat_1_1_mapping": mapping, "winner": {"source": "stun", "address": mapping[0] if mapping else None}}
        data.update(extra)
        with open(path, "w") as handle:
            json.dump(data, handle)
        return path

    def jcfg(self, rtp="10000-10200", nat=""):
        path = os.path.join(self.dir, "janus.jcfg")
        with open(path, "w") as handle:
            handle.write('general: {\n}\nmedia: {\n\trtp_port_range = "%s"\n}\nnat: {\n'
                         '\t#stun_server = "stun.voip.eutelia.it"\n%s\n\tice_ignore_list = "vmnet"\n}\n' % (rtp, nat))
        return path

    def inbound(self, port=10000, age_s=60, advertised=("174.82.163.190",), now=None, **extra):
        now = time.time() if now is None else now
        path = os.path.join(self.dir, "selfcheck-inbound.json")
        record = {"schema": 1, "result": PASS, "time": selfcheck.iso(now - age_s), "epoch": int(now - age_s),
                  "port": port, "source": "198.51.100.23:40001", "advertised": list(advertised), "range": "10000-10200"}
        record.update(extra)
        with open(path, "w") as handle:
            json.dump(record, handle)
        return path


class C1(Base):
    def test_public_pass(self):
        r = selfcheck.check_c1(self.state(["174.82.163.190", "192.168.1.225"]))
        self.assertEqual(r["status"], PASS)
        self.assertIn("public: 174.82.163.190 (discovered by stun)", r["observed"])
        self.assertIsNone(r["remediation"])

    def test_cgnat_is_its_own_fail(self):
        r = selfcheck.check_c1(self.state(["100.64.12.34"]))
        self.assertEqual(r["status"], FAIL)
        self.assertIn("carrier-grade NAT (CGNAT)", r["observed"])
        self.assertIn("TURN", r["remediation"])
        self.assertNotIn("no public address", r["observed"])

    def test_private_fail(self):
        r = selfcheck.check_c1(self.state(["192.168.1.225"]))
        self.assertEqual(r["status"], FAIL)
        self.assertIn("has no public address (192.168.1.225 private)", r["observed"])

    def test_empty_mapping_fail(self):
        self.assertEqual(selfcheck.check_c1(self.state([]))["status"], FAIL)

    def test_missing_state_inconclusive(self):
        self.assertEqual(selfcheck.check_c1(os.path.join(self.dir, "absent.json"))["status"], INCONCLUSIVE)

    def test_confirmed_change_warns(self):
        path = self.state(["174.82.163.190"], running_address="174.82.163.190", observed_address="198.51.100.4",
                          agreeing_checks=2)
        self.assertEqual(selfcheck.check_c1(path)["status"], WARN)


class C2a(Base):
    def setUp(self):
        super(C2a, self).setUp()
        self.lo, self.hi = free_udp_range(21)
        self.range = "%d-%d" % (self.lo, self.hi)

    def run_c2a(self, mapping, seconds=5.0, advertised=()):
        stun = self.track(fakes.FakeStun(mapping))
        return selfcheck.check_c2a(stun.address, self.range, selfcheck.Budget(seconds), "127.0.0.1", list(advertised))

    def assert_inbound_note(self, r):
        self.assertTrue(any("separate mapping this probe cannot see" in n for n in r["notes"]), r["notes"])

    def test_preserved_pass(self):
        r = self.run_c2a(lambda ip, port: ("203.0.113.7", port), advertised=["203.0.113.7"])
        self.assertEqual(r["status"], PASS, r["observed"])
        for port in (self.lo, (self.lo + self.hi) // 2, self.hi):
            self.assertIn("local %d -> 203.0.113.7:%d" % (port, port), r["observed"])
        self.assert_inbound_note(r)

    def test_vps_shape_pass_without_nat_words(self):
        r = self.run_c2a(lambda ip, port: (ip, port))
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("no address translation", r["observed"])
        for word in NAT_WORDS:
            self.assertNotIn(word, " ".join([r["observed"]] + r["notes"]))

    def test_remapped_is_information_not_fail(self):
        r = self.run_c2a(lambda ip, port: ("203.0.113.7", 40000 + (port % 1000)))
        self.assertEqual(r["status"], WARN)
        self.assertTrue(r["observed"].startswith(REMAP_TEXT), r["observed"])
        self.assertIn("legion-voice-selfcheck --listen", r["remediation"])
        self.assertNotIn("TURN", r["observed"] + r["remediation"])
        self.assert_inbound_note(r)

    def test_blocked_fail(self):
        lo, hi = self.lo, self.hi
        r = self.run_c2a(lambda ip, port: None if lo <= port <= hi else ("203.0.113.7", port))
        self.assertEqual(r["status"], FAIL)
        self.assertTrue(r["observed"].startswith("outbound UDP from the media range is blocked"), r["observed"])
        self.assertIn("egress", r["remediation"])
        self.assert_inbound_note(r)

    def test_stun_unreachable_inconclusive(self):
        r = self.run_c2a(lambda ip, port: None, seconds=1.0)
        self.assertEqual(r["status"], INCONCLUSIVE)
        self.assert_inbound_note(r)

    def test_expired_budget_not_run(self):
        r = selfcheck.check_c2a("127.0.0.1:3478", self.range, selfcheck.Budget(0.0), "127.0.0.1")
        self.assertEqual(r["status"], INCONCLUSIVE)
        self.assertIn("time bound", r["observed"])

    def test_port_in_use_moves_inward(self):
        self.hold_udp(self.lo)
        r = self.run_c2a(lambda ip, port: ("203.0.113.7", port))
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("local %d (for %d, in use)" % (self.lo + 1, self.lo), r["observed"])


class C2b(Base):
    RANGE = "10000-10200"
    PUBLIC = ["174.82.163.190"]

    def run_c2b(self, path, now=None, max_age_h=168, public=None):
        return selfcheck.check_c2b(path, self.RANGE, max_age_h, self.PUBLIC if public is None else public, now)

    def test_no_record_inconclusive_with_the_listen_command(self):
        r = self.run_c2b(os.path.join(self.dir, "absent.json"))
        self.assertEqual(r["status"], INCONCLUSIVE)
        self.assertIn("cannot be determined from inside the container", r["observed"])
        self.assertIn("docker compose exec janus legion-voice-selfcheck --listen", r["remediation"])

    def test_fresh_record_pass(self):
        r = self.run_c2b(self.inbound(age_s=3 * 3600))
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("inbound UDP reached port 10000 from outside (observed ", r["observed"])
        self.assertIn("from 198.51.100.23:40001, 3.0 h ago)", r["observed"])

    def test_stale_record_inconclusive(self):
        r = self.run_c2b(self.inbound(age_s=200 * 3600))
        self.assertEqual(r["status"], INCONCLUSIVE)
        self.assertIn("older than JS_SELFCHECK_INBOUND_MAX_AGE_H=168", r["observed"])
        self.assertIn("--listen", r["remediation"])

    def test_age_boundary(self):
        now = 1800000000.0
        path = self.inbound(age_s=0, now=now)
        epoch = read_json(path)["epoch"]
        self.assertEqual(self.run_c2b(path, now=epoch + 168 * 3600)["status"], PASS)
        self.assertEqual(self.run_c2b(path, now=epoch + 168 * 3600 + 1)["status"], INCONCLUSIVE)

    def test_unreadable_record_inconclusive(self):
        path = os.path.join(self.dir, "bad.json")
        with open(path, "w") as handle:
            handle.write("{not json")
        self.assertEqual(self.run_c2b(path)["status"], INCONCLUSIVE)
        self.assertEqual(self.run_c2b(self.inbound(result="INCONCLUSIVE"))["status"], INCONCLUSIVE)

    def test_port_outside_the_range_inconclusive(self):
        r = self.run_c2b(self.inbound(port=20000))
        self.assertEqual(r["status"], INCONCLUSIVE)
        self.assertIn("outside the current JS_RTP_PORT_RANGE", r["observed"])

    def test_address_changed_since_the_receipt_inconclusive(self):
        r = self.run_c2b(self.inbound(), public=["198.51.100.99"])
        self.assertEqual(r["status"], INCONCLUSIVE)
        self.assertIn("now advertises 198.51.100.99", r["observed"])


class Listen(Base):
    TOKEN = "legion-voice-probe-test"

    def setUp(self):
        super(Listen, self).setUp()
        self.lo, self.hi = free_udp_range(11)
        self.cfg = {"rtp_range": "%d-%d" % (self.lo, self.hi), "bind_host": "127.0.0.1",
                    "state_file": self.state(["174.82.163.190", "192.168.1.225"]),
                    "inbound_file": os.path.join(self.dir, "run", "selfcheck-inbound.json"),
                    "inbound_max_age_h": 168.0}

    def send_later(self, port, payloads, delay=0.3, repeat=10):
        def run():
            time.sleep(delay)
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for _ in range(repeat):
                for payload in payloads:
                    sock.sendto(payload, ("127.0.0.1", port))
                time.sleep(0.1)
            sock.close()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def test_receipt_is_recorded_and_c2b_then_passes(self):
        out = io.StringIO()
        self.send_later(self.lo, [b"unrelated", (self.TOKEN + "\n").encode("ascii")])
        code = selfcheck.listen(self.cfg, None, 5.0, out, token=self.TOKEN)
        text = out.getvalue()
        self.assertEqual(code, 0, text)
        self.assertIn("listening on UDP %d (the lowest free port of JS_RTP_PORT_RANGE" % self.lo, text)
        self.assertIn("  bash -c 'echo %s > /dev/udp/174.82.163.190/%d'" % (self.TOKEN, self.lo), text)
        self.assertIn("  python3 -c 'import socket; socket.socket(socket.AF_INET, socket.SOCK_DGRAM)"
                      ".sendto(b\"%s\", (\"174.82.163.190\", %d))'" % (self.TOKEN, self.lo), text)
        self.assertIn("PASS: the probe arrived on UDP %d from 127.0.0.1:" % self.lo, text)
        record = read_json(self.cfg["inbound_file"])
        self.assertEqual((record["result"], record["port"], record["advertised"]),
                         (PASS, self.lo, ["174.82.163.190"]))
        self.assertTrue(record["source"].startswith("127.0.0.1:"))
        r = selfcheck.check_c2b(self.cfg["inbound_file"], self.cfg["rtp_range"], 168, ["174.82.163.190"])
        self.assertEqual(r["status"], PASS, r["observed"])

    def test_picks_a_free_port_and_says_which(self):
        self.hold_udp(self.lo)
        out = io.StringIO()
        self.send_later(self.lo + 1, [self.TOKEN.encode("ascii")])
        self.assertEqual(selfcheck.listen(self.cfg, None, 5.0, out, token=self.TOKEN), 0, out.getvalue())
        self.assertIn("listening on UDP %d (the lowest free port of JS_RTP_PORT_RANGE %d-%d; 1 lower port(s) in use"
                      % (self.lo + 1, self.lo, self.hi), out.getvalue())

    def test_requested_port_in_use_refused(self):
        self.hold_udp(self.lo)
        out = io.StringIO()
        self.assertEqual(selfcheck.listen(self.cfg, self.lo, 1.0, out), 1)
        self.assertIn("UDP %d is in use" % self.lo, out.getvalue())

    def test_port_outside_range_is_a_usage_error(self):
        self.assertEqual(selfcheck.listen(self.cfg, self.hi + 1, 1.0, io.StringIO()), 64)

    def test_nothing_arrives_inconclusive_and_no_record(self):
        out = io.StringIO()
        started = time.monotonic()
        self.assertEqual(selfcheck.listen(self.cfg, None, 0.6, out, token=self.TOKEN), 2)
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertIn("INCONCLUSIVE: nothing carrying the probe arrived", out.getvalue())
        self.assertFalse(os.path.exists(self.cfg["inbound_file"]))


class C3(Base):
    def setUp(self):
        super(C3, self).setUp()
        self.lo, self.hi = free_udp_range(21)
        self.range = "%d-%d" % (self.lo, self.hi)

    def test_bindable_and_matching_pass(self):
        self.assertEqual(selfcheck.check_c3(self.range, self.jcfg(self.range), "127.0.0.1")["status"], PASS)

    def test_port_in_use_still_pass(self):
        self.hold_udp(self.hi)
        r = selfcheck.check_c3(self.range, self.jcfg(self.range), "127.0.0.1")
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("in use (e.g. by Janus media): %d" % self.hi, r["observed"])

    def test_mismatch_fail(self):
        r = selfcheck.check_c3(self.range, self.jcfg("20000-20200"), "127.0.0.1")
        self.assertEqual(r["status"], FAIL)
        self.assertIn("but Janus is configured with rtp_port_range=20000-20200", r["observed"])

    def test_invalid_range_fail(self):
        self.assertEqual(selfcheck.check_c3("70000-70010", self.jcfg(), "127.0.0.1")["status"], FAIL)

    def test_janus_config_unreadable_inconclusive(self):
        r = selfcheck.check_c3(self.range, os.path.join(self.dir, "absent.jcfg"), "127.0.0.1")
        self.assertEqual(r["status"], INCONCLUSIVE)

    def test_every_sampled_port_in_use_inconclusive(self):
        rng = "%d-%d" % (self.lo, self.lo + 2)
        for port in range(self.lo, self.lo + 3):
            self.hold_udp(port)
        self.assertEqual(selfcheck.check_c3(rng, self.jcfg(rng), "127.0.0.1")["status"], INCONCLUSIVE)


class C4(Base):
    def test_janus_answers_pass(self):
        janus = self.track(fakes.FakeJanusHTTP())
        r = selfcheck.check_c4(janus.port, "/voice", selfcheck.Budget(5), 3)
        self.assertEqual(r["status"], PASS, r["observed"])

    def test_wrong_base_path_fail(self):
        janus = self.track(fakes.FakeJanusHTTP())
        r = selfcheck.check_c4(janus.port, "/janus", selfcheck.Budget(5), 3)
        self.assertEqual(r["status"], FAIL)
        self.assertIn("HTTP 404", r["observed"])

    def test_plugin_missing_fail(self):
        janus = self.track(fakes.FakeJanusHTTP(mode="no_slvoice"))
        self.assertEqual(selfcheck.check_c4(janus.port, "/voice", selfcheck.Budget(5), 3)["status"], FAIL)

    def test_nothing_listening_fail(self):
        r = selfcheck.check_c4(fakes.closed_tcp_port(), "/voice", selfcheck.Budget(5), 0.6)
        self.assertEqual(r["status"], FAIL)
        self.assertIn("connection refused", r["observed"])

    def test_no_reply_inconclusive(self):
        silent = self.track(fakes.SilentTCP())
        self.assertEqual(selfcheck.check_c4(silent.port, "/voice", selfcheck.Budget(5), 1.0)["status"], INCONCLUSIVE)


class C5(Base):
    ADDR = "203.0.113.7"

    def run_c5(self, port, public=(ADDR,), seconds=5):
        return selfcheck.check_c5("0.0.0.0", str(port), "/voice", list(public), selfcheck.Budget(seconds),
                                  {self.ADDR: "127.0.0.1"})

    def test_unreachable_pass(self):
        r = self.run_c5(fakes.closed_tcp_port())
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("unreachable is the desired result", r["observed"])

    def test_admin_answers_fail(self):
        janus = self.track(fakes.FakeJanusHTTP())
        r = self.run_c5(janus.port)
        self.assertEqual(r["status"], FAIL)
        self.assertIn("JS_ADMIN_BIND", r["remediation"])

    def test_something_else_warns(self):
        silent = self.track(fakes.SilentTCP())
        self.assertEqual(self.run_c5(silent.port, seconds=1.5)["status"], WARN)

    def test_no_public_address_inconclusive(self):
        self.assertEqual(self.run_c5(fakes.closed_tcp_port(), public=())["status"], INCONCLUSIVE)


class C6(Base):
    SENTENCE = ("No TURN configured: viewers whose own network gives them no direct path (symmetric NAT, CGNAT or "
                "blocked UDP) get no voice, whatever this server's own mapping.")

    def test_turn_configured_pass(self):
        path = self.jcfg(nat='\tturn_server = "turn.example.test"\n\tturn_port = 3478\n\tturn_type = "udp"')
        r = selfcheck.check_c6(path)
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("turn turn.example.test:3478 (udp)", r["observed"])

    def test_no_turn_warns_in_one_viewer_side_sentence(self):
        r = selfcheck.check_c6(self.jcfg())
        self.assertEqual(r["status"], WARN)
        self.assertTrue(r["observed"].endswith(self.SENTENCE), r["observed"])
        self.assertNotIn("C2", r["observed"])
        self.assertIn("turn_server", r["remediation"])

    def test_config_unreadable_inconclusive(self):
        self.assertEqual(selfcheck.check_c6(os.path.join(self.dir, "absent.jcfg"))["status"], INCONCLUSIVE)


def fake_result(cid, status):
    return selfcheck.result(cid, status, "observed", None if status == PASS else "remedy")


class Report(Base):
    def run_main(self, statuses, argv=("--json",)):
        out = io.StringIO()
        report_file = os.path.join(self.dir, "run", "selfcheck.json")
        env = {"SLV_SELFCHECK_FILE": report_file, "SLV_EFFECTIVE_CONFIG": os.path.join(self.dir, "none.json")}
        ids = ["C1", "C2a", "C2b", "C3", "C4", "C5", "C6"]
        runner = lambda cfg, budget, startup: [fake_result(ids[i], s) for i, s in enumerate(statuses)]
        code = selfcheck.main(list(argv), env, runner, out)
        return code, out.getvalue(), report_file

    def test_exit_codes(self):
        self.assertEqual(selfcheck.exit_code_for([fake_result("C1", PASS), fake_result("C6", WARN)]), 0)
        self.assertEqual(selfcheck.exit_code_for([fake_result("C1", PASS), fake_result("C2a", FAIL),
                                                  fake_result("C2b", INCONCLUSIVE)]), 1)
        self.assertEqual(selfcheck.exit_code_for([fake_result("C1", PASS), fake_result("C2b", INCONCLUSIVE)]), 2)

    def test_warn_only_exits_0_with_verdict_warn(self):
        code, text, path = self.run_main([PASS, WARN, PASS, PASS, PASS, PASS, WARN])
        self.assertEqual(code, 0)
        printed = json.loads(text)
        self.assertEqual(printed, read_json(path))
        self.assertEqual(printed["verdict"], WARN)
        self.assertEqual(printed["summary"], {"pass": 5, "warn": 2, "fail": 0, "inconclusive": 0})
        self.assertNotIn("_facts", printed["checks"][0])

    def test_inconclusive_exits_2_and_fail_exits_1(self):
        self.assertEqual(self.run_main([PASS, WARN, INCONCLUSIVE, WARN])[0], 2)
        self.assertEqual(self.run_main([PASS, FAIL, INCONCLUSIVE])[0], 1)

    def test_block_is_bracketed_with_a_worst_status_verdict(self):
        code, text, _ = self.run_main([PASS, WARN, INCONCLUSIVE], argv=())
        lines = text.rstrip("\n").split("\n")
        self.assertTrue(all(line.startswith("[selfcheck] ") for line in lines), lines)
        self.assertIn("===== BEGIN legion-voice self-check", lines[0])
        self.assertIn("===== END legion-voice self-check: worst INCONCLUSIVE; 1 PASS, 1 WARN, 0 FAIL, 1 INCONCLUSIVE; "
                      "exit 2", lines[-1])

    def test_usage_errors(self):
        self.assertEqual(self.run_main([PASS], argv=("--bogus",))[0], 64)
        self.assertEqual(self.run_main([PASS], argv=("--port", "10000"))[0], 64)


class WholeRun(Base):
    def cfg(self, lo, hi, stun, http_port, admin_port, public, inbound_file=None):
        return {"rtp_range": "%d-%d" % (lo, hi), "stun_server": stun.address, "http_port": http_port,
                "http_base": "/voice", "admin_port": str(admin_port), "admin_base": "/voiceAdmin",
                "admin_bind": "0.0.0.0", "state_file": self.state([public, "192.168.1.225"]),
                "jcfg_path": self.jcfg("%d-%d" % (lo, hi)), "bind_host": "127.0.0.1",
                "connect_map": {public: "127.0.0.1"}, "inbound_max_age_h": 168.0,
                "inbound_file": inbound_file or os.path.join(self.dir, "no-receipt.json")}

    def test_time_bound_is_honoured(self):
        lo, hi = free_udp_range(21)
        silent_http = self.track(fakes.SilentTCP())
        silent_stun = self.track(fakes.FakeStun(lambda ip, port: None))
        cfg = self.cfg(lo, hi, silent_stun, silent_http.port, silent_http.port, "203.0.113.7")
        bound = 1.5
        started = time.monotonic()
        results = selfcheck.run_checks(cfg, selfcheck.Budget(bound), startup=True)
        self.assertLess(time.monotonic() - started, bound + 1.0)
        status = dict((r["id"], r["status"]) for r in results)
        self.assertEqual((status["C4"], status["C2a"], status["C2b"]), (INCONCLUSIVE, INCONCLUSIVE, INCONCLUSIVE))
        self.assertEqual(status["C1"], PASS)

    def test_correct_forwarded_deployment_without_turn_worst_is_warn(self):
        """A published-port container with the router forward in place and no TURN: C2a sees container-initiated
        UDP remapped (normal), C6 warns. Before --listen, C2b is INCONCLUSIVE (exit 2). Once an outside receipt is
        recorded, the worst status is WARN (exit 0). There is no FAIL either way."""
        lo, hi = free_udp_range(21)
        janus = self.track(fakes.FakeJanusHTTP())
        stun = self.track(fakes.FakeStun(lambda ip, port: ("174.82.163.190", 50000 + (port % 1000))))
        cfg = self.cfg(lo, hi, stun, janus.port, fakes.closed_tcp_port(), "174.82.163.190")

        before = selfcheck.run_checks(cfg, selfcheck.Budget(10))
        status = dict((r["id"], r["status"]) for r in before)
        self.assertEqual(status, {"C1": PASS, "C2a": WARN, "C2b": INCONCLUSIVE, "C3": PASS, "C4": PASS, "C5": PASS,
                                  "C6": WARN}, before)
        self.assertNotIn(FAIL, status.values())
        self.assertEqual(selfcheck.exit_code_for(before), 2)

        cfg["inbound_file"] = self.inbound(port=lo, advertised=["174.82.163.190"])
        after = selfcheck.run_checks(cfg, selfcheck.Budget(10))
        report = selfcheck.build_report(after, "on-demand", "now", 1.0, 10)
        self.assertEqual(dict((r["id"], r["status"]) for r in after)["C2b"], PASS)
        self.assertEqual((report["verdict"], report["exit_code"], report["summary"]["fail"]), (WARN, 0, 0))
        self.assertIn("worst WARN; 5 PASS, 2 WARN, 0 FAIL, 0 INCONCLUSIVE; exit 0", selfcheck.render_block(report, "x"))

    def test_vps_shape_has_no_nat_wording_outside_the_viewer_side_turn_warning(self):
        lo, hi = free_udp_range(21)
        janus = self.track(fakes.FakeJanusHTTP())
        stun = self.track(fakes.FakeStun(lambda ip, port: (ip, port)))
        cfg = self.cfg(lo, hi, stun, janus.port, fakes.closed_tcp_port(), "198.51.100.10")
        cfg["state_file"] = self.state(["198.51.100.10"])
        results = selfcheck.run_checks(cfg, selfcheck.Budget(10))
        status = dict((r["id"], r["status"]) for r in results)
        self.assertEqual([status[c] for c in ("C1", "C2a", "C3", "C4", "C5")], [PASS] * 5, results)
        self.assertNotIn(FAIL, status.values())
        for r in results:
            if r["id"] == "C6":
                continue   # names the viewer-side network types by design
            text = " ".join([r["observed"], r["remediation"] or ""] + r["notes"])
            for word in NAT_WORDS:
                self.assertNotIn(word, text, "%s mentions %r: %s" % (r["id"], word, text))


if __name__ == "__main__":
    unittest.main(verbosity=2)
