#!/usr/bin/env python3
"""addr_probe: address-discovery probes for the mixer entrypoint (slice A.1), reused by the self-check (A.2).

Used by entrypoint/public-address.sh. One subcommand per source. Each prints ONE line and exits 0 on
success, prints nothing on stdout and exits 1 when the source gave no usable answer, and exits 2 on a
usage error:

  stun SERVER               STUN Binding request (RFC 5389) to SERVER (host, host:port or
                            stun:host:port; default port 3478). Prints the mapped (srflx) IPv4: the
                            address the internet sees this container's traffic come from.
  dns NAME RESOLVER         A query for NAME sent straight to RESOLVER (host or host:port, default
                            port 53), bypassing the container resolver. Prints the first A record.
  system NAME               The container resolver (getaddrinfo, IPv4), like `getent ahostsv4`.
  participants URL          Total participants across all slvoice rooms, via the Janus client API
                            at URL (JS_API_SECRET from the environment). Prints an integer. On a
                            failed or unauthorised poll it prints the reason on stderr and exits 1:
                            that is never a count, and never zero.

SLV_ADDR_PROBE_TIMEOUT_S (default 3) bounds each network probe. It is a test seam, not an operator
knob. The image's python3 is 3.6: no dataclasses, no assignment expressions.
"""

import json
import os
import random
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request

STUN_MAGIC = 0x2112A442
STUN_BINDING_REQUEST = 0x0001
STUN_BINDING_SUCCESS = 0x0101
ATTR_MAPPED_ADDRESS = 0x0001
ATTR_XOR_MAPPED_ADDRESS = 0x0020
FAMILY_IPV4 = 0x01


def parse_hostport(text, default_port):
    """'host', 'host:port' or 'stun:host:port' -> (host, port). Raises ValueError."""
    t = text.strip()
    if t.lower().startswith("stun:"):
        t = t[5:]
    if not t:
        raise ValueError("empty address")
    host, sep, port = t.rpartition(":")
    if not sep:
        return t, default_port
    if not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError("bad host:port %r" % text)
    return host, int(port)


def build_stun_request(txid):
    """A 20-byte Binding request with no attributes."""
    if len(txid) != 12:
        raise ValueError("STUN transaction id must be 12 bytes")
    return struct.pack(">HHI", STUN_BINDING_REQUEST, 0, STUN_MAGIC) + txid


def parse_stun_mapped(data, txid):
    """The mapped (IPv4, port) from a Binding success response to `txid`, or None.
    XOR-MAPPED-ADDRESS wins over MAPPED-ADDRESS; IPv6 answers are ignored."""
    if len(data) < 20:
        return None
    mtype, mlen, magic = struct.unpack(">HHI", data[:8])
    if mtype != STUN_BINDING_SUCCESS or magic != STUN_MAGIC or data[8:20] != txid:
        return None
    end = 20 + mlen
    if end > len(data):
        return None
    pos = 20
    mapped = None
    xor_mapped = None
    while pos + 4 <= end:
        atype, alen = struct.unpack(">HH", data[pos:pos + 4])
        val = data[pos + 4:pos + 4 + alen]
        if len(val) < alen:
            return None
        if atype in (ATTR_MAPPED_ADDRESS, ATTR_XOR_MAPPED_ADDRESS) and alen >= 8 and val[1] == FAMILY_IPV4:
            port = struct.unpack(">H", val[2:4])[0]
            if atype == ATTR_XOR_MAPPED_ADDRESS:
                raw = struct.unpack(">I", val[4:8])[0] ^ STUN_MAGIC
                xor_mapped = (socket.inet_ntoa(struct.pack(">I", raw)), port ^ (STUN_MAGIC >> 16))
            else:
                mapped = (socket.inet_ntoa(val[4:8]), port)
        pos += 4 + alen + (-alen % 4)   # attributes are padded to a 4-byte boundary
    return xor_mapped or mapped


def parse_stun_response(data, txid):
    """The mapped IPv4 from a Binding success response to `txid`, or None."""
    mapped = parse_stun_mapped(data, txid)
    return mapped[0] if mapped else None


