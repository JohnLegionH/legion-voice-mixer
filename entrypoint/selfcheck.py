#!/usr/bin/env python3
"""legion-voice-selfcheck: reachability self-check for the mixer container (slices A.2, A.2b, A.3).

  C1   the advertised address is public (the A.1 state file); CGNAT is its own FAIL
  C2a  media path, outbound: STUN from the lowest, middle and highest port of JS_RTP_PORT_RANGE.
       Information about container-initiated UDP. A remapped source port is a WARN (normal for a
       published-port container); only no answer at all is a FAIL.
  C2b  media path, inbound: whether UDP from outside reaches the RTP range. This cannot be seen from
       inside the container, so it is INCONCLUSIVE unless `--listen` recorded a receipt from an outside
       device within JS_SELFCHECK_INBOUND_MAX_AGE_H.
  C3   the RTP range is bindable in the container and matches Janus's rtp_port_range
  C4   signalling: the HTTP transport answers on its port and base path
  C5   the admin API does not answer at the public address
  C6   TURN for this server. Mixer-side TURN rescues a CGNAT or unreachable SERVER (C1). Without TURN, a public
       server is PASS and a non-public one WARN. With TURN, a real Allocate (through the REST API when configured)
       is PASS with the relay address, or FAIL with the reason. turn_type is information, never a pass criterion.

Each check reports an id, a title, PASS | FAIL | WARN | INCONCLUSIVE, what was observed, and a
remediation for anything that is not PASS. INCONCLUSIVE means the check could not decide; it is never
reported as PASS. The report's verdict is its worst status, in the order FAIL > INCONCLUSIVE > WARN > PASS.
Every run also reports the ICE candidate types Janus offered each slvoice handle (host / srflx / relay), from
the Admin API's handle_info, and writes them to /run/legion-voice/candidates.json.

usage: legion-voice-selfcheck [--json] [--startup] [--timeout SECONDS]
       legion-voice-selfcheck --listen [--port N] [--seconds N]
       legion-voice-selfcheck --candidates [--json]
       legion-voice-selfcheck --sessions [--json] [--agent UUID-PREFIX] [--room N] [--failed] [--limit N]
       legion-voice-selfcheck --session HANDLE|AGENT [--json]
  (no flag)     print the bracketed [selfcheck] block
  --json        print the report as JSON instead
  --startup     the container-start run: wait for Janus's HTTP transport within the bound
  --timeout     the time bound in seconds (default JS_SELFCHECK_TIMEOUT_S, 20)
  --listen      prove C2b: bind a free port of JS_RTP_PORT_RANGE (or --port N), print the one-line commands to run
                from a device outside the network, and wait --seconds (default 120) for one. A receipt goes to
                /run/legion-voice/selfcheck-inbound.json.
  --candidates  only the candidate-type report (JS_ADMIN_SECRET from the environment)
  --sessions    recent slvoice sessions, live and ended, from the ICE diagnostics collector (slice A.5), newest first:
                each one's outcome, agent, room, handle and path. --limit N (default 20, 0 = all); --agent filters by
                agent UUID prefix, --room by room, --failed keeps FAIL and WARN.
  --session     one session in detail: agent, room, ICE and DTLS states, the selected pair with both candidate types,
                local and remote candidate type counts, relay or direct as far as this side can tell (with the prflx
                caveat), the reason and last state reached on failure, and a timeline. HANDLE is the Janus handle id;
                an agent UUID, or a prefix of 4 or more characters, picks that agent's newest session.
Every check run writes the JSON report to /run/legion-voice/selfcheck.json.
Exit status (check run):   0 = no FAIL and no INCONCLUSIVE (PASS, or WARN advice only); 1 = any FAIL;
                           2 = any INCONCLUSIVE and no FAIL; 64 = usage error.
Exit status (--listen):    0 = the probe arrived and was recorded; 2 = nothing arrived in time;
                           1 = no port could be bound, or the receipt could not be written; 64 = usage error.
Exit status (--candidates): 0 = the Admin API answered; 2 = it did not.
Exit status (--sessions, --session): the check run's rule over the sessions shown. 0 = no FAIL and no INCONCLUSIVE;
                           1 = any FAIL; 2 = any INCONCLUSIVE and no FAIL, or the collector is not running (unless a
                           FAIL already gives 1), or there is no diagnostics file, or no such session; 64 = usage error.
                           The sessions come from /run/legion-voice/ice-diag.json (SLV_ICE_DIAG_FILE).

Inputs come from the effective values the entrypoint wrote to /run/legion-voice/effective-config.json,
else from the environment, else from the entrypoint's defaults. TURN settings come from janus.jcfg, so C6
checks what Janus actually uses, mounted overrides included. The image's python3 is 3.6.
Test seams (not operator knobs): SLV_EFFECTIVE_CONFIG, SLV_ADDR_STATE_FILE, JANUS_CONF_DIR,
SLV_SELFCHECK_FILE, SLV_SELFCHECK_INBOUND_FILE, SLV_SELFCHECK_CANDIDATES_FILE, SLV_SELFCHECK_BIND (the address
the RTP sockets bind; default 0.0.0.0) and SLV_SELFCHECK_CONNECT_MAP ("addr=addr,...": where C5 connects for
an advertised address).
"""

