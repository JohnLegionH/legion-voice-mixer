# Voice connector (O-40) — build plan

**Authority:** `Docs/voice/connector-assessment-20260831.md` (ground truth against
`tranquillity-develop @ 6d012a40d2` / `legion-voice-mixer @ 0eb38f1`) and
`connector-design-brief.md` Amendment 1 (identity: NPC presence + policy record) and Amendment 2
(D1–D5: authorisation, injection, disclosure, hypergrid, runtime). Line refs below are those
documents' refs and are valid at their tips only.

**Status:** DECIDED items are not to be relitigated. Sim slices end in commits on
`feature/voice-visibility-matrix`; peer slices end in commits on `legion-voice-mixer` `main`
(under `connectors/recorder/`). Deployment is batched later per the ledger's §5.0 conventions
(core Release / voice Debug; mixer-repo containers by compose).

---

## 1. Decided

The decisions live in the brief (Amendment 2, D1–D5) and are not restated here. Plan-level
consequences, fixed for every slice:

- **No mixer change in any slice.** The peer joins `janus.plugin.slvoice` through the ordinary
  Janus client API; exclusions arrive via the existing visibility feed (fan-out by display,
  deferred store for pre-join columns). If a slice appears to need a mixer change, the design is
  wrong (brief §Constraints) — stop and reassess.
- **No new sim HTTP endpoint.** The sim's contribution is registration (assessment §7(a)): NPC
  presence, voice-session registration, room record, policy gate, disclosure. The one outbound
  exception is S-CON-7's HTTP CLIENT call to `InjectSourceUrl` — the sim originates it; nothing
  listens.
- **Scope = the estate room only** in this plan (per-parcel rooms ride O-1, out of scope).
- **The `SessionId == UUID.Zero` trap** (assessment §7(d)) is closed at registration: the
  connector module always sets a stable non-zero `ClientSessionId` per NPC incarnation.

---

## 2. Slices

Baseline for every sim slice: `dotnet test` over the two voice test projects
(`Tests/WebRtcJanusService.Tests`, `Tests/WebRtcVoiceRegionModule.Tests`) at **175/177 and
146/146**, the 2 failures being the known O-20 stall-guard cases. Each slice must leave that at
+N passed with the same two failures and no new ones. Peer slices (S-CON-4, S-CON-6, S-CON-7
connector side) carry their own tests in the mixer repo.

### S-CON-1 — policy record: module, loader, guard
**Files:** new `VoiceConnectorModule` (region module, in the `WebRtcVoiceRegionModule` project);
an ini loader for `[VoiceConnector.<name>]` — keys `Enabled`, `NpcFirstName`, `NpcLastName`,
`Position`, `Scope` (= `estate`, the only value in v1), `MayInject`, `AuthorisedBy`,
`InjectSourceUrl` (optional, S-CON-7); the `NpcNameToken` check at load (a name not carrying the
token → the record is REFUSED with an ERROR naming the record and the token; brief D3(i)); an
A2A-shaped in-memory registry holding the loaded records (`A2ASessionRegistry.cs:34-103` is the
shape to copy — sealed record class, lock-guarded registry); the `AllowNpcVoice` guard in the
service module's provision path (`[WebRtcVoice] AllowNpcVoice`, default false: an NPC-presence
provision is refused except through the connector module's own registration path; brief D2).
**Tests:** loader — full record parses; missing mandatory key refuses; `Enabled=false` skips;
token refusal — a name without `NpcNameToken` never enters the registry; guard — NPC provision
refused when `AllowNpcVoice=false`, connector registration path exempt.
**Live test watches:** none (no live behaviour until S-CON-2); config parse lines at region
start.

