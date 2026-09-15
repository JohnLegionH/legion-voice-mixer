#!/usr/bin/env python3
"""Unit tests for entrypoint/addr_probe.py (slice A.1): STUN and DNS codecs, the participant sum, and
both probes end to end against throwaway loopback UDP servers. There are no external network calls,
so the image build can run this:

    python3 tests/test_addr_probe.py

ADDR_PROBE_DIR points at the directory holding addr_probe.py (default: ../entrypoint).
"""

import contextlib
import io
import os
import socket
import struct
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ.get("ADDR_PROBE_DIR", os.path.join(HERE, "..", "entrypoint")))

import addr_probe  # noqa: E402
import fakes  # noqa: E402

TXID = bytes(range(12))


def stun_success(txid, attrs):
    body = b"".join(attrs)
    return struct.pack(">HHI", 0x0101, len(body), addr_probe.STUN_MAGIC) + txid + body


def xor_mapped(ip, port):
    raw = struct.unpack(">I", socket.inet_aton(ip))[0] ^ addr_probe.STUN_MAGIC
    value = struct.pack(">BBHI", 0, 1, port ^ (addr_probe.STUN_MAGIC >> 16), raw)
    return struct.pack(">HH", 0x0020, len(value)) + value


def mapped(ip, port):
    value = struct.pack(">BBH", 0, 1, port) + socket.inet_aton(ip)
    return struct.pack(">HH", 0x0001, len(value)) + value


def dns_name(name):
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"


def dns_reply(tid, name, answers, rcode=0):
    header = struct.pack(">HHHHHH", tid, 0x8180 | rcode, 1, len(answers), 0, 0)
    question = dns_name(name) + struct.pack(">HH", 1, 1)
    return header + question + b"".join(answers)


def rr_cname(target):
    rdata = dns_name(target)
    return b"\xc0\x0c" + struct.pack(">HHIH", 5, 1, 60, len(rdata)) + rdata


def rr_a(ip):
    return b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + socket.inet_aton(ip)


