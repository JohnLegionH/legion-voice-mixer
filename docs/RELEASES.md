# Releases

One section per deployed change to the voice lane since 2026-09-01, newest first.

- **Mixer releases** are this repo's image (`ghcr.io/johnlegionh/legion-voice-mixer`)
  running on Legion Grid.
- **Sim releases** are the regionserver builds of the tranquillity `os-webrtc-janus`
  addon. They are listed only when they change something a mixer or connector
  operator sees. Sim deploy records live in the tranquillity tree.

Each release has the two subsections the compatibility rule requires
(`docs/docker-notes.md` → "Configuration compatibility rule"): **Behaviour changes on
upgrade** and **One-time migrations**, even when empty. O-items are rows in
`docs/voice/voice-programme-ledger.md`.

---

## Mixer A.3: TURN for the mixer (untagged) — 2026-09-14

| Deployed (CDT) | Image | Rollback tag |
|---|---|---|
| 21:55 | `7539c51c` | `legion-voice-mixer:rollback-pre-a3` (`3b416bbe`, the A.2b image, tagged before the rebuild) |

This is an entrypoint and self-check change; the plugin code is the same as V-4.
- **New knobs:** `JS_TURN_*` configure the mixer's own TURN in two styles, static or REST/ephemeral, never both. A partial configuration is FATAL. Credentials are logged only as `set`/`EMPTY`.
- **C6** judges TURN against C1 and proves configured TURN with a real Allocate.
- **Candidate types:** Janus's host/srflx/relay candidates per handle, read from Admin API `handle_info`, in the report and `/run/legion-voice/candidates.json`.
- **`turn-test` compose profile:** a coturn and a REST backend, test fixture only.
- **Harness S13:** the relay path end to end.
- **`--listen`:** gains a busybox `nc` form.

Mixer-side TURN rescues a CGNAT or unreachable **server**. It does not give a UDP-blocked viewer a path; that is A.4 (sim side).

**Verified:**
- **Image build:** C suites; `test_addr_probe.py` 39 tests, OK; `test_selfcheck.py` 66 tests, OK; `entrypoint_test.sh` 114 passed, 0 failed. The entrypoint suite includes a byte-for-byte golden check against the pre-A.3 `janus.jcfg`.
- **No TURN:** the live `janus.jcfg` is byte-identical to the pre-A.3 container's (sha256 `e38fd435…` both), and the banner reads `turn=none`. **C6 PASS:** "no TURN configured, and this server is publicly reachable per C1 (174.82.163.190): the server-side media path is correct and complete without a relay", with the A.4 note. The rest of the board is unchanged: C1 PASS, C2a WARN, C2b INCONCLUSIVE, C3–C5 PASS; exit 2.
- **TURN configured against `turn-test`:**
  - **REST:** C6 PASS, "TURN Allocate on turn:turn-test:3478?transport=udp with credentials from the TURN REST API at http://turn-test-rest:8089/turn succeeded: relay 172.23.0.3:49163".
  - **Relay candidates on a live handle:** host 3, relay 3 in Janus's local candidates.
  - **Static over TLS** (`turn-test:5349`): C6 PASS with relay `172.23.0.3:49176`, and the S12 session's handle had host 3, relay 3.
  - **No leaks:** the credentials and REST key appear 0 times in the container log.
- **Grid left with no TURN.** `.env` was restored from `.env.bak-a3`, and `turn-test` is torn down.
- **Harness:** 12/12 in 278.3 s. S13: A offered only relay candidates, its ICE agent nominated a relay pair with 0 non-relay sockets, B heard A, and A received audio.

**Findings:**
- **TURN over TLS works** in this Janus with libnice 0.1.21.1 against coturn's self-signed certificate: Janus gathered relay candidates. Whether libnice verifies the certificate is not shown by this test.
- **TURN REST support is compiled in:** the `janus_turnrest_*` symbols are in the binary, and Janus logged `TURN REST API backend: http://turn-test-rest:8089/turn`.
- **Janus core logs REST credentials at raised debug levels.** It logs the REST request URI, key included, at debug level 5 (VERB) and REST-issued credentials at 6 (HUGE). The image runs at the default, 4.
- **coturn rejects a shared-secret username expiring after 2^31.** A 2100 expiry got `Cannot find credentials of user`, so the fixture's static pair expires on 2038-01-01.
- **aioice's `TransportPolicy.RELAY` hides host candidates but still checks from host sockets.** The harness detaches those sockets instead. Closing them breaks DTLS through aioice's `connection_lost`.
- **On a published-port mixer, Janus cannot prove the relay path.** Its selected pair shows the relay's packets as a gateway prflx pair, so S13 proves the relay from the peer's own ICE agent.

### Behaviour changes on upgrade
- **New knobs** `JS_TURN_SERVER`, `JS_TURN_PORT`, `JS_TURN_TYPE`, `JS_TURN_USER`, `JS_TURN_PWD`, `JS_TURN_REST_API`, `JS_TURN_REST_API_KEY` and `JS_TURN_REST_API_METHOD`. Unset, nothing changes: `janus.jcfg` is byte-identical. Set, a partial or mixed configuration refuses to start.
- **C6:** a publicly reachable server with no TURN is now PASS; it was a WARN. A CGNAT or unreachable server with no TURN is WARN. Configured TURN is PASS with the relay address, or FAIL with the reason.
- **Candidate report:** every self-check run adds an `INFO candidates` line, a `candidates` object in the JSON and `/run/legion-voice/candidates.json`. `legion-voice-selfcheck --candidates` prints it alone.
- **`--listen`** prints a busybox `nc` form as well.
- **New `turn-test` compose profile**, a test fixture that only starts when named.

### One-time migrations
- None.

---

## Mixer A.2b: C2 corrected, inbound proof (untagged) — 2026-09-14

| Deployed (CDT) | Image | Rollback tag |
|---|---|---|
| 19:29 | `3b416bbe` | `legion-voice-mixer:rollback-pre-a2b` (`de395537`, the A.2 image, tagged before the rebuild) |

