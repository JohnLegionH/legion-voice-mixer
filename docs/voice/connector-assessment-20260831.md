# Voice connector (O-40) — ground-truth assessment (2026-08-31)

**Ground-truth map against `tranquillity-develop @ 6d012a40d2` and `legion-voice-mixer @ 0eb38f1`.
Line refs valid at those tips only. Decisions marked OPEN are not decided.**

Branch `feature/voice-visibility-matrix` (sim) / `main` (mixer). Read-only assessment; no code was
changed, nothing was built, no container was touched. Every `file:line` below was read at the named
tips. Mixer paths are relative to the mixer repo root and unqualified C files live under `src/`
(unqualified line refs are `src/janus_slvoice.c`); sim paths are relative to this repo root, and
unqualified `WebRtcVoiceRegionModule.cs` / `WebRtcVoiceServiceModule.cs` live under
`Addons/os-webrtc-janus/`.

**Basis documents:** `Docs/voice/connector-design-brief.md` (DRAFT, basis mixer `b4672ff`,
2026-08-21, Amendment 1 2026-08-22) — this assessment re-verifies it against trees that have since
gained the join backlog, the join-window deferral, the moderation mute channel, the M-A2A slices,
and the whole A2A programme. Inference is marked [INF] with its basis; everything else was read.

---

## 1. Citation drift — brief vs current mixer

Every brief line has drifted (`src/janus_slvoice.c` grew to 3,240 lines across the join-backlog,
deferral, mute-channel and M-A2A work), but **every claim survives**. All paths
`src/janus_slvoice.c` unless noted.

| Claim | Brief line | Current line | Still true? |
|---|---|---|---|
| `incoming_rtp` is a handle-keyed core callback | :2474 | :3034 (decl :223, registered :250) | Yes |
| `gateway->relay_rtp` is the only egress | :2123 | :2678 | Yes |
| Join requires a JSEP offer + SDP negotiation | :1508, :1539 | :1998–2002 ("join requires a JSEP offer"), negotiate :1728, `janus_sdp_generate_answer` :1761 | Yes |
| Per-source PCM in `tick_decode_locked` → `s->decbuf`/`dec_samples` | :2013, :2035–:2049 | :2568; decode :2589–2591; `dec_samples` set :2604 | Yes |
| Pass 1 zeroes `dec_samples` (no raw-PCM inject) | :2018 | :2573 | Yes |
| Per-listener mixdown as stack `frame` before `opus_encode_float` | :2100 | pass 2 body :2787–2850; encode :2655 | Yes |
| `s->excluded` created EMPTY at session-create | :815 | :1072 | Yes |
| Visibility merge is the only population path | :1083–:1135 | `apply_visbatch` :1330 (mute channel parallel at :1548) | Yes, **with one addition**: the deferred store (`27977c8`) now holds a column for a not-yet-joined listener and replays it inside the locked join (:2052–2055). See §6/§7(b) — this matters. |
| Listener-mix exclusion `slv_roster_excludes` | :2276 | :2836 | Yes |
| Back-door insert fires no `push_presence` | :1590–:1593 | join-arm push :2109–2113 (fired :2234); function :655 | Yes — and stronger now: M-A2A-2 pushes the leave on EVERY removal path (`leave_room` :595), but a synthetic insert still bypasses both |
| Later joiner's roster scans `room->participants` | :1565–:1578 | :2056–2098, filtered by the joiner's own exclusions :2095 | Yes |
| `participant_summary` reports `setup` from `webrtc_up` | :1348 | :1837 (`muted` from last SLData `m`, :1838–1839) | Yes — a fake `webrtc_up=1` still shows `setup:true` |
| SLData field set `j,l,sp,sh,lp,lh,m,ug` (+`echo`), no tap/record field | `sldata.h:48–:56` | `sldata.h:47–:57` | Yes — no disclosure affordance has been added |

New facts the brief predates: the join-time **ROOM_FULL cap** (`SLV_MAX_MIX` = 110, :2018–2027) now
enforces the encode-slot constraint deterministically, and `apply_visbatch` **fans a listener entry
out by display match** (:1354–1375) — an exclusion column keyed on an avatar UUID reaches EVERY
session whose `display` equals it.

