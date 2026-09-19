# Recipe: VPS or colo server with a public IPv4

**For:** a Linux VPS or colocated server with a public IPv4, either on its own interface or 1:1-NATed by the provider
(an "elastic" or "floating" IP). The OpenSim region server is on the same machine, or reaches it over a private
network.

**You end with:** a self-check board you can compare line for line with the one below, an inbound proof recorded,
and a first real session in the diagnostics.

**Provenance, stated plainly:**
- **Measured (A.6, 2026-09-15):** how a Linux dockerd 28.3.0 treats viewers' source addresses, in all three network
  modes. This ran on an isolated dockerd, not on a real VPS.
- **Not measured on a real VPS in this slice:** the board in section 6. It comes from the self-check's VPS-shape
  unit test (`tests/test_selfcheck.py`, `test_vps_shape_has_no_nat_wording`: C1, C2a, C3–C6 PASS, with no NAT wording
  anywhere), plus how each check works. Where a row could honestly read two ways, both are given.

---

## 1. Before you start

- **The public IPv4**, called `PUBLIC_IP` here. Check whether it sits on the interface (`ip -4 addr` shows it), or the
  interface holds a private address that the provider 1:1-NATs. Both work the same way in this recipe.
- **Docker Engine with the compose plugin** (`docker compose version`). Not Docker Desktop.
- **Where the region server is.** Either on the same machine (the simplest), or elsewhere with a private network to
  this one (a provider VLAN, WireGuard, Tailscale). A region server reaching the admin API over the public internet
  is not a supported shape here (section 6, C5).
- `docker-compose.yml` and `env.sample` from the repo, in an empty directory.

## 2. Provider firewall / security group

| Direction | Protocol, ports | Source | Why |
|---|---|---|---|
| inbound | **UDP 10000–10200** | anywhere (`0.0.0.0/0`) | WebRTC media from viewers |
| inbound | TCP HTTP signalling port (`14223`) | **the region server's address only**, and only if it is not on this machine or the private network | the region server's provisioning calls |
| inbound | TCP admin port (`14225`) | **nobody** from the internet | admin stays on loopback or the private network |

The signalling port carries `JS_API_SECRET` in plain HTTP: the image configures no TLS for its transports. Over the
public internet, prefer the private network.

## 3. Host firewall

