"""The source-address oracle (slice A.6, S14 and source_probe.py): did the peer's own socket address reach Janus?

Pure functions, no aiortc or aiohttp, so tests/integration/test_source_oracle.py runs them on synthetic input.

"Preserved" means the address:port Janus saw as the remote end of its selected pair is one of the endpoints the peer
signalled: a host candidate (a local socket) or a srflx candidate (that socket's NAT mapping). An IP the peer owns
is not enough. On a single host a published-port proxy re-sends from the Docker network gateway, which is also one
of the peer's own interface addresses, but from an ephemeral port no candidate carries (CI run 36252361452:
172.18.0.1:33673 against A's 172.18.0.1 host candidate). That source was rewritten.
"""

from __future__ import annotations

import re

_CANDIDATE = re.compile(r"^a=candidate:\S+ \d+ (udp|tcp) \d+ (\S+) (\d+) typ (\w+)", re.M)
_PAIR = re.compile(r"^(.+):(\d+) \[(\w+),(\w+)\] <-> (.+):(\d+) \[(\w+),(\w+)\]$")
#: the connection-address field of one candidate attribute (with or without the "a=" of an SDP line)
_CANDIDATE_ADDRESS = re.compile(r"^((?:a=)?candidate:\S+ \d+ \S+ \d+ )(\S+)( \d+ typ )")

#: the candidate types whose address:port is the peer's own endpoint: a local socket, or its NAT mapping
OWN_ENDPOINT_TYPES = ("host", "srflx")

#: the diagnostics path verdict the record must carry for each ground-truth verdict
EXPECTED_PATH = {"preserved": "direct", "rewritten": "undetermined"}


def own_candidates(sdp: str) -> list:
    return [{"address": m.group(2), "port": int(m.group(3)), "type": m.group(4), "transport": m.group(1)}
            for m in _CANDIDATE.finditer(sdp or "")]


def parse_pair(text: str | None):
    m = _PAIR.match((text or "").strip())
    if not m:
        return None
    g = m.groups()
    return {"local": {"address": g[0], "port": int(g[1]), "type": g[2], "transport": g[3]},
            "remote": {"address": g[4], "port": int(g[5]), "type": g[6], "transport": g[7]}}


def verdict(own: list, pair) -> dict:
    """Ground truth for one selected pair against the peer's own candidates.

    verdict: "preserved" (the remote address:port is a signalled host/srflx endpoint), "rewritten" (it is not), or
    "no-pair". branch says why: "own-endpoint", "own-address-foreign-port" (a proxy on the peer's own host, the CI
    case) or "foreign-address"."""
    if pair is None:
        return {"verdict": "no-pair", "branch": None, "remote_address_is_peers_own": None,
                "remote_endpoint_is_peers_own": None}
    addresses = sorted({c["address"] for c in own})
    endpoints = sorted({(c["address"], c["port"]) for c in own if c["type"] in OWN_ENDPOINT_TYPES})
    remote = (pair["remote"]["address"], pair["remote"]["port"])
    endpoint_own = remote in endpoints
    address_own = remote[0] in addresses
    branch = ("own-endpoint" if endpoint_own
              else "own-address-foreign-port" if address_own else "foreign-address")
    return {"verdict": "preserved" if endpoint_own else "rewritten", "branch": branch,
            "remote_address_is_peers_own": address_own, "remote_endpoint_is_peers_own": endpoint_own,
            "peer_addresses": addresses, "peer_endpoints": [f"{a}:{p}" for a, p in endpoints]}


def expected_path(truth: dict) -> str | None:
    """The diagnostics path verdict S14 requires for this ground truth; None when there is no pair to judge."""
    return EXPECTED_PATH.get(truth.get("verdict"))


def retarget_candidate(line: str, address: str) -> str:
    """One candidate attribute with its connection address replaced by `address`; anything else unchanged."""
    return _CANDIDATE_ADDRESS.sub(lambda m: m.group(1) + address + m.group(3), line, count=1)


def retarget_sdp(sdp: str, address: str) -> str:
    """Janus's answer with every candidate pointed at `address` (--media-address). Candidates that collapse onto the
    same address, port and transport (the nat_1_1 host and the kept private host share the port) keep only the
    first, so the peer checks each endpoint once."""
    newline = "\r\n" if "\r\n" in sdp else "\n"
    seen = set()
    out = []
    for line in sdp.split(newline):
        if line.startswith("a=candidate:"):
            line = retarget_candidate(line, address)
            m = _CANDIDATE.match(line)
            if m:
                key = (m.group(1).lower(), m.group(2), m.group(3))
                if key in seen:
                    continue
                seen.add(key)
        out.append(line)
    return newline.join(out)