def build_dns_query(name, tid):
    """A recursive A/IN query for `name`."""
    labels = name.rstrip(".").split(".")
    if not name or any(not label or len(label) > 63 for label in labels):
        raise ValueError("bad DNS name %r" % name)
    query = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    for label in labels:
        raw = label.encode("ascii")
        query += bytes([len(raw)]) + raw
    return query + b"\x00" + struct.pack(">HH", 1, 1)


def _skip_name(data, pos):
    """The offset just past a (possibly compressed) name. Raises ValueError when malformed."""
    while True:
        if pos >= len(data):
            raise ValueError("truncated name")
        length = data[pos]
        if length == 0:
            return pos + 1
        if length & 0xC0 == 0xC0:
            if pos + 2 > len(data):
                raise ValueError("truncated pointer")
            return pos + 2
        if length & 0xC0:
            raise ValueError("bad label type")
        pos += 1 + length


def parse_dns_response(data, tid):
    """The first A/IN record in a reply to query `tid`, or None (NXDOMAIN, no A record, malformed)."""
    try:
        if len(data) < 12:
            return None
        rtid, flags, qdcount, ancount, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
        if rtid != tid or not flags & 0x8000 or flags & 0x000F:
            return None
        pos = 12
        for _ in range(qdcount):
            pos = _skip_name(data, pos) + 4
        for _ in range(ancount):
            pos = _skip_name(data, pos)
            if pos + 10 > len(data):
                return None
            rtype, rclass, _ttl, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
            pos += 10
            if pos + rdlen > len(data):
                return None
            if rtype == 1 and rclass == 1 and rdlen == 4:
                return socket.inet_ntoa(data[pos:pos + 4])
            pos += rdlen   # e.g. the CNAME a DDNS name often answers with first
        return None
    except (ValueError, struct.error):
        return None


def _udp_exchange(host, port, payload, check, timeout, attempts=3, sock=None):
    """Send `payload` up to `attempts` times within `timeout`; return the first check(reply) result.
    Uses `sock` when given (the caller owns it and closes it), else a new ephemeral socket."""
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return None
    if not infos:
        return None
    addr = infos[0][4]
    per_attempt = max(timeout / attempts, 0.05)
    own = sock is None
    if own:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for _ in range(attempts):
            try:
                sock.sendto(payload, addr)
                deadline = time.monotonic() + per_attempt
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    sock.settimeout(left)
                    data, _src = sock.recvfrom(2048)
                    result = check(data)
                    if result:
                        return result
            except socket.timeout:
                continue
            except OSError:
                return None
        return None
    finally:
        if own:
            sock.close()


def stun_query(server, timeout, sock=None):
    """The mapped (IPv4, port) for a Binding request sent from `sock` (or a new ephemeral socket), or None."""
    host, port = parse_hostport(server, 3478)
    txid = os.urandom(12)
    return _udp_exchange(host, port, build_stun_request(txid),
                         lambda data: parse_stun_mapped(data, txid), timeout, sock=sock)


def stun_probe(server, timeout):
    mapped = stun_query(server, timeout)
    return mapped[0] if mapped else None


def dns_probe(name, resolver, timeout):
    host, port = parse_hostport(resolver, 53)
    tid = random.randint(0, 0xFFFF)
    return _udp_exchange(host, port, build_dns_query(name, tid),
                         lambda data: parse_dns_response(data, tid), timeout)


def system_probe(name):
    try:
        infos = socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return None
    return infos[0][4][0] if infos else None


def sum_participants(plugindata):
    """Total num_participants from an slvoice "list" reply's plugin data, or None if not that shape."""
    if not isinstance(plugindata, dict):
        return None
    rooms = plugindata.get("list")
    if not isinstance(rooms, list):
        return None
    total = 0
    for room in rooms:
        count = room.get("num_participants") if isinstance(room, dict) else None
        if not isinstance(count, int) or isinstance(count, bool):
            return None
        total += count
    return total


def _post(url, body, timeout):
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.loads(reply.read().decode("utf-8"))


def _get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as reply:
        return json.loads(reply.read().decode("utf-8"))


