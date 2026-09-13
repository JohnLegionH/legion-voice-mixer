# WebRTC Voice Audit — 2026-09-09 (amended 2026-09-11, 2026-09-12)

**Artifact type:** Audit report — FROZEN at the basis below. Findings are transcribed into
`voice-programme-ledger.md` §4.1 as O-48…O-71 (rows in §6); amend the ledger, not this file.
Lives in `Docs/voice/` and is mirrored to `legion-voice-mixer:docs/voice/` per the O-28 rule.

**Basis** [SRC: `git log`, `diff -rq`]:
- Sim: `JohnLegionH/OpenSim-Tranquillity` `feature/ais-v3` at `691a52bb9cb077ecc9414d798fabb052e2930b91`
  (2026-09-08, *merge: upstream/develop (a8b956742c) into feature/ais-v3*). `Addons/os-webrtc-janus/`
  and `Docs/voice/` are byte-identical to `feature/voice-visibility-matrix` at `cb141dd`, so every
  line cite below is valid on both branches.
- Mixer: `JohnLegionH/legion-voice-mixer` `main` at `230ac0fd071297811dd3a570e780b9a46e232975`
  (2026-09-01, *chore(connectors): untrack __pycache__*; includes S-CON-6 `87e2076`).
- Ledger: `voice-programme-ledger.md` last reconciled 2026-09-01 (S-CON-4). O-1…O-47 were read
  before auditing; nothing below duplicates an existing O-item. Where a finding sharpens one, the
  O-item is named.

**Method.** Source read only, in the claude.ai sandbox from a shallow clone of both repos — no
build, no test run, no live capture. Every finding is therefore **[SRC: source]**, never
**[SRC: live]**, except where §8 records the 2026-09-12 live read. Surface covered: all 24
`.cs` files under `Addons/os-webrtc-janus/` (10,624 lines), `janus_slvoice.c` / `sldata.c` /
`visbatch.c` / `deferred.c` (3,877 lines), the compose file and entrypoint, `connectors/common/`,
and both `os-webrtc-janus.ini*`. Line numbers are against the basis commits and go stale on
rebase — cite by subject when amending.

**Severity.** High = security or enforcement failure, or a permanent resource loss per
occurrence. Medium = correctness defect with a plausible live trigger, or unbounded growth.
Low = latent, cosmetic, or defence-in-depth.

---

## 1. High

### W-1 — Provisioning trusts the viewer's `parcel_local_id` for authorization AND room choice (parcel spoof)

- **Where:** `WebRtcVoiceRegionModule/WebRtcVoiceRegionModule.cs:596-635`;
  `Janus/WebRtcJanusService.cs:256`; `Janus/JanusAudioBridge.cs:237-241` (`CalcRoomNumber` local arm);
  `Visibility/VisibilityRules.cs:16-48`.
- **What.** The CAP handler resolves `scene.LandChannel.GetLandObject(parcelID)` from the body
  (`:598`), runs `AllowVoiceChat` / `IsRestrictedFromLand` / `IsBannedFromLand` against THAT parcel
  (`:612-631`), and forwards the same id to the service, which hashes it into the room number
  (`:256` → `CalcRoomNumber` → `:241`). `sp.AbsolutePosition` is never consulted; the presence
  lookup at `:588` only proves the agent exists. Vivox and FreeSwitch derive the parcel from
  `scene.GetLandData(avatar.AbsolutePosition)` (`parcel-voice-semantics.md` §3.1). The in-source
  comment at `:565` — `//do fully not trust viewers voice parcel requests` — is not fulfilled.
- **Two spoofs.** (a) Send parcel B's id while standing on A: authorization runs against B, the
  agent joins B's per-parcel room. (b) Omit `parcel_local_id`: the whole `if(map.TryGetInt(...))`
  block at `:596` is skipped — no `AllowVoiceChat`, no ban/restrict — and the service defaults to
  `REGION_ROOM_ID` (-999), the estate room.
