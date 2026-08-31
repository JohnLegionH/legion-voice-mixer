# Avatar-to-Avatar voice — ground-truth assessment (2026-08-30)

**Ground-truth map against `b7fbc717fa`. Line refs valid at this tip only. Decisions marked OPEN are not decided.**

Branch `feature/voice-visibility-matrix`. Read-only assessment; no code was changed. Every `file:line` below was read at `b7fbc717fa`. Paths are relative to the repo root; unqualified `WebRtcVoiceRegionModule.cs` means `Addons/os-webrtc-janus/WebRtcVoiceRegionModule/WebRtcVoiceRegionModule.cs`, and the other addon files live under `Addons/os-webrtc-janus/`.

Decided elsewhere and not relitigated here: A2A is another mixer room (no P2P, no client ever sees another client's ICE/SDP/transport); the O-29 deny is not removed — A2A's own server-side authorization replaces it for `multiagent` only; deliverable 1 of the build plan is that authorization model.

---

## 1. The `ChatSessionRequest` handler

Registered at `WebRtcVoiceRegionModule.cs:286-290` (a `SimpleStreamHandler` on a random path, alongside `ProvisionVoiceAccountRequest` :274, `VoiceSignalingRequest` :280, `SpatialVoiceModerationRequest` :295). Handler body `:679-764`. It requires POST (:682), a live scene presence for the *calling* agent (:688), an LLSD body (:695), and two fields: `method` (:705) and `session-id` (:712). The dispatch is `switch (method.ToLower())` at `:719`:

| method string | what it does today | reply |
|---|---|---|
| `decline p2p voice`, `decline invitation`, `start conference`, `fetch history` (`:723-728`) | nothing — one shared `case` fall-through under the comment "we don't know how to handle. Just return OK for now" | bare HTTP 200, empty body, to the requesting agent only |
| `start p2p voice` (`:731-759`) | derives a session id, enqueues one event | HTTP 200 to the requester; one `ChatterBoxSessionStartReply` event to the requester |
| anything else (`:760-762`) | — | HTTP 400 |

There is **no `accept` method** in this switch — the viewer's accept of a P2P voice call is not a `ChatSessionRequest` method the sim ever sees; nothing in this handler models acceptance.

Claims checked against `:731-755`:

- **Session id = XOR of the two agent UUIDs — CONFIRMED** (`:733-734`): `new UUID(otherID.ulonga ^ agentID.ulonga, otherID.ulongb ^ agentID.ulongb)` where `otherID` is the `params` field; if `params` is absent it falls back to `UUID.Random()` (`:736`). XOR is commutative, so both parties derive the same id from each other — but note the field name is `params` (:733), a single UUID.
- **Reply goes to the CALLER ONLY — CONFIRMED**: the ninth argument, `toAgent`, is `agentID` (`:755`); the `IEventQueue` signature is `ChatterBoxSessionStartReply(UUID sessionID, string sessionName, int type, bool voiceEnabled, bool voiceModerated, UUID tmpSessionID, bool sucess, string error, UUID toAgent)` (`Source/OpenSim.Region.Framework/Interfaces/IEventQueue.cs:93-96`). Nothing is sent to `otherID`.
- **`voice_enabled` sent false — CONFIRMED**: fourth argument literal `false` (`:750`); `voiceModerated` is literal `true` (`:751`), `type` = 2 (`:749`), `sucess` = `true` (`:753`), `error` = empty (`:754`), `tmpSessionID` = the viewer-supplied `session-id` (`:752`).
- **Session name = caller's own name — CONFIRMED**: `sp.Name` (`:748`), where `sp` is the *requester's* presence (`:688`).
- **Credential read and discarded — CONFIRMED, but not in this handler.** This handler never touches a credential (grep of the module for "credential": zero hits). The read-and-drop is in the *service*: `Janus/WebRtcJanusService.cs:242` reads `credentials` into `channel_credentials`, which has no further reference in the file (the only hit for that identifier is `:242`).

## 2. `ChatterBoxInvitation`

"Zero callers" — **REFUTED tree-wide, CONFIRMED for voice.** The tree has exactly two callers, both group-chat text: `Addons/OpenSim.Addons.Groups/GroupsMessagingModule.cs:603` and `Source/OpenSim.Region.OptionalModules/Avatar/XmlRpcGroups/GroupsMessagingModule.cs:490`. Nothing under `Addons/os-webrtc-janus` calls it, so no voice callee is ever invited.

The helper exists and needs no new plumbing: `IEventQueue.ChatterboxInvitation(UUID sessionID, string sessionName, UUID fromAgent, string message, UUID toAgent, string fromName, byte dialog, uint timeStamp, bool offline, int parentEstateID, Vector3 position, uint ttl, UUID transactionID, bool fromGroup, byte[] binaryBucket)` — declared `Source/OpenSim.Region.Framework/Interfaces/IEventQueue.cs:89-92`, implemented `Source/OpenSim.Region.ClientStack.LindenCaps/EventQueue/EventQueueGetHandlers.cs:214` (emits event name `ChatterBoxInvitation`, `:219`). The group module's call at `Addons/OpenSim.Addons.Groups/GroupsMessagingModule.cs:603-619` is the working template (note it follows with `ChatterBoxSessionAgentListUpdates` at `:623`, declared `IEventQueue.cs:97`). Obtained per scene via `scene.RequestModuleInterface<IEventQueue>()`, exactly as the voice handler already does at `WebRtcVoiceRegionModule.cs:738`; it delivers to whichever region the *callee* is in only if you resolve the callee's scene — the group module does that through `GetActiveClient(AgentID)` (`:589`).

## 3. `CalcRoomNumber`, `multiagent` arm

`Janus/JanusAudioBridge.cs:195-221`. The `"multiagent"` case (`:207-212`) hashes **only** `pChannelID` then `pChannelType` with `BHasherMdjb2`; region id and parcel are not in the hash (the in-source comment "should add a GridId here" `:209` — ledger O-35). `local` (`:201-206`) hashes region + type + parcel. `FoldHashToRoom` (`:229-230`) makes it a positive int. `SelectRoom` (`:231-246`) creates the Janus room with `pSpatial=false` for multiagent (`Janus/WebRtcJanusService.cs:244` sets `isSpatial = channel_type == "local"`), i.e. `spatial_audio: false` in the create request (`Janus/JanusMessages.cs:513`), description `region/type/parcel/channelID` (`JanusAudioBridge.cs:238`).

**Same room in either direction?** Yes, *iff both provisions carry the same `channel_id`* — the room depends on nothing else. Since the session id the handler mints is the XOR of the two agent ids (`WebRtcVoiceRegionModule.cs:734`), it is symmetric; if the viewer forwards that session id as `channel_id`, A→B and B→A land in one room. The server does **not** enforce that the `channel_id` a viewer presents equals the XOR of its own id and its peer's — see §4.

## 4. The O-29 deny

Commit `d9fa72c351` (2026-08-27, "fail closed on non-local channel_type in voice provisioning"; 2 files, +123/−1). At this tip: predicate `IsProvisionableChannelType(OSDMap map, out string channelType)` at `WebRtcVoiceRegionModule.cs:439-443` — `channelType = map["channel_type"] ?? ""; return channelType == "local"`. Applied at `:495-501` in `ProvisionVoiceAccountRequest`, after the `voice_server_type` check (`:473-482`) and **before** any presence/parcel lookup, room selection or Janus session creation: any request whose `channel_type` is absent or not exactly `local` gets `llsd <undef/>` + HTTP 403 (`:498-499`) and a WARN naming the value (`:497`). The local path then runs the estate/parcel/ban checks `:504-578`, and only after that calls the service (`:581`).

**What a multiagent provision would present** (server-read fields, from the two consumers): `voice_server_type` (`:473`), `channel_type` (`:441`), then in the service `parcel_local_id` (defaulted to `REGION_ROOM_ID` −999 when absent, `Janus/WebRtcJanusService.cs:240`), `channel_id` (`:241` — the session id), `credentials` (`:242`, discarded), `jsep` offer (`:249-254`), plus the framing fields the service module reads: `viewer_session` (`WebRtcVoiceServiceModule/WebRtcVoiceServiceModule.cs:324-335`) and `logout` (`Janus/WebRtcJanusService.cs:226`). A multiagent request carries no `parcel_local_id`. There are no sip/uri fields anywhere on this path. (What the viewer *actually* sends for `multiagent` remains ledger U-13 — unverified sim-side.)

**Routing note the plan must know:** the region module does not hand the request to `WebRtcJanusService` directly. `IWebRtcVoiceService` is `WebRtcVoiceServiceModule`, whose 3-arg `ProvisionVoiceAccountRequest` (`WebRtcVoiceServiceModule/WebRtcVoiceServiceModule.cs:338-394`) sends `channel_type == "local"` to `m_spatialVoiceService` and **everything else to `m_nonSpatialVoiceService`** (`:367-382`); both are loaded from `[WebRtcVoice] SpatialVoiceService` / `NonSpatialVoiceService` (`:83-84`, `:101`, `:112`). Live Legion config points both at `WebRtcJanusService.dll:WebRtcJanusService` (`regionserver\config\OpenSim.ini:138-139`). `WebRtcJanusService`'s own 3-arg overload throws `NotImplementedException` (`Janus/WebRtcJanusService.cs:390-393`); the 4-arg session-bound entry is `:206-208` → `:211`.

**Server-side facts available at the deny point** (`:495`, inside the region module): the requesting `agentID` (cap-bound: the handler is registered per agent at `:286-289`), the `Scene`, the parsed body (so `channel_id`, `channel_type`), and — for a *repeat* provision — the `viewer_session` id. Facts the plan needs that are **not** there: the *other* party of the session, any record that the requester was invited/accepted, or that the `channel_id` equals XOR(self, peer). Two authorization hazards already in the tree that the model must close: (a) the viewer-session lookup binds by id string only — `TryGetViewerSession(viewerSessionId)` (`WebRtcVoiceServiceModule.cs:359`, and `:404` for signaling) never checks `vSession.AgentId == pUserID`, so any agent presenting another agent's `viewer_session` id drives that session; (b) `HasRealViewerSession` (`:324-335`) validates only shape. The session object *does* carry `AgentId`, `RegionId`, `ClientSessionId` (`WebRtcVoice/VoiceViewerSession.cs:52-54`; `ClientSessionId` captured from the live presence at `WebRtcVoiceServiceModule.cs:303-313`), so the binding check is one comparison away.

## 5. Accept/decline state

**None exists for voice; the model is new.** The handler is stateless (§1): `start p2p voice` mints an id and forgets it; the `decline …` methods do nothing; there is no accept path. The only session-state precedent in the tree is group chat: `Addons/OpenSim.Addons.Groups/GroupsMessagingModule.cs:78-79` keeps two in-memory `Dictionary<UUID, List<string>>` — `m_groupsAgentsInvitedToChatSession` and `m_groupsAgentsDroppedFromChatSession` — keyed by group id, with helpers at `:774-803`, and drives membership from IM dialogs `SessionAdd`/`SessionDrop`/`SessionSend`/`SessionGroupStart` (`:544-552`, `:643`, `:669`). That is per-region, in-memory, and keyed on a group rather than a pair; the "invited" side is exactly the state A2A lacks (an invitee added at `:587`, invitation sent `:603`). No ad-hoc (non-group conference) session state exists anywhere.

## 6. Mixer-facing

**Provision/join today for a multiagent room** — behind the deny, but the plumbing exists end to end: `SelectRoom(…, pSpatial:false, parcel −999, channelID)` → `CreateRoom` with `spatial_audio:false`, `is_private:false`, `permanent:false`, `sampling_rate:48000` (`Janus/JanusMessages.cs:508-519`), coalesced per room number process-wide (`Janus/JanusAudioBridge.cs:252-262`); then `JoinRoom` sends `AudioBridgeJoinRoomReq(RoomId, AgentId.ToString())` — the agent UUID is the mixer `display` (`Janus/JanusRoom.cs:73`, `Janus/JanusMessages.cs:539`) — with the viewer's SDP offer (`JanusRoom.cs:75`); the response builder returns the answer, `viewer_session` and `room` (`Janus/ProvisionResponseBuilder.cs:21`). Logout is the same provision with `logout:true` → `LeaveRoom` + `BuildClosed` (`Janus/WebRtcJanusService.cs:226-236`).

**Where a non-spatial room breaks the visibility/mute path — three places:**

1. **`AgentRoomTable` is single-valued.** A successful provision records `resp["room"]` via `OnListenerProvisioned(agent, room)` (`WebRtcVoiceRegionModule.cs:608-612` → `WebRtcVoiceRegionModule/VoiceVisibilityService.cs:144-148` → `WebRtcVoiceRegionModule/AgentRoomTable.cs:36-41`, `Record`, which *replaces* the previous record, `:33-35`). An agent in a parcel room who then provisions an A2A room now resolves to the A2A room — and so does their *spatial* exclusion column.
2. **The partitioner addresses each listener's column at `roomOf(agent)`** (`Visibility/PeerCtlBatchPartitioner.cs:82-137`; `roomOf = record ?? estateRoom`, sink `RoomOf`/`FallbackRoom` at `WebRtcVoiceRegionModule/JanusPeerCtlBatchSink.cs:123-137`) and keeps only same-room sources. After (1), that agent's spatial bans/mutes are sent to the A2A room and dropped from the spatial room — wrong in both rooms.
3. **The pending-join re-send** (`WebRtcVoiceRegionModule/VisibilityBatchSender.cs:102-107`, drained at `:193-235`) fires on *every* provision, so an A2A join re-sends the caller's *spatial* column into the A2A room via the same resolver. The exclusion columns themselves come only from spatial rules — estate voice/ban, parcel ban/restrict, see-avatars, moderation mute (`Visibility/VisibilityRules.cs:15-45`, `:60-65`) evaluated over scene parcels (`WebRtcVoiceRegionModule/FeederWorldFromScene.cs`) — so for the two parties of an A2A room they are at best meaningless and at worst (a parcel ban between the two) would mute the call. The deferral/invalidation triggers are parcel-entry and estate-change only (`VoiceVisibilityService.cs:102-107`, `:219-244`).

Net: the mixer side can host an A2A room today; the sim's per-agent room model cannot represent an agent in two rooms, which the build plan has to decide before the batch path is allowed to see A2A rooms at all.

---

## Architectural decision points

### Room-model fork — OPEN

The per-agent room record (§6, items 1–3) is the load-bearing assumption the visibility/mute path makes: one agent, one room, `record ?? estateRoom`. A2A puts an agent in two rooms at once. Two shapes fit the existing code; neither is chosen here:

- **(a) Partition exclusion.** Keep `AgentRoomTable` single-valued and spatial-only: an A2A provision does *not* call `OnListenerProvisioned(agent, room)` with the A2A room (or the visibility service ignores non-spatial rooms), so spatial columns keep addressing the spatial room and the A2A room never receives a peer_ctl batch. Consequence: no visibility/mute enforcement of any kind inside an A2A room — the two parties hear each other unconditionally, and moderation mutes do not follow them into the call.
- **(b) Session-keyed room table.** Key the record by (agent, session/room) rather than agent, and teach the partitioner that an agent may be a listener in more than one room; A2A rooms then receive their own (probably empty) columns and spatial columns stay in the spatial room. Consequence: touches the partitioner's "one room per agent" invariant (`PeerCtlBatchPartitioner.cs:92-133`, including the fast path at `:127-133`) and the pending-join drain (`VisibilityBatchSender.cs:193-235`).

Marked OPEN. Not decided in this assessment.

### O-35 grid collision in the room derivation — OPEN

`CalcRoomNumber`'s `multiagent` arm (`JanusAudioBridge.cs:207-212`) derives the room from `channel_id` + channel type alone; two grids sharing one mixer collide on the same room number for the same session id (which is itself an XOR of two agent ids, `WebRtcVoiceRegionModule.cs:734`). The authorization model that replaces the deny for `multiagent` must either include the grid in the derivation (which changes room numbers for any existing non-local rooms — none exist in production today because O-29 denies them) or accept the collision explicitly and document why it is tolerable. Marked OPEN. Not decided in this assessment.

### Credential layer

The only credential on the path is read in the *service* (`Janus/WebRtcJanusService.cs:242`) and discarded; the region module — where the deny sits and where `agentID`, `Scene` and the parsed body are all in hand (`WebRtcVoiceRegionModule.cs:495`) — never sees it. If the authorization model uses a credential to prove "I am one of the two parties of this session id", it has to be read and checked at the region-module layer (the deny point), not where it is read today. This is a placement finding, not a decision about whether a credential is the mechanism.

---

## Deviations reported, not designed

- The reply-to-caller-only and invite gap are one defect, not two: the fix point is the `start p2p voice` arm (`WebRtcVoiceRegionModule.cs:731-759`), which has the callee id in hand (`otherID`) and an `IEventQueue`; the callee's scene must still be resolved (group module pattern `Addons/OpenSim.Addons.Groups/GroupsMessagingModule.cs:589`).
- The credential is dropped at the service (`Janus/WebRtcJanusService.cs:242`), not the handler — if the authorization model uses a credential, it is read in the wrong layer to be checked against the session id.
- O-35 (grid-unique room derived from `channel_id` alone) means two grids on one mixer can collide on an A2A room number; the authorization model that "replaces the deny for multiagent only" should include the grid in the room derivation or accept the collision explicitly.

---

## Addendum 2026-08-30 — U-13 resolved by the viewer wire trace

Source: `docs/voice-a2a-wire-trace-20260830.md` in the Firestorm tree (`/d/phoenix-firestorm` @ `a9a34638a3`, branch `fix/voice-webrtc-fixes`). The traced call paths are LL-upstream code, so the findings are the SL-compatible contract, not a Firestorm quirk. The sections above are left as written; where they conflict with this addendum, this addendum governs.

1. **Provision key is `channel`, NOT `channel_id`.** The viewer's ad-hoc/P2P provision body sends the session channel as `channel` (`llvoicewebrtc.cpp:3682`), alongside `credentials`, `channel_type="multiagent"`, `voice_server_type`, `jsep`, and no `parcel_local_id`. §3 ("iff both provisions carry the same `channel_id`") and §4 ("`channel_id` (`:241` — the session id)") assumed `channel_id`; that is what the sim reads (`Janus/WebRtcJanusService.cs:241`), and no viewer ever sends it. Superseded: the `multiagent` room and the authorization lookup must key on `channel`.

2. **`IEventQueue.ChatterboxInvitation` is insufficient for a voice invite.** The helper emits `session_id`, `from_name`, `session_name`, `from_id` and an `instantmessage` block only (`EventQueueGetHandlers.cs:219-227`). The viewer's `ChatterBoxInvitation` handler branches on the body: with `instantmessage` present it treats the event as an IM and auto-POSTs `accept invitation` with `INVITATION_TYPE_INSTANT_MESSAGE` — no incoming-call floater, no voice (`llimview.cpp:5047-5195`). A voice invite must carry a `voice` map (`invitation_type: 2`, `channel_uri`, `channel_credentials`) plus top-level `session_id`, `session_name`, `from_id`, `from_name` (`llimview.cpp:5196-5214`); the only sim-side path that can emit that shape is the generic `queue.BuildEvent("ChatterBoxInvitation", body)` + `Enqueue`, as `GroupsMessagingModule.cs:706` already does for its start reply. §2's "the helper exists and needs no new plumbing" stands for text invites only.

3. Two further wire facts the plan depends on: the caller's credentials are obtained with `ChatSessionRequest` `method: "call"` and expected in that POST's HTTP response body as `voice_credentials { channel_uri, channel_credentials }` (`llvoicechannel.cpp:623-688`), a method the sim's switch does not have (it returns 400, §1); and `voice_enabled` in `ChatterBoxSessionStartReply` is never read by the viewer, so §1's `voice_enabled=false` finding is inert on the wire.