# Recipe: home-hosted mixer behind consumer NAT, with DDNS

**For:** a PC at home running the mixer, behind a consumer router with a dynamic public IPv4 and a DDNS name.
The OpenSim region server is on the same LAN, or on the same PC.

**You end with:** a self-check board you can compare line for line with the one below, an inbound proof recorded,
and a first real session in the diagnostics. Anything that differs from the expected board is a real problem.

**Measured on:** Legion Grid, 2026-09-15. Windows 11, Docker Desktop 28.3.0 (WSL2 backend), image `c7b57f56`.
A Linux home server differs in two places, both marked **Linux**.

---

## 1. Before you start

- **A real public IPv4 on the router.** Compare the WAN address on the router's status page with what the internet
  sees (search "what is my IP" from the LAN). If the router's WAN address starts with `100.64.`–`100.127.`, you are
  behind carrier-grade NAT: no forward can reach you, and the self-check's C1 will FAIL. Ask the ISP for a public
  IPv4, or see section 9.
- **A DDNS name** that tracks the router's public address (e.g. `yourgrid.ddns.net`), kept current by the router or
  a DDNS client.
- **A fixed LAN address for the host** (a DHCP reservation on the router). This recipe calls it `HOST_LAN_IP`
  (Legion Grid: `192.168.1.225`).
- **Docker:** Docker Desktop with the WSL2 backend (Windows), or Docker Engine with the compose plugin (**Linux**).
- The two files from the repo, `docker-compose.yml` and `env.sample`, in an empty directory.

## 2. Router forwards

| Forward | To | Why |
|---|---|---|
| **UDP 10000–10200** (WAN) | `HOST_LAN_IP` UDP 10000–10200 (same ports) | WebRTC media from viewers outside |

**Nothing else, for the mixer.** Do **not** forward the HTTP signalling port, the admin port or the WebSockets port:
the region server reaches them on the LAN. (Viewers reaching the region server itself need the grid's own forwards,
which are not part of this recipe.)

NAT loopback (hairpin) on the router is not required: LAN viewers use the private candidate (`JS_KEEP_PRIVATE_HOST`).

## 3. Host firewall

**Windows** (PowerShell as Administrator). This is the rule Legion Grid runs:
```powershell
New-NetFirewallRule -DisplayName "Legion Voice RTP" -Direction Inbound -Protocol UDP -LocalPort 10000-10200 -Action Allow
```
Docker Desktop also installs its own "Docker Desktop Backend" allow rules when it is installed. Leave them.

**Linux:** allow the range, e.g. `sudo ufw allow 10000:10200/udp`. Be aware that Docker publishes ports with its own
iptables rules (the `DOCKER` nat chain, seen during A.6's measurements), so a `ufw deny` does not close a port Docker
published. The admin port is narrowed by `JS_ADMIN_BIND` below, not by ufw.

## 4. `.env`

`cp env.sample .env`, then set **every** value below. Generate each secret with `openssl rand -hex 32` (or any long
random string) and put the same values in the region server's `os-webrtc-janus` config (`APIToken`, `AdminAPIToken`).

| Variable | Value for this target | Why |
|---|---|---|
| `JS_PUBLIC_HOST` | your DDNS name, e.g. `yourgrid.ddns.net` | the start-up discovers the public address from it (STUN first) |
| `JS_PUBLIC_IP` | `HOST_LAN_IP` | kept after the discovered address, so LAN viewers get a LAN candidate |
| `JS_PUBLIC_IP_DISCOVERY` | `auto` (default) | STUN, then DNS via the resolver below, then the container resolver |
| `JS_STUN_SERVER` | `stun.l.google.com:19302` (default) | |
| `JS_PUBLIC_IP_DNS_RESOLVER` | `1.1.1.1` (default) | |
| `JS_PUBLIC_IP_REFRESH_S` | `300` (default) | a home IP changes; the watcher re-checks every 5 min |
| `JS_PUBLIC_IP_CHANGE_ACTION` | **`restart`** | with a dynamic IP, re-advertise the new address without you (it waits for an empty mixer) |
| `JS_PUBLIC_IP_RESTART_MAX_WAIT_S` | `900` (default) | |
| `JS_KEEP_PRIVATE_HOST` | `true` (the default when a public address is set) | LAN viewers without router hairpin |
| `JS_NAT_EXTRA_IPS` | *(unset)* | discovery finds the public address |
| `JS_API_SECRET` | a random secret | required; must match `APIToken` |
| `JS_ADMIN_SECRET` | a different random secret | required; must match `AdminAPIToken` |
| `ALLOW_INSECURE_DEV` | `false` (default) | never on a reachable host |
| `JS_RTP_PORT_RANGE` | `10000-10200` | must equal the router forward and the firewall rule |
| `JS_SERVER_NAME` | any name | shown by `/voice/info` |
| `JS_HTTP_PORT` | `14223` (Legion Grid uses `24223`) | any free port; the region server's `JanusGatewayURI` must match |
| `JS_HTTP_BASEPATH` | `/voice` (default) | |
| `JS_ADMIN_PORT` | `14225` (Legion Grid uses `24225`) | the region server's `JanusGatewayAdminURI` must match |
| `JS_ADMIN_BIND` | `HOST_LAN_IP` | publishes the admin port on the LAN address only (the start-up WARNs when unset) |
| `JS_ADMIN_BASEPATH` | `/voiceAdmin` (default) | |
| `JS_WS_ENABLED` | `false` | the region server uses HTTP only; this narrows |
| `JS_WS_PORT` | `8188` (default; unused with WebSockets off) | |
| `SLV_ECHO_AUTOSTART` | `false` | echo only for a bring-up test |
| `JS_EMPTY_ROOM_GRACE_S` | `60` (default) | |
| `JS_JOIN_MEDIA_TIMEOUT_S` | `30` (default) | |
| `JS_SELFCHECK` | `on` (default) | the board below |
| `JS_SELFCHECK_TIMEOUT_S` | `20` (default) | |
| `JS_SELFCHECK_INBOUND_MAX_AGE_H` | `168` (default) | how long the section 7 proof counts |
| `JS_ICE_DIAG_HISTORY` | `200` (default) | session diagnostics (section 8) |
| `JS_TURN_*` (all eight) | *(unset)* | not needed on a reachable home server (section 9) |


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

Start with the **default profile**. The `recorder`, `injector` and `turn-test` profiles are not part of this recipe and
never start unless named.
```sh
docker compose up -d
```
Keep the shipped **published ports**. On Docker Desktop that is the only working mode. On a **Linux** home server it
is also the recommended one (`docs/docker-notes.md` → "Source addresses (A.6)").

**What the network mode means for you.** On **Docker Desktop**, every viewer's packets reach Janus from the Docker
gateway (measured: `172.23.0.1`), not from the viewer's address. Media is unaffected, but the session diagnostics can
never tell a relayed viewer from a direct one, and show `undetermined`. On a **Linux** home server the viewer's address
arrives and the diagnostics show `direct`.

**Also on Docker Desktop for Windows: after a mixer restart, give it time before you judge it.** A restarted
container can be reachable from *inside* while still unreachable from *outside* for minutes, because the published
ports are re-established one proxy at a time and the mixer publishes a whole UDP media range. Observed on this host
(O-113): the signalling port answered within a second, went away again, and did not answer steadily until **167 s**
with 51 media ports published — against **13–26 s** with 20. A separate restart of the live mixer under load on the
same host recovered in ~31 s, so treat this as a range to expect, not a fixed number. The diagnostic is one pair of
commands — if the first answers and the second does not, nothing is wrong with the mixer and you are waiting on
Docker's port forwarding:

```sh
docker exec <container> curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:14223/voice/info   # inside
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:14223/voice/info                           # outside
```

Not seen on Linux hosts, but never measured there across a restart.

## 6. The expected self-check board

```sh
docker compose logs janus | grep '^\[selfcheck\]'
```

This is Legion Grid's board from 2026-09-15, abridged to the status lines and the key observations:

```
[selfcheck] C1  PASS         advertised address is public
[selfcheck]     observed: nat_1_1_mapping 174.82.163.190,192.168.1.225; public: 174.82.163.190 (discovered by stun)
[selfcheck] C2a WARN         media path, outbound: UDP mapping from the RTP range (information)
[selfcheck]     observed: container-initiated UDP is source-port remapped; ... local 10000 -> 174.82.163.190:63888; ...
[selfcheck] C2b INCONCLUSIVE media path, inbound: UDP from outside reaches the RTP range
[selfcheck] C3  PASS         RTP port range is bindable and matches Janus
[selfcheck] C4  PASS         signalling: HTTP transport answers
[selfcheck] C5  PASS         admin API is not reachable from outside
[selfcheck] C6  PASS         TURN for this server (mixer-side relay)
[selfcheck] ===== END legion-voice self-check: worst INCONCLUSIVE; 5 PASS, 1 WARN, 0 FAIL, 1 INCONCLUSIVE; exit 2; ... =====
```

| Row | Expected here | Legitimate because | If you see something else |
|---|---|---|---|
| C1 | PASS, your public IPv4 "discovered by stun" | | FAIL with CGNAT: section 1. FAIL no public address: `JS_PUBLIC_HOST` wrong or the DDNS name is stale. WARN address change pending: the watcher saw a new IP and restarts when the mixer is empty |
| C2a | **WARN** "source-port remapped" (Docker Desktop). **Linux:** PASS or WARN, both fine | outbound UDP from a published-port container is always translated; it does not affect a forwarded server | FAIL "outbound UDP from the media range is blocked": an outbound firewall rule |
| C2b | **INCONCLUSIVE** until section 7, then PASS | inbound reachability cannot be seen from inside the container | after a proof: a receipt older than `JS_SELFCHECK_INBOUND_MAX_AGE_H`, or the public address changed |
| C3 | PASS | | FAIL: the range in `.env` differs from `janus.jcfg` (a mounted override) or ports cannot be bound |
| C4 | PASS | | FAIL: the container is not serving signalling; read `docker compose logs janus` |
| C5 | PASS "not reachable at <public>:<admin port>" | | **FAIL: the admin API answers on your public address. Remove any router forward to the admin port now.** Note that a router without TCP hairpin also reads as unreachable: confirm from outside once with a phone browser to `http://<public>:<admin port>/voiceAdmin` (it must not load) |
| C6 | PASS "no TURN configured, and this server is publicly reachable" | | WARN: C1 is not public (CGNAT or no address): section 9 |

**Exit codes:** 2 now (C2b INCONCLUSIVE); **0 after section 7** (only C2a's WARN left). A 1 means a FAIL row.

## 7. Prove the inbound path (C2b)

On the Docker host, in the directory with `docker-compose.yml`:
```sh
docker compose exec janus legion-voice-selfcheck --listen
```
It binds a free RTP port, waits 120 s (`--seconds N` for longer) and prints the ways to send the probe **from outside
your network**. The token and port below are examples; use the ones it prints.

- **bash** (a laptop tethered to a phone, a friend's machine, a VPS):
  `bash -c 'echo legion-voice-probe-b87b2819 > /dev/udp/<public-ip>/10000'`
- **python3:**
  `python3 -c 'import socket; socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"legion-voice-probe-b87b2819", ("<public-ip>", 10000))'`
- **busybox nc** (minimal images, Android terminal apps):
  `echo legion-voice-probe-b87b2819 | timeout 3 nc -u -w1 <public-ip> 10000`
- **A phone with nothing installed.** It prints a link starting `data:text/html;charset=utf-8,`.
  1. Turn the phone's **Wi-Fi off**, so it is on mobile data.
  2. Send the link to yourself, e.g. in a message, and open it in the phone's browser while the listener waits.
  3. Keep the page open for 20 s. It uses the browser's own WebRTC to send ICE connectivity checks carrying the
     probe to the port. Nothing is installed and nothing is loaded from anywhere.

  **Status:** verified on Legion Grid with desktop Chrome, which got `PASS: the probe arrived on UDP 10000`. **Not
  yet tried on a real phone:** whether Android Chrome or iOS Safari open a pasted `data:` link is untested. If yours
  refuses it, use one of the command forms from any machine off your network.

**Success:** `[selfcheck-listen] PASS: the probe arrived on UDP 10000 from <source> at <time>`, and the receipt is
recorded. On Docker Desktop the source is always `172.23.0.1` (section 5), which is expected.

**Send it from outside.** A device on your own Wi-Fi can reach the public address through router hairpin and pass
without proving the forward. Then run the self-check again:
```sh
docker compose exec janus legion-voice-selfcheck
```
C2b now reads PASS and the END line `exit 0`. The receipt survives `docker compose restart`, but not a recreate
(`up -d` with a new image or `.env`), after which C2b is INCONCLUSIVE again until you repeat this section.

## 8. First real session

Connect a viewer to a voice-enabled region, speak, then:
```sh
docker compose exec janus legion-voice-selfcheck --sessions
```
**Expected:** your session with outcome `PASS media up (live)`, or `media came up; ended: ...` once you leave. The path
reads **`undetermined` on Docker Desktop** (section 5) and **`direct` on Linux**. A FAIL row carries its reason and the
last state reached; see `legion-voice-selfcheck --session <handle or agent UUID>`.

## 9. TURN: what it does and does not fix

- **Viewer-side TURN is BLOCKED (O-87).** No stock viewer (Second Life or Firestorm) accepts TURN servers or
  credentials from the region server. **A viewer whose own network blocks UDP cannot use voice, and no TURN you buy
  or run changes that today.**
- **Mixer-side TURN (`JS_TURN_*`) rescues a server nobody can reach:** one behind CGNAT, or with no usable forward.
  Janus offers a relay on your TURN server as an extra candidate. On a home server with a working forward (C1 and C6
  PASS) it adds nothing, so leave it unset.
- **TURN over TLS (`JS_TURN_TYPE=tls`) against a real CA-signed certificate is UNTESTED.** A.3 proved TLS only against
  a self-signed test certificate, which does not show whether Janus's TURN client (libnice) validates certificates at
  all. The image carries Ubuntu 18.04's `ca-certificates` bundle (137 certificates, package `20230311ubuntu0.18.04.1`).
  The likely failure is that bundle's availability or age against your certificate chain. If you use it, check C6's
  Allocate and a real session before relying on it.

## 10. Done when

- [ ] C1, C3, C4, C5, C6 PASS; C2a WARN (Docker Desktop) or PASS/WARN (Linux); C2b PASS after section 7; END `exit 0`.
- [ ] `--listen` recorded a probe sent from outside your network.
- [ ] `--sessions` shows a real session with media up.
- [ ] The router forwards only UDP 10000–10200 to the host, and C5 is PASS.