- **Why the matrix does not close it.** `VisibilityRules.Excluded` encodes moderation, ban/restrict
  (symmetric) and SeeAVs; there is no same-parcel rule — channel separation is the ROOM's job, by
  design. `SourceVoiceAudible` (`:64`) does use the source's *actual* parcel, so a voice-disabled
  parcel's occupant is still culled as a source; but nothing stops a listener from hearing a
  neighbouring per-parcel channel from outside it. Bound: `SLV_CUTOFF_DIST_M` = 60 m
  (`janus_slvoice.c:174`) — i.e. exactly the adjacent-parcel case SL's per-parcel channel exists
  to prevent.
- **Impact.** Parcel voice privacy is client-honour-system. Any modified viewer (or a hand-crafted
  POST with the agent's own caps) eavesdrops on an adjacent private parcel's channel.
- **Fix direction.** Derive the parcel server-side from `sp.AbsolutePosition` (the Vivox pattern),
  use the client value as a hint only (log a WARN on mismatch), and make an absent id take the
  position path too. Room hash input becomes the server-derived local id — no room renumbers for
  honest clients. **FIXED IN CODE 2026-09-12, `7191e9b6a1` (ProvisionParcelResolver + tests,
  170/170), not deployed.**

### W-2 — Moderation mute shares the 32-slot `peer_ctl` table and is silently dropped when it is full

- **Where:** `janus_slvoice.c:1520-1538` (`set_mod_muted_locked`), `:2986-3013`
  (`apply_peer_ctl_locked`), `:423` (`peer_ctl[SLV_MAX_PEER_ADJ]`), `sldata.h:39`
  (`SLV_MAX_PEER_ADJ 32`), `:580-604` (`leave_room` clears `excluded`, not `peer_ctl`).
- **What.** `mod_muted` (the sim's moderation-mute channel, Option A) lives inside `slv_peer_ctl`,
  the same 32-entry array that holds every viewer-issued per-avatar mute/gain. `:1528-1529`:
  `if(L->n_peer_ctl >= SLV_MAX_PEER_ADJ) return FALSE; /* table full: drop (source stays audible;
  bounded) */`. Entries are never evicted, and `leave_room` empties only `excluded` (`:600-602`),
  so `n_peer_ctl` grows monotonically for the life of the session, across rooms. The stock viewer
  re-sends stored `volume_settings.xml` gains as `ug` for every participant it has an entry for
  (the O-37 mechanism) — a long-lived listener fills 32 slots without ever muting anyone.
- **Impact.** For that listener, a moderator's mute of a source reaches the mixer and is discarded
  with no error, no counter (`mod_muted_entries` reports what was applied, not what was refused).
  The 3b brief (`phase3b-design-brief.md:271`) accepted "a 33rd explicit mute simply falls back to
  default gain" for VIEWER adjustments; that predates `mod_muted` moving into the same array.
- **Fix direction.** Give the mute channel its own per-session set (`GHashTable`, the `excluded`
  pattern, cleared at leave) and read it in the mix loop alongside `peer_ctl.muted`; add a
  `peer_ctl_full_drops` counter to `query_session`. Mixer-only. In slice 2.

### W-3 — Ack'd Janus requests wait forever; with `.Result` a lost event pins a caps thread permanently

- **Where:** `Janus/JanusSession.cs:266-276` (ack → `await outstandingRequest.TaskCompletionSource.Task`),
  `:217` (`RequestTime` stored, never read), `:124-169` (`DestroySession`), `:602-629` (GETERROR
  exit), `:641-646` (loop exception); `Janus/WebRtcJanusService.cs:218`, `:352` (the `.Result`s,
  O-32).
- **What.** Any request Janus answers with `ack` (join, create, leave, destroy on the plugin
  handle) parks a `TaskCompletionSource` in `_OutstandingRequests` and awaits it with no timeout
  and no cancellation. Nothing completes or faults the pending entries when the long-poll exits
  (`running=false` at `:627`/`:644`), when `DestroySession` runs, or when Janus drops the event
  (session destroyed between ack and event — e.g. `Handle_Hangup` → `DisconnectViewerSession` →
  `Shutdown` racing a join). The awaiting `ProvisionVoiceAccountRequestBAD` never returns, so
  `ProvisionVoiceAccountRequest(...).Result` never returns, so the HTTP handler thread is gone
  for the process lifetime.
- **Impact.** One caps thread per occurrence, forever. Rare per event, permanent per hit; the
  sim's HTTP server pool is finite.
- **Fix direction.** `Task.WhenAny(tcs.Task, Task.Delay(timeout, ct))` with `AdminTimeoutMs`-class
  timeout (5 s) returning a synthetic error resp; fault every outstanding TCS in `DestroySession`
  and at long-poll exit. Pairs with W-4 (same file, same dictionary). In slice 3.

## 2. Medium

### W-4 — `_OutstandingRequests` is locked in one place and unlocked in four
- **Where:** `JanusSession.cs:367` (`lock` in `TryGetOutstandingRequest`); `:253` Add, `:270`
  TryGetValue, `:273`/`:280`/`:287` Remove — all unlocked.
- **What.** The long-poll's event task (`:561`, `:573`) takes the lock; `SendToJanus` on the same
  session does not. A concurrent send and event on one `JanusSession` (any session that carries
  more than one request in flight — the service's `_ViewerSession` during `janus list rooms`,
  `:487`, is the reliable reproducer) corrupts the `Dictionary`.
- **Fix.** Lock every access; fold into the W-3 slice.

### W-5 — A2A registry marks a party "gone" on ANY `OnClientClosed`, including child-agent closes
- **Where:** `WebRtcVoiceRegionModule.cs:196-210` vs `WebRtcVoiceServiceModule.cs:216-220`.
- **What.** The region module's handler calls `m_a2aSessions.MarkGoneSessions(clientID, null)`
  (`:198`) with no presence lookup and no `IsChildAgent` check. The service module's handler for
  the same event explicitly returns on `sp == null || sp.IsChildAgent` (`:219`) with the comment
  "child teardown (border crossing / draw distance) — the agent's voice lives in its root region".
  With three neighbouring regions in one process, a party's CHILD agent closing in Transylvania
  flips `CallerProvisioned=false` on a live Ebony call (`A2ASessionRegistry.cs:193-194`); the record
  then removes on the other party's next logout with a LEAVE the departed party never sent, and a
  re-ring between the pair is unsuppressed while the call is still up.
- **Fix.** Copy the `:219` guard. In slice 4.

### W-6 — Failed provisions leak a registry entry, a Janus session and a plugin handle per viewer retry
- **Where:** `WebRtcVoiceServiceModule.cs:459-473` (`CreateViewerSession` + `AddViewerSession`
  BEFORE the service provisions); `WebRtcJanusService.cs:232` (return value of
  `ConnectToSessionAndAudioBridge` ignored), `:281-285`, `:298-317`, `:320-330` (failure arms leave
  the session registered and connected).
- **What.** On room-full (409), JoinRoom-failed, room-selection-failed or no-jsep, the
  `JanusViewerSession` stays in `ViewerSessions` with a live `JanusSession` + `AudioBridge` handle,
  and the failure map carries no `viewer_session`, so the viewer can never log it out. It is swept
  only by `CaptureSessionsForClose` at client close. `IsAgentInRegion` (`VoiceViewerSession.cs:65`)
  meanwhile reports the agent as voiced, so the matrix carries a column for an agent in no room.
  The stock viewer retries a failed spatial provision with backoff; each retry is a new entry,
  session and handle. Sub-case: if `ConnectToSessionAndAudioBridge` fails at `:232`,
  `viewerSession.AudioBridge` is null and `:279` throws `NullReferenceException` — logged by O-43
  now, but it is a 500, not a failure map, and the half-built `JanusSession` (created, plugin
  attach failed) is never destroyed.
- **Fix.** Register only on success (or remove + shutdown on every failure arm); check the `:232`
  return and answer `BuildFailure("janus unavailable")`. In slice 4.

### W-7 — Rooms and their 50 Hz tick threads are never destroyed (one per parcel ever provisioned, one per A2A pair)
- **Where:** `JanusAudioBridge.DestroyRoom` (`:192`) — zero callers [SRC: grep];
  `WebRtcJanusService.cs:235` (the TODO); `janus_slvoice.c:526-544` (`room_create` starts the
  ticker), `:1924-1965` (the only destroy path), `:2937-2952` (ticker loops until stopped).
- **What.** `per-room-visibility-emission-design-brief.md:409` records "rooms are never destroyed
  on empty" as a fact, not a defect, and before A2A the population was bounded by parcels. Since
  S-A2A-4 every distinct avatar pair that has ever called each other owns a room
  (grid id + XOR session id) with its own `GThread` waking 50×/s to lock an empty table, for the
  life of the mixer process. Unbounded over uptime; each thread also pins a stack.
- **Fix.** Mixer: on the last leave, arm a grace timer (e.g. 60 s) and destroy the non-permanent
  room if still empty (stop ticker, drop from `rooms`). MUST land together with O-68 (§8). In slice 5.

### W-8 — Compose publishes the Admin API and WebSocket transports on all host interfaces
- **Where:** `docker-compose.yml` ports block; `docker-entrypoint.sh:131-140`.
- **What.** `"${JS_ADMIN_PORT}:${JS_ADMIN_PORT}/tcp"` binds 0.0.0.0 (live: 24225). The admin
  transport is the `peer_ctl_batch` entry point — who hears whom — guarded only by
  `JS_ADMIN_SECRET`, and is reachable from wherever the host is. The WS transport (`ws=true`,
  `:139`, live 8188) is enabled and published though the sim uses HTTP only. Sharpens O-46.
- **Fix.** Bind admin to `127.0.0.1:`/LAN, `ws=false` unless a consumer appears. In slice 6.

### W-9 — `hangup_media` does not leave the room; a dead PeerConnection stays a roster row and a capacity slot
- **Where:** `janus_slvoice.c:3227-3240`.
- **What.** On PC death the plugin clears `webrtc_up`/`dc_open`/`backlog_confirmed` and stops
  echo — nothing else. Stock audiobridge's `hangup_media` removes the participant and emits
  `leaving`. Here the participant persists (media buffers allocated, `SLV_MAX_MIX` slot held,
  visible in `list`/roster) until the SESSION is destroyed, which depends on the sim's long-poll
  being alive AND `TryGetViewerSessionByVSSessionId` succeeding. Connector peers and any orphan
  get neither. Bound: the Janus core session timeout (60 s without keepalive) — but a sim that is
  alive and simply never destroys the session is not bounded.
- **Fix.** Call `janus_slvoice_leave_room` + `media_free_locked` from `hangup_media`; the sim's
  later `leave`/destroy becomes idempotent (`NOT_JOINED`). Sharpens O-13. In slice 5.

### W-15 — The shipped `os-webrtc-janus.ini` (not just the `.example`) lacks `StunServers`, `AllowNpcVoice`, and the connector records
- **Where:** `os-webrtc-janus.ini` (28 keys read by code, 9 present [SRC: key diff]);
  `os-webrtc-janus.ini.example:16-26` carries `StunServers` with the correct warning;
  `WebRtcVoiceRegionModule.cs:217-225`.
- **What.** O-21 covers the three visibility keys, `AdminTimeoutMs` and `PluginName`. Beyond those,
  `StunServers` is absent from the non-example ini, and the code comment at `:220` states the
  consequence: stock viewers REQUIRE a non-empty `stun-servers` SimulatorFeature or
  `CreatePeerConnection` fails "ICE server parsing failed: Empty uri". A fresh operator using the
  shipped ini gets a voice service no stock viewer can connect to. `AllowNpcVoice` and the
  `[VoiceConnector.<n>]` block exist only in the `.example`.
- **Fix.** Fold into the O-21 fix: make the `.ini` a copy of the `.example` with the live
  defaults. In slice 6.

## 3. Low

- **W-10** — `VoiceViewerSession.VoiceServiceSessionId` getter THROWS (`VoiceViewerSession.cs:18-22`)
  and `TryGetViewerSessionByVSSessionId` (`:217`) enumerates it across every entry: one non-Janus
  session in the registry breaks every hangup lookup. Latent in the region-local topology; live on
  the O-39 grid-mode path.
- **W-11** — Long-poll exits on any GETERROR that is not 404/400/499 (`JanusSession.cs:621-628`,
  e.g. 0 "Connection refused", 503): a transient Janus blip tears down every viewer session
  sim-side via `OnDisconnect`; the loop's own exception arm (`:641-646`) exits WITHOUT
  `OnDisconnect`, so that session is orphaned silently (only reachable if `GetFromJanus`'s internal
  catches are bypassed — practically unreachable today, but the asymmetry is a trap).
- **W-12** — A failed `JoinRoom` leaves `viewerSession.Room` set (`WebRtcJanusService.cs:298-317`),
  so a later logout sends `leave` for a never-joined room; `JanusRoom.LeaveRoom` always returns
  false and discards the reply (`JanusRoom.cs:91-104`).
- **W-13** — `VoiceSignalingRequestBAD` silently drops the singular `candidate` form (`:371-373`,
  empty `else`) and returns `{response:error}`; `:368` NREs if `Session` is null (signalling before
  a successful provision). `ChatSessionRequest` has no child-agent check (`:801`): a child agent can
  ring from a neighbour region.
- **W-14** — The service-level `_ViewerSession` never reconnects after a Janus restart
  (`Handle_Hangup` finds no viewer session for it, `:186-194`); `janus info` / `janus list rooms`
  go dead until sim restart. `_roomCreateLocks` (`JanusAudioBridge.cs:222`) grows one
  `SemaphoreSlim` per room number forever (same population as W-7).
- **W-16** — Connector peers (`connectors/common/peer.py:64-99`) have no reconnect: `create()`
  with no readiness wait on Janus, stop on `hangup`, exit on poll exception. Compose
  `restart: unless-stopped` turns every case into a container restart, which works but means a
  mixer restart produces a burst of container restarts and a gap = restart backoff. Acceptable;
  recorded so nobody files it as a mixer bug.

## 4. Verified clean [SRC: source]

- `sldata.c` parse: length-capped (8 KB), `json_loadb` with explicit length, per-key UUID
  truncation to `SLV_UUID_LEN-1`, peer table capped at 32 with silent drop (see W-2 for the
  consequence). No overflow. (The whole-struct assignment in the CALLER is O-64 — §8.)
- `visbatch.c` parse: 64 KB cap (O-2), 128 entries × 128 excl caps, UUID length-checked before
  copy, calloc per entry with OOM tolerated. `slv_vis_parse_channel` assigns `*out_entries`/`*out_n`
  (`:122-123`).
- `deferred.c`: 256-entry store, oldest-evicted (`evicted` counter), `channel_update` assigns
  `*col`/`*n_col` on every path (`:99-147`); no leak, no double free. (OOM-on-REPLACE ordering
  is O-70 — §8.)
- Jitter buffer (`janus_slvoice.c:2508-2566`): payloads > 1500 dropped before `memcpy`; slot index
  is `seq % SLV_JB_SLOTS`; `int16_t` seq arithmetic for wrap. (20 ms assumption is O-69 — §8.)
- Handler is single-threaded (`:923` one `slvoice handler` thread) → the `SLV_MAX_MIX` capacity
  check at `:2018-2028` has no TOCTOU.
- Lock order room→session is consistent across tick (`:2757-2790`), `push_presence`, `apply_visbatch`
  (`:1338`, `:1383`), `apply_mutebatch`, `leave_room` (`:583-604`), `incoming_data` (`:3121-3132`),
  and the destroy arm; `rooms_mutex` is always outermost.
- Destroy arm (`:1924-1965`): stops the ticker before evicting, transitions each session's `room`
  under its own mutex, unrefs once per transition. Correct as far as refcounts go (state reset
  is O-68 — §8).
- `negotiate` (`:1728-1832`): Janus SDP utilities throughout; Opus pt from `janus_sdp_get_codec_pt`;
  no hand-rolled string parsing.
- `A2ASessionRegistry`: every public method takes `_lock`, sweeps TTL first, iterates a snapshot
  where it mutates. `Decline` refuses non-parties.
- `VoiceViewerSession` registry: every access under `lock(ViewerSessions)`; the membership index is
  reference-keyed so `UpdateViewerSessionId` cannot desynchronise it.

## 5. Not covered

- Test suites not executed in the sandbox; slice 1 (2026-09-12) measured the region-module suite
  at 167/167 baseline → 170/170 after, on `9402cf6550`.
- `janus_slvoice.c` pass-2 DSP (`:2800-2935`), `encode_relay`, and the sender thread
  (`:2248-2393`) were skimmed for locking only, not for audio correctness.
- `JanusAdminClient.cs`, `PeerCtlBatchPartitioner.cs`, `VoiceModerationStore.cs` persistence: read
  by grep only.
- The two S-CON-6 live findings (O-62, O-63) are the operator's 2026-09-01 observations
  [DOC: memory notes], not re-derived here.

## 6. Ledger amendment — rows for §4.1 (paste after O-47)

*Rows O-48 – O-63 from the 2026-09-09 audit; O-64 – O-71 from the 2026-09-11 reconciliation (§8).
All [SRC: source] at `691a52bb9c` / `230ac0f` unless marked.*

| ID | Item | Status | Recorded in |
|---|---|---|---|
| O-48 | **Provisioning trusts the viewer's `parcel_local_id`** for authorization and room choice; position never checked; absent id skips every parcel check; matrix has no same-parcel rule (audit W-1) | **FIXED IN CODE `7191e9b6a1` 2026-09-12, not deployed** | audit §1 |
| O-49 | **Moderation mute shares the 32-slot `peer_ctl` table** (never cleared on leave) and is silently dropped when full; a long-lived listener becomes un-moderatable (W-2) | open, **HIGH** — slice 2 | audit §1 |
| O-50 | **Ack'd Janus requests have no timeout**; a lost event + `.Result` pins a caps thread permanently; `RequestTime` unused; pending TCSs never faulted on disconnect (W-3) | open, **HIGH** — slice 3; sharpens O-32 | audit §1 |
| O-51 | `_OutstandingRequests` locked in one method, unlocked in four (W-4) | open — slice 3 | audit §2 |
| O-52 | A2A `OnClientClosed` handler lacks the `IsChildAgent` guard the service module has; child closes mark a live party gone (W-5) | open — slice 4 | audit §2 |
| O-53 | Failed provisions leak registry entry + Janus session + handle per viewer retry; `IsAgentInRegion` true for an agent in no room; `:232` connect failure NREs (W-6) | open — slice 4 | audit §2 |
| O-54 | Rooms and 50 Hz tick threads never destroyed; unbounded since A2A (one per pair) (W-7) | open — slice 5, MUST ship with O-68 | audit §2; per-room brief §7 |
| O-55 | Compose publishes admin (live 24225) and WS (8188) on 0.0.0.0; WS unused by the sim (W-8) | open — slice 6; sharpens O-46 | audit §2 |
| O-56 | `hangup_media` does not leave the room (diverges from audiobridge); dead PC holds roster row + capacity slot until session destroy (W-9) | open — slice 5; sharpens O-13 | audit §2 |
| O-57 | Shipped `os-webrtc-janus.ini` lacks `StunServers` (stock viewers cannot `CreatePeerConnection`), `AllowNpcVoice`, connector records — beyond O-21's five keys (W-15) | open — slice 6, folds into O-21 | audit §2 |
| O-58 | `VoiceViewerSession.VoiceServiceSessionId` throws; `TryGetViewerSessionByVSSessionId` enumerates it (W-10) | open, low — live on the O-39 path | audit §3 |
| O-59 | Long-poll exit asymmetry: GETERROR default arm tears down all sessions on a Janus blip; exception arm exits without `OnDisconnect` (W-11) | open, low | audit §3 |
| O-60 | Failed join leaves `Room` set; `LeaveRoom` always false; singular `candidate` dropped; signalling NRE on null `Session`; `ChatSessionRequest` no child-agent check; service `_ViewerSession` never reconnects; `_roomCreateLocks` unbounded (W-12/13/14) | open, low — slice 7 sweep | audit §3 |
| O-61 | Connector peers have no reconnect; rely on compose restart (W-16) | recorded, **not a defect** | audit §3 |
| O-62 | **Injected (connector) audio is not spatialised** — no distance fade, the NPC's stream is position-less in the mixer [DOC: operator, S-CON-6 live run 2026-09-01] | open — ground-truth pass owed | S-CON-6 notes; audit §5 |
| O-63 | **Connector NPC UUID changes on every regionserver restart**, forcing a peer `DISPLAY` re-edit [DOC: operator, 2026-09-01]. Consequence noted 2026-09-12: a peer still joined under the OLD UUID is not the NPC the sim moderation-muted at registration, so a stale injector plays unmuted and undisclosed until its container is restarted | open — slice 7: deterministic identity (UUIDv5 of grid+region+record name; fixed-id `CreateNPC` overload) | S-CON-6 notes; audit §5, §8 |
| O-64 | **SLData overwrites geometry**: `incoming_data` assigns `last_data` as a whole struct (`:3078`); a message without `sp`/`lp` flattens the mix until the next geometry update. Live read 2026-09-12: stock viewer sends SLData only on change and always with `sp,sh,lp,lh`, so the trigger is a separately-sent `m`/`ug` map (volume slider or mute while stationary) and it heals on the next camera/avatar move | open, **MEDIUM, self-healing** — slice 2 | §8; ChatGPT audit 09-11 |
| O-65 | `docker-entrypoint.sh:33,103` starts with blank `JS_API_SECRET`/`JS_ADMIN_SECRET` | open — slice 6: fail closed unless `ALLOW_INSECURE_DEV=true` | §8 |
| O-66 | Full offer/answer SDP logged at INFO per join (`:1756`, `:1822`) | open — slice 6: `LOG_VERB` + redact ICE creds | §8 |
| O-67 | `room_start` result ignored (`:541`); init thread failure leaves `initialized=0` so destroy refuses cleanup (`:981-1003`) | open, low — slice 5 | §8 |
| O-68 | Destroy arm nulls `room` without clearing room-scoped state (`:1955` vs `leave_room:600`) | open, latent — MUST land with O-54; `reset_room_state()` — slice 5 | §8 |
| O-69 | 20 ms packetisation assumed (`:2590-2604`); answer advertises `minptime=10`, no `ptime`/`maxptime` | open, latent — slice 2: `a=ptime:20`, `a=maxptime:20` | §8 |
| O-70 | `deferred.c` REPLACE frees before alloc; OOM empties instead of "unchanged" (`:134-139`) | open, trivial — slice 2 | §8 |
| O-71 | Source header/README say Phase 1B / mixing out of scope; version 0.9.0 | doc debt — slice 8 (O-28 resync) | §8 |
| O-46 (amend) | Add: sim-issued join capability (avatar, room, generation, expiry) as defence-in-depth against a leaked `JS_API_SECRET`; the viewer never reaches the Janus API, so not critical | status unchanged | §8 |

## 7. Work order (AI timescale, CC wall-clock minutes anchored to the 09-01/09-02 actuals)

| # | Slice | Repo | Est. | Stop at | Status |
|---|---|---|---|---|---|
| 1 | O-48 parcel derivation from position (+ tests) | sim | 25 | 50 | DONE 2026-09-12, 7 min, `7191e9b6a1` — not deployed |
| 2 | O-49 mute set + O-64 geometry merge + O-70 + O-69 ptime | mixer | 30 | 60 | issued 2026-09-12 |
| 3 | O-50 + O-51: timeout on ack'd requests, fault pending on destroy/exit, lock every dictionary access | sim | 25 | 50 | |
| 4 | O-52 + O-53: child-agent guard; register-on-success; `:232` return check | sim | 25 | 50 | |
| 5 | O-54 + O-56 + O-68 + O-67: empty-room grace destroy with `reset_room_state()`; `hangup_media` leaves; init cleanup | mixer | 35 | 70 | |
| 6 | O-55 + O-65 + O-57 + O-66: compose binds; fail-closed secrets; ini = example; SDP log level | both | 15 | 30 | |
| 7 | O-60 sweep + O-63 deterministic NPC identity | sim | 25 | 50 | |
| 8 | O-71 doc resync + push/PR CI + aiortc two-peer integration test | mixer | 30 | 60 | |

Sim slices deploy together (regionserver restart, Debug voice DLLs per the convention); mixer
slices are one container rebuild each. Slice 6 needs no deploy beyond a compose `up -d`.

## 8. Addendum 2026-09-11/12 — reconciliation with a second audit, and the live read

A second, independent audit of the mixer ZIP (ChatGPT, 2026-09-11) was verified claim-by-claim
against `230ac0f`. Its scope was the mixer only; it did not see the sim side.

| Its claim | Verdict | Evidence |
|---|---|---|
| Display trusted at join → "Critical"; wants a sim-issued capability | Overstated; already O-46. The viewer never touches the Janus API — the sim proxies every join and sets `display` (`WebRtcJanusService.cs:298`, `JanusRoom.cs:57`). Only `JS_API_SECRET` holders (sim, connector envs) can claim an identity. Defence-in-depth, Medium. | `:1974-2046` |
| Entrypoint starts with blank secrets | Confirmed, new → O-65 | `docker-entrypoint.sh:33-34, 103-104` |
| SLData overwrite wipes geometry | Confirmed, new → O-64; the one finding the 09-09 audit missed | `:3078-3080`, `:2696-2698`, `docs/sldata-extensions.md:79` |
| Full SDP at INFO | Confirmed → O-66 | `:1756`, `:1822` |
| Init failure leaks; `room_start` result ignored | Confirmed, rare → O-67 | `:541`, `:981-1003` |
| Room destroy bypasses `excluded` clear | Confirmed, latent → O-68; becomes live with O-54. Its list is partly wrong: `mod_muted` lives in `peer_ctl`, never cleared on any transition — that is O-49 | `:1955` vs `:600` |
| Tick holds `room->mutex` through encode | Known design (scaling-assessment §3); agree not to rewrite now nor add HRTF first | `:2757-2917` |
| 1 packet = 20 ms assumed | Confirmed, latent → O-69; enforce via `ptime`/`maxptime` rather than a FIFO | `:2590-2604`, `:1797` |
| `deferred.c` REPLACE frees before alloc | Confirmed, comment/OOM nit → O-70 | `deferred.c:134-147` |
| README/header say Phase 1B, version 0.9.0 | Confirmed → O-71 | `:34-39`, `:74` |
| CI only on tags; wants push/PR CI + aiortc integration test | Agree; slice 8 | `.github/workflows` |

**Live read 2026-09-12 [SRC: live, admin `handle_info` on Legion's handles, Ebony]:** the stock
viewer sends SLData only on change (`data_msgs_received` 2 and 7 over several minutes) and every
message carried `sp,sh,lp,lh`. O-64 therefore triggers only on a separately-sent `m`/`ug` map
while stationary and heals on the next camera/avatar move — Medium, not High. The same read
showed one avatar legitimately holding two mixer handles (Ebony room `226001844` plus a
neighbour-region room `1578726032`, identical `rtp_in_count`) because the LL viewer opens a
spatial connection to each adjacent region — not a leak. Live endpoints: `24223/voice`,
`24225/voiceAdmin` (not the `env.sample` defaults).