### S-CON-2 — NPC lifecycle + voice registration
**Files:** `VoiceConnectorModule` — at region-ready, for each enabled record:
`INPCModule.CreateNPC` at the configured `Position` (`NPCModule.cs:151-156`); on region close,
remove the NPC. Voice registration via the service (assessment §3): create the session for the
NPC's UUID with a **non-zero `ClientSessionId`** (the `SessionId==Zero` trap,
`NPCAvatar.cs:577-580`), `AddViewerSession` (flips `IsAgentInRegion` → the feeder emits the NPC's
column), and record the estate room for it (the `OnListenerProvisioned` equivalent, so visibility
batches address the right room). Push a **moderation mute for the NPC identity iff
`MayInject=false`** (the existing mute channel; brief D2). One INFO line at registration —
`[CONNECTOR] <name> uuid=<uuid> room=<room> inject=<bool>` — the operator configures the peer
from this line (brief D1). `[CONNECTOR]` DEBUG instruments for register / remove / mute-push.
**Tests:** registration sets a non-zero `ClientSessionId`; `IsAgentInRegion` true after
registration and false after removal; the room record lands; the mute push happens iff
`MayInject=false`; removal is idempotent.
**Live test watches:** the INFO line; the NPC visible in-world at the position;
`show voice moderation` (or the admin API `mod_muted_entries`) showing the mute when
`MayInject=false`.

### S-CON-3 — disclosure
**Files:** `VoiceConnectorModule` — (i) attach/detach **region alert** (the peer's mixer join
and leave for the connector's display, or — v1 simplification — the module's own
registration/removal, stated in code comments as the trigger actually used); (ii) **entry
notice** to any agent becoming root in scope while a connector is attached; (iii) **proximity
notice** — first time an agent comes within voice range of a voiced (`MayInject`) NPC, once per
agent per NPC per login session (dedupe keyed agent+NPC+login `SessionId`), one local chat line
stating it is an NPC whose voice is automated or remotely operated. Voice range comes from the
existing spatial constants (the mixer's cull distance / the module's configured equivalent —
cite the actual constant in code, do not invent a new knob).
**Tests:** dedupe — second approach in the same login session is silent, a relog re-arms; entry
notice fires on root transition only (child agents silent); notices carry the record's name.
**Live test watches:** all three layers observed on a stock Firestorm (part of S-CON-5).

### S-CON-4 — recorder peer (mixer repo, `connectors/recorder/`)
**Files (mixer repo):** an aiortc process — Janus HTTP API `create` (session) / `attach`
(`janus.plugin.slvoice`) / `join` with `display=<NPC uuid>` and a **sendrecv** audio offer it
never sends on / event poll / ICE; opens the data channel (so presence flows and
`presence_dropped_dc_closed` stays quiet); writes the received Opus mix to **timestamped WAV
segments**. Env config: `JANUS_URL`, `ROOM`, `DISPLAY`, `OUT_DIR`. `Dockerfile` + a compose
service in the mixer repo's compose file (brief D5). `README` covering config, the INFO-line
handshake with S-CON-2, and the disclosure obligations.
**Tests (mixer repo):** signalling-layer unit tests against canned Janus replies (join accepted,
ROOM_FULL, room gone); WAV segmenter writes valid headers and rolls on the boundary.
**Live test watches:** S-CON-5 is this slice's acceptance.

### S-CON-5 — live verification, recorder
The acceptance run for S-CON-1..4, on a started region with a stock Firestorm present:
- **(a)** roster / radar / Nearby Voice all show the marked NPC name (D3(i) on every surface);
- **(b)** door notices observed on stock Firestorm — attach alert, entry notice (D3(ii));
- **(c)** admin API `handle_info`: the connector's handle shows `datachannel_open` true;
  `mod_muted_entries` carries the connector on EVERY listener when `MayInject=false`;
- **(d)** exclusion honoured: a parcel-banned avatar speaking is **absent** from the recording;
  an allowed avatar is present;
- **(e)** the peer sends test audio with `MayInject=false` → **nobody hears it** (the moderation
  mute holds);
- **(f)** playback of the WAV segments confirms an intelligible room mixdown.
Results are recorded here as a dated addendum; any deviation is recorded against the assessment
before code changes.