## 2. Headless peer join contract

**What a join needs today** (:1974–2116): attach a Janus handle to `janus.plugin.slvoice`, then one
message `{"request":"join","room":<int>,"display":<string>}` with a JSEP offer attached. `display`
is optional — but a NULL display fires **no** presence push (:2109) and matches no exclusion
column, so an honest peer must send one. **No secret, token, or authorization of any kind exists on
the join path** — brief Q3 is still exactly open. The joined event returns the answer SDP and the
participant list, already filtered by the joiner's exclusions (:2095–2097).

**Audio relay does not depend on the data channel.** The pass-2 gate is `webrtc_up && media_ready`
only (:2790–2791) — ICE/DTLS up (:3021) plus codec/buffer allocation at join (:2044, :2471).
`dc_open` (:369, set at `data_ready` :3198) gates only presence/backlog JSON sends. An audio-only
peer receives the full mix and simply accumulates `presence_dropped_dc_closed`; the negotiate WARN
at :1824–1827 fires only if the offer HAD a data channel the answer couldn't take.

**A recv-only peer that never sends RTP**: `tick_decode_locked` computes `act` from `last_rtp_us`
within the VAD release window (:2570–2574) — never having sent, it is never active, `dec_samples`
stays 0, `audible=0`, power/VAD zeroed. It contributes nothing to anyone's mix but, passing the
pass-2 gate, receives its own N-minus-one mix every tick — one encode slot, and one seat against
the 110 cap (:2021). In the roster it is `{id, display, setup:true, muted:false}` (:1832–1840) —
an ordinary-looking silent participant.

**M-A2A-3's backlog re-send requires the peer's own `"j"` SLData** (:3116–3117: `SLV_FIELD_J` +
the `backlog_confirmed` CAS). A headless peer that never sends it just doesn't get the re-send —
harmless, since the re-send exists for the stock viewer's observer-attach window and the peer
already gets the roster in the join reply and (if it opens a data channel) the `data_ready` backlog
(:3198–3204).

**What its arrival/departure triggers for others**: a real join pushes `"j"` to every present
participant, filtered per-recipient by THEIR exclusion of the joiner (:2109–2113, :688); departure
pushes `"l"` on every removal path (M-A2A-2, :595). So a properly-joining connector is announced
and un-announced exactly like an avatar — the brief's honest-visibility finding holds and is now
stronger.

## 3. Sim provision path for a non-viewer principal

**Caps-bound layer**: all four handlers are registered per-agent in `OnRegisterCaps`
(`WebRtcVoiceRegionModule.cs:303–334`), with the agent id closed over from the caps registration
(:308–318) — the requester identity is cap-bound, not body-supplied. This layer holds: the
O-29/S-A2A-3 admission (`A2AProvisionAdmission.Decide`, :536, refusal :547–553), all estate/parcel
authorization for `local` (:557–631), the room recording via `OnListenerProvisioned` (:667–673),
and the A2A registry bookkeeping (:675–722). `VoiceSignalingRequest` (:732–778) forwards trickle
to the service and **discards the service's response** (:773–776).

**Direct invocation is possible and clean.** `IWebRtcVoiceService` is an ordinary scene module
interface (`scene.RequestModuleInterface<IWebRtcVoiceService>()`, :482); its
`ProvisionVoiceAccountRequest(OSDMap, UUID, UUID)` (`WebRtcVoiceServiceModule.cs:372`) and
`VoiceSignalingRequest` are callable from any region module with an agent id, an offer-bearing map,
and `channel_type:"local"`. The module creates the session (:425–427), and the service returns
`BuildSuccess {jsep answer, viewer_session, room}`. Reverse ICE: Janus→client trickle events are
dropped (`JanusSession.cs:466`, `:480`, `:532–534` — `OnTrickle` has no subscribers), yet every
live viewer connects through exactly this path, so [INF] the answer SDP as returned carries
sufficient server-side ICE for a full connection — the working viewer path is the evidence.