import errno
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import addr_probe  # noqa: E402
import ice_diag  # noqa: E402

PASS, FAIL, WARN, INCONCLUSIVE = "PASS", "FAIL", "WARN", "INCONCLUSIVE"
SEVERITY = {PASS: 0, WARN: 1, INCONCLUSIVE: 2, FAIL: 3}

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
    "JS_SELFCHECK_INBOUND_MAX_AGE_H": "168",
}

TITLES = {
    "C1": "advertised address is public",
    "C2a": "media path, outbound: UDP mapping from the RTP range (information)",
    "C2b": "media path, inbound: UDP from outside reaches the RTP range",
    "C3": "RTP port range is bindable and matches Janus",
    "C4": "signalling: HTTP transport answers",
    "C5": "admin API is not reachable from outside",
    "C6": "TURN for this server (mixer-side relay)",
}

# Run on the Docker host, in the directory holding docker-compose.yml (the service is named janus).
LISTEN_COMMAND = "docker compose exec janus legion-voice-selfcheck --listen"

INBOUND_NOTE = (
    "This observes container-initiated (outbound) UDP only. Inbound-forwarded flows, the ones a forwarded "
    "server's media rides (a viewer's packets arriving through the router forward and the published port), are "
    "a separate mapping this probe cannot see: that is C2b.")
BLOCKED_REMEDIATION = (
    "allow outbound (egress) UDP from source ports %s in the host firewall, and in any cloud security group or "
    "router egress rule")
TURN_KNOBS = ("JS_TURN_SERVER, JS_TURN_PORT and JS_TURN_TYPE with JS_TURN_USER and JS_TURN_PWD, or JS_TURN_REST_API "
              "(with JS_TURN_REST_API_KEY and JS_TURN_REST_API_METHOD)")
CANDIDATE_TYPES = ("host", "srflx", "relay", "prflx")
_CANDIDATE_TYPE = re.compile(r"\btyp (host|srflx|relay|prflx)\b")


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


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


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


def write_json(path, data):
    """Atomic write; returns an error string or None."""
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
        return None
    except OSError as err:
        return str(err)


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


# ---- C2a: outbound mapping (information) ---------------------------------------------------------

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