def _janus_error(reply):
    """The reason for a janus:"error" reply ("unauthorized: ..." for 403), else None."""
    if isinstance(reply, dict) and reply.get("janus") == "error":
        error = reply.get("error") or {}
        if error.get("code") == 403:
            return "unauthorized: %s (check JS_API_SECRET)" % error.get("reason", "")
        return "janus error %s: %s" % (error.get("code"), error.get("reason", ""))
    return None


def _total_or_reason(plugindata):
    total = sum_participants(plugindata)
    if total is None:
        return None, "unexpected list reply: %.200s" % json.dumps(plugindata)
    return total, None


def participants_probe(url, secret, timeout):
    """Create a session, attach slvoice, send {"request":"list"}, sum the rooms, destroy the session.
    The plugin answers "list" as an event, so a reply that is only an ack is followed by a long poll.
    Returns (total, None), or (None, reason) for a failed, unauthorised or malformed poll: a poll that
    did not produce a count never yields one."""
    url = url.rstrip("/")
    base = {"apisecret": secret} if secret else {}

    def transaction():
        return "addr-probe-%08x" % random.getrandbits(32)

    try:
        created = _post(url, dict(base, janus="create", transaction=transaction()), timeout)
    except (OSError, ValueError) as err:
        return None, "no reply from %s (%s)" % (url, err)
    reason = _janus_error(created)
    if reason:
        return None, reason
    try:
        session = created["data"]["id"]
    except (KeyError, TypeError):
        return None, "unexpected reply to create: %.200s" % json.dumps(created)
    try:
        attached = _post("%s/%d" % (url, session),
                         dict(base, janus="attach", plugin="janus.plugin.slvoice", transaction=transaction()), timeout)
        reason = _janus_error(attached)
        if reason:
            return None, reason
        handle = attached["data"]["id"]
        tx = transaction()
        reply = _post("%s/%d/%d" % (url, session, handle),
                      dict(base, janus="message", transaction=tx, body={"request": "list"}), timeout)
        reason = _janus_error(reply)
        if reason:
            return None, reason
        if reply.get("janus") == "success":
            return _total_or_reason((reply.get("plugindata") or {}).get("data"))
        query = "?maxev=1" + ("&apisecret=" + urllib.parse.quote(secret) if secret else "")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            events = _get("%s/%d%s" % (url, session, query), timeout)
            for event in events if isinstance(events, list) else [events]:
                reason = _janus_error(event)
                if reason:
                    return None, reason
                if isinstance(event, dict) and event.get("janus") == "event" and event.get("transaction") == tx:
                    return _total_or_reason((event.get("plugindata") or {}).get("data"))
        return None, "no reply to the list request within %.0f s" % timeout
    except (OSError, ValueError, KeyError, TypeError) as err:
        return None, "poll failed (%s)" % err
    finally:
        try:
            _post("%s/%d" % (url, session), dict(base, janus="destroy", transaction=transaction()), timeout)
        except (OSError, ValueError):
            pass


USAGE = "usage: addr_probe.py stun SERVER | dns NAME RESOLVER | system NAME | participants URL"


def main(argv):
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        timeout = float(os.environ.get("SLV_ADDR_PROBE_TIMEOUT_S", "3"))
    except ValueError:
        timeout = 3.0
    command = argv[1]
    try:
        if command == "stun" and len(argv) == 3:
            result = stun_probe(argv[2], timeout)
        elif command == "dns" and len(argv) == 4:
            result = dns_probe(argv[2], argv[3], timeout)
        elif command == "system" and len(argv) == 3:
            result = system_probe(argv[2])
        elif command == "participants" and len(argv) == 3:
            total, reason = participants_probe(argv[2], os.environ.get("JS_API_SECRET", ""), max(timeout, 10.0))
            if total is None:
                print("addr_probe: participants: %s" % reason, file=sys.stderr)
                return 1
            result = str(total)
        else:
            print(USAGE, file=sys.stderr)
            return 2
    except ValueError as err:
        print("addr_probe: %s" % err, file=sys.stderr)
        return 2
    if result is None:
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