**What a direct caller bypasses, and must therefore own**: the admission decision (:536) and the
parcel/estate checks (:557–631) never run — this is precisely where the **policy record** must
gate instead; and `OnListenerProvisioned` (:673) never fires, so the caller must record the NPC's
room itself or visibility batches address the estate room by default. **S-A2A-5 IS on the direct
path**: both `viewer_session` lookups go through `TryGetViewerSessionFor`
(`WebRtcVoiceServiceModule.cs:336–349`, enforced at :400), which requires only that the session's
`AgentId` equal the calling id — an NPC's UUID satisfies it; no token is involved. One trap:
`CaptureGenerationToken` (:313–323) reads `sp.ControllingClient.SessionId`, and
**`NPCAvatar.SessionId` returns `UUID.Zero`** (`NPCAvatar.cs:577–580`) — a zero token logs the
"sweepable by ANY close" WARN and makes the session sweepable by any close for that agent
(`VoiceViewerSession.cs:158`). A direct caller should set `ClientSessionId` itself (any stable
non-zero UUID per NPC incarnation). See §7(d).

## 4. NPC presence

A region module creates an NPC through `INPCModule.CreateNPC` (`NPCModule.cs:151–156`, full
overload :158–234): `new NPCAvatar(...)`, a random circuit (:187–188),
`scene.AuthenticateHandler.AddNewCircuit` (:217), `scene.AddNewAgent(npcAvatar, PresenceType.Npc)`
(:218), `CompleteMovement` (:224). **No seed cap**: nothing in that path creates caps or calls
`NewUserConnection`, so `OnRegisterCaps` never fires for an NPC [INF from the absence of any
caps-creating call in :158–234 — consistent with the voice caps never having existed for NPCs].
That is fine: the connector uses the direct service path (§3), not caps.

**Enumerated by the feeder**: yes — `SnapshotAgents` walks `ForEachScenePresence`
(`FeederWorldFromScene.cs:61–67`), and an NPC is a real `ScenePresence` with position, parcel, and
root status. The "registered voice session" filter Amendment 1 cites is the `IsAgentInRegion` gate
at `FeederWorldFromScene.cs:63`, backed by `AgentMembershipByRegion`
(`VoiceViewerSession.cs:94–102`), maintained solely by `AddViewerSession`/`RemoveViewerSession`
(:256–277). So the entire integration delta Amendment 1 predicted is still exactly one step:
create a session via the service and `AddViewerSession` registers membership as a side effect
(the module path at `WebRtcVoiceServiceModule.cs:425–427` already does this). **`IsNPC` /
`PresenceType` is consulted nowhere in the voice path** — zero grep hits across
`Addons/os-webrtc-janus`, still true.

## 5. Policy record

Least new machinery, in order: **(a)** an ini section in `os-webrtc-janus.ini` (the module already
reads its config at `Initialise`) — zero machinery, but static; **(b)** an in-memory registry
loaded from that ini at `Initialise` — the recommended shape; **(c)** a region-DB table —
migrations and a write path, only warranted if records must be granted/revoked at runtime and
persist independently of config. The **A2A invitation registry is the nearest analogue and is the
right shape to copy**: a sealed record class with immutable identity fields and lock-guarded
mutable state, a registry with TTLs and typed transitions (`A2ASessionRegistry.cs:34–103`) — but
note it is deliberately transient (invitations die with the process), whereas a connector policy
record must survive restart, hence "ini-declared, registry-held". `VoiceModerationStore` is the
other in-memory analogue, also deliberately non-persistent. Amendment 1's field list (identity,
scope, may-inject, authorising principal, disclosure state) maps one-to-one onto ini keys.

## 6. Smallest first slice — "recorder"