Allow the media range (e.g. `sudo ufw allow 10000:10200/udp`). Docker publishes ports with its own iptables rules (the
`DOCKER` nat chain, seen in A.6's measurements), so a `ufw deny` does **not** close a port Docker published. The admin
port is narrowed by `JS_ADMIN_BIND` (section 4) and the provider firewall, not by ufw.

## 4. `.env`

`cp env.sample .env`, then set **every** value below. Generate each secret with `openssl rand -hex 32` and put the
same values in the region server's `os-webrtc-janus` config (`APIToken`, `AdminAPIToken`).

| Variable | Value for this target | Why |
|---|---|---|
| `JS_PUBLIC_IP` | `PUBLIC_IP` | a static public address: used as-is, no discovery |
| `JS_PUBLIC_HOST` | *(unset)* | a DNS name also works, but a static IP needs none |
| `JS_PUBLIC_IP_DISCOVERY` | `auto` (default; unused with a literal IP) | |
| `JS_STUN_SERVER` | `stun.l.google.com:19302` (default) | used by the self-check's C2a |
| `JS_PUBLIC_IP_DNS_RESOLVER` | `1.1.1.1` (default) | |
| `JS_PUBLIC_IP_REFRESH_S` | `300` (default; the watcher only runs for a hostname) | |
| `JS_PUBLIC_IP_CHANGE_ACTION` | `warn` (default) | |
| `JS_PUBLIC_IP_RESTART_MAX_WAIT_S` | `900` (default) | |
| `JS_KEEP_PRIVATE_HOST` | **`false`** | no LAN viewers; advertise only the public candidate |
| `JS_NAT_EXTRA_IPS` | *(unset)* | |
| `JS_API_SECRET` | a random secret | required; must match `APIToken` |
| `JS_ADMIN_SECRET` | a different random secret | required; must match `AdminAPIToken` |
| `ALLOW_INSECURE_DEV` | `false` (default) | |
| `JS_RTP_PORT_RANGE` | `10000-10200` | must equal the provider firewall rule |
| `JS_SERVER_NAME` | any name | |
| `JS_HTTP_PORT` | `14223` (default) | the region server's `JanusGatewayURI` must match |
| `JS_HTTP_BASEPATH` | `/voice` (default) | |
| `JS_ADMIN_PORT` | `14225` (default) | the region server's `JanusGatewayAdminURI` must match |
| `JS_ADMIN_BIND` | **`127.0.0.1`** when the region server runs on this machine; the private-network address otherwise | never the public address |
| `JS_ADMIN_BASEPATH` | `/voiceAdmin` (default) | |
| `JS_WS_ENABLED` | `false` | the region server uses HTTP only |
| `JS_WS_PORT` | `8188` (default; unused) | |
| `SLV_ECHO_AUTOSTART` | `false` | |
| `JS_EMPTY_ROOM_GRACE_S` | `60` (default) | |
| `JS_JOIN_MEDIA_TIMEOUT_S` | `30` (default) | |
| `JS_SELFCHECK` | `on` (default) | |
| `JS_SELFCHECK_TIMEOUT_S` | `20` (default) | |
| `JS_SELFCHECK_INBOUND_MAX_AGE_H` | `168` (default) | |
| `JS_ICE_DIAG_HISTORY` | `200` (default) | |
| `JS_TURN_*` (all eight) | *(unset)* | a public server needs no mixer-side relay (section 8) |

With `JS_ADMIN_BIND=127.0.0.1` the region server must call `http://127.0.0.1:14225/voiceAdmin`.


> **Never put the Janus API behind a proxy that logs query strings.** A long poll carries the API secret as a
> URL query argument, `GET .../voice/<session>?maxev=1&apisecret=...`, and there is no alternative: Janus's HTTP
> transport reads a GET's secret only from query arguments
> (`vendor/janus-gateway/src/transports/janus_http.c:1601`, `MHD_GET_ARGUMENT_KIND`; `token` auth is read the
> same way). Every POST carries it in the JSON body instead, and this repo has tests pinning that
> (`connectors/common/test_apisecret.py`). So an nginx/Caddy/ALB access log in default configuration, or any
> tracing proxy that records full URLs, writes your `JS_API_SECRET` to disk on every poll. If you must proxy,
> strip or mask the query string in the access-log format, and keep the admin API (`/voiceAdmin`, which uses a
> body-carried `admin_secret`) on its own bind address. Ledger O-101.

## 5. Compose profile and network mode

**Default profile:** `docker compose up -d`. The `recorder`, `injector` and `turn-test` profiles are not part of this
recipe.

**Keep the shipped published ports.** Measured on Linux dockerd: published ports preserve each viewer's real address,
so the session diagnostics can tell relay from direct. The two alternatives:
- **`network_mode: host`** also preserves it and removes the per-port `docker-proxy` processes (46–48 for a 23-port test
  mixer). The cost is port control. Janus then binds HTTP, admin and WebSockets on **every** host interface,
  `JS_ADMIN_BIND` stops having any effect, and only the host firewall protects the admin port. Choose it only if you
  firewall every Janus port on the host itself.
- **`"userland-proxy": false`** in `/etc/docker/daemon.json` removes the proxies too (46–48 → 2), keeps port control,
  and changes nothing about addresses. It applies to every container on the host.

Details: `docs/docker-notes.md` → "Source addresses (A.6)".

## 6. The expected self-check board

```sh
docker compose logs janus | grep '^\[selfcheck\]'
```

| Row | Expected here | Legitimate because | If you see something else |
|---|---|---|---|
| C1 | PASS: `PUBLIC_IP` is public | | FAIL no public address: `JS_PUBLIC_IP` unset or private |
| C2a | **PASS** "no address translation" when the address is on the interface; PASS or **WARN "source-port remapped"** behind the provider's 1:1 NAT or Docker's outbound translation. Both are fine | outbound UDP from a published-port container is translated by Docker, and a remap does not affect inbound media | FAIL "outbound UDP from the media range is blocked": an egress rule in the provider firewall |
| C2b | **INCONCLUSIVE** until section 7, then PASS | inbound reachability cannot be seen from inside the container | |
| C3 | PASS | | FAIL: range mismatch (a mounted override) or ports taken |
| C4 | PASS | | FAIL: signalling not served; read the log |
| C5 | PASS "not reachable at `PUBLIC_IP`:14225" | the admin port is bound to loopback or the private network | **FAIL: the admin API answers on the public address.** Set `JS_ADMIN_BIND` (section 4) and recreate the container |
| C6 | PASS "no TURN configured, and this server is publicly reachable" | | WARN: C1 is not PASS |

**Exit codes:** 2 until section 7. After it: 0 (all PASS, or C2a's WARN only). A 1 means a FAIL row.

## 7. Prove the inbound path (C2b)

```sh
docker compose exec janus legion-voice-selfcheck --listen
```
From **any machine outside the provider's network** (your laptop at home, another server), run one of the printed
commands. The token and port below are examples:
- **bash:** `bash -c 'echo legion-voice-probe-b87b2819 > /dev/udp/PUBLIC_IP/10000'`
- **python3:** `python3 -c 'import socket; socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"legion-voice-probe-b87b2819", ("PUBLIC_IP", 10000))'`
- **busybox nc:** `echo legion-voice-probe-b87b2819 | timeout 3 nc -u -w1 PUBLIC_IP 10000`
- **A phone with nothing installed:** open the printed `data:text/html;...` link in the phone's browser and keep it
  open for 20 s (Wi-Fi or mobile data both count as outside a VPS). Verified with desktop Chrome; **not yet tried on a
  real phone browser** (see the home-hosted recipe, section 7).

**Success:** `[selfcheck-listen] PASS: the probe arrived on UDP 10000 from <source>`. On Linux published ports the
source should be the sending machine's public address. That is a consequence of the A.6 measurement, not observed on
a real VPS. Then `docker compose exec janus legion-voice-selfcheck`: C2b PASS, END `exit 0`.

## 8. First real session, and TURN

**First session.** Connect a viewer, speak, then run `docker compose exec janus legion-voice-selfcheck --sessions`.
- **Expected:** your session with media up, and path **`direct`**: its own address reached Janus. This follows from
  the measurement.
- **`undetermined`** would mean the viewer's address was rewritten somewhere in front of the container, e.g. a
  provider load balancer or a proxy. That is worth investigating.

**TURN:**
- **Viewer-side TURN is BLOCKED (O-87).** No stock viewer accepts TURN from the region server, so a viewer whose own
  network blocks UDP cannot use voice. Buying or running TURN does not change that today.
- **Mixer-side TURN (`JS_TURN_*`) only rescues a server nobody can reach.** A VPS with a public IPv4 is not that
  server. Leave it unset.
- **TURN over TLS against a real CA-signed certificate is UNTESTED.** It was proven only against a self-signed
  certificate, which says nothing about whether libnice validates certificates. The image ships Ubuntu 18.04's
  `ca-certificates` (137 certificates). The likely failure is that bundle's availability or age against your chain.
  Verify with C6 and a real session before relying on it.

## 9. Done when

- [ ] C1, C3, C4, C5, C6 PASS; C2a PASS or WARN; C2b PASS after section 7; END `exit 0`.
- [ ] The provider firewall opens UDP 10000–10200 to anyone, signalling only to the region server, admin to nobody.
- [ ] `--sessions` shows a real session with media up and path `direct`.
