#!/usr/bin/env python3
"""legion-voice-selfcheck: reachability self-check for the mixer container (slice A.2).

  C1  the advertised address is public (the A.1 state file); CGNAT is its own FAIL
  C2  media path: STUN from the lowest, middle and highest port of JS_RTP_PORT_RANGE; is each
      source port preserved in the mapping? (outbound only)
  C3  the RTP range is bindable in the container and matches Janus's rtp_port_range
  C4  signalling: the HTTP transport answers on its port and base path
  C5  the admin API does not answer at the public address
  C6  the STUN/TURN servers Janus is configured with; WARN when there is no TURN

Each check reports an id, a title, PASS | FAIL | WARN | INCONCLUSIVE, what was observed, and a
remediation for anything that is not PASS. INCONCLUSIVE means the check could not decide; it is never
reported as PASS.

usage: legion-voice-selfcheck [--json] [--startup] [--timeout SECONDS]
  (no flag)   print the bracketed [selfcheck] block
  --json      print the report as JSON instead
  --startup   the container-start run: wait for Janus's HTTP transport within the bound
  --timeout   the time bound in seconds (default JS_SELFCHECK_TIMEOUT_S, 20)
Every run also writes the JSON report to /run/legion-voice/selfcheck.json.
Exit status: 0 = no FAIL and no INCONCLUSIVE (every check PASS, or WARN advice only);
             1 = any FAIL; 2 = any INCONCLUSIVE and no FAIL; 64 = usage error.

Inputs come from the effective values the entrypoint wrote to /run/legion-voice/effective-config.json,
else from the environment, else from the entrypoint's defaults. The image's python3 is 3.6.
Test seams (not operator knobs): SLV_EFFECTIVE_CONFIG, SLV_ADDR_STATE_FILE, JANUS_CONF_DIR,
SLV_SELFCHECK_FILE, SLV_SELFCHECK_BIND (the address the RTP sockets bind; default 0.0.0.0) and
SLV_SELFCHECK_CONNECT_MAP ("addr=addr,...": where C5 connects for an advertised address).
"""

import errno
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import addr_probe  # noqa: E402

PASS, FAIL, WARN, INCONCLUSIVE = "PASS", "FAIL", "WARN", "INCONCLUSIVE"

# The entrypoint's defaults, for a run with neither the effective-config file nor the variable.
DEFAULTS = {
    "JS_RTP_PORT_RANGE": "10000-10200",
    "JS_HTTP_PORT": "14223",
    "JS_HTTP_BASEPATH": "/voice",
    "JS_ADMIN_PORT": "14225",
    "JS_ADMIN_BASEPATH": "/voiceAdmin",
    "JS_ADMIN_BIND": "0.0.0.0",
    "JS_STUN_SERVER": "stun.l.google.com:19302",
    "JS_SELFCHECK_TIMEOUT_S": "20",
}

TITLES = {
    "C1": "advertised address is public",
    "C2": "media path: outbound UDP mapping from the RTP range",
    "C3": "RTP port range is bindable and matches Janus",
    "C4": "signalling: HTTP transport answers",
    "C5": "admin API is not reachable from outside",
    "C6": "STUN/TURN servers Janus will use",
}

REMAP_REMEDIATION = (
    "configure a TURN server so those users get a relay (see C6). On a home router, forward UDP %s to "
    "this host and look for an endpoint-independent (\"full cone\") mapping option. A container "
    "runtime's own network layer (e.g. Docker Desktop's) can also remap outbound UDP before the "
    "router sees it.")
BLOCKED_REMEDIATION = (
    "allow outbound (egress) UDP from source ports %s in the host firewall, and in any cloud security "
    "group or router egress rule")
TURN_REMEDIATION = (
    "configure a TURN server for Janus: turn_server, turn_port, turn_type, turn_user and turn_pwd (or "
    "turn_rest_api) in the nat section of janus.jcfg, via a mounted override in "
    "/opt/janus/etc/janus.d. Env knobs for this arrive with A.3/A.4.")