This is an entrypoint-only change; the plugin code is the same as V-4.
- **Why C2 changed.** A.2's C2 failed any container whose container-initiated UDP had its source port
  remapped. That happens on every containerised deployment (Docker Desktop's VM NAT, iptables MASQUERADE),
  while a forwarded server's media rides the inbound forward and the published port, a separate mapping.
- **Now it is split:**
  - **C2a** reports the outbound mapping as information: a remap is WARN, and only blocked outbound UDP
    FAILs.
  - **C2b** is inbound reachability. It is INCONCLUSIVE until
    `docker compose exec janus legion-voice-selfcheck --listen` records a receipt from an outside device,
    and it goes stale after `JS_SELFCHECK_INBOUND_MAX_AGE_H`.
- **C6** warns in one viewer-side sentence.
- **The END line and JSON** carry the worst status.

**Verified:**
- **Image build:** C suites; `test_addr_probe.py` 24 tests, OK; `test_selfcheck.py` 51 tests, OK;
  `entrypoint_test.sh` 94 passed, 0 failed.
- **Startup block on Legion Grid:**
  - C1 PASS.
  - **C2a WARN:** 10000/10100/10200 → 56371/56372/56373.
  - **C2b INCONCLUSIVE:** no receipt.
  - C3, C4, C5 PASS; C6 WARN.
  - `worst INCONCLUSIVE; 4 PASS, 2 WARN, 0 FAIL, 1 INCONCLUSIVE; exit 2`. On demand: exit 2.
- **`--listen --seconds 20`:** bound UDP 10000 and printed the bash and python3 commands for
  `174.82.163.190:10000`. Nothing was sent from outside, so it ended INCONCLUSIVE, exit 2, and wrote no
  receipt. Inbound reachability on this grid is **still unproven**.
- **Harness:** 11/11 in 272.2 s.

### Behaviour changes on upgrade
- **C2 → C2a + C2b.** A deployment A.2 reported as C2 FAIL (exit 1) now reports C2a WARN and C2b
  INCONCLUSIVE (exit 2) until an inbound receipt exists; then exit 0 when nothing else is wrong.
- **C6's no-TURN WARN** no longer reasons from C2.
- **The END line starts with `worst <status>`**; the JSON gains `verdict`. Check ids are
  `C1 C2a C2b C3 C4 C5 C6`.
- **New knob** `JS_SELFCHECK_INBOUND_MAX_AGE_H` (168). New file `/run/legion-voice/selfcheck-inbound.json`,
  written only by `--listen`; it survives a restart but not a container recreate.

### One-time migrations
- None. To move C2b to PASS, run `--listen` once from the Docker host with an outside device at hand.

---

## Mixer A.2: startup self-check (untagged) — 2026-09-14

| Deployed (CDT) | Image | Rollback |
|---|---|---|
| 18:47 | `de395537` | no tag. The A.1 image `d2669777` was dropped from the image store when the rebuild moved `:latest`. Rebuild commit `9831943` for A.1, or use `legion-voice-mixer:rollback-pre-a1` (V-4) with `.env.bak-a1` |

This is an entrypoint-only change; the plugin code is the same as V-4. `legion-voice-selfcheck` checks
six things: C1 the advertised address, C2 the outbound media mapping, C3 the RTP range, C4 signalling,
C5 admin exposure and C6 STUN/TURN.
- **At start:** it runs in the background and prints one `[selfcheck]` block.
- **On demand:** `--json`, exit 0/1/2.
- **Report file:** `/run/legion-voice/selfcheck.json`.

Details are in `docs/docker-notes.md`, "Startup self-check".

**Verified:**
- **Image build:** C suites; `test_addr_probe.py` 24 tests, OK; `test_selfcheck.py` 41 tests, OK;
  `entrypoint_test.sh` 91 passed, 0 failed.
- **Startup block on Legion Grid** (2.3 s, bound 20 s):
  - C1 PASS: `174.82.163.190`.
  - **C2 FAIL:** local 10000/10100/10200 → `174.82.163.190:59257/59255/59258`, remapped. The next run
    mapped them to 49934–49936.
  - C3 PASS; C4 PASS; C5 PASS (admin port not reachable at the public address).
  - C6 WARN: no TURN.
- **On demand:** `legion-voice-selfcheck --json` → the same results, exit 1. Its stdout is identical to
  the report file.
- **Harness:** 11/11 in 272.7 s.
- **Participant poll (A.1 restart action), against the live API with `JS_API_SECRET`, during the
  harness:**
  - counts of 1 and 2 were returned;
  - a wrong secret returned `unauthorized: Unauthorized request (wrong or missing secret/token)`, exit 1,
    no count;
  - during S4's restart it returned `no reply … Connection refused`, exit 1, no count.

**Open finding (C2).** Outbound UDP from the media range leaves with remapped, sequential ephemeral
ports. That fits a remap by Docker Desktop's network layer before the router, but it is not proven. C2
proves the outbound mapping only. Whether inbound UDP 10000-10200 reaches Janus through the router
forward is for an external test (A.6).

### Behaviour changes on upgrade
- **Self-check at every start** (`JS_SELFCHECK=on`, `JS_SELFCHECK_TIMEOUT_S=20`). It only reports:
  it changes no configuration and never delays Janus. Traffic it adds:
  - brief UDP binds on sampled RTP ports;
  - three STUN requests from the media range, plus one from an ephemeral port;
  - one TCP connection per public address to the admin port.
- **New files:** `/run/legion-voice/effective-config.json` (non-secret effective values) and
  `/run/legion-voice/selfcheck.json`.
- **A failed, unauthorised or malformed participant poll** under `JS_PUBLIC_IP_CHANGE_ACTION=restart`
  is now logged with its reason, and is still never counted as zero.
- **New knobs:** `JS_SELFCHECK`, `JS_SELFCHECK_TIMEOUT_S`.

### One-time migrations
- None.

---

## Mixer A.1: address configuration (untagged) — 2026-09-14

| Deployed (CDT) | Image | Rollback tag |
|---|---|---|
| 18:06 | `d2669777` | `legion-voice-mixer:rollback-pre-a1` (`6c443c7d`, the V-4 image) |

It is an entrypoint-only change: the plugin code is as in V-4, and the plugin still reports `1.1.0`. A
hostname in `JS_PUBLIC_HOST` (or `JS_PUBLIC_IP`) is now discovered in this order:
1. STUN;
2. an external DNS resolver;
3. the container resolver.

The first public answer wins. The verdict judges the final `nat_1_1_mapping`. A background watcher
re-checks the address every 300 s. Each start records the resolution as one `ADDRESS_RESOLUTION`
JSON log line and in `/run/legion-voice/public-address.json`. Details are in `docs/docker-notes.md`,
"External access".

**Verified:**
- **Image build:** every C suite, `test_addr_probe.py` (19 tests, OK) and `entrypoint_test.sh`
  (81 passed, 0 failed) ran inside the build.
- **Legion Grid, with `JS_NAT_EXTRA_IPS` commented out** (`.env` backed up to `.env.bak-a1`):
  - STUN `174.82.163.190` (public), DNS via `1.1.1.1` `174.82.163.190` (public), container resolver
    `192.168.1.225` (private); winner stun.
  - `nat_1_1_mapping = 174.82.163.190,192.168.1.225`, verdict `public`.
  - The private-address WARNING is gone.
- **The previous hand fix is no longer needed:** before this, the mapping held the router address
  only because `JS_NAT_EXTRA_IPS=174.82.163.190` was added by hand.
- **Harness:** 11/11 passed in 272.9 s. S4's restart re-ran discovery with the same result.

### Behaviour changes on upgrade
- **A DDNS name that hairpins to the LAN now yields the router's public address** in
  `nat_1_1_mapping` (deliberate: it is the fix).
  - A literal `JS_PUBLIC_IP` is kept after the discovered address; before, `JS_PUBLIC_HOST` overrode it.
  - `JS_PUBLIC_IP_DISCOVERY=static` restores container-resolver-only resolution.
- **Outbound UDP** to `JS_STUN_SERVER` (default `stun.l.google.com:19302`) and
  `JS_PUBLIC_IP_DNS_RESOLVER` (default `1.1.1.1`) at start and every `JS_PUBLIC_IP_REFRESH_S`
  (default 300; `0` disables).
- **Log and verdict changes:**
  - No public address in the final mapping logs an ERROR with remediation (it used to be a WARNING).
  - A 100.64.0.0/10 address logs a CGNAT WARNING (TURN required).
  - The per-address "is private/loopback" WARNING is removed.
- **New knobs:** `JS_PUBLIC_IP_DISCOVERY`, `JS_STUN_SERVER`, `JS_PUBLIC_IP_DNS_RESOLVER`,
  `JS_PUBLIC_IP_REFRESH_S`, `JS_PUBLIC_IP_CHANGE_ACTION` (default `warn`) and
  `JS_PUBLIC_IP_RESTART_MAX_WAIT_S` (default 900).
  - `restart` needs the compose restart policy `unless-stopped` or `always`.

### One-time migrations
- None required. An install that added its router address to `JS_NAT_EXTRA_IPS` as a workaround can
  remove it once the start log shows discovery finding that address. Legion Grid's is commented out
  in `.env`.

---

## Mixer V-2 to V-4 (untagged) — 2026-09-14

Three local builds were deployed on 2026-09-14. The plugin still reports `1.1.0`; none has a
version bump or a tag.

| Slice | Deployed (CDT) | Image | Commits | Rollback tag |
|---|---|---|---|---|
| V-2 | 14:29 | `14ccf97d` | O-81 `15f52cb`, O-82 `36eae88`, O-80 `34935a2` | `legion-voice-mixer:rollback-pre-v2` (`7936e030`, the 1.1.0 image) |
| V-3 | 14:55 | `8f423e86` | O-83 `fc48ea6`, SC-87 `4fbfaf4`, SC-96 `ae159b0` | `legion-voice-mixer:rollback-pre-v3` (`14ccf97d`, the V-2 image) |
| V-4 | 15:16 | `6c443c7d` | harness only: S11 fix `fd2289f`, S12 `dced461`; plugin code as V-3 | none: same plugin as V-3 |

**Verified:**
- **V-2:** the harness (S1–S8, S10) passed 9/9 in 244.0 s.
- **V-3:** 9 passed and S11 failed. That was a harness receive bug, fixed in V-4 (ledger O-86).
- **V-4:** the harness (S1–S8, S10–S12) passed 11/11 in 271.7 s.
- **Regression check:** S11 and S12 both fail against the V-2 image.

### Behaviour changes on upgrade
- **Spatialisation follows the room's flag (O-80, O-83).**
  - A room created with `spatial_audio=false` is a flat mix: no distance cull, falloff or pan. The
    sim creates avatar-to-avatar rooms that way, so A2A calls are heard regardless of distance.
  - A room created without the key is spatial, as it was before O-80.
  - From V-2 (14:29) to V-3 (14:55), a room without the key was flat instead (O-83). Static jcfg
    rooms and the harness's rooms were affected in that window.
  - Room create logs `spatial_audio=true|false`, and adds `(key absent: default)` when the key was
    missing.
- **Voice dots follow what the listener hears (SC-87).** A source that is moderation-muted for a
  listener, muted by that listener, or distance-culled for it now reports `{p:0, v:false}` in that
  listener's power batch. Excluded sources are still omitted.
- **Recording needs an opt-in (SC-96).**
  - The recorder, and the injector with `RECORD=1`, refuse to start until `RECORDING_OPT_IN=yes`.
    This is a new connector knob, default off; the effective value is the peer's first start-up line.
  - A recording peer joins with `"recorder": true`. The mixer logs `[slvoice] RECORDER <display>
    joined/left room <id>` and adds `"recorder": true` to that participant's `listparticipants` row
    and `handle_info`.
- **Quiet but audible mixes are now encoded and sent (O-82).** Encode-skip no longer applies the
  0.02 RMS floor; it skips only an empty or exactly-zero mix.
  - Under the default curve, a talker at speech level (RMS 0.1) beyond ~33 m is now heard.
  - Encode CPU rises for listeners whose only in-range talker fell below the old floor. Opus DTX
    still suppresses silence on the wire.
- **10 ms packets are now mixed correctly (O-81).** A 20 ms sender is unchanged.
- **New knob:** `RECORDING_OPT_IN` (connectors only). No new mixer knobs.

### One-time migrations
- **Recorder, or injector with `RECORD=1`:** once the room has been told, add
  `RECORDING_OPT_IN=yes` to `recorder.env` or `injector.env`, then rebuild the connector image
  (`docker compose --profile recorder build recorder`). The gate lives in the connector image,
  which these slices did not rebuild.

---

## Mixer 1.1.0 — 2026-09-14

Plugin version `1.1.0` (`JANUS_SLVOICE_VERSION` 110). O-items: **O-75** (join-without-media reap).
- Code: `3618e9a` (`fix(mixer): O-75 reap participants with no media 30s after join; harness S10`).
- Release commit: `release(mixer): 1.1.0 — O-75 join-without-media reap` (version bump and these
  notes only).

The O-75 code was first deployed at 07:39 CDT still reporting `1.0.0`; 1.1.0 is the same code under
its own version.

### Behaviour changes on upgrade
- **A participant that joins a room and never establishes media is now removed** after
  `JS_JOIN_MEDIA_TIMEOUT_S` seconds (default 30), exactly as a hangup removes it, logging
  `[slvoice] <display> reaped from room <id>: no media <n>s after join`. Previously such a participant
  persisted for the life of its Janus session, holding a roster row and a mix slot and keeping the room
  from its grace destroy.
  - This default deliberately changes behaviour: the old behaviour is the defect being fixed.
    `JS_JOIN_MEDIA_TIMEOUT_S=0` restores it.
  - A participant that had media and lost it is not affected (the O-56 hangup path).
- **`JS_JOIN_MEDIA_TIMEOUT_S` is a new knob** (default 30, `0` disables). The entrypoint exports it
  and prints its effective value at start.

### One-time migrations
- None.

---

## Mixer 1.0.0 — 2026-09-14 (slice 8b)

Commit `docs(mixer): O-71 resync header/README/docs to the shipped plugin, version
1.0.0; ci: run suites + harness on push/PR; add RELEASES.md`. Deployed 2026-09-14
07:03 CDT (image `bccfd7ef`; full local harness S1–S8 passed against it).

O-items: **O-71** (plugin header, README and docs describe the shipped plugin;
version 1.0.0), **O-76** (CI runs the unit suites and the integration harness on
every push and pull request).

### Behaviour changes on upgrade
- The plugin reports version `1.0.0` (`JANUS_SLVOICE_VERSION` 100) in `/info`, the
  admin API and its init line, instead of `0.9.0`. Anything matching the old
  string needs updating. No runtime behaviour changed.

### One-time migrations
- None.

---

## Mixer hotfix 6m-1 — 2026-09-13 18:55 CDT

Commit `8d56823`. O-items: **O-55** (defaults restored). Reverts slice 6m's two
narrowing defaults and adds the configuration compatibility rule and knob register.

### Behaviour changes on upgrade
- From before slice 6m: only what 6m kept, listed under 6m below (fail-closed
  secrets, SDP logs at VERB, new start-up log lines).
- From slice 6m: the Admin API is published on all interfaces again and the
  WebSocket transport is loaded and published again. `JS_ADMIN_BIND` and
  `JS_WS_ENABLED=false` now narrow only when set.
- The entrypoint's first lines print the effective security/connectivity knobs, and
  a WARNING when the admin API is on all interfaces. Log content only.

### One-time migrations
- None. An `.env` that added `JS_ADMIN_BIND` or `JS_WS_ENABLED` for 6m keeps
  working; those values are valid explicit narrowings.

---

## Mixer slice 6m — 2026-09-13 18:28 CDT

Commit `b96e7b3`. O-items: **O-55** (admin/WS binds), **O-65** (fail-closed secrets),
**O-66** (SDP dumps at VERB, ICE credentials redacted). Also the `nat_1_1_mapping`
RFC 1918 guard and `JS_NAT_EXTRA_IPS`. The image also carried the O-73 integration
harness (`735cae8`, tests only, no runtime change) and a Makefile default-goal fix.

### Behaviour changes on upgrade
- **Blank `JS_API_SECRET` or `JS_ADMIN_SECRET` refuses to start** (exit 1, a FATAL
  naming the keys and `ALLOW_INSECURE_DEV=true`). Before, a blank secret started
  with an open API.
- The plugin's full offer/answer SDP dumps moved from INFO to VERB. Each join still
  logs one INFO line with the pt/ptime.
- A WARNING at start when every `nat_1_1_mapping` address is private or loopback.
- *Reverted 27 minutes later by hotfix 6m-1:* admin published on `127.0.0.1` only,
  and the WebSocket transport off and unpublished. That cut the regionserver off
  `192.168.1.225:24225` until 6m-1.

### One-time migrations
- Set both secrets in `.env` if either was blank.

---

## Sim 1.1.392-alpha+d347102272 — 2026-09-13 15:41 CDT

Regionserver voice assemblies + `OpenSim.Region.OptionalModules` (slices 7a + 7b).
O-items: **O-60** (join/leave/signalling hygiene, service-session reconnect),
**O-72** (provision refusal cache), **O-63** (deterministic connector NPC identity),
plus the console `voice moderation mute` command.

### Behaviour changes on upgrade
- A refused or failed viewer provision is answered from a 5 s cache
  (`RefusalCacheSeconds`, 0 disables). The mixer sees at most one provision attempt
  per agent per 5 s instead of the viewer's ~2 Hz retry storm.
- The sim treats a `leave` answered with NOT_JOINED (487) as success. This is the
  normal answer after the mixer's O-56 hangup-leaves.
- Connector NPC ids are derived (UUIDv5 of grid, region and record name) and stable
  across restarts.

### One-time migrations
- **Connector NPC ids change once.** On the first regionserver restart onto this
  build, each connector NPC's id changes from the last random one to its derived one.
  Re-edit `DISPLAY` in `connectors/recorder/recorder.env` / `connectors/injector/injector.env`
  one last time from the `[CONNECTOR] registered … npc=…` line, then restart the peer.
  Until then the peer is joined under an id the sim no longer moderation-mutes or
  discloses. See `connectors/README.md`.

---

## Sim 1.1.390-alpha+e0f36ea6db — 2026-09-13 13:09 CDT

O-items: **O-74** (a root client close ends the agent's voice sessions in every
region on the instance, not only the root region).

### Behaviour changes on upgrade
- A viewer that quits no longer leaves a ghost participant in neighbour-region
  rooms. Those rooms now empty out and reach the mixer's grace destroy.

### One-time migrations
- None.

---

## Mixer slice 5 — 2026-09-13 11:57 CDT

Commit `1859a7f`. O-items: **O-54** (empty-room grace destroy), **O-56** (hangup
leaves the room), **O-67** (init/room_start failure paths), **O-68** (room-scoped
state reset).

### Behaviour changes on upgrade
- **Empty non-permanent rooms are destroyed** after `JS_EMPTY_ROOM_GRACE_S` (default
  60 s). Before, they lived until the mixer restarted. The sim self-heals: a join to
  a destroyed room answers 485 and the sim re-creates it. `JS_EMPTY_ROOM_GRACE_S=0`
  restores the old behaviour. This is a pre-rule exception, kept.
- A PeerConnection hangup now leaves the room at once: the roster row and the mix
  slot go immediately and a leave notice ("l") is sent. Before, the dead handle held
  both until the session was destroyed. The sim's later `leave` gets NOT_JOINED.

### One-time migrations
- None.

---

## Sim 1.1.388-alpha+7aaab38a03 — 2026-09-13 09:58 CDT

Region module hotfix (fix `0f19e4e584`). O-items: **O-48a**. The O-48 change
shipped at 08:54 made neighbour-region (child-agent) provisions fail silently; the
viewer's no-backoff retry turned that into a ~2 Hz provision storm, filed as O-72.

### Behaviour changes on upgrade
- Child-agent provisions for neighbour regions work again. The O-48 guarantee (the
  parcel comes from the avatar's position, not the viewer's claim) stays.

### One-time migrations
- None.

---

## Sim 1.1.386-alpha+30ef218e22 — 2026-09-13 08:54 CDT

Voice assemblies. O-items: **O-48** (provision parcel from the avatar's
position), **O-50/O-51** (ack'd Janus requests time out; outstanding-request
locking), **O-52** (child-agent close guard for A2A), **O-53** (failed provisions
clean up their Janus session).

### Behaviour changes on upgrade
- A Janus request whose completion event does not arrive within 5 s
  (`RequestTimeoutMs`) now fails with a synthetic `"timeout"` instead of waiting
  forever. This is a pre-rule exception, kept.
- A failed provision removes the Janus session and handle it created. The mixer
  stops accumulating leaked sessions under viewer retries.

### One-time migrations
- None.

---

## Mixer O-49/O-64/O-69/O-70 — 2026-09-12 19:09 CDT

Commit `f54a116`. O-items: **O-49** (moderation mutes in their own set), **O-64**
(SLData merges fields instead of replacing geometry), **O-69** (20 ms packetisation
in the answer), **O-70** (deferred store allocate-then-swap).

### Behaviour changes on upgrade
- The SDP answer's audio line now carries `a=ptime:20` and `a=maxptime:20`.
- An SLData message without geometry (e.g. a volume-slider `ug` or a mute `m`) no
  longer flattens the listener's spatial mix until the next move.
- A moderation mute can no longer be lost when the per-listener `peer_ctl` table is
  full.

### One-time migrations
- None.

---

## Connectors S-CON-6 — 2026-09-01

Commits `87e2076` (injector peer; shared plumbing in `connectors/common`),
`1c37b4c` (recorder wrote the decoder's whole 120 ms plane per 20 ms frame),
`230ac0f` (untrack `__pycache__`). No O-item for the build; its live run filed
**O-61**, **O-62** and **O-63**.

### Behaviour changes on upgrade
- Recorder segments now hold real-time audio. Before, each 20 ms frame wrote the
  decoder's whole 120 ms buffer.
- The recorder and injector images build from `connectors/` (shared `common/`
  package); the compose build context changed accordingly.

### One-time migrations
- None beyond creating `injector.env` from its example if you use the injector.
