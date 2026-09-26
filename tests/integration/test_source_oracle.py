"""Unit tests for source_oracle.py, the S14 / source_probe ground truth, on synthetic SDP and pairs.

No mixer, no network, no aiortc:

    python -m unittest tests.integration.test_source_oracle -v
"""

import unittest

from tests.integration.source_oracle import (expected_path, own_candidates, parse_pair, retarget_candidate,
                                             retarget_sdp, verdict)

# A's offer as CI run 36252361452 saw it: host candidates on the runner's three IPv4 interfaces. The ports are
# synthetic; the CI log printed only the addresses.
OFFER = "\r\n".join([
    "v=0",
    "m=audio 9 UDP/TLS/RTP/SAVPF 96",
    "a=candidate:1 1 udp 2130706431 10.1.0.95 51000 typ host",
    "a=candidate:2 1 udp 2130706431 172.17.0.1 51001 typ host",
    "a=candidate:3 1 udp 2130706431 172.18.0.1 51002 typ host",
    "a=candidate:4 1 udp 1694498815 203.0.113.9 62000 typ srflx raddr 10.1.0.95 rport 51000",
    "a=end-of-candidates",
    "",
])


def pair(remote: str) -> dict:
    return parse_pair(f"127.0.0.1:10097 [prflx,udp] <-> {remote} [prflx,udp]")


class Verdict(unittest.TestCase):
    def setUp(self):
        self.own = own_candidates(OFFER)

    def test_parses_the_offer_with_ports(self):
        self.assertEqual([(c["address"], c["port"], c["type"]) for c in self.own],
                         [("10.1.0.95", 51000, "host"), ("172.17.0.1", 51001, "host"),
                          ("172.18.0.1", 51002, "host"), ("203.0.113.9", 62000, "srflx")])

    def test_preserved_ip_and_port(self):
        truth = verdict(self.own, pair("172.18.0.1:51002"))
        self.assertEqual((truth["verdict"], truth["branch"]), ("preserved", "own-endpoint"))
        self.assertTrue(truth["remote_endpoint_is_peers_own"])
        self.assertEqual(expected_path(truth), "direct")

    def test_preserved_srflx_mapping(self):
        truth = verdict(self.own, pair("203.0.113.9:62000"))
        self.assertEqual((truth["verdict"], truth["branch"]), ("preserved", "own-endpoint"))
        self.assertEqual(expected_path(truth), "direct")

    def test_gateway_ip_owned_by_the_peer_with_a_foreign_port(self):
        # The CI case: docker-proxy re-sends from the bridge gateway, one of A's own addresses, on its own port.
        truth = verdict(self.own, pair("172.18.0.1:33673"))
        self.assertEqual((truth["verdict"], truth["branch"]), ("rewritten", "own-address-foreign-port"))
        self.assertTrue(truth["remote_address_is_peers_own"])
        self.assertFalse(truth["remote_endpoint_is_peers_own"])
        self.assertEqual(expected_path(truth), "undetermined")

    def test_own_port_on_another_of_the_peers_addresses_is_foreign(self):
        # the right port on the wrong interface is still not an endpoint A signalled
        truth = verdict(self.own, pair("172.18.0.1:51000"))
        self.assertEqual(truth["verdict"], "rewritten")

    def test_foreign_ip(self):
        # Docker Desktop: the VM's gateway, an address A does not hold at all.
        truth = verdict(self.own, pair("192.168.65.1:40211"))
        self.assertEqual((truth["verdict"], truth["branch"]), ("rewritten", "foreign-address"))
        self.assertFalse(truth["remote_address_is_peers_own"])
        self.assertEqual(expected_path(truth), "undetermined")

    def test_relay_candidate_is_not_an_own_endpoint(self):
        own = own_candidates("a=candidate:5 1 udp 16777215 198.51.100.7 49152 typ relay raddr 0.0.0.0 rport 0\r\n")
        self.assertEqual(verdict(own, pair("198.51.100.7:49152"))["verdict"], "rewritten")

    def test_no_pair(self):
        truth = verdict(self.own, parse_pair("not a pair"))
        self.assertEqual(truth["verdict"], "no-pair")
        self.assertIsNone(expected_path(truth))


class Retarget(unittest.TestCase):
    ANSWER = "\r\n".join([
        "v=0",
        "c=IN IP4 127.0.0.1",
        "a=candidate:1 1 udp 2015363327 127.0.0.1 10097 typ host",
        "a=candidate:2 1 udp 2015363071 172.18.0.2 10097 typ host",
        "a=candidate:3 1 udp 2015362815 172.18.0.2 10098 typ host",
        "a=end-of-candidates",
        "",
    ])

    def test_every_candidate_points_at_the_address_and_duplicates_collapse(self):
        out = retarget_sdp(self.ANSWER, "10.1.0.95")
        cands = [line for line in out.split("\r\n") if line.startswith("a=candidate:")]
        self.assertEqual(cands, ["a=candidate:1 1 udp 2015363327 10.1.0.95 10097 typ host",
                                 "a=candidate:3 1 udp 2015362815 10.1.0.95 10098 typ host"])
        self.assertIn("c=IN IP4 127.0.0.1", out)          # only candidates move
        self.assertIn("a=end-of-candidates", out)
        self.assertTrue(out.endswith("\r\n"))

    def test_lf_only_sdp(self):
        out = retarget_sdp(self.ANSWER.replace("\r\n", "\n"), "10.1.0.95")
        self.assertNotIn("\r", out)
        self.assertEqual(out.count("a=candidate:"), 2)

    def test_trickle_candidate_without_the_a_prefix(self):
        self.assertEqual(retarget_candidate("candidate:9 1 udp 1 172.18.0.2 10099 typ host", "10.1.0.95"),
                         "candidate:9 1 udp 1 10.1.0.95 10099 typ host")


if __name__ == "__main__":
    unittest.main()