def result(cid, status, observed, remediation=None, notes=None, facts=None):
    """One check result. _facts carries what later checks need and is not part of the report."""
    return {"id": cid, "title": TITLES[cid], "status": status, "observed": observed,
            "remediation": None if status == PASS else remediation,
            "notes": list(notes or []), "_facts": dict(facts or {})}


class Budget(object):
    """The run's time bound. Network work never starts after it and never waits past it."""

    def __init__(self, seconds):
        self.seconds = seconds
        self.deadline = time.monotonic() + seconds

    def remaining(self):
        return max(0.0, self.deadline - time.monotonic())

    def expired(self):
        return self.remaining() <= 0.0


def not_run(cid, budget, notes=None):
    return result(cid, INCONCLUSIVE,
                  "not run: the self-check time bound (%g s) was reached first" % budget.seconds,
                  "run legion-voice-selfcheck again, or raise JS_SELFCHECK_TIMEOUT_S", notes)


def ipv4_class(text):
    """public | private | loopback | linklocal | unspecified | cgnat | invalid (as addr_class in
    public-address.sh)."""
    if not isinstance(text, str) or text.count(".") != 3:
        return "invalid"
    try:
        packed = socket.inet_aton(text)
    except OSError:
        return "invalid"
    a, b = packed[0], packed[1]
    if a == 10:
        return "private"
    if a == 127:
        return "loopback"
    if a == 0:
        return "unspecified"
    if a == 169 and b == 254:
        return "linklocal"
    if a == 172 and 16 <= b <= 31:
        return "private"
    if a == 192 and b == 168:
        return "private"
    if a == 100 and 64 <= b <= 127:
        return "cgnat"
    return "public"


def parse_range(text):
    """'10000-10200' -> (10000, 10200), or None."""
    try:
        lo_text, hi_text = (text or "").strip().split("-")
        lo, hi = int(lo_text), int(hi_text)
    except ValueError:
        return None
    if not 1 <= lo <= hi <= 65535:
        return None
    return lo, hi