**Shape**: the sim registers the identity and pushes the policy; the external peer talks to Janus
directly; **no mixer change** (satisfying the brief's design-smell constraint), and no new sim
HTTP endpoint either.

1. **Policy record** — a `[VoiceConnector.<name>]` ini section (NPC name, authorising principal,
   scope = this region's estate room, `may_inject=false`, `disclosed=true`), loaded at
   `Initialise` into an A2A-shaped in-memory registry. No record → nothing below happens.
2. **NPC lifecycle** — a small connector module calls `INPCModule.CreateNPC` at a configured
   position (that position is the parcel identity the matrix reasons about), removes the NPC on
   region close. Named legibly ("Recorder" per the record).
3. **Sim registration** — the module calls the voice service's `CreateViewerSession` /
   `AddViewerSession` for the NPC's UUID (setting a non-zero `ClientSessionId`, §3 trap) and
   records the estate room for it (the `OnListenerProvisioned` equivalent). This flips
   `IsAgentInRegion` true → the feeder emits a real exclusion column for the NPC, computed by
   `VisibilityRules` from its actual parcel/position. The **deferred store closes the brief's GATE
   ordering problem**: a column sent before the peer joins is held and replayed inside the locked
   join, before the roster or any presence (:2052–2055) — the sim can have the connector's
   exclusions IN FORCE at the instant it appears.
4. **External peer process** — a headless WebRTC client (aiortc/GStreamer-class) attaches to
   `janus.plugin.slvoice`, joins the estate room with `display = <NPC UUID>` and a sendrecv audio
   offer it never sends on, and writes the received Opus mix to disk. The display match is what
   binds it to the NPC's column (`apply_visbatch` fan-out, :1354–1375), so `slv_roster_excludes`
   at :2836 culls exactly what the NPC may not hear — the GATE is answered by Amendment 1's own
   mechanism, no new code on the mixer.

**Honestly visible**: real join → `"j"` pushed to everyone, roster row, radar/Nearby-Voice
presence via the NPC — the brief's only honest tap shape, implemented as specified.

**What it does NOT cover**: per-speaker separated audio (still the in-tick tap, still rejected);
injection (Q4, and the record's `may_inject` stays false); per-parcel-room recording (rides O-1's
delivery gap — slice 1 is the estate room only); **mixer-side authorization** — the join is still
ungated (:1974–2002), so the policy record gates what the SIM does, not what a rogue process with
network reach to Janus can do (that is Q3, unresolved, and network isolation of the Janus port
remains the only real gate); disclosure beyond roster presence (Q2/Q6); consent capture (spec
§3.5) and downstream handling of recordings; HG policy (Q5). One encode slot and one seat against
the 110 cap per connector, now enforced at join.

## 7. Findings that change the brief

- **(a) No sim endpoint is needed in slice 1.** The brief's first slice assumed the work was "a
  WebRTC client on the connector side"; this assessment sharpens it: the sim's whole contribution
  is REGISTRATION (NPC presence + voice-session registration + room record + policy gate), and the
  peer joins Janus directly with `display = <NPC UUID>`. No provision relay, no SDP plumbing, no
  new cap or HTTP endpoint on the sim.
- **(b) The deferred store answers the GATE ordering.** The brief's GATE said the tap hears
  everyone until the sim can compute and deliver an exclusion set. Since `27977c8`, a column for a
  not-yet-joined listener is deferred and replayed INSIDE the locked join, before the roster and
  any presence (:2052–2055) — so the sim can mint and push the connector's exclusions BEFORE the
  peer connects, and they are in force from its first tick. The GATE's mechanism problem is
  solved; only the identity decision (Amendment 1, taken) remained.
- **(c) The mixer join is ungated and `display` is trusted.** Anyone with network reach to the
  Janus API can attach, join any room (:1974–2002, no secret/token), and claim any avatar's UUID
  as `display` — inheriting that avatar's exclusion column (fan-out by display, :1354–1375) and
  roster identity. This is PRE-EXISTING and not connector-specific (the viewer path has always
  worked this way; the admin API and Janus port are network-isolated in the shipped topology), but
  the connector work makes it pointed: the policy record gates the sim, not the mixer. **To be
  filed as its own O-item** — it is brief Q3 generalised, and it belongs in the ledger's open
  list, not buried here.
- **(d) The NPC `SessionId == UUID.Zero` trap.** `NPCAvatar.SessionId` returns `UUID.Zero`
  (`NPCAvatar.cs:577–580`), so the generation-token capture (`WebRtcVoiceServiceModule.cs:313–323`)
  would register the connector's voice session as sweepable by ANY close for that agent
  (`VoiceViewerSession.cs:158`) and log the capture-failure WARN on every connector provision. The
  connector module must set `ClientSessionId` to a stable non-zero UUID per NPC incarnation when
  it registers the session.
