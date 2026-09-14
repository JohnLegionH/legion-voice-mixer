#!/usr/bin/env python3
"""Unit tests for entrypoint/selfcheck.py (slice A.2). Every check's PASS, FAIL (or WARN) and INCONCLUSIVE
branch, C2's port-preserving, remapped and no-answer cases, the VPS shape, exit codes and the time bound.
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
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ.get("ADDR_PROBE_DIR", os.path.join(HERE, "..", "entrypoint")))

import fakes  # noqa: E402
import selfcheck  # noqa: E402
from selfcheck import FAIL, INCONCLUSIVE, PASS, WARN  # noqa: E402

NAT_WORDS = ("NAT", "symmetric", "remapp", "carrier")


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
        self.assertTrue(r["_facts"]["cgnat"])

    def test_private_fail(self):
        r = selfcheck.check_c1(self.state(["192.168.1.225"]))
        self.assertEqual(r["status"], FAIL)
        self.assertIn("has no public address (192.168.1.225 private)", r["observed"])
        self.assertIn("JS_PUBLIC_HOST", r["remediation"])

    def test_empty_mapping_fail(self):
        self.assertEqual(selfcheck.check_c1(self.state([]))["status"], FAIL)

    def test_missing_state_inconclusive(self):
        r = selfcheck.check_c1(os.path.join(self.dir, "absent.json"))
        self.assertEqual(r["status"], INCONCLUSIVE)
        self.assertIn("ADDRESS_RESOLUTION", r["remediation"])

    def test_confirmed_change_warns(self):
        path = self.state(["174.82.163.190"], running_address="174.82.163.190", observed_address="198.51.100.4",
                          agreeing_checks=2)
        r = selfcheck.check_c1(path)
        self.assertEqual(r["status"], WARN)
        self.assertIn("docker compose restart janus", r["remediation"])


class C2(Base):
    def setUp(self):
        super(C2, self).setUp()
        self.lo, self.hi = free_udp_range(21)
        self.range = "%d-%d" % (self.lo, self.hi)

    def run_c2(self, mapping, seconds=5.0, advertised=()):
        stun = self.track(fakes.FakeStun(mapping))
        return selfcheck.check_c2(stun.address, self.range, selfcheck.Budget(seconds), "127.0.0.1", list(advertised))

    def assert_outbound_caveat(self, r):
        self.assertTrue(any("OUTBOUND mapping only" in n for n in r["notes"]), r["notes"])

    def test_port_preserving_pass(self):
        r = self.run_c2(lambda ip, port: ("203.0.113.7", port), advertised=["203.0.113.7"])
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("ports preserved", r["observed"])
        for port in (self.lo, (self.lo + self.hi) // 2, self.hi):
            self.assertIn("local %d -> 203.0.113.7:%d" % (port, port), r["observed"])
        self.assert_outbound_caveat(r)
        self.assertFalse(r["_facts"]["direct"])

    def test_vps_shape_pass_without_nat_words(self):
        r = self.run_c2(lambda ip, port: (ip, port))
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("no address translation", r["observed"])
        self.assertTrue(r["_facts"]["direct"])
        for word in NAT_WORDS:
            self.assertNotIn(word, r["observed"])
            self.assertFalse(any(word in n for n in r["notes"]), r["notes"])

    def test_remapped_fail(self):
        r = self.run_c2(lambda ip, port: ("203.0.113.7", 40000 + (port % 1000)))
        self.assertEqual(r["status"], FAIL)
        self.assertTrue(r["observed"].startswith(
            "port-remapping or symmetric NAT: some users will have no direct path, TURN required"), r["observed"])
        self.assertIn("TURN", r["remediation"])
        self.assert_outbound_caveat(r)

    def test_media_range_blocked_fail(self):
        lo, hi = self.lo, self.hi
        r = self.run_c2(lambda ip, port: None if lo <= port <= hi else ("203.0.113.7", port))
        self.assertEqual(r["status"], FAIL)
        self.assertTrue(r["observed"].startswith("outbound UDP from the media range is blocked"), r["observed"])
        self.assertIn("answered an ephemeral port", r["observed"])
        self.assertIn("egress", r["remediation"])
        self.assert_outbound_caveat(r)

    def test_stun_unreachable_inconclusive(self):
        r = self.run_c2(lambda ip, port: None, seconds=1.0)
        self.assertEqual(r["status"], INCONCLUSIVE)
        self.assertIn("cannot be judged", r["observed"])
        self.assert_outbound_caveat(r)

    def test_expired_budget_not_run(self):
        budget = selfcheck.Budget(0.0)
        r = selfcheck.check_c2("127.0.0.1:3478", self.range, budget, "127.0.0.1")
        self.assertEqual(r["status"], INCONCLUSIVE)
        self.assertIn("time bound", r["observed"])

    def test_port_in_use_moves_inward(self):
        held = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        held.bind(("127.0.0.1", self.lo))
        self.closers.append(held.close)
        r = self.run_c2(lambda ip, port: ("203.0.113.7", port))
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("local %d (for %d, in use)" % (self.lo + 1, self.lo), r["observed"])


class C3(Base):
    def setUp(self):
        super(C3, self).setUp()
        self.lo, self.hi = free_udp_range(21)
        self.range = "%d-%d" % (self.lo, self.hi)

    def test_bindable_and_matching_pass(self):
        r = selfcheck.check_c3(self.range, self.jcfg(self.range), "127.0.0.1")
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("matches JS_RTP_PORT_RANGE", r["observed"])

    def test_port_in_use_still_pass(self):
        held = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        held.bind(("127.0.0.1", self.hi))
        self.closers.append(held.close)
        r = selfcheck.check_c3(self.range, self.jcfg(self.range), "127.0.0.1")
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("in use (e.g. by Janus media): %d" % self.hi, r["observed"])

    def test_mismatch_fail(self):
        r = selfcheck.check_c3(self.range, self.jcfg("20000-20200"), "127.0.0.1")
        self.assertEqual(r["status"], FAIL)
        self.assertIn("but Janus is configured with rtp_port_range=20000-20200", r["observed"])
        self.assertIn("/opt/janus/etc/janus.d", r["remediation"])

    def test_invalid_range_fail(self):
        self.assertEqual(selfcheck.check_c3("70000-70010", self.jcfg(), "127.0.0.1")["status"], FAIL)

    def test_janus_config_unreadable_inconclusive(self):
        r = selfcheck.check_c3(self.range, os.path.join(self.dir, "absent.jcfg"), "127.0.0.1")
        self.assertEqual(r["status"], INCONCLUSIVE)

    def test_every_sampled_port_in_use_inconclusive(self):
        rng = "%d-%d" % (self.lo, self.lo + 2)
        for port in range(self.lo, self.lo + 3):
            held = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            held.bind(("127.0.0.1", port))
            self.closers.append(held.close)
        r = selfcheck.check_c3(rng, self.jcfg(rng), "127.0.0.1")
        self.assertEqual(r["status"], INCONCLUSIVE)
        self.assertIn("unproven", r["observed"])


class C4(Base):
    def test_janus_answers_pass(self):
        janus = self.track(fakes.FakeJanusHTTP())
        r = selfcheck.check_c4(janus.port, "/voice", selfcheck.Budget(5), 3)
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("janus.plugin.slvoice loaded", r["observed"])

    def test_wrong_base_path_fail(self):
        janus = self.track(fakes.FakeJanusHTTP())
        r = selfcheck.check_c4(janus.port, "/janus", selfcheck.Budget(5), 3)
        self.assertEqual(r["status"], FAIL)
        self.assertIn("HTTP 404", r["observed"])
        self.assertIn("JS_HTTP_BASEPATH", r["remediation"])

    def test_plugin_missing_fail(self):
        janus = self.track(fakes.FakeJanusHTTP(mode="no_slvoice"))
        r = selfcheck.check_c4(janus.port, "/voice", selfcheck.Budget(5), 3)
        self.assertEqual(r["status"], FAIL)
        self.assertIn("not loaded", r["observed"])

    def test_nothing_listening_fail(self):
        r = selfcheck.check_c4(fakes.closed_tcp_port(), "/voice", selfcheck.Budget(5), 0.6)
        self.assertEqual(r["status"], FAIL)
        self.assertIn("connection refused", r["observed"])

    def test_no_reply_inconclusive(self):
        silent = self.track(fakes.SilentTCP())
        r = selfcheck.check_c4(silent.port, "/voice", selfcheck.Budget(5), 1.0)
        self.assertEqual(r["status"], INCONCLUSIVE, r["observed"])


class C5(Base):
    ADDR = "203.0.113.7"

    def run_c5(self, port, public=(ADDR,)):
        return selfcheck.check_c5("0.0.0.0", str(port), "/voice", list(public), selfcheck.Budget(5),
                                  {self.ADDR: "127.0.0.1"})

    def test_unreachable_pass(self):
        r = self.run_c5(fakes.closed_tcp_port())
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("unreachable is the desired result", r["observed"])
        self.assertIn("admin API published on 0.0.0.0", r["observed"])

    def test_admin_answers_fail(self):
        janus = self.track(fakes.FakeJanusHTTP())
        r = self.run_c5(janus.port)
        self.assertEqual(r["status"], FAIL)
        self.assertIn("the admin API answers at 203.0.113.7:%d" % janus.port, r["observed"])
        self.assertIn("JS_ADMIN_BIND", r["remediation"])

    def test_something_else_warns(self):
        silent = self.track(fakes.SilentTCP())
        r = selfcheck.check_c5("0.0.0.0", str(silent.port), "/voice", [self.ADDR], selfcheck.Budget(1.5),
                               {self.ADDR: "127.0.0.1"})
        self.assertEqual(r["status"], WARN, r["observed"])

    def test_no_public_address_inconclusive(self):
        r = self.run_c5(fakes.closed_tcp_port(), public=())
        self.assertEqual(r["status"], INCONCLUSIVE)


class C6(Base):
    HOME_C1 = {"_facts": {"cgnat": False}}
    HOME_C2 = {"_facts": {"direct": False, "remapped": False}}
    VPS_C2 = {"_facts": {"direct": True, "remapped": False}}

    def test_turn_configured_pass(self):
        path = self.jcfg(nat='\tturn_server = "turn.example.test"\n\tturn_port = 3478\n\tturn_type = "udp"')
        r = selfcheck.check_c6(path, "10000-10200", self.HOME_C1, self.HOME_C2)
        self.assertEqual(r["status"], PASS, r["observed"])
        self.assertIn("turn turn.example.test:3478 (udp)", r["observed"])

    def test_no_turn_warns_and_names_who_has_no_path(self):
        r = selfcheck.check_c6(self.jcfg(), "10000-10200", self.HOME_C1, self.HOME_C2)
        self.assertEqual(r["status"], WARN)
        self.assertIn("stun_server none; turn none", r["observed"])
        self.assertIn("symmetric NAT or CGNAT", r["observed"])
        self.assertIn("turn_server", r["remediation"])

    def test_no_turn_after_remapping_names_them_without_overclaiming(self):
        r = selfcheck.check_c6(self.jcfg(), "10000-10200", self.HOME_C1, {"_facts": {"remapped": True}})
        self.assertEqual(r["status"], WARN)
        self.assertIn("symmetric NAT or CGNAT have no direct path unless inbound UDP 10000-10200 reaches this server "
                      "(C2 found outbound port remapping and cannot see inbound)", r["observed"])

    def test_no_turn_behind_cgnat_means_no_path_at_all(self):
        r = selfcheck.check_c6(self.jcfg(), "10000-10200", {"_facts": {"cgnat": True}}, self.HOME_C2)
        self.assertIn("have no path at all (C1 found this server behind CGNAT", r["observed"])

    def test_vps_shape_warns_without_nat_words(self):
        r = selfcheck.check_c6(self.jcfg(), "10000-10200", self.HOME_C1, self.VPS_C2)
        self.assertEqual(r["status"], WARN)
        for word in NAT_WORDS:
            self.assertNotIn(word, r["observed"])

    def test_config_unreadable_inconclusive(self):
        r = selfcheck.check_c6(os.path.join(self.dir, "absent.jcfg"), "10000-10200", self.HOME_C1, self.HOME_C2)
        self.assertEqual(r["status"], INCONCLUSIVE)


def fake_result(cid, status):
    return selfcheck.result(cid, status, "observed", None if status == PASS else "remedy")


class Report(Base):
    def run_main(self, statuses, argv=("--json",)):
        out = io.StringIO()
        report_file = os.path.join(self.dir, "run", "selfcheck.json")
        env = {"SLV_SELFCHECK_FILE": report_file, "SLV_EFFECTIVE_CONFIG": os.path.join(self.dir, "none.json")}
        runner = lambda cfg, budget, startup: [fake_result("C%d" % (i + 1), s) for i, s in enumerate(statuses)]
        code = selfcheck.main(list(argv), env, runner, out)
        return code, out.getvalue(), report_file

    def test_exit_codes(self):
        self.assertEqual(selfcheck.exit_code_for([fake_result("C1", PASS), fake_result("C6", WARN)]), 0)
        self.assertEqual(selfcheck.exit_code_for([fake_result("C1", PASS), fake_result("C2", FAIL),
                                                  fake_result("C3", INCONCLUSIVE)]), 1)
        self.assertEqual(selfcheck.exit_code_for([fake_result("C1", PASS), fake_result("C3", INCONCLUSIVE)]), 2)

    def test_main_json_exit_0_writes_the_same_json(self):
        code, text, path = self.run_main([PASS, PASS, PASS, PASS, PASS, WARN])
        self.assertEqual(code, 0)
        printed = json.loads(text)
        with open(path) as handle:
            written = json.load(handle)
        self.assertEqual(printed, written)
        self.assertEqual(printed["summary"], {"pass": 5, "warn": 1, "fail": 0, "inconclusive": 0})
        self.assertNotIn("_facts", printed["checks"][0])

    def test_main_exit_1_and_2(self):
        self.assertEqual(self.run_main([PASS, FAIL, INCONCLUSIVE])[0], 1)
        self.assertEqual(self.run_main([PASS, INCONCLUSIVE, WARN])[0], 2)

    def test_block_is_bracketed_and_greppable(self):
        code, text, _ = self.run_main([PASS, FAIL], argv=())
        lines = text.rstrip("\n").split("\n")
        self.assertTrue(all(line.startswith("[selfcheck] ") for line in lines), lines)
        self.assertIn("===== BEGIN legion-voice self-check", lines[0])
        self.assertIn("===== END legion-voice self-check: 1 PASS, 0 WARN, 1 FAIL, 0 INCONCLUSIVE; exit 1", lines[-1])
        self.assertIn("[selfcheck]    remediation: remedy", text)

    def test_usage_error(self):
        self.assertEqual(self.run_main([PASS], argv=("--bogus",))[0], 64)


class WholeRun(Base):
    def test_time_bound_is_honoured(self):
        lo, hi = free_udp_range(21)
        silent_http = self.track(fakes.SilentTCP())
        silent_stun = self.track(fakes.FakeStun(lambda ip, port: None))
        cfg = {"rtp_range": "%d-%d" % (lo, hi), "stun_server": silent_stun.address, "http_port": silent_http.port,
               "http_base": "/voice", "admin_port": str(silent_http.port), "admin_base": "/voiceAdmin",
               "admin_bind": "0.0.0.0", "state_file": self.state(["203.0.113.7"]),
               "jcfg_path": self.jcfg("%d-%d" % (lo, hi)), "bind_host": "127.0.0.1",
               "connect_map": {"203.0.113.7": "127.0.0.1"}}
        bound = 1.5
        started = time.monotonic()
        results = selfcheck.run_checks(cfg, selfcheck.Budget(bound), startup=True)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, bound + 1.0)
        status = dict((r["id"], r["status"]) for r in results)
        self.assertEqual(status["C4"], INCONCLUSIVE)
        self.assertEqual(status["C2"], INCONCLUSIVE)
        self.assertIn(status["C5"], (INCONCLUSIVE, WARN))
        self.assertEqual(status["C1"], PASS)

    def test_vps_shape_produces_no_nat_warnings(self):
        lo, hi = free_udp_range(21)
        janus = self.track(fakes.FakeJanusHTTP())
        stun = self.track(fakes.FakeStun(lambda ip, port: (ip, port)))
        cfg = {"rtp_range": "%d-%d" % (lo, hi), "stun_server": stun.address, "http_port": janus.port,
               "http_base": "/voice", "admin_port": str(fakes.closed_tcp_port()), "admin_base": "/voiceAdmin",
               "admin_bind": "10.0.0.5", "state_file": self.state(["198.51.100.10"]),
               "jcfg_path": self.jcfg("%d-%d" % (lo, hi)), "bind_host": "127.0.0.1",
               "connect_map": {"198.51.100.10": "127.0.0.1"}}
        results = selfcheck.run_checks(cfg, selfcheck.Budget(10))
        status = dict((r["id"], r["status"]) for r in results)
        self.assertEqual([status[c] for c in ("C1", "C2", "C3", "C4", "C5")], [PASS] * 5, results)
        self.assertEqual(status["C6"], WARN)
        for r in results:
            text = " ".join([r["observed"], r["remediation"] or ""] + r["notes"])
            for word in NAT_WORDS:
                self.assertNotIn(word, text, "%s mentions %r: %s" % (r["id"], word, text))


if __name__ == "__main__":
    unittest.main(verbosity=2)