### S-CON-6 — injector peer (same codebase, the send path)
**Files (mixer repo):** the send path in `connectors/`: audio source = a PCM/WAV pipe first
(deliberately dumb — the TTS engine and the live mic are just different writers to the same
pipe); Opus-encodes and sends under the NPC identity.
**Verify (live):** a `MayInject=true` record; the injected audio is **spatialised at the NPC's
position**; the name marker shows on the speaking dot; the proximity notice fires for an
approaching avatar (D3(iii)); parcel-excluded listeners do **not** hear it (the inject-side
mirror of the §Scope GATE, brief Q4 — the NPC's matrix row constrains its audio like any
avatar's).
**Tests (mixer repo):** the pipe source under- and over-run behaviour; silence on an empty pipe
(no junk frames).

### S-CON-7 — TTS bridge (last)
**Files:** sim side — a hook on the NPC's chat output (script text) forwarded by the sim **as an
HTTP CLIENT** to the record's `InjectSourceUrl` (no inbound sim endpoint; the sim originates the
call). Connector side — a TTS engine (**Piper as the local default**) rendering the received
text into the S-CON-6 injector's pipe.
**Note — puppeteering (live mic)** is the same send path with a different source writing the
pipe; recorded here as covered by S-CON-6's machinery, **no separate slice**.
**Tests:** the sim-side forwarder — fire-and-forget with a bounded timeout, failure logged and
dropped (a dead connector must not stall chat); connector side — text in, audio out, bounded
queue.

---

## 3. Verification (open items to confirm during the build)

- **aiortc ↔ mixer codec fit:** the mixer answers Opus stereo 48k with the §9 fmtp
  (`stereo=1;sprop-stereo=1;maxplaybackrate=48000`); confirm aiortc's Opus decoder honours it
  and the received mix is stereo, not folded mono.
- **Janus HTTP API reachability:** the shipped container exposes the client API the peer needs
  (the sim uses it today; confirm from the peer's network position, not just the sim's).
- **Answer-carried ICE suffices for aiortc** as it does for the viewer (assessment §3 [INF]) —
  the first S-CON-4 connection proves or falsifies it; if it fails, the peer polls the Janus
  event stream for trickle candidates (client-side change only).
- **The registration-vs-join trigger for the attach alert** (S-CON-3's v1 simplification):
  confirm the chosen trigger's wording cannot claim "recording" while the peer is down; if it
  can, gate the alert on the peer's actual mixer join (admin API poll) in a follow-up.
- **O-45 noise:** the `[JANUS AUDIO BRIDGE] CreateRoom … inconclusive` ERROR (benign, ledger
  O-45) will appear in connector bring-up logs; do not chase it.

## 4. Not in scope

- **Per-speaker separated audio** — the in-tick tap, with its stall and fault risks (brief
  §Tap-and-inject; unchanged).
- **Per-parcel rooms** — rides the O-1 delivery gap; this plan is estate-room only.
- **HG enforcement** — D4 is a provisioning rule owed to the trusted-HG rollout (ledger O-item);
  no code here.
- **A mixer join secret** — the ungated join / trusted display is its own ledger O-item
  (assessment §7(c)); this plan neither fixes nor depends on it.
- **Cloud TTS providers** — Piper local is the default; anything remote raises the downstream-
  sensitivity questions the brief records (transcript/recording custody) and waits.

## 5. Watch list

- Each connector consumes **one encode slot and one ROOM_FULL seat** (cap 110, enforced at
  join) — visible in `janus list rooms`; watch the count at S-CON-5.
- The connector's handle in `handle_info`: `setup:true, muted:false`, `audible` never true while
  recv-only; `presence_dropped_dc_closed` staying 0 once the data channel opens.
- The S-CON-2 INFO line and the mute push (`addressed 1 room(s) [..:excl0+mute1]`-shaped sink
  line) at registration when `MayInject=false`.
- A duplicate-display WARN at the mixer would mean two peers joined under one NPC UUID —
  operator error; the join-time detection (§M) names it.
- The proximity-notice dedupe across relogs (re-arms) and region restarts (registry is
  in-memory; re-arms — acceptable, disclosure repeats rather than lapses).