def check_c2a(stun_server, range_text, budget, bind_host, advertised=()):
    """STUN from the RTP range. Observes container-initiated UDP only: a remapped port is information (WARN),
    no answer at all from the range while an ephemeral port is answered is the one FAIL."""
    rng = parse_range(range_text)
    notes = [INBOUND_NOTE]
    facts = {"direct": False, "remapped": False}
    if rng is None:
        return result("C2a", INCONCLUSIVE, "JS_RTP_PORT_RANGE=%r is not a valid LOW-HIGH range (see C3)" % range_text,
                      "set JS_RTP_PORT_RANGE=LOW-HIGH, e.g. 10000-10200", notes, facts)
    try:
        host, port = addr_probe.parse_hostport(stun_server or "", 3478)
    except ValueError:
        return result("C2a", INCONCLUSIVE, "no usable STUN server (JS_STUN_SERVER=%r)" % stun_server,
                      "set JS_STUN_SERVER=host:port, e.g. stun.l.google.com:19302", notes, facts)
    if budget.expired():
        return not_run("C2a", budget, notes)
    try:
        server_addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    except (OSError, IndexError) as err:
        return result("C2a", INCONCLUSIVE, "cannot resolve the STUN server %s (%s)" % (stun_server, err),
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
        return result("C2a", INCONCLUSIVE, "could not bind any sampled media port: %s" % ", ".join(bind_errors),
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
    if silent:
        silent_text = ", ".join(label(w, g) for w, g in silent)
        control_answer = answers.get("control")
        if control_answer is None and not answered:
            return result("C2a", INCONCLUSIVE,
                          "no STUN answer from %s to local ports %s, nor to an ephemeral port: the STUN server is "
                          "unreachable, so the media range cannot be judged" % (stun_server, silent_text),
                          "check outbound UDP to %s, or set JS_STUN_SERVER to a reachable STUN server, and run "
                          "legion-voice-selfcheck again" % stun_server, notes, facts)
        observed = "outbound UDP from the media range is blocked: no STUN answer to local ports %s" % silent_text
        if control_answer:
            observed += "; the same server answered an ephemeral port (mapped %s:%d)" % control_answer
        if answered:
            observed += "; answered: %s" % detail
        return result("C2a", FAIL, observed, BLOCKED_REMEDIATION % range_text, notes, facts)
    if any(a[1] != got for _, got, a in answered):
        facts["remapped"] = True
        return result("C2a", WARN,
                      "container-initiated UDP is source-port remapped; this is normal for a published-port container "
                      "and does not by itself break a forwarded server. Mapped source port differs from the local port: "
                      "%s" % detail,
                      "no action needed for a forwarded server; prove the inbound path with C2b: %s" % LISTEN_COMMAND,
                      notes, facts)
    mapped_ips = sorted(set(a[0] for _, _, a in answered))
    observed = "mapped port equals the local port for all %d sampled ports (ports preserved): %s" % (len(answered), detail)
    if mapped_ips == [_egress_ip(server_addr)]:
        facts["direct"] = True
        observed += "; the mapped address is this host's own interface address (no address translation)"
    if advertised and any(ip not in advertised for ip in mapped_ips):
        notes.append("mapped address %s is not in nat_1_1_mapping %s: this host's traffic leaves from a different "
                     "address than the one viewers are told" % (", ".join(mapped_ips), ",".join(advertised)))
    return result("C2a", PASS, observed, None, notes, facts)


# ---- C2b: inbound reachability ------------------------------------------------------------------

def _age_text(hours):
    return "%d min" % int(hours * 60) if hours < 1 else "%.1f h" % hours


def check_c2b(inbound_file, range_text, max_age_h, public_addrs, now=None):
    """Inbound UDP to the RTP range cannot be observed from inside the container. PASS only on a receipt that
    `--listen` recorded from an outside device, for a port still in the range, recorded while advertising an
    address still advertised, and no older than max_age_h. Anything else is INCONCLUSIVE, never a guessed PASS."""
    now = time.time() if now is None else now
    how = ("run `%s` on the Docker host, then run the one-line command it prints from a device outside this "
           "network (e.g. a phone on mobile data)" % LISTEN_COMMAND)
    try:
        with open(inbound_file) as handle:
            record = json.load(handle)
    except OSError:
        return result("C2b", INCONCLUSIVE,
                      "inbound UDP to the RTP range cannot be determined from inside the container, and no inbound "
                      "receipt is recorded (%s)" % inbound_file,
                      "to prove it: " + how)
    except ValueError as err:
        return result("C2b", INCONCLUSIVE, "the inbound receipt %s is unreadable (%s)" % (inbound_file, err),
                      "record a new one: " + how)
    try:
        if record.get("result") != PASS:
            raise ValueError("its result is %r, not PASS" % record.get("result"))
        epoch, port = float(record["epoch"]), int(record["port"])
        when, source = str(record["time"]), str(record["source"])
    except (AttributeError, KeyError, TypeError, ValueError) as err:
        return result("C2b", INCONCLUSIVE, "the inbound receipt %s is unusable (%s)" % (inbound_file, err),
                      "record a new one: " + how)
    rng = parse_range(range_text)
    if rng is None or not rng[0] <= port <= rng[1]:
        return result("C2b", INCONCLUSIVE,
                      "the inbound receipt (UDP %d, observed %s) is for a port outside the current JS_RTP_PORT_RANGE "
                      "%s" % (port, when, range_text),
                      "record a new one: " + how)
    advertised = [str(a) for a in (record.get("advertised") or [])]
    if advertised and public_addrs and not set(advertised) & set(public_addrs):
        return result("C2b", INCONCLUSIVE,
                      "the inbound receipt (observed %s) was recorded while advertising %s; this server now "
                      "advertises %s" % (when, ", ".join(advertised), ", ".join(public_addrs)),
                      "record a new one: " + how)
    age_h = max(0.0, (now - epoch) / 3600.0)
    if age_h > max_age_h:
        return result("C2b", INCONCLUSIVE,
                      "the last inbound receipt (UDP %d, observed %s, from %s) is %s old, older than "
                      "JS_SELFCHECK_INBOUND_MAX_AGE_H=%g" % (port, when, source, _age_text(age_h), max_age_h),
                      "record a fresh one: " + how)
    return result("C2b", PASS,
                  "inbound UDP reached port %d from outside (observed %s, from %s, %s ago)"
                  % (port, when, source, _age_text(age_h)),
                  notes=["a receipt proves the path when it was recorded; a router or firewall change since then is "
                         "not seen until the receipt goes stale (JS_SELFCHECK_INBOUND_MAX_AGE_H)"])


def probe_ufrag(token):
    """The token as an ICE ufrag (letters, digits, + and / only), for the browser method."""
    return re.sub(r"[^A-Za-z0-9+/]", "", token)


def probe_page_url(ufrag, address, port):
    """A data: URL whose page needs nothing installed: it gives the browser a WebRTC offer whose only candidate is
    address:port, so the browser's ICE connectivity checks (STUN Binding requests, USERNAME "<ufrag>:<its own>") carry
    the probe to the listening port. Nothing but those checks is sent, and nothing is loaded from anywhere."""
    sdp = "\\r\\n".join([
        "v=0", "o=- 1 1 IN IP4 0.0.0.0", "s=-", "t=0 0", "a=group:BUNDLE 0",
        "m=application 9 UDP/DTLS/SCTP webrtc-datachannel", "c=IN IP4 0.0.0.0", "a=mid:0",
        "a=ice-ufrag:%s" % ufrag, "a=ice-pwd:legionvoiceprobepassword0", "a=fingerprint:sha-256 " + ":".join(["AB"] * 32),
        "a=setup:actpass", "a=sctp-port:5000", "a=candidate:1 1 udp 2130706431 %s %d typ host" % (address, port), ""])
    page = ("<!doctype html><meta name=viewport content='width=device-width'><title>legion-voice probe</title>"
            "<body style='font:18px sans-serif;margin:1em'><b>legion-voice inbound probe</b><p id=s>starting</p><script>"
            "(async()=>{const s=document.getElementById('s');try{const pc=new RTCPeerConnection();"
            "await pc.setRemoteDescription({type:'offer',sdp:'%s'});await pc.setLocalDescription(await pc.createAnswer());"
            "s.textContent='sending to %s port %d: keep this page open for 20 seconds';"
            "setTimeout(()=>{pc.close();s.textContent='done: look at the listener'},20000)}"
            "catch(e){s.textContent='this browser refused: '+e}})()</script>" % (sdp, address, port))
    return "data:text/html;charset=utf-8," + urllib.parse.quote(page, safe="")


def listen(cfg, port=None, seconds=120.0, out=None, token=None, clock=time.time):
    """The --listen mode: bind a free port of the RTP range, print the outside-device commands, wait for one, and
    record a receipt for C2b. Returns the exit status (see the module docstring)."""
    out = sys.stdout if out is None else out

    def say(*lines):
        out.write("".join("[selfcheck-listen] %s\n" % line for line in lines))
        out.flush()

    rng = parse_range(cfg["rtp_range"])
    if rng is None:
        say("JS_RTP_PORT_RANGE=%r is not a valid LOW-HIGH range; cannot listen" % cfg["rtp_range"])
        return 64
    lo, hi = rng
    if port is not None and not lo <= port <= hi:
        say("--port %d is outside JS_RTP_PORT_RANGE %d-%d" % (port, lo, hi))
        return 64
    sock = chosen = None
    in_use = 0
    for candidate in ([port] if port is not None else range(lo, hi + 1)):
        attempt = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            attempt.bind((cfg["bind_host"], candidate))
            sock, chosen = attempt, candidate
            break
        except OSError as err:
            attempt.close()
            if err.errno != errno.EADDRINUSE:
                say("cannot bind UDP %d (%s); see C3" % (candidate, err.strerror or err))
                return 1
            if port is not None:
                say("UDP %d is in use, probably by Janus media; omit --port to pick a free port" % port)
                return 1
            in_use += 1
    if sock is None:
        say("every port of JS_RTP_PORT_RANGE %d-%d is in use; try again when fewer calls are active" % (lo, hi))
        return 1

    token = token or "legion-voice-probe-" + os.urandom(4).hex()
    ufrag = probe_ufrag(token)
    public = check_c1(cfg["state_file"])["_facts"].get("public", [])
    target = public[0] if public else "<this server's public IPv4>"
    if port is not None:
        why = "the port asked for"
    else:
        why = "the lowest free port of JS_RTP_PORT_RANGE %d-%d" % (lo, hi)
        if in_use:
            why += "; %d lower port(s) in use, e.g. by Janus media" % in_use
    lines = ["listening on UDP %d (%s) for %g s" % (chosen, why, seconds),
             "from a device OUTSIDE this network (e.g. a phone on mobile data, not this LAN), run one of these:",
             "  bash -c 'echo %s > /dev/udp/%s/%d'" % (token, target, chosen),
             "  python3 -c 'import socket; socket.socket(socket.AF_INET, socket.SOCK_DGRAM)"
             ".sendto(b\"%s\", (\"%s\", %d))'" % (token, target, chosen),
             "  echo %s | timeout 3 nc -u -w1 %s %d" % (token, target, chosen),
             "(the nc form is for phones and minimal images without bash or python3; busybox nc -u -w1 can hang after "
             "sending, so timeout stops it, or use busybox timeout 3 if timeout is missing)",
             "from a phone with nothing installed: turn its Wi-Fi OFF (mobile data only), send yourself the link below "
             "(e.g. in a message), open it in the phone's browser within the %g s and keep the page open for 20 s. "
             "The page makes the browser's WebRTC send connectivity checks carrying the probe to this port:" % seconds,
             "  " + probe_page_url(ufrag, target, chosen),
             "UDP can drop a packet, so send it two or three times. Waiting..."]
    if not public:
        lines.insert(2, "no public address is advertised (see C1): replace %s with the address outside viewers use"
                     % target)
    elif len(public) > 1:
        lines.insert(len(lines) - 1, "other advertised public addresses: %s" % ", ".join(public[1:]))
    say(*lines)

    deadline = time.monotonic() + seconds
    strays = 0
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            sock.settimeout(left)
            try:
                data, source = sock.recvfrom(4096)
            except socket.timeout:
                break
            if token.encode("ascii") not in data and ufrag.encode("ascii") not in data:
                strays += 1
                continue
            received = clock()
            record = {"schema": 1, "result": PASS, "time": iso(received), "epoch": int(received), "port": chosen,
                      "source": "%s:%d" % source[:2], "advertised": public, "range": "%d-%d" % (lo, hi)}
            error = write_json(cfg["inbound_file"], record)
            say("PASS: the probe arrived on UDP %d from %s at %s" % (chosen, record["source"], record["time"]))
            if error:
                say("could not record it in %s (%s)" % (cfg["inbound_file"], error))
                return 1
            say("recorded in %s: C2b reports PASS until the receipt is older than JS_SELFCHECK_INBOUND_MAX_AGE_H "
                "(%g h)" % (cfg["inbound_file"], cfg["inbound_max_age_h"]),
                "the source shown can be the container runtime's port proxy rather than the outside device")
            return 0
    finally:
        sock.close()
    say("INCONCLUSIVE: nothing carrying the probe arrived on UDP %d within %g s%s"
        % (chosen, seconds, " (%d unrelated packet(s) ignored)" % strays if strays else ""),
        "either it was not sent from outside, or inbound UDP does not reach this server: check the router forward "
        "(UDP %d-%d to this host) and the host firewall. Any earlier receipt is kept." % (lo, hi))
    return 2


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


# ---- C6: TURN for this server -------------------------------------------------------------------

def _turn_type_note(turn_type):
    return ("turn_type: %s. It only sets how Janus reaches the TURN server; the relay speaks UDP toward viewers, so it "
            "is information and never a pass criterion" % turn_type)


def _c6_pass(how, where, relay, public, notes):
    if public:
        notes.append("this server is also publicly reachable per C1, so the relay is a fallback candidate rather than "
                     "the only path")
    return result("C6", PASS,
                  "TURN Allocate on %s with %s succeeded: relay %s:%d. Mixer-side TURN rescues a CGNAT or unreachable "
                  "server by offering viewers this relay as a candidate" % (where, how, relay[0], relay[1]),
                  notes=notes, facts={"relay": "%s:%d" % relay})


def check_c6(jcfg_path, c1, budget):
    """Whether mixer-side TURN is the right remedy for THIS server (C1), and whether the configured TURN works. The
    TURN settings are read from janus.jcfg, so this checks what Janus uses. No reason carries a credential."""
    try:
        values = read_jcfg(jcfg_path)
    except OSError as err:
        return result("C6", INCONCLUSIVE, "cannot read %s (%s)" % (jcfg_path, err),
                      "check the container start log: the entrypoint generates this file")

    def nat(key):
        return values.get(("nat", key))

    public = (c1.get("_facts") or {}).get("public") or []
    server, rest_api = nat("turn_server"), nat("turn_rest_api")
    if not server and not rest_api:
        notes = [_turn_type_note("n/a (no TURN configured)")]
        if c1["status"] in (PASS, WARN) and public:
            notes.insert(0, "viewers whose own network blocks UDP need viewer-side TURN, which this component cannot "
                            "provide (A.4)")
            return result("C6", PASS,
                          "no TURN configured, and this server is publicly reachable per C1 (%s): the server-side media "
                          "path is correct and complete without a relay" % ", ".join(public), notes=notes)
        if c1["status"] == INCONCLUSIVE:
            return result("C6", INCONCLUSIVE,
                          "no TURN configured, and C1 could not tell whether this server is publicly reachable",
                          "resolve C1 first: whether mixer-side TURN is needed depends on it", notes)
        why = "behind CGNAT" if (c1.get("_facts") or {}).get("cgnat") else "not publicly reachable"
        return result("C6", WARN,
                      "no TURN configured, and this server is %s per C1: here mixer-side TURN is the remedy, offering "
                      "viewers a relay candidate on a TURN server they can reach" % why,
                      "configure a TURN server for the mixer: %s. The product ships no TURN server: run your own (coturn "
                      "is the usual choice)." % TURN_KNOBS, notes)
    if budget.expired():
        return not_run("C6", budget)
    if rest_api:
        return _check_c6_rest(rest_api, nat("turn_rest_api_key"), nat("turn_rest_api_method") or "POST", public, budget)
    try:
        port = int(nat("turn_port") or 3478)
    except ValueError:
        return result("C6", FAIL, "turn_port %r in the nat section is not a port number" % nat("turn_port"),
                      "set JS_TURN_PORT to the TURN server's port")
    transport = (nat("turn_type") or "udp").lower()
    notes = [_turn_type_note(transport)]
    where = "%s:%d over %s" % (server, port, transport)
    if not nat("turn_user") or not nat("turn_pwd"):
        return result("C6", FAIL, "turn_server %s is configured without turn_user and turn_pwd" % where,
                      "set JS_TURN_USER and JS_TURN_PWD, or use JS_TURN_REST_API instead", notes)
    try:
        relay = addr_probe.turn_allocate(server, port, transport, nat("turn_user"), nat("turn_pwd"),
                                         min(5.0, max(0.5, budget.remaining())))
    except addr_probe.TurnError as err:
        return result("C6", FAIL, "TURN Allocate on %s with the configured static credentials failed: %s" % (where, err),
                      "check JS_TURN_SERVER, JS_TURN_PORT and JS_TURN_TYPE against the TURN server, the credentials in "
                      "JS_TURN_USER and JS_TURN_PWD, and that the server is reachable from this container", notes)
    return _c6_pass("the configured static credentials", where, relay, public, notes)


def _check_c6_rest(api, key, method, public, budget):
    where_api = addr_probe.redact_url(api)
    remediation = ("check JS_TURN_REST_API, JS_TURN_REST_API_KEY and JS_TURN_REST_API_METHOD, that the backend is "
                   "reachable from this container, and that the TURN URIs it returns are reachable too")
    try:
        username, password, _ttl, uris = addr_probe.turn_rest_request(api, key, method, "legion-voice-selfcheck",
                                                                      min(5.0, max(0.5, budget.remaining())))
    except addr_probe.TurnError as err:
        return result("C6", FAIL, "the TURN REST API gave no usable credentials: %s" % err, remediation,
                      [_turn_type_note("n/a (the TURN REST API's URIs decide it)")])
    usable = [(uri, addr_probe.parse_turn_uri(uri)) for uri in uris]
    usable = [(uri, parsed) for uri, parsed in usable if parsed]
    if not usable:
        return result("C6", FAIL, "the TURN REST API at %s returned no usable TURN URI (%s)"
                      % (where_api, ", ".join(uris) or "none"), remediation,
                      [_turn_type_note("n/a (no usable URI)")])
    notes = [_turn_type_note(", ".join(sorted(set(parsed[2] for _, parsed in usable))) + " (from the REST API's URIs)")]
    failures = []
    for uri, (host, port, transport) in usable:
        if budget.expired():
            failures.append("%s: not tried before the time bound" % uri)
            continue
        try:
            relay = addr_probe.turn_allocate(host, port, transport, username, password,
                                             min(5.0, max(0.5, budget.remaining())))
        except addr_probe.TurnError as err:
            failures.append("%s: %s" % (uri, err))
            continue
        if failures:
            notes.append("earlier URIs failed: %s" % "; ".join(failures))
        return _c6_pass("credentials from the TURN REST API at %s" % where_api, uri, relay, public, notes)
    return result("C6", FAIL, "credentials came from the TURN REST API at %s, but TURN Allocate failed on every URI: %s"
                  % (where_api, "; ".join(failures)), remediation, notes)


# ---- ICE candidate types per handle (from the Admin API) -----------------------------------------

def count_candidate_types(lines):
    counts = dict((kind, 0) for kind in CANDIDATE_TYPES)
    for line in lines or []:
        match = _CANDIDATE_TYPE.search(str(line))
        if match:
            counts[match.group(1)] += 1
    return counts


def _admin_post(url, body, secret, timeout):
    request = urllib.request.Request(url, data=json.dumps(dict(body, transaction="selfcheck-" + os.urandom(4).hex(),
                                                               admin_secret=secret)).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        data = json.loads(reply.read().decode("utf-8"))
    if data.get("janus") == "error":
        error = data.get("error") or {}
        raise ValueError("Admin API error %s: %s" % (error.get("code"), error.get("reason", "")))
    return data


def collect_candidates(admin_url, admin_secret, timeout=5.0, clock=time.time):
    """For every slvoice handle: the ICE candidate types Janus offered (its local candidates), the peer's (remote), and
    the selected pair, from Admin API handle_info. Never raises: an unusable Admin API gives available=False with the
    reason, which never carries the secret."""
    report = {"schema": 1, "time": iso(clock()), "available": False, "reason": None, "handles": [],
              "totals": dict((kind, 0) for kind in CANDIDATE_TYPES)}
    if not admin_secret:
        report["reason"] = "JS_ADMIN_SECRET is not set in this environment"
        return report
    base = admin_url.rstrip("/")
    deadline = time.monotonic() + timeout

    def left():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("the Admin API walk took longer than %g s" % timeout)
        return remaining

    try:
        sessions = _admin_post(base, {"janus": "list_sessions"}, admin_secret, left()).get("sessions") or []
        for session in sessions:
            try:
                handles = _admin_post("%s/%s" % (base, session), {"janus": "list_handles"}, admin_secret,
                                      left()).get("handles") or []
            except ValueError:
                continue   # the session ended between the two calls
            for handle in handles:
                try:
                    info = _admin_post("%s/%s/%s" % (base, session, handle), {"janus": "handle_info"}, admin_secret,
                                       left()).get("info") or {}
                except ValueError:
                    continue   # the handle went away
                if info.get("plugin") != "janus.plugin.slvoice":
                    continue
                ice = (info.get("webrtc") or {}).get("ice") or {}
                specific = info.get("plugin_specific") or {}
                local = count_candidate_types(ice.get("local-candidates"))
                report["handles"].append({"session": session, "handle": handle, "display": specific.get("display"),
                                          "room": specific.get("room"), "local": local,
                                          "remote": count_candidate_types(ice.get("remote-candidates")),
                                          "selected_pair": ice.get("selected-pair")})
                for kind in CANDIDATE_TYPES:
                    report["totals"][kind] += local[kind]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
        report["reason"] = "Admin API at %s: %s" % (base, getattr(err, "reason", err))
        report["handles"], report["totals"] = [], dict((kind, 0) for kind in CANDIDATE_TYPES)
        return report
    report["available"] = True
    return report


def candidates_summary(report):
    if not report.get("available"):
        return "unavailable (%s)" % report.get("reason")
    handles = report.get("handles") or []
    if not handles:
        return "no slvoice handles right now"
    totals = report["totals"]
    parts = ["%d slvoice handle(s); Janus offered host %d, srflx %d, relay %d in total"
             % (len(handles), totals["host"], totals["srflx"], totals["relay"])]
    for entry in handles[:5]:
        parts.append("session %s (%s, room %s): host %d, srflx %d, relay %d"
                     % (entry["session"], str(entry.get("display") or "?")[:8], entry.get("room"),
                        entry["local"]["host"], entry["local"]["srflx"], entry["local"]["relay"]))
    if len(handles) > 5:
        parts.append("%d more in the JSON" % (len(handles) - 5))
    return "; ".join(parts)


def default_collector(cfg, budget=None):
    timeout = 5.0 if budget is None else min(5.0, budget.remaining())
    if timeout < 0.5:
        return {"schema": 1, "time": iso(time.time()), "available": False,
                "reason": "not collected: the self-check time bound was reached", "handles": [],
                "totals": dict((kind, 0) for kind in CANDIDATE_TYPES)}
    return collect_candidates("http://127.0.0.1:%s%s" % (cfg["admin_port"], cfg["admin_base"]), cfg["admin_secret"],
                              timeout)


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

    def positive(key, fallback):
        try:
            value = float(values[key])
        except ValueError:
            return fallback
        return value if value > 0 else fallback

    return {
        "rtp_range": values["JS_RTP_PORT_RANGE"],
        "stun_server": values["JS_STUN_SERVER"],
        "http_port": values["JS_HTTP_PORT"],
        "http_base": values["JS_HTTP_BASEPATH"],
        "admin_port": values["JS_ADMIN_PORT"],
        "admin_base": values["JS_ADMIN_BASEPATH"],
        "admin_bind": values["JS_ADMIN_BIND"],
        "admin_secret": environ.get("JS_ADMIN_SECRET") or "",
        "timeout": positive("JS_SELFCHECK_TIMEOUT_S", 20.0),
        "inbound_max_age_h": positive("JS_SELFCHECK_INBOUND_MAX_AGE_H", 168.0),
        "state_file": environ.get("SLV_ADDR_STATE_FILE") or "/run/legion-voice/public-address.json",
        "jcfg_path": os.path.join(environ.get("JANUS_CONF_DIR") or "/opt/janus/etc/janus", "janus.jcfg"),
        "report_file": environ.get("SLV_SELFCHECK_FILE") or "/run/legion-voice/selfcheck.json",
        "inbound_file": environ.get("SLV_SELFCHECK_INBOUND_FILE") or "/run/legion-voice/selfcheck-inbound.json",
        "candidates_file": environ.get("SLV_SELFCHECK_CANDIDATES_FILE") or "/run/legion-voice/candidates.json",
        "bind_host": environ.get("SLV_SELFCHECK_BIND") or "0.0.0.0",
        "connect_map": connect_map,
    }


def run_checks(cfg, budget, startup=False):
    """C1, C2b and C3 are local and always run. C4, C2a, C5 and C6 touch the network inside the budget; at startup
    C4 first waits for Janus, leaving a reserve of the bound for the rest."""
    c1 = check_c1(cfg["state_file"])
    c3 = check_c3(cfg["rtp_range"], cfg["jcfg_path"], cfg["bind_host"])
    if startup:
        wait = max(0.5, budget.remaining() - min(8.0, budget.seconds / 2.0))
    else:
        wait = min(3.0, budget.remaining())
    c4 = check_c4(cfg["http_port"], cfg["http_base"], budget, wait)
    c2a = check_c2a(cfg["stun_server"], cfg["rtp_range"], budget, cfg["bind_host"], c1["_facts"].get("mapping", []))
    c2b = check_c2b(cfg["inbound_file"], cfg["rtp_range"], cfg["inbound_max_age_h"], c1["_facts"].get("public", []))
    c5 = check_c5(cfg["admin_bind"], cfg["admin_port"], cfg["admin_base"], c1["_facts"].get("public", []),
                  budget, cfg["connect_map"])
    c6 = check_c6(cfg["jcfg_path"], c1, budget)
    return [c1, c2a, c2b, c3, c4, c5, c6]


def worst_status(results):
    return max((r["status"] for r in results), key=lambda status: SEVERITY[status]) if results else PASS


def exit_code_for(results):
    statuses = [r["status"] for r in results]
    if FAIL in statuses:
        return 1
    if INCONCLUSIVE in statuses:
        return 2
    return 0


def build_report(results, mode, when, duration, bound, candidates=None, candidates_file=None):
    counts = dict((status.lower(), sum(1 for r in results if r["status"] == status))
                  for status in (PASS, WARN, FAIL, INCONCLUSIVE))
    checks = [dict((k, v) for k, v in r.items() if not k.startswith("_")) for r in results]
    report = {"schema": 1, "tool": "legion-voice-selfcheck", "mode": mode, "time": when,
              "duration_s": round(duration, 1), "timeout_s": bound, "verdict": worst_status(results),
              "summary": counts, "exit_code": exit_code_for(results), "checks": checks}
    if candidates is not None:
        report["candidates"] = {"available": candidates.get("available"), "reason": candidates.get("reason"),
                                "handles": len(candidates.get("handles") or []), "totals": candidates.get("totals"),
                                "summary": candidates_summary(candidates), "file": candidates_file}
    return report


def render_block(report, report_file):
    """The one bracketed, greppable block: every line starts with [selfcheck]."""
    s = report["summary"]
    lines = ["[selfcheck] ===== BEGIN legion-voice self-check (%s run, %s, %.1f s, bound %g s) ====="
             % (report["mode"], report["time"], report["duration_s"], report["timeout_s"])]
    for check in report["checks"]:
        lines.append("[selfcheck] %-3s %-12s %s" % (check["id"], check["status"], check["title"]))
        lines.append("[selfcheck]     observed: %s" % check["observed"])
        if check["remediation"]:
            lines.append("[selfcheck]     remediation: %s" % check["remediation"])
        for note in check["notes"]:
            lines.append("[selfcheck]     note: %s" % note)
    if report.get("candidates"):
        lines.append("[selfcheck] INFO candidates (from Admin API handle_info): %s; JSON %s"
                     % (report["candidates"]["summary"], report["candidates"]["file"]))
    lines.append("[selfcheck] ===== END legion-voice self-check: worst %s; %d PASS, %d WARN, %d FAIL, %d INCONCLUSIVE; "
                 "exit %d; JSON %s =====" % (report["verdict"], s["pass"], s["warn"], s["fail"], s["inconclusive"],
                                             report["exit_code"], report_file))
    return "\n".join(lines) + "\n"


USAGE = ("usage: legion-voice-selfcheck [--json] [--startup] [--timeout SECONDS]\n"
         "       legion-voice-selfcheck --listen [--port N] [--seconds N]\n"
         "       legion-voice-selfcheck --candidates [--json]\n"
         "       legion-voice-selfcheck --sessions [--json] [--agent UUID-PREFIX] [--room N] [--failed] [--limit N]\n"
         "       legion-voice-selfcheck --session HANDLE|AGENT [--json]\n")


def main(argv=None, environ=None, runner=None, out=None, collector=None):
    argv = sys.argv[1:] if argv is None else argv
    environ = os.environ if environ is None else environ
    out = sys.stdout if out is None else out
    collector = collector or default_collector
    flags = {"json": False, "startup": False, "listen": False, "candidates": False, "sessions": False, "failed": False}
    numbers = {"--timeout": None, "--port": None, "--seconds": None, "--room": None, "--limit": None}
    texts = {"--session": None, "--agent": None}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--json", "--startup", "--listen", "--candidates", "--sessions", "--failed"):
            flags[arg[2:]] = True
        elif arg in texts and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            texts[arg] = argv[i + 1]
            i += 1
        elif arg in numbers and i + 1 < len(argv):
            try:
                numbers[arg] = int(argv[i + 1]) if arg in ("--port", "--room", "--limit") else float(argv[i + 1])
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
    if (numbers["--port"] is not None or numbers["--seconds"] is not None) and not flags["listen"]:
        sys.stderr.write(USAGE)
        return 64
    diag = flags["sessions"] or texts["--session"] is not None
    list_filters = flags["failed"] or texts["--agent"] is not None or numbers["--room"] is not None \
        or numbers["--limit"] is not None
    if (list_filters and not flags["sessions"]) or (flags["sessions"] and texts["--session"] is not None) \
            or (diag and (flags["startup"] or flags["listen"] or flags["candidates"] or numbers["--timeout"] is not None)) \
            or (numbers["--limit"] is not None and numbers["--limit"] < 0):
        sys.stderr.write(USAGE)
        return 64
    if diag:
        path = environ.get("SLV_ICE_DIAG_FILE") or ice_diag.DEFAULT_FILE
        if flags["sessions"]:
            return ice_diag.cli_list(path, out, flags["json"], texts["--agent"], numbers["--room"], flags["failed"],
                                     20 if numbers["--limit"] is None else numbers["--limit"])
        return ice_diag.cli_detail(path, out, texts["--session"], flags["json"])
    cfg = load_config(environ)
    if flags["listen"]:
        seconds = numbers["--seconds"] if numbers["--seconds"] and numbers["--seconds"] > 0 else 120.0
        return listen(cfg, numbers["--port"], seconds, out)
    if flags["candidates"]:
        candidates = collector(cfg, None)
        write_json(cfg["candidates_file"], candidates)
        out.write(json.dumps(candidates, indent=2) + "\n" if flags["json"]
                  else "[selfcheck] INFO candidates (from Admin API handle_info): %s; JSON %s\n"
                  % (candidates_summary(candidates), cfg["candidates_file"]))
        out.flush()
        return 0 if candidates.get("available") else 2
    bound = numbers["--timeout"] if numbers["--timeout"] and numbers["--timeout"] > 0 else cfg["timeout"]
    budget = Budget(bound)
    started = time.monotonic()
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results = (runner or run_checks)(cfg, budget, flags["startup"])
    candidates = collector(cfg, budget)
    write_json(cfg["candidates_file"], candidates)
    report = build_report(results, "startup" if flags["startup"] else "on-demand", when, time.monotonic() - started,
                          bound, candidates, cfg["candidates_file"])
    error = write_json(cfg["report_file"], report)
    text = json.dumps(report, indent=2) + "\n" if flags["json"] else render_block(report, cfg["report_file"])
    out.write(text)   # one write, so the block is not interleaved with other log output
    out.flush()
    if error:
        sys.stderr.write("legion-voice-selfcheck: could not write %s (%s)\n" % (cfg["report_file"], error))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