def sample_ports(lo, hi, count=8):
    """The lowest, middle and highest port plus evenly spaced ones: at most `count` + 1 ports."""
    if hi - lo + 1 <= count:
        return list(range(lo, hi + 1))
    ports = {lo, hi, (lo + hi) // 2}
    for i in range(count):
        ports.add(lo + (hi - lo) * i // (count - 1))
    return sorted(ports)


def read_jcfg(path):
    """{(section, key): value} for every uncommented `key = value` line, quotes stripped. Raises OSError."""
    values = {}
    section = None
    with open(path) as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.endswith("{") and ":" in line:
                section = line.split(":", 1)[0].strip()
                continue
            if line.startswith("}"):
                section = None
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                value = value.strip().rstrip(",;").strip()
                if len(value) >= 2 and value[0] == value[-1] == '"':
                    value = value[1:-1]
                values[(section, key.strip())] = value
    return values


# ---- C1 -----------------------------------------------------------------------------------------

def check_c1(state_path):
    facts = {"public": [], "cgnat": False, "mapping": []}
    try:
        with open(state_path) as handle:
            state = json.load(handle)
        mapping = state["nat_1_1_mapping"]
        if not isinstance(mapping, list):
            raise ValueError("nat_1_1_mapping is not a list")
    except (OSError, ValueError, KeyError, TypeError) as err:
        return result("C1", INCONCLUSIVE, "cannot read the advertised address from %s (%s)" % (state_path, err),
                      "the entrypoint writes this file at start (A.1): check the start log for "
                      "[entrypoint] ADDRESS_RESOLUTION", facts=facts)
    classes = [(str(ip), ipv4_class(str(ip))) for ip in mapping]
    public = [ip for ip, cls in classes if cls == "public"]
    cgnat = [ip for ip, cls in classes if cls == "cgnat"]
    facts.update(public=public, cgnat=bool(cgnat) and not public, mapping=[ip for ip, _ in classes])
    shown = ",".join(ip for ip, _ in classes) or "(empty)"
    if public:
        winner = state.get("winner") or {}
        how = " (discovered by %s)" % winner.get("source") if winner.get("address") in public else ""
        observed = "nat_1_1_mapping %s; public: %s%s" % (shown, ", ".join(public), how)
        now, running = state.get("observed_address"), state.get("running_address")
        if now and running and now != running and (state.get("agreeing_checks") or 0) >= 2:
            return result("C1", WARN, observed + "; the address re-check found the public address is now %s" % now,
                          "restart Janus so it advertises %s: docker compose restart janus, or set "
                          "JS_PUBLIC_IP_CHANGE_ACTION=restart" % now, facts=facts)
        return result("C1", PASS, observed, facts=facts)
    if cgnat:
        return result("C1", FAIL,
                      "nat_1_1_mapping %s: %s is in 100.64.0.0/10, carrier-grade NAT (CGNAT), not a public address"
                      % (shown, ", ".join(cgnat)),
                      "CGNAT cannot be port-forwarded, so off-LAN viewers need a TURN relay: configure TURN (see C6). "
                      "Or get a public IPv4 from the ISP, or run the mixer on a host that has one.", facts=facts)
    if not classes:
        return result("C1", FAIL, "nat_1_1_mapping is empty: Janus advertises only its container address",
                      "set JS_PUBLIC_HOST=<this server's DNS/DDNS name> (its public address is discovered) or "
                      "JS_PUBLIC_IP=<public IPv4> in .env, then recreate the container", facts=facts)
    return result("C1", FAIL, "nat_1_1_mapping %s has no public address (%s)"
                  % (shown, ", ".join("%s %s" % pair for pair in classes)),
                  "set JS_PUBLIC_HOST=<this server's DNS/DDNS name> with JS_PUBLIC_IP_DISCOVERY=auto so STUN finds "
                  "the public address, or set JS_PUBLIC_IP or JS_NAT_EXTRA_IPS to the public IPv4; then recreate "
                  "the container", facts=facts)


# ---- C2 -----------------------------------------------------------------------------------------

def _bind_near(port, lo, hi, host, direction, tries=16):
    """Bind UDP to `port`, else to the nearest free port moving `direction` inside the range.
    Returns (sock, port, None) or (None, None, last_error)."""
    last = None
    candidate = port
    for _ in range(tries):
        if not lo <= candidate <= hi:
            break
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((host, candidate))
            return sock, candidate, None
        except OSError as err:
            sock.close()
            last = err
            if err.errno != errno.EADDRINUSE:
                break
        candidate += direction
    return None, None, last


def _egress_ip(server_addr):
    """This host's source address toward `server_addr`, or None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(server_addr)
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def check_c2(stun_server, range_text, budget, bind_host, advertised=()):
    rng = parse_range(range_text)
    notes = ["This proves the OUTBOUND mapping only: a missing router forward or host firewall rule for inbound "
             "UDP %s can still pass C2. Test inbound from outside the network (A.6 recipes)." % range_text]
    facts = {"direct": False, "remapped": False}
    if rng is None:
        return result("C2", INCONCLUSIVE, "JS_RTP_PORT_RANGE=%r is not a valid LOW-HIGH range (see C3)" % range_text,
                      "set JS_RTP_PORT_RANGE=LOW-HIGH, e.g. 10000-10200", notes, facts)
    try:
        host, port = addr_probe.parse_hostport(stun_server or "", 3478)
    except ValueError:
        return result("C2", INCONCLUSIVE, "no usable STUN server (JS_STUN_SERVER=%r)" % stun_server,
                      "set JS_STUN_SERVER=host:port, e.g. stun.l.google.com:19302", notes, facts)
    if budget.expired():
        return not_run("C2", budget, notes)
    try:
        server_addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    except (OSError, IndexError) as err:
        return result("C2", INCONCLUSIVE, "cannot resolve the STUN server %s (%s)" % (stun_server, err),
                      "check DNS inside the container, or set JS_STUN_SERVER to an IP:port", notes, facts)

    lo, hi = rng
    targets = [(lo, 1), ((lo + hi) // 2, 1), (hi, -1)]
    bound, bind_errors, taken = [], [], set()
    for wanted, direction in targets:
        sock, got, err = _bind_near(wanted, lo, hi, bind_host, direction)
        if sock is not None and got in taken:
            sock.close()
            continue
        if sock is None:
            bind_errors.append("%d (%s)" % (wanted, getattr(err, "strerror", None) or err))
            continue
        taken.add(got)
        bound.append((wanted, got, sock))
    if not bound:
        return result("C2", INCONCLUSIVE, "could not bind any sampled media port: %s" % ", ".join(bind_errors),
                      "see C3: the RTP range must be bindable inside the container", notes, facts)

    timeout = min(3.0, budget.remaining())
    answers = {}
    control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    control.bind((bind_host, 0))

    def ask(key, sock):
        answers[key] = addr_probe.stun_query(stun_server, timeout, sock=sock)

    threads = [threading.Thread(target=ask, args=(got, sock), daemon=True) for _, got, sock in bound]
    threads.append(threading.Thread(target=ask, args=("control", control), daemon=True))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout + 1.0)
    for _, _, sock in bound:
        sock.close()
    control.close()

    def label(wanted, got):
        return "%d" % got if got == wanted else "%d (for %d, in use)" % (got, wanted)

    answered = [(wanted, got, answers[got]) for wanted, got, _ in bound if answers.get(got)]
    silent = [(wanted, got) for wanted, got, _ in bound if not answers.get(got)]
    detail = "; ".join("local %s -> %s:%d" % (label(w, g), a[0], a[1]) for w, g, a in answered)
    remapped = [entry for entry in answered if entry[2][1] != entry[1]]
    if remapped:
        facts["remapped"] = True
        observed = ("port-remapping or symmetric NAT: some users will have no direct path, TURN required. "
                    "Mapped source port differs from the local port: %s" % detail)
        if silent:
            observed += "; no answer to local %s" % ", ".join(label(w, g) for w, g in silent)
        return result("C2", FAIL, observed, REMAP_REMEDIATION % range_text, notes, facts)
    if silent:
        silent_text = ", ".join(label(w, g) for w, g in silent)
        control_answer = answers.get("control")
        if control_answer is None and not answered:
            return result("C2", INCONCLUSIVE,
                          "no STUN answer from %s to local ports %s, nor to an ephemeral port: the STUN server is "
                          "unreachable, so the media range cannot be judged" % (stun_server, silent_text),
                          "check outbound UDP to %s, or set JS_STUN_SERVER to a reachable STUN server, and run "
                          "legion-voice-selfcheck again" % stun_server, notes, facts)
        observed = "outbound UDP from the media range is blocked: no STUN answer to local ports %s" % silent_text
        if control_answer:
            observed += "; the same server answered an ephemeral port (mapped %s:%d)" % control_answer
        if answered:
            observed += "; answered: %s" % detail
        return result("C2", FAIL, observed, BLOCKED_REMEDIATION % range_text, notes, facts)

    mapped_ips = sorted(set(a[0] for _, _, a in answered))
    observed = "mapped port equals the local port for all %d sampled ports (ports preserved): %s" % (len(answered), detail)
    if mapped_ips == [_egress_ip(server_addr)]:
        facts["direct"] = True
        observed += "; the mapped address is this host's own interface address (no address translation)"
    if advertised and any(ip not in advertised for ip in mapped_ips):
        notes.append("mapped address %s is not in nat_1_1_mapping %s: viewers are told a different address than "
                     "the one this host's traffic leaves from" % (", ".join(mapped_ips), ",".join(advertised)))
    return result("C2", PASS, observed, None, notes, facts)


# ---- C3 -----------------------------------------------------------------------------------------

def check_c3(range_text, jcfg_path, bind_host):
    rng = parse_range(range_text)
    if rng is None:
        return result("C3", FAIL, "JS_RTP_PORT_RANGE=%r is not LOW-HIGH within 1-65535" % range_text,
                      "set JS_RTP_PORT_RANGE=LOW-HIGH (e.g. 10000-10200) and publish the same UDP range in "
                      "docker-compose.yml")
    lo, hi = rng
    ports = sample_ports(lo, hi)
    bound, in_use, denied = [], [], []
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((bind_host, port))
            bound.append(port)
        except OSError as err:
            if err.errno == errno.EADDRINUSE:
                in_use.append(str(port))
            else:
                denied.append("%d (%s)" % (port, err.strerror or err))
        finally:
            sock.close()
    observed = "sampled %d ports of %d-%d (%s): %d bound" % (len(ports), lo, hi, ",".join(str(p) for p in ports), len(bound))
    if in_use:
        observed += ", in use (e.g. by Janus media): %s" % ",".join(in_use)
    try:
        configured = read_jcfg(jcfg_path).get(("media", "rtp_port_range"))
        jcfg_problem = None if configured else "no rtp_port_range in its media section"
    except OSError as err:
        configured, jcfg_problem = None, str(err)
    wanted = "%d-%d" % rng
    problems = []
    remediation = []
    if denied:
        problems.append("cannot bind %s" % ", ".join(denied))
        remediation.append("the container cannot bind these UDP ports: choose a range it can (above 1024, not in "
                           "net.ipv4.ip_local_reserved_ports) in JS_RTP_PORT_RANGE, and publish the same range in "
                           "docker-compose.yml")
    if configured and configured.replace(" ", "") != wanted:
        problems.append("JS_RTP_PORT_RANGE=%s but Janus is configured with rtp_port_range=%s" % (wanted, configured))
        remediation.append("a mounted janus.jcfg in /opt/janus/etc/janus.d overrides the range: make its rtp_port_range "
                           "match JS_RTP_PORT_RANGE, which docker-compose.yml publishes")
    if problems:
        return result("C3", FAIL, observed + "; " + "; ".join(problems), "; ".join(remediation))
    if configured is None:
        return result("C3", INCONCLUSIVE, observed + "; cannot read Janus's rtp_port_range from %s (%s)" % (jcfg_path, jcfg_problem),
                      "check %s: the entrypoint generates it at start, and a mounted override in /opt/janus/etc/janus.d "
                      "replaces it" % jcfg_path)
    if not bound:
        return result("C3", INCONCLUSIVE, observed + "; every sampled port is in use, so bindability is unproven",
                      "run legion-voice-selfcheck again when fewer calls are active")
    return result("C3", PASS, observed + "; Janus rtp_port_range=%s matches JS_RTP_PORT_RANGE" % configured)


# ---- C4 -----------------------------------------------------------------------------------------

def check_c4(port, base, budget, wait_s):
    url = "http://127.0.0.1:%s%s/info" % (port, base)
    if budget.expired():
        return not_run("C4", budget)
    started = time.monotonic()
    stop = min(budget.deadline, started + wait_s)
    last = None
    attempts = 0
    while time.monotonic() < stop:
        attempts += 1
        try:
            with urllib.request.urlopen(url, timeout=max(0.05, min(2.0, stop - time.monotonic()))) as reply:
                body = reply.read(65536)
        except urllib.error.HTTPError as err:
            return result("C4", FAIL, "HTTP %d from %s" % (err.code, url),
                          "the Janus HTTP transport does not serve JS_HTTP_BASEPATH=%s on JS_HTTP_PORT=%s: check for a "
                          "mounted janus.transport.http.jcfg override in /opt/janus/etc/janus.d" % (base, port))
        except (urllib.error.URLError, OSError) as err:
            last = getattr(err, "reason", err)
            time.sleep(max(0.0, min(0.25, stop - time.monotonic())))
            continue
        try:
            info = json.loads(body.decode("utf-8"))
        except ValueError:
            info = None
        if not isinstance(info, dict) or info.get("janus") != "server_info":
            return result("C4", FAIL, "%s answered, but not with Janus server_info" % url,
                          "another service answers on JS_HTTP_PORT=%s: change JS_HTTP_PORT (and the sim's "
                          "JanusGatewayURI) or stop that service" % port)
        if "janus.plugin.slvoice" not in (info.get("plugins") or {}):
            return result("C4", FAIL, "Janus answers at %s but janus.plugin.slvoice is not loaded" % url,
                          "check the Janus log for the slvoice plugin's load error")
        return result("C4", PASS, "%s answered: %s %s, janus.plugin.slvoice loaded"
                      % (url, info.get("name", "Janus"), info.get("version_string", "")))
    waited = time.monotonic() - started
    if last is None:
        return not_run("C4", budget)
    refused = isinstance(last, ConnectionRefusedError) or getattr(last, "errno", None) == errno.ECONNREFUSED
    if refused:
        return result("C4", FAIL, "nothing listens at %s (connection refused, %d attempt(s) over %.1f s)" % (url, attempts, waited),
                      "Janus has not opened its HTTP transport on JS_HTTP_PORT=%s: check the Janus log for a port "
                      "conflict or a transport that failed to load" % port)
    return result("C4", INCONCLUSIVE, "no answer from %s within %.1f s (%s)" % (url, waited, last),
                  "Janus may still be starting or is overloaded: run legion-voice-selfcheck again, and check the Janus "
                  "log if it persists")


# ---- C5 -----------------------------------------------------------------------------------------

def _tcp_answer(host, port, path, timeout):
    """'janus' when an HTTP GET for `path` is answered by Janus, 'other' when the port accepts but the reply is
    not Janus, None when it does not connect."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError:
        return None
    data = b""
    try:
        sock.settimeout(timeout)
        sock.sendall(("GET %s HTTP/1.0\r\nHost: %s\r\n\r\n" % (path, host)).encode("ascii"))
        while len(data) < 4096:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    except OSError:
        pass
    finally:
        sock.close()
    return "janus" if b"janus" in data.lower() else "other"


def check_c5(bind, port_text, base, public_addrs, budget, connect_map):
    head = "admin API published on %s:%s (JS_ADMIN_BIND, JS_ADMIN_PORT)" % (bind, port_text)
    try:
        port = int(port_text)
    except (TypeError, ValueError):
        return result("C5", INCONCLUSIVE, head + "; JS_ADMIN_PORT is not a port number",
                      "set JS_ADMIN_PORT to the admin port number")
    if not public_addrs:
        return result("C5", INCONCLUSIVE, head + "; no public address to probe (see C1)",
                      "fix C1 first; meanwhile make sure no router forward or firewall rule exposes TCP %d (A.6 "
                      "recipes)" % port)
    if budget.expired():
        return not_run("C5", budget)
    janus, other, closed = [], [], []
    for addr in public_addrs:
        left = min(2.0, budget.remaining())
        if left <= 0:
            break
        answer = _tcp_answer(connect_map.get(addr, addr), port, base, left)
        {"janus": janus, "other": other}.get(answer, closed).append("%s:%d" % (addr, port))
    unprobed = len(public_addrs) - len(janus) - len(other) - len(closed)
    if janus:
        return result("C5", FAIL, head + "; the admin API answers at %s" % ", ".join(janus),
                      "remove the router forward or firewall opening for TCP %d, and set JS_ADMIN_BIND to the address "
                      "the sim uses (its JanusGatewayAdminURI) instead of all interfaces: the admin API must not be "
                      "reachable from the internet" % port)
    if other:
        return result("C5", WARN, head + "; TCP %d accepts connections at %s but did not answer as the Janus admin API"
                      % (port, ", ".join(other)),
                      "check what the router or host forwards on TCP %d" % port)
    if unprobed:
        return result("C5", INCONCLUSIVE, head + "; %d public address(es) not probed before the time bound" % unprobed,
                      "run legion-voice-selfcheck again, or raise JS_SELFCHECK_TIMEOUT_S")
    return result("C5", PASS, head + "; not reachable at %s (unreachable is the desired result)" % ", ".join(closed),
                  notes=["probed from inside the container through the public address; a router that does not "
                         "hairpin TCP also reads as unreachable, so confirm from outside the network (A.6 recipes)"])


# ---- C6 -----------------------------------------------------------------------------------------

def check_c6(jcfg_path, range_text, c1, c2):
    try:
        values = read_jcfg(jcfg_path)
    except OSError as err:
        return result("C6", INCONCLUSIVE, "cannot read %s (%s)" % (jcfg_path, err),
                      "check the container start log: the entrypoint generates this file")

    def nat(key):
        return values.get(("nat", key))

    stun = "%s:%s" % (nat("stun_server"), nat("stun_port") or "3478") if nat("stun_server") else "none"
    if nat("turn_server"):
        turn = "%s:%s (%s)" % (nat("turn_server"), nat("turn_port") or "3478", nat("turn_type") or "udp")
    elif nat("turn_rest_api"):
        turn = "TURN REST API %s" % nat("turn_rest_api")
    else:
        turn = None
    observed = ("Janus nat config: stun_server %s; turn %s. Viewers get their own STUN list from the sim "
                "(StunServers), which this check cannot see" % (stun, turn or "none"))
    if turn:
        return result("C6", PASS, observed)
    c1_facts, c2_facts = c1.get("_facts", {}), c2.get("_facts", {})
    if c1_facts.get("cgnat"):
        who = ("viewers behind symmetric NAT or CGNAT have no path at all (C1 found this server behind CGNAT, which "
               "cannot be port-forwarded), and neither do viewers on networks that block UDP")
    elif c2_facts.get("remapped"):
        who = ("viewers behind symmetric NAT or CGNAT have no direct path unless inbound UDP %s reaches this server "
               "(C2 found outbound port remapping and cannot see inbound), and viewers on networks that block UDP "
               "have no path at all" % range_text)
    elif c2_facts.get("direct"):
        who = ("viewers on networks that block UDP have no path at all, and neither does any viewer if a host "
               "firewall blocks inbound UDP %s (C2 cannot see inbound)" % range_text)
    else:
        who = ("viewers on networks that block UDP have no path at all, and viewers behind symmetric NAT or CGNAT "
               "have none either if the router forward or firewall rule for inbound UDP %s is missing (C2 cannot "
               "see inbound)" % range_text)
    return result("C6", WARN, observed + ". No TURN configured: " + who, TURN_REMEDIATION)


# ---- run, report ---------------------------------------------------------------------------------

def load_config(environ):
    values = dict(DEFAULTS)
    for key in DEFAULTS:
        if environ.get(key):
            values[key] = environ[key]
    effective = environ.get("SLV_EFFECTIVE_CONFIG") or "/run/legion-voice/effective-config.json"
    try:
        with open(effective) as handle:
            data = json.load(handle)
        for key in DEFAULTS:
            if isinstance(data.get(key), str) and data[key]:
                values[key] = data[key]
    except (OSError, ValueError, AttributeError):
        pass
    connect_map = {}
    for pair in (environ.get("SLV_SELFCHECK_CONNECT_MAP") or "").split(","):
        if "=" in pair:
            advertised, target = pair.split("=", 1)
            connect_map[advertised.strip()] = target.strip()
    try:
        timeout = float(values["JS_SELFCHECK_TIMEOUT_S"])
    except ValueError:
        timeout = 20.0
    return {
        "rtp_range": values["JS_RTP_PORT_RANGE"],
        "stun_server": values["JS_STUN_SERVER"],
        "http_port": values["JS_HTTP_PORT"],
        "http_base": values["JS_HTTP_BASEPATH"],
        "admin_port": values["JS_ADMIN_PORT"],
        "admin_base": values["JS_ADMIN_BASEPATH"],
        "admin_bind": values["JS_ADMIN_BIND"],
        "timeout": timeout if timeout > 0 else 20.0,
        "state_file": environ.get("SLV_ADDR_STATE_FILE") or "/run/legion-voice/public-address.json",
        "jcfg_path": os.path.join(environ.get("JANUS_CONF_DIR") or "/opt/janus/etc/janus", "janus.jcfg"),
        "report_file": environ.get("SLV_SELFCHECK_FILE") or "/run/legion-voice/selfcheck.json",
        "bind_host": environ.get("SLV_SELFCHECK_BIND") or "0.0.0.0",
        "connect_map": connect_map,
    }


def run_checks(cfg, budget, startup=False):
    """C1 and C3 are local and always run. C4, C2 and C5 touch the network inside the budget; at startup C4
    first waits for Janus, leaving a reserve of the bound for C2 and C5."""
    c1 = check_c1(cfg["state_file"])
    c3 = check_c3(cfg["rtp_range"], cfg["jcfg_path"], cfg["bind_host"])
    if startup:
        wait = max(0.5, budget.remaining() - min(6.0, budget.seconds / 3.0))
    else:
        wait = min(3.0, budget.remaining())
    c4 = check_c4(cfg["http_port"], cfg["http_base"], budget, wait)
    c2 = check_c2(cfg["stun_server"], cfg["rtp_range"], budget, cfg["bind_host"], c1["_facts"].get("mapping", []))
    c5 = check_c5(cfg["admin_bind"], cfg["admin_port"], cfg["admin_base"], c1["_facts"].get("public", []),
                  budget, cfg["connect_map"])
    c6 = check_c6(cfg["jcfg_path"], cfg["rtp_range"], c1, c2)
    return [c1, c2, c3, c4, c5, c6]


def exit_code_for(results):
    statuses = [r["status"] for r in results]
    if FAIL in statuses:
        return 1
    if INCONCLUSIVE in statuses:
        return 2
    return 0


def build_report(results, mode, when, duration, bound):
    counts = dict((status.lower(), sum(1 for r in results if r["status"] == status))
                  for status in (PASS, WARN, FAIL, INCONCLUSIVE))
    checks = [dict((k, v) for k, v in r.items() if not k.startswith("_")) for r in results]
    return {"schema": 1, "tool": "legion-voice-selfcheck", "mode": mode, "time": when,
            "duration_s": round(duration, 1), "timeout_s": bound, "summary": counts,
            "exit_code": exit_code_for(results), "checks": checks}


def render_block(report, report_file):
    """The one bracketed, greppable block: every line starts with [selfcheck]."""
    s = report["summary"]
    lines = ["[selfcheck] ===== BEGIN legion-voice self-check (%s run, %s, %.1f s, bound %g s) ====="
             % (report["mode"], report["time"], report["duration_s"], report["timeout_s"])]
    for check in report["checks"]:
        lines.append("[selfcheck] %s %-12s %s" % (check["id"], check["status"], check["title"]))
        lines.append("[selfcheck]    observed: %s" % check["observed"])
        if check["remediation"]:
            lines.append("[selfcheck]    remediation: %s" % check["remediation"])
        for note in check["notes"]:
            lines.append("[selfcheck]    note: %s" % note)
    lines.append("[selfcheck] ===== END legion-voice self-check: %d PASS, %d WARN, %d FAIL, %d INCONCLUSIVE; exit %d; "
                 "JSON %s =====" % (s["pass"], s["warn"], s["fail"], s["inconclusive"], report["exit_code"], report_file))
    return "\n".join(lines) + "\n"


def write_report(path, report):
    """Atomic write; returns an error string or None."""
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
        return None
    except OSError as err:
        return str(err)


USAGE = "usage: legion-voice-selfcheck [--json] [--startup] [--timeout SECONDS]\n"


def main(argv=None, environ=None, runner=None, out=None):
    argv = sys.argv[1:] if argv is None else argv
    environ = os.environ if environ is None else environ
    out = sys.stdout if out is None else out
    as_json = startup = False
    timeout = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--json":
            as_json = True
        elif arg == "--startup":
            startup = True
        elif arg == "--timeout" and i + 1 < len(argv):
            try:
                timeout = float(argv[i + 1])
            except ValueError:
                sys.stderr.write(USAGE)
                return 64
            i += 1
        elif arg in ("-h", "--help"):
            out.write(__doc__)
            return 0
        else:
            sys.stderr.write(USAGE)
            return 64
        i += 1
    cfg = load_config(environ)
    bound = timeout if timeout and timeout > 0 else cfg["timeout"]
    budget = Budget(bound)
    started = time.monotonic()
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results = (runner or run_checks)(cfg, budget, startup)
    report = build_report(results, "startup" if startup else "on-demand", when, time.monotonic() - started, bound)
    error = write_report(cfg["report_file"], report)
    text = json.dumps(report, indent=2) + "\n" if as_json else render_block(report, cfg["report_file"])
    out.write(text)   # one write, so the block is not interleaved with other log output
    out.flush()
    if error:
        sys.stderr.write("legion-voice-selfcheck: could not write %s (%s)\n" % (cfg["report_file"], error))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