class HostPort(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(addr_probe.parse_hostport("stun.l.google.com:19302", 3478), ("stun.l.google.com", 19302))
        self.assertEqual(addr_probe.parse_hostport("stun:stun.l.google.com:19302", 3478), ("stun.l.google.com", 19302))
        self.assertEqual(addr_probe.parse_hostport("1.1.1.1", 53), ("1.1.1.1", 53))

    def test_bad(self):
        for bad in ("", "stun:", "host:", "host:0", "host:70000", ":53"):
            with self.assertRaises(ValueError):
                addr_probe.parse_hostport(bad, 53)


class Stun(unittest.TestCase):
    def test_request_shape(self):
        req = addr_probe.build_stun_request(TXID)
        self.assertEqual(len(req), 20)
        self.assertEqual(req[:8], bytes.fromhex("000100002112a442"))
        self.assertEqual(req[8:], TXID)

    def test_xor_mapped_address(self):
        reply = stun_success(TXID, [xor_mapped("174.82.163.190", 65234)])
        self.assertEqual(addr_probe.parse_stun_response(reply, TXID), "174.82.163.190")

    def test_mapped_port(self):
        reply = stun_success(TXID, [xor_mapped("174.82.163.190", 10050)])
        self.assertEqual(addr_probe.parse_stun_mapped(reply, TXID), ("174.82.163.190", 10050))
        reply = stun_success(TXID, [mapped("198.51.100.4", 20000)])
        self.assertEqual(addr_probe.parse_stun_mapped(reply, TXID), ("198.51.100.4", 20000))

    def test_xor_mapped_wins_over_mapped(self):
        reply = stun_success(TXID, [mapped("192.0.2.1", 1), xor_mapped("203.0.113.7", 2)])
        self.assertEqual(addr_probe.parse_stun_response(reply, TXID), "203.0.113.7")

    def test_mapped_address_fallback(self):
        reply = stun_success(TXID, [mapped("198.51.100.4", 3478)])
        self.assertEqual(addr_probe.parse_stun_response(reply, TXID), "198.51.100.4")

    def test_padded_unknown_attribute_is_skipped(self):
        software = struct.pack(">HH", 0x8022, 5) + b"abcde" + b"\x00\x00\x00"
        reply = stun_success(TXID, [software, xor_mapped("203.0.113.9", 9)])
        self.assertEqual(addr_probe.parse_stun_response(reply, TXID), "203.0.113.9")

    def test_rejects(self):
        good = stun_success(TXID, [xor_mapped("203.0.113.7", 2)])
        self.assertIsNone(addr_probe.parse_stun_response(good, bytes(12)))          # other transaction
        self.assertIsNone(addr_probe.parse_stun_response(good[:10], TXID))          # truncated header
        self.assertIsNone(addr_probe.parse_stun_response(good[:-2], TXID))          # truncated body
        error = struct.pack(">HHI", 0x0111, 0, addr_probe.STUN_MAGIC) + TXID
        self.assertIsNone(addr_probe.parse_stun_response(error, TXID))              # error response
        ipv6 = struct.pack(">HH", 0x0020, 20) + struct.pack(">BBH", 0, 2, 1) + bytes(16)
        self.assertIsNone(addr_probe.parse_stun_response(stun_success(TXID, [ipv6]), TXID))


class Dns(unittest.TestCase):
    def test_query_shape(self):
        q = addr_probe.build_dns_query("legiongrid.ddns.net", 0x1234)
        self.assertEqual(q[:12], bytes.fromhex("123401000001000000000000"))
        self.assertEqual(q[12:], dns_name("legiongrid.ddns.net") + b"\x00\x01\x00\x01")

    def test_bad_names(self):
        for bad in ("", "a..b", "x" * 64 + ".net"):
            with self.assertRaises(ValueError):
                addr_probe.build_dns_query(bad, 1)

    def test_a_record(self):
        reply = dns_reply(7, "legiongrid.ddns.net", [rr_a("174.82.163.190")])
        self.assertEqual(addr_probe.parse_dns_response(reply, 7), "174.82.163.190")

    def test_cname_then_a(self):
        reply = dns_reply(7, "voice.example.test", [rr_cname("edge.example.test"), rr_a("203.0.113.7")])
        self.assertEqual(addr_probe.parse_dns_response(reply, 7), "203.0.113.7")

    def test_rejects(self):
        good = dns_reply(7, "a.example", [rr_a("203.0.113.7")])
        self.assertIsNone(addr_probe.parse_dns_response(good, 8))                               # other id
        self.assertIsNone(addr_probe.parse_dns_response(dns_reply(7, "a.example", [], rcode=3), 7))  # NXDOMAIN
        self.assertIsNone(addr_probe.parse_dns_response(dns_reply(7, "a.example", [rr_cname("b.example")]), 7))
        self.assertIsNone(addr_probe.parse_dns_response(good[:-3], 7))                          # truncated
        self.assertIsNone(addr_probe.parse_dns_response(b"\x00" * 5, 7))


class Participants(unittest.TestCase):
    def test_sum(self):
        data = {"audiobridge": "success", "list": [{"room": 1, "num_participants": 2}, {"room": 2, "num_participants": 1}]}
        self.assertEqual(addr_probe.sum_participants(data), 3)
        self.assertEqual(addr_probe.sum_participants({"list": []}), 0)

    def test_malformed(self):
        self.assertIsNone(addr_probe.sum_participants(None))
        self.assertIsNone(addr_probe.sum_participants({"error": "x"}))
        self.assertIsNone(addr_probe.sum_participants({"list": [{"room": 1}]}))
        self.assertIsNone(addr_probe.sum_participants({"list": [{"num_participants": True}]}))


class ParticipantsPoll(unittest.TestCase):
    """The poll the A.1 restart action waits on (A.2 carry-over): a failed or unauthorised poll is never a count."""

    def setUp(self):
        self.janus = fakes.FakeJanusHTTP(secret="s3cret", rooms=(2, 1))

    def tearDown(self):
        self.janus.close()

    def test_counts_participants_with_the_secret(self):
        self.assertEqual(addr_probe.participants_probe(self.janus.url, "s3cret", 5), (3, None))

    def test_unauthorised_poll_is_not_zero(self):
        total, reason = addr_probe.participants_probe(self.janus.url, "wrong", 5)
        self.assertIsNone(total)
        self.assertTrue(reason.startswith("unauthorized: Unauthorized request"), reason)
        self.assertIn("JS_API_SECRET", reason)

    def test_unreachable_poll_is_not_zero(self):
        total, reason = addr_probe.participants_probe("http://127.0.0.1:%d/voice" % fakes.closed_tcp_port(), "s3cret", 2)
        self.assertIsNone(total)
        self.assertTrue(reason.startswith("no reply from"), reason)

    def test_main_prints_no_count_for_an_unauthorised_poll(self):
        out, err = io.StringIO(), io.StringIO()
        old = os.environ.get("JS_API_SECRET")
        os.environ["JS_API_SECRET"] = "wrong"
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = addr_probe.main(["addr_probe.py", "participants", self.janus.url])
        finally:
            if old is None:
                del os.environ["JS_API_SECRET"]
            else:
                os.environ["JS_API_SECRET"] = old
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("addr_probe: participants: unauthorized", err.getvalue())


class TurnRestVector(unittest.TestCase):
    """coturn shared-secret (TURN REST) credentials against vectors computed independently with openssl:
        printf '%s' '1700000000:legion-voice' | openssl dgst -sha1 -hmac 'turn-test-secret' -binary | base64
        printf '%s' '2145916800:legion-voice-static' | openssl dgst -sha1 -hmac 'turn-test-secret' -binary | base64
    The second is the turn-test fixture's documented static pair. Its expiry stays below 2^31, because coturn rejected a
    pair expiring in 2100."""

    def test_known_good_vector(self):
        self.assertEqual(addr_probe.rest_credentials("turn-test-secret", "legion-voice", 1700000000),
                         ("1700000000:legion-voice", "ScDtB8SL4GiYLaXf7MwGKlzp4ys="))

    def test_turn_test_fixture_static_pair(self):
        self.assertEqual(addr_probe.rest_credentials("turn-test-secret", "legion-voice-static", 2145916800),
                         ("2145916800:legion-voice-static", "NGwezoW6rFCQ0gcjQKg34HJa1W8="))
        self.assertLess(2145916800, 2 ** 31)


class TurnUris(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(addr_probe.parse_turn_uri("turn:turn.example.test"), ("turn.example.test", 3478, "udp"))
        self.assertEqual(addr_probe.parse_turn_uri("turn:t:3480?transport=tcp"), ("t", 3480, "tcp"))
        self.assertEqual(addr_probe.parse_turn_uri("turns:t"), ("t", 5349, "tls"))
        self.assertEqual(addr_probe.parse_turn_uri("turns:t:443?transport=tcp"), ("t", 443, "tls"))
        for bad in ("turns:t?transport=udp", "stun:t:3478", "turn:", "http://t/"):
            self.assertIsNone(addr_probe.parse_turn_uri(bad), bad)

    def test_redact_url(self):
        self.assertEqual(addr_probe.redact_url("https://user:pw@rest.example.test:8443/turn?key=abc#frag"),
                         "https://rest.example.test:8443/turn")
        self.assertEqual(addr_probe.redact_url("not a url"), "(invalid URL)")


class TurnAllocate(unittest.TestCase):
    USER, PWD = "turn-user", "turn-pwd"

    def serve(self, **kwargs):
        server = fakes.FakeTurn(users={self.USER: self.PWD}, **kwargs)
        self.addCleanup(server.close)
        return server

    def test_udp_allocates_and_releases(self):
        server = self.serve()
        relay = addr_probe.turn_allocate("127.0.0.1", server.port, "udp", self.USER, self.PWD, 3)
        self.assertEqual(relay, ("203.0.113.50", 49152))
        self.assertEqual([t for t, _ in server.requests], [0x0003, 0x0004])

    def test_tcp_allocates(self):
        server = self.serve(transport="tcp")
        self.assertEqual(addr_probe.turn_allocate("127.0.0.1", server.port, "tcp", self.USER, self.PWD, 3),
                         ("203.0.113.50", 49152))

    def test_wrong_password_is_the_reason(self):
        server = self.serve()
        with self.assertRaises(addr_probe.TurnError) as caught:
            addr_probe.turn_allocate("127.0.0.1", server.port, "udp", self.USER, "wrong", 3)
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("wrong", str(caught.exception).replace("wrong credentials", ""))

    def test_silent_server(self):
        server = self.serve(mode="silent")
        with self.assertRaises(addr_probe.TurnError) as caught:
            addr_probe.turn_allocate("127.0.0.1", server.port, "udp", self.USER, self.PWD, 1)
        self.assertIn("no answer", str(caught.exception))

    def test_closed_tcp_port(self):
        with self.assertRaises(addr_probe.TurnError) as caught:
            addr_probe.turn_allocate("127.0.0.1", fakes.closed_tcp_port(), "tcp", self.USER, self.PWD, 2)
        self.assertIn("cannot connect", str(caught.exception))

    def test_unsupported_transport(self):
        with self.assertRaises(addr_probe.TurnError):
            addr_probe.turn_allocate("127.0.0.1", 3478, "sctp", self.USER, self.PWD, 1)


class TurnRestRequest(unittest.TestCase):
    def serve(self, **kwargs):
        server = fakes.FakeTurnRest("s3cret", ["turn:127.0.0.1:3478?transport=udp"], key="rest-key", **kwargs)
        self.addCleanup(server.close)
        return server

    def test_post_like_janus(self):
        server = self.serve()
        username, password, ttl, uris = addr_probe.turn_rest_request(server.url, "rest-key", "POST", "probe", 3)
        self.assertTrue(username.endswith(":probe"))
        self.assertEqual(password, fakes.rest_password("s3cret", username))
        self.assertEqual((ttl, uris), (3600, ["turn:127.0.0.1:3478?transport=udp"]))
        method, params = server.requests[-1]
        self.assertEqual(method, "POST")
        self.assertEqual({k: params[k] for k in ("service", "api", "key", "username")},
                         {"service": "turn", "api": "rest-key", "key": "rest-key", "username": "probe"})

    def test_get(self):
        server = self.serve()
        addr_probe.turn_rest_request(server.url, "rest-key", "get", "probe", 3)
        self.assertEqual(server.requests[-1][0], "GET")

    def test_wrong_key_reason_has_no_key(self):
        server = self.serve()
        with self.assertRaises(addr_probe.TurnError) as caught:
            addr_probe.turn_rest_request(server.url + "?tenant=secret-tenant", "wrong-key-value", "POST", "probe", 3)
        message = str(caught.exception)
        self.assertIn("HTTP", message)
        self.assertNotIn("wrong-key-value", message)
        self.assertNotIn("secret-tenant", message)

    def test_bad_json(self):
        server = self.serve(mode="bad_json")
        with self.assertRaises(addr_probe.TurnError) as caught:
            addr_probe.turn_rest_request(server.url, "rest-key", "POST", "probe", 3)
        self.assertIn("JSON", str(caught.exception))

    def test_unreachable(self):
        with self.assertRaises(addr_probe.TurnError) as caught:
            addr_probe.turn_rest_request("http://127.0.0.1:%d/turn" % fakes.closed_tcp_port(), "k", "POST", "p", 2)
        self.assertIn("cannot reach", str(caught.exception))


class LoopbackServer(object):
    """A one-thread UDP server on 127.0.0.1 that answers each datagram with responder(datagram)."""

    def __init__(self, responder):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(5)
        self.port = self.sock.getsockname()[1]
        self.responder = responder
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        try:
            data, peer = self.sock.recvfrom(2048)
            reply = self.responder(data)
            if reply is not None:
                self.sock.sendto(reply, peer)
        except OSError:
            pass

    def close(self):
        self.sock.close()


class EndToEnd(unittest.TestCase):
    def test_stun_probe(self):
        server = LoopbackServer(lambda req: stun_success(req[8:20], [xor_mapped("203.0.113.44", 5555)]))
        try:
            self.assertEqual(addr_probe.stun_probe("127.0.0.1:%d" % server.port, 2.0), "203.0.113.44")
        finally:
            server.close()

    def test_dns_probe(self):
        def answer(query):
            tid = struct.unpack(">H", query[:2])[0]
            return dns_reply(tid, "legiongrid.ddns.net", [rr_a("174.82.163.190")])
        server = LoopbackServer(answer)
        try:
            self.assertEqual(addr_probe.dns_probe("legiongrid.ddns.net", "127.0.0.1:%d" % server.port, 2.0),
                             "174.82.163.190")
        finally:
            server.close()

    def test_silent_server_times_out(self):
        server = LoopbackServer(lambda req: None)
        try:
            self.assertIsNone(addr_probe.stun_probe("127.0.0.1:%d" % server.port, 0.6))
        finally:
            server.close()

    def test_main_exit_codes(self):
        self.assertEqual(addr_probe.main(["addr_probe.py"]), 2)
        self.assertEqual(addr_probe.main(["addr_probe.py", "stun", "host:0"]), 2)
        self.assertEqual(addr_probe.main(["addr_probe.py", "system", "no-such-host.invalid"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
