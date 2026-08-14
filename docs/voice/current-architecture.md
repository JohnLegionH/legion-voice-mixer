# WebRTC Voice — Current Architecture (os-webrtc-janus)

**Purpose.** Read-only inventory of the WebRTC/Janus voice implementation as it exists in
this tree: the region-side capability module, the service-dispatch layer, the two provisioning
topologies (region-local Janus vs. Robust grid service), the Janus API surface actually used,
the config surface, the C# seams for swapping the Janus plugin, and the upstream/local-patch
status of these files. It proposes no redesign. Where the code is dead or a comment is stale,
that is called out explicitly. Companion to `parcel-voice-semantics.md`; that doc covers the
parcel/estate/presence gating semantics (the "voice ON ⇔ …" rule, per-listener gaps) and is
not repeated here.

## Citation baseline

| | |
|---|---|
| Tree | `D:\tranquillity-develop` |
| Branch | `feature/membership-tiers` |
| Commit | `0bdeb0bf08cb1f5c9921f28b0293a5d82eba9cea` (`0bdeb0bf08`) |
| Commit date | 2026-08-12 17:57:42 -0500 |
| Working tree | only untracked `Docs/voice/` at survey time |

All `path:line` citations are relative to the tree root and were read at this commit. Line
numbers drift if the tree is re-based or edited. The addon lives under
`Addons/os-webrtc-janus/`, split into four projects: `WebRtcVoiceRegionModule/` (viewer-facing
caps), `WebRtcVoiceServiceModule/` (spatial/non-spatial dispatcher), `WebRtcVoice/` (interfaces,
session registry, region↔Robust connectors), and `Janus/` (Janus REST client and audiobridge
logic).

---

## SECTION 1 — Every file and its role

### Region side (loaded in the simulator as `ISharedRegionModule`s)

| File | Role |
|---|---|
| `WebRtcVoiceRegionModule/WebRtcVoiceRegionModule.cs` | Viewer-facing cap module. Registers `ProvisionVoiceAccountRequest`, `VoiceSignalingRequest`, `ChatSessionRequest` caps; advertises `VoiceServerType=webrtc`; runs the parcel/estate voice gate; forwards to the scene's `IWebRtcVoiceService`. |
| `WebRtcVoiceRegionModule/PluginRegistration.cs` | Registers `WebRtcVoiceRegionModule` into `/OpenSim/RegionModules` (`:37`). |
| `WebRtcVoiceServiceModule/WebRtcVoiceServiceModule.cs` | The dispatcher. Registers itself as the scene's `IWebRtcVoiceService` (`:150`); loads a *spatial* and *non-spatial* leaf service from config; routes each request to the right leaf and owns the viewer-session registry lookups. |
| `WebRtcVoiceServiceModule/PluginRegistration.cs` | Registers `WebRtcVoiceServiceModule` into `/OpenSim/RegionModules` (`:37`). |

### Shared / connector layer (`WebRtcVoice/`)

| File | Role |
|---|---|
| `WebRtcVoice/IWebRtcVoiceService.cs` | Service contract. Five methods: 3-arg + 4-arg `ProvisionVoiceAccountRequest`/`VoiceSignalingRequest`, and `CreateViewerSession` (`:45`–`:53`). |
| `WebRtcVoice/IVoiceViewerSession.cs` | Per-viewer session contract: `ViewerSessionID`, `VoiceService`, `VoiceServiceSessionId`, `RegionId`, `AgentId`, `Shutdown()`. |
| `WebRtcVoice/VoiceViewerSession.cs` | Lightweight session used region-side when the real session lives remotely (topology b). Also hosts the **static, simulator-wide** `ViewerSessions` registry and all its lookups (`:58`–`:120`). |
| `WebRtcVoice/WebRtcVoiceServiceConnector.cs` | **Leaf** that forwards a region's request to a Robust grid service over JSON-RPC (`provision_voice_account_request`/`voice_signaling_request`). Region-side of topology (b). |
| `WebRtcVoice/WebRtcVoiceServerConnector.cs` | **Robust-side** `IServiceConnector`. Registers the two JSON-RPC handlers (`:87`–`:88`), loads a local `IWebRtcVoiceService` (`LocalServiceModule`, default `WebRtcVoiceServiceModule`) and hands requests to it. |

### Janus client (`Janus/`)

| File | Role |
|---|---|
| `Janus/WebRtcJanusService.cs` | **Leaf** that actually talks to a Janus gateway. Owns the initial "viewer session," turns provision/signaling requests into room join/leave + trickle operations, and registers the `janus info` / `janus list rooms` console commands. |
| `Janus/JanusSession.cs` | One Janus REST session: create/destroy session, `SendToJanus`/`GetFromJanus`, transaction matching, trickle, and the long-poll event loop (`:495`) that dispatches Janus events. |
| `Janus/JanusPlugin.cs` | Generic Janus plugin handle: attach/detach by plugin *name*, `SendPluginMsg`, event/message hooks. Plugin-name-agnostic. |
| `Janus/JanusAudioBridge.cs` | `JanusPlugin` subclass bound to `"janus.plugin.audiobridge"` (`:41`). Room create/destroy/select, the deterministic room-number hash (`CalcRoomNumber` `:141`), and the in-memory `_rooms` cache (`:135`). |
| `Janus/JanusRoom.cs` | One audiobridge room. `JoinRoom` (`:55`) and `LeaveRoom` (`:109`). |
| `Janus/JanusViewerSession.cs` | The real (Janus-backed) viewer session: holds `JanusSession`/`JanusAudioBridge`/`JanusRoom`, the SDP offer/answer, participant id; `Shutdown()` tears the chain down (`:84`). |
| `Janus/JanusMessages.cs` | All Janus request/response wrappers over `OSDMap`, plus the `OSDToLong` helper (`:136`). See §3. |
| `Janus/BHasher.cs` | `BHasherMdjb2` — deterministic djb2 hash used by `CalcRoomNumber` so every region/session computes the same room id for the same parcel. |

> The three leaf/plumbing classes `WebRtcJanusService`, `WebRtcVoiceServiceConnector`, and
> `WebRtcVoiceServerConnector` are **not** region modules. They are loaded by
> `ServerUtils.LoadPlugin<…>` from `dll:Class` strings in config (see §5), not via
> `PluginRegistration`. Only `WebRtcVoiceRegionModule` and `WebRtcVoiceServiceModule` are
> `ISharedRegionModule`s registered into `/OpenSim/RegionModules`.

---

## SECTION 2 — Request flow and the overload map

### 2.1 The five-method contract and every implementation

`IWebRtcVoiceService` (`WebRtcVoice/IWebRtcVoiceService.cs:45`–`:53`) declares:

- **3-arg** `ProvisionVoiceAccountRequest(OSDMap, UUID, UUID)` — the *dispatcher* entry point.
- **3-arg** `VoiceSignalingRequest(OSDMap, UUID, UUID)`.
- **4-arg** `ProvisionVoiceAccountRequest(IVoiceViewerSession, OSDMap, UUID, UUID)` — the *leaf* entry point (session already resolved).
- **4-arg** `VoiceSignalingRequest(IVoiceViewerSession, OSDMap, UUID, UUID)`.
- `CreateViewerSession(OSDMap, UUID, UUID)`.

| Class | 3-arg PVAR | 3-arg VSR | 4-arg PVAR | 4-arg VSR | CreateViewerSession |
|---|---|---|---|---|---|
| `WebRtcVoiceServiceModule` (dispatcher) | **live** `:203` | **live** `:246` | throws NIE `:270` | throws NIE `:276` | throws NIE `:281` |
| `WebRtcJanusService` (leaf, local Janus) | throws NIE `:364` | throws NIE `:371` | **live** `:191`→`:196` | **live** `:303`→`:308` | **live** `:378` |
| `WebRtcVoiceServiceConnector` (leaf, →Robust) | returns null `:87` | returns null `:116` | **live** `:94` | **live** `:122` | **live** `:81` |

NIE = `throw new NotImplementedException()`. The pattern is deliberate: a **dispatcher**
implements only the 3-arg overloads; a **leaf** implements only the 4-arg overloads +
`CreateViewerSession`. The un-implemented half of each class is unreachable by design (see §2.4).

`WebRtcVoiceServerConnector` is **not** an `IWebRtcVoiceService` — it is the Robust JSON-RPC
front door and *calls* the 3-arg overload on its loaded `LocalServiceModule` (`:112`, `:145`).

### 2.2 Common region-side entry (both topologies)

1. Viewer POSTs to the `ProvisionVoiceAccountRequest` cap. Handler:
   `WebRtcVoiceRegionModule.ProvisionVoiceAccountRequest` (`:187`), wired at cap-register time
   (`:158`).
2. It resolves `voiceService = scene.RequestModuleInterface<IWebRtcVoiceService>()` (`:190`) —
   this is **always `WebRtcVoiceServiceModule`**, registered at `WebRtcVoiceServiceModule.cs:150`.
3. Body is LLSD→`OSDMap` (`BodyToMap` `:471`); `voice_server_type` must be `webrtc` (`:215`);
   for `channel_type=="local"` the **parcel/estate voice gate** runs (`:231`–`:295`): estate
   `AllowVoice` (`:233`), land lookup by the *viewer-supplied* `parcel_local_id`
   (`:256`–`:258`, comment "do fully not trust viewers voice parcel requests" `:230`), parcel
   `AllowVoiceChat` unless estate `TaxFree` (`:274`), estate-channel substitution by *removing*
   `parcel_local_id` when `UseEstateVoiceChan` is set (`:282`–`:284`), else parcel
   ban/restrict → Forbidden (`:286`). (Semantics detailed in `parcel-voice-semantics.md` §3.1.)
4. It calls the **3-arg** overload:
   `voiceService.ProvisionVoiceAccountRequest(map, agentID, scene.RegionInfo.RegionID)` (`:299`).
   This lands on `WebRtcVoiceServiceModule.cs:203` — **not** on any leaf directly.
5. Dispatcher logic (`:203`–`:243`):
   - If the request already carries `viewer_session`, look it up in the static registry
     (`:207`–`:210`).
   - Otherwise (initial request) create a session on the correct leaf:
     `m_spatialVoiceService.CreateViewerSession(...)` for `channel_type=="local"` (`:223`),
     else `m_nonSpatialVoiceService.CreateViewerSession(...)` (`:229`); add it to the registry.
   - Dispatch to that session's leaf: `vSession.VoiceService.ProvisionVoiceAccountRequest(vSession, …)`
     — the **4-arg** overload (`:240`).

`vSession.VoiceService` was set inside the leaf's `CreateViewerSession`, so it points at the
leaf that made the session. That is what selects the topology.

### 2.3 (a) Region module → local `WebRtcJanusService`

Config: `SpatialVoiceService = WebRtcJanusService.dll:WebRtcJanusService`
(`os-webrtc-janus.ini:5`).

- `WebRtcJanusService.CreateViewerSession` (`:378`) returns a `JanusViewerSession` whose
  `VoiceService` is the `WebRtcJanusService` instance.
- The dispatcher's 4-arg call (`WebRtcVoiceServiceModule.cs:240`) therefore lands on
  `WebRtcJanusService.ProvisionVoiceAccountRequest(4-arg)` (`:191`), which delegates to
  `ProvisionVoiceAccountRequestBAD` (`:196`, `.Result` blocks the async).
- That method: lazily creates the Janus session+audiobridge if needed
  (`ConnectToSessionAndAudioBridge` `:206`/`:125`); handles `logout` (`:210`); pulls
  `parcel_local_id` (defaulting to `REGION_ROOM_ID = -999` when absent — the estate-channel
  case, `:227`), `channel_id`, `channel_type`; on a JSEP `offer` (`:241`) calls
  `AudioBridge.SelectRoom(...)` (`:245`) then `Room.JoinRoom(...)` (`:256`), returning
  `{ jsep: answer, viewer_session }` (`:258`).

All in-process; no JSON-RPC. This is the standalone / single-region-Janus deployment.

### 2.4 (b) Region module → Robust voice handler → Janus

Config region-side: `SpatialVoiceService = WebRtcVoice.dll:WebRtcVoiceServiceConnector` +
`WebRtcVoiceServerURI = …` (README `:110`–`:124`, `:141`).

- `WebRtcVoiceServiceConnector.CreateViewerSession` (`:81`) returns a `VoiceViewerSession`
  whose `VoiceService` is the connector.
- The dispatcher's 4-arg call lands on `WebRtcVoiceServiceConnector.ProvisionVoiceAccountRequest(4-arg)`
  (`:94`). It wraps `{request, userID, scene}` and issues
  `JsonRpcRequest("provision_voice_account_request", m_serverURI, …)` (`:103`) via
  `WebUtil.PostToService` (`:154`). On reply it re-syncs the local viewer-session id to the
  remote one (`VoiceViewerSession.UpdateViewerSessionId` `:110`, the documented "kludge").

**Robust side.** Registration: `WebRtcVoiceServerConnector` (an `IServiceConnector`) is loaded
via Robust `[ServiceList] VoiceServiceConnector = "…/WebRtcVoice.dll:WebRtcVoiceServerConnector"`
(README `:167`). Its constructor:
- loads `LocalServiceModule` (default `WebRtcVoiceServiceModule.dll:WebRtcVoiceServiceModule`,
  `:69`) via `ServerUtils.LoadPlugin<IWebRtcVoiceService>` (`:73`), initializes it as a shared
  module (`:84`), then
- registers the two JSON-RPC handlers:
  `pServer.AddJsonRPCHandler("provision_voice_account_request", Handle_ProvisionVoiceAccountRequest)`
  and `("voice_signaling_request", Handle_VoiceSignalingRequest)` (`:87`–`:88`).

`Handle_ProvisionVoiceAccountRequest` (`:93`) unpacks `params` and calls the **3-arg**
`m_WebRtcVoiceService.ProvisionVoiceAccountRequest(request, userID, sceneID)` (`:112`). That is
a **second `WebRtcVoiceServiceModule` dispatcher**, now running on the Robust host, whose config
sets `SpatialVoiceService = WebRtcJanusService.dll:WebRtcJanusService` (README `:172`). So the
Robust dispatcher creates a `JanusViewerSession` and dispatches to `WebRtcJanusService`
(4-arg, `:191`) → Janus, exactly as in (a).

Net: **`WebRtcVoiceServiceModule` runs on both sides** in topology (b) — region-side it forwards
over JSON-RPC via the connector leaf; Robust-side it drives Janus via the Janus leaf. The
viewer-session registry (`VoiceViewerSession.ViewerSessions`) is a separate static map in each
process; the id is reconciled by the `UpdateViewerSessionId` kludge.

### 2.5 Which overloads are dead code

- **`WebRtcJanusService` 3-arg `ProvisionVoiceAccountRequest` (`:364`) / `VoiceSignalingRequest`
  (`:371`) — dead.** `WebRtcJanusService` is only ever reached as a *leaf* through the 4-arg
  overload (a dispatcher never stores a leaf in `IWebRtcVoiceService` scene slot). The NIE at
  `:364` the semantics doc flagged is real but **not on the live path**: the region module's
  3-arg call (`WebRtcVoiceRegionModule.cs:299`) resolves to the *dispatcher*
  (`WebRtcVoiceServiceModule.cs:203`), never to `WebRtcJanusService:364`. This refines
  `parcel-voice-semantics.md` §3.3 / final caveat: the working path is
  region → `WebRtcVoiceServiceModule`(3-arg) → leaf(4-arg); the `WebRtcJanusService` 3-arg
  overload is unreachable in any supported config.
- **`WebRtcVoiceServiceModule` 4-arg PVAR (`:270`), 4-arg VSR (`:276`), `CreateViewerSession`
  (`:281`) — dead.** The dispatcher is never used as a leaf.
- **`WebRtcVoiceServiceConnector` 3-arg PVAR (`:87`) / VSR (`:116`) — effectively dead**
  (return `null`); reachable only if the connector were configured as a dispatcher, which no
  documented config does.
- **`VoiceViewerSession.VoiceServiceSessionId` get/set (`:48`–`:51`) and `Shutdown()` (`:122`)
  throw NIE.** The region-side placeholder session never has these exercised (the real
  Janus-backed session lives on the Robust host). ⚠ Landmine:
  `VoiceViewerSession.TryGetViewerSessionByVSSessionId` (`:77`) reads `VoiceServiceSessionId`,
  which throws for any `VoiceViewerSession` entry in the shared registry — but that lookup is
  only called from `WebRtcJanusService.Handle_Hangup` (`:161`), which runs on the Janus host
  where entries are `JanusViewerSession`s, so it does not fire in practice.
- **`AudioBridgeConfigRoomReq`/`AudioBridgeConfigRoomResp` (JanusMessages `:543`/`:552`) and
  `HangupReq` (`:361`) — dead message classes** (defined, never constructed; confirmed by
  grep). `AudioBridgeConfigRoomReq` is a TODO stub.
- **`JanusRoom.Hangup` (`:95`) — commented out.**

---

## SECTION 3 — Janus API surface used today

`JanusMessages.cs` wraps every Janus message as an `OSDMap`. Two layers: **core** Janus verbs
(`janus:` field) and **audiobridge plugin** requests (`body.request` field inside a
`janus:"message"` envelope, `PluginMsgReq` `:381`).

### 3.1 Core Janus messages (session/handle level)

| Class (ctor line) | `janus` verb | Constructed at |
|---|---|---|
| `CreateSessionReq` (`:291`) | `create` | `JanusSession.CreateSession:99` |
| `DestroySessionReq` (`:310`) | `destroy` | `JanusSession.DestroySession:128` |
| `AttachPluginReq` (`:339`) | `attach` (`plugin` field) | `JanusPlugin.Activate:88` |
| `DetachPluginReq` (`:355`) | `detach` | `JanusPlugin.Detach:126` |
| `TrickleReq` (`:319`/`:327`) | `trickle` | `JanusSession.TrickleCandidates:177/181`, `TrickleCompleted:192/196` |
| `HangupReq` (`:361`) | `hangup` | **never constructed (dead)** |

### 3.2 Audiobridge plugin requests (`body.request` → AudioBridge op)

| Class (ctor line) | `body.request` | Fixed body fields | Constructed at |
|---|---|---|---|
| `AudioBridgeCreateRoomReq` (`:497`) | `create` | `is_private:false`, `permanent:false`, `sampling_rate:48000`, `spatial_audio:<bool>`, `denoise:false`, `record:false`, optional `description` | `JanusAudioBridge.CreateRoom:85` |
| `AudioBridgeDestroyRoomReq` (`:515`) | `destroy` | `room`, `permanent:true` | `JanusAudioBridge.DestroyRoom:123` |
| `AudioBridgeJoinRoomReq` (`:526`) | `join` | `room`, `display` (agent UUID string) — **no** mute/allow/hide list | `JanusRoom.JoinRoom:66` (+ `SetJsep("offer", …)` `:68`) |
| `AudioBridgeLeaveRoomReq` (`:562`) | `leave` | `room`, `id` (participant id) | `JanusRoom.LeaveRoom:114` |
| `AudioBridgeListRoomsReq` (`:571`) | `list` | — | `WebRtcJanusService.HandleJanusListRooms:423` (console only) |
| `AudioBridgeListParticipantsReq` (`:580`) | `listparticipants` | `room` | `HandleJanusListRooms:440` (console only) |
| `AudioBridgeConfigRoomReq` (`:543`) | `configure` | (TODO stub) | **never constructed (dead)** |

The `join` body confirms the flat-membership finding of `parcel-voice-semantics.md` §3.2: the
only per-participant fields are `room` and `display`.

### 3.3 Where responses are parsed

- Transport read: `JanusSession.SendToJanus` (`:237`) → `JanusMessageResp.FromJson` (`:264`,
  defined `:205`); `ack` responses park on a `TaskCompletionSource` keyed by transaction id
  (`:265`–`:273`) and are completed later by the long-poll loop.
- Long-poll event demux: `JanusSession.StartLongPoll` (`:495`), giant `switch` on
  `resp.ReturnCode` (`:511`) — routes `event`/`error` back to the waiting transaction
  (`:560`, `:572`) and raises `OnHangup`/`OnTrickle`/`OnJoined`/… events; `GETERROR` (`:601`,
  synthesized by `GetFromJanus` on HTTP failure `:418`) tears the session down.
- Response wrappers (all in `JanusMessages.cs`): `CreateSessionResp.returnedId` (`:299`),
  `AttachPluginResp.pluginId` (`:348`), `PluginMsgResp` lifts `plugindata.data` into `m_data`
  (`:431`), `AudioBridgeResp` reads the `audiobridge` return-code key (`:483` isSuccess ==
  `"success"`, `:485` `AudioBridgeReturnCode`, `:487` error_code, `:489` room),
  `AudioBridgeJoinRoomResp.ParticipantId` (`:540`).
- Consumption of parsed results: room create switch on `AudioBridgeReturnCode`
  (`JanusAudioBridge.CreateRoom:89`, treats error 486 "room exists" as success `:95`);
  join success on `== "joined"` (`JanusRoom.JoinRoom:73`) sets `ParticipantId` + `Answer`.

---

## SECTION 4 — SLData / data-channel handling on the OpenSim side

**None.** A whole-addon search for `SLData`, `data_channel`/`datachannel`/`DataChannel`,
`application/data`, `sctp`, `m=application` returns no matches. The OpenSim side is audio-only:
it relays the viewer's SDP `offer` into the audiobridge `join` and returns the `answer`
(`JanusRoom.JoinRoom:66`–`:77`), and separately relays ICE `trickle` candidates
(`WebRtcJanusService.VoiceSignalingRequestBAD:308`, `JanusSession.TrickleCandidates:171`).

Two related notes:
- The SDP is passed through **unmodified** — `JanusRoom.JoinRoom:64`–`:68` documents that
  stripping the data section reordered `m=` lines and broke the viewer, so the offer is sent
  as-is. Any SL data-channel `m=application` section the viewer includes is forwarded to Janus
  verbatim but is **not** interpreted, routed, or answered by any OpenSim-side code.
- `ChatSessionRequest` (`WebRtcVoiceRegionModule.cs:377`) handles viewer P2P/conference session
  *signaling* (it mints a session id and emits `ChatterBoxSessionStartReply` `:444`) but carries
  no media/data payload and never reaches Janus.

---

## SECTION 5 — Config surface (every INI key read)

### `[WebRtcVoice]`

| Key | Read at | Default | Notes |
|---|---|---|---|
| `Enabled` | RegionModule `:77`; ServiceModule `:73`; ServiceConnector `:59`; ServerConnector `:60`; JanusService `:71` | `false` | Master gate, read independently by each component. |
| `MessageDetails` | RegionModule `:80`; ServiceConnector `:73`; ServerConnector `:64` | `false` | Verbose request/response logging. |
| `SpatialVoiceService` | ServiceModule `:77` | `""` | `dll:Class` of the spatial leaf. `WebRtcJanusService…` (local) or `WebRtcVoice.dll:WebRtcVoiceServiceConnector` (grid). |
| `NonSpatialVoiceService` | ServiceModule `:78` | `""` → falls back to `SpatialVoiceService` (`:87`–`:91`) | `dll:Class` of the non-spatial (group/IM) leaf. |
| `WebRtcVoiceServerURI` | ServiceConnector `:62` | `""` (disables connector if blank; field inits to `http://localhost:8080` `:50`) | Robust grid-service URL for topology (b). |
| `LocalServiceModule` | ServerConnector `:69` | `WebRtcVoiceServiceModule.dll:WebRtcVoiceServiceModule` | **Robust side only** — which `IWebRtcVoiceService` the JSON-RPC front door drives. |

At least one of `SpatialVoiceService`/`NonSpatialVoiceService` must be set or the module
disables (`ServiceModule:79`–`:84`).

### `[JanusWebRtcVoice]` (read only by `WebRtcJanusService`, i.e. wherever the Janus leaf runs)

| Key | Read at | Default | Notes |
|---|---|---|---|
| `JanusGatewayURI` | `:75` | `""` | Required. |
| `APIToken` | `:76` | `""` | Required. |
| `JanusGatewayAdminURI` | `:77` | `""` | Required. |
| `AdminAPIToken` | `:78` | `""` | Required. |
| `PluginName` | `:79` | `"janus.plugin.audiobridge"` | Janus plugin (mixer) to attach handles to. Added by `feature/voice-plugin-select` (commit `1fb6371`); default preserves the original hardcoded behaviour. Set to `janus.plugin.slvoice` to select the Legion mixer. See §6. |
| `MessageDetails` | `:80` | `false` | Janus wire logging (separate from `[WebRtcVoice]` one). |

Any of the four required fields blank → the Janus service disables itself
(`WebRtcJanusService.cs:82`–`:87`).

### Registration config (not an INI key read by this code, but required to load)

- Region: both `PluginRegistration.cs` files register into `/OpenSim/RegionModules`
  (RegionModule `:37`, ServiceModule `:37`).
- Robust: `[ServiceList] VoiceServiceConnector = "${Const|PrivatePort}/WebRtcVoice.dll:WebRtcVoiceServerConnector"`
  (README `:167`) — the only way `WebRtcVoiceServerConnector` loads; there is **no** in-tree
  `Robust.ini`/`Robust.HG.ini.example` entry for it, so an operator must add it by hand
  (README §"Configure Robust Server", `:157`–`:184`).

Shipped example: `os-webrtc-janus.ini` and `os-webrtc-janus.ini.example` (identical), which
default to topology (a) with `Enabled=false`.

---

## SECTION 6 — Seams for a replacement Janus plugin

Goal: point the OpenSim side at a **different Janus plugin name** (not
`janus.plugin.audiobridge`) over the **same Janus REST/long-poll transport**, minimal C# diff.

### The single-line seam (different plugin, same audiobridge-style protocol)

- **`Janus/JanusAudioBridge.cs:41`** — the plugin name is a hardcoded string passed to the
  `JanusPlugin` base:
  `public JanusAudioBridge(JanusSession pSession) : base(pSession, "janus.plugin.audiobridge")`.
  This is the **only live occurrence** of the plugin-name string (the three others,
  `JanusMessages.cs:420`, `:471`, `:605`, are comments). `AttachPluginReq` already takes the
  name as a parameter (`:339`), and `JanusPlugin` is entirely name-agnostic
  (`Activate:88`, `Detach:126`). So if the new plugin speaks the same `join`/`leave`/`create`
  vocabulary, changing this one string (or making it config-driven) is the whole diff.

> **DONE (commit `1fb6371`, branch `feature/voice-plugin-select`).** This seam is
> now config-driven: `WebRtcJanusService` reads the `[JanusWebRtcVoice] PluginName`
> key (default `janus.plugin.audiobridge`, §5), logs the selected plugin at INFO,
> and passes it to `JanusAudioBridge(JanusSession, string)` → the name-agnostic
> base. Set `PluginName = janus.plugin.slvoice` to attach the Legion mixer; no
> other change (the vocabulary is unchanged). Covered by
> `Tests/WebRtcJanusService.Tests` (default-when-absent, honored-when-set,
> flows-to-JanusPlugin).

### If the replacement plugin speaks a *different* request/response vocabulary

These are the vocabulary-bound sites that would also change (class · method · line):

- `JanusMessages.cs` — the audiobridge request classes: `AudioBridgeCreateRoomReq` ctor `:497`
  (body verbs/fields), `AudioBridgeDestroyRoomReq` `:515`, `AudioBridgeJoinRoomReq` `:526`,
  `AudioBridgeLeaveRoomReq` `:562`, `AudioBridgeListRoomsReq` `:571`,
  `AudioBridgeListParticipantsReq` `:580`.
- `JanusMessages.cs` — the response readers keyed on the `"audiobridge"` field:
  `AudioBridgeResp.isSuccess` `:483`, `AudioBridgeReturnCode` `:485`, `AudioBridgeErrorCode`
  `:487`, `RoomId` `:489`; `AudioBridgeJoinRoomResp.ParticipantId` `:540`.
- `JanusAudioBridge.cs` — the ops that build those and branch on return codes: `CreateRoom`
  `:80` (switch at `:89`, the "486 = already exists" reuse `:95`), `DestroyRoom` `:118`,
  `SelectRoom` `:168`, `Handle_Event`/`Handle_Message` `:225`/`:236`; and the room-number
  scheme `CalcRoomNumber` `:141` + `REGION_ROOM_ID` `:134` **if** room addressing changes.
- `JanusRoom.cs` — `JoinRoom` `:55` (esp. the `SetJsep("offer", …)` at `:68` and the
  `== "joined"` check `:73`) and `LeaveRoom` `:109`, if join/leave semantics differ.

### Sites that do NOT change (plugin-agnostic — the reusable substrate)

- **Whole OpenSim/viewer edge:** `WebRtcVoiceRegionModule.cs` (caps, the parcel/estate gate,
  `ChatSessionRequest`), `WebRtcVoiceServiceModule.cs` (spatial/non-spatial dispatch),
  `WebRtcVoiceServiceConnector.cs` / `WebRtcVoiceServerConnector.cs` (region↔Robust JSON-RPC,
  including the handler names `provision_voice_account_request`/`voice_signaling_request`),
  `IWebRtcVoiceService`/`IVoiceViewerSession`, `VoiceViewerSession.cs` registry. None of these
  name a Janus plugin.
- **`WebRtcJanusService.cs`** — provisioning→room orchestration is written against the
  `JanusAudioBridge`/`JanusRoom` *types*, not the plugin string; unchanged unless method
  signatures on those types change.
- **Transport/session layer — fully reusable:** `JanusSession.cs` (create/destroy, `SendToJanus`
  `:237`, `GetFromJanus` `:392`, `AddJanusHeaders` `:331`, long-poll `:495`, trickle `:171`),
  `JanusPlugin.cs` (generic attach/detach/send), the core message classes
  (`CreateSessionReq`/`DestroySessionReq`/`AttachPluginReq`/`DetachPluginReq`/`TrickleReq`/
  `PluginMsgReq`), `JanusViewerSession.cs` fields, `BHasher.cs`.

> Minimal-diff summary: a same-protocol plugin rename is **one line** (`JanusAudioBridge.cs:41`).
> A different-protocol plugin is contained to `JanusMessages.cs` (audiobridge classes),
> `JanusAudioBridge.cs`, and `JanusRoom.cs`; everything above `JanusPlugin`/`JanusSession` and
> the entire OpenSim/Robust edge is untouched.

---

## SECTION 7 — Local patches vs upstream NGC

**These files carry no local patches.** All 17 C# files under `Addons/os-webrtc-janus/` are
byte-identical to `upstream/develop` (`OpenSim-NGC/OpenSim-Tranquillity`):
`git diff --quiet upstream/develop -- 'Addons/os-webrtc-janus/**/*.cs'` reports **IDENTICAL**.
(`upstream/master` does not contain the addon at all — the addon lives only on the NGC
`develop` line.) The files entered this tree via `2ecb8a9b96` *"Plugin Support + Ported LibOMV
via GH Nuget (#172)"* (2026-06-10), the last commit to touch `JanusMessages.cs`.

**The "OSD.FromLong fix" — subsumed / non-existent as a surviving patch.** The literal
`OSD.FromLong` appears in **no** commit that touches this addon (`git log --all -S 'OSD.FromLong'
-- Addons/os-webrtc-janus/` returns nothing; `git log -S 'FromLong' -- Addons/os-webrtc-janus/`
likewise empty). What exists today is the long-in-JSON workaround `OSDToLong(OSD)`
(`JanusMessages.cs:136`–`:167`): it inspects the `OSDType` and extracts a `long` from
`Integer`/`Binary`/`Array` encodings, because the JSON→OSD parser historically had no `OSDLong`.
Longs are now stored natively (`m_message["session_id"] = long.Parse(value)` `:98`;
`AddSessionId(long)` `:105`; `AddHandleId` `:112`), which only works because the **ported
libOMV** brought in by `#172` provides a long-capable OSD — as acknowledged at
`CreateSessionResp.returnedId` (`:299`–`:305`, "the OSDMap conversion interprets it as a long
(OSDLong)"). Note the now-**stale comment** at `:131` still claims "there is not an OSDLong
type," contradicting `:304`; that staleness is the fingerprint of the pre-libOMV workaround the
integration superseded. So: the upstream libOMV integration subsumed the need for any
`OSD.FromLong`-style patch; `OSDToLong` remains as defensive belt-and-suspenders and matches
upstream exactly — there is nothing local to carry forward or reconcile here.

> **FIXED — the stale `:131` comment marked a real defect (commit `be94487`,
> branch `fix/janus-osdlong-session-id`).** `OSDToLong` (`:136`) switched on the
> OSD type with cases only for `Integer`/`Binary`/`Array` — it had **no case for
> `OSDType.Long`**, the type the ported-libOMV parser returns for any number
> exceeding int32. Such ids fell through to the initialized `long ret = 0`, so
> `CreateSessionResp.returnedId` (`:304`) returned `"0"`: every Janus session was
> created with id 0, all follow-up requests 404'd (`…/voice/0`), plugin attach
> failed, and the service disabled itself. Because Janus session/handle ids
> exceed int32 in practice, this broke **every** NGC deployment using the Janus
> voice addon (a coincidentally-small id was the only way it worked); the addon
> file being byte-identical to `upstream/develop` means the defect is upstream.
> The `:131`↔`:304` contradiction the note above flagged was its fingerprint. Fix:
> `case OSDType.Long: ret = pIn.AsLong();` (+ corrected `:131` comment). Covered by
> `Tests/WebRtcJanusService.Tests` (round-trips `923631757106466` and other >int32
> values). Worth upstreaming to OpenSim-NGC.

---

## Caveats carried from the code

- The 3-arg/4-arg split is a dispatcher-vs-leaf contract; the NIE bodies are intentional dead
  ends, but three are genuinely unreachable (`WebRtcJanusService:364/371`,
  `WebRtcVoiceServiceModule:270/276/281`) and one region-side placeholder
  (`VoiceViewerSession.Shutdown/VoiceServiceSessionId`) throws if ever exercised.
- The semantics doc's "3-arg throws NotImplementedException" flag is technically true of
  `WebRtcJanusService:364` but **off the live path** — the region's 3-arg call hits the
  dispatcher, not the Janus leaf (§2.5).
- Rooms are not reference-counted (`WebRtcJanusService.cs:209` TODO); `_rooms` is per-process
  in-memory (`JanusAudioBridge.cs:135`).
- `.Result` is used to block on async in `ProvisionVoiceAccountRequest` (`:193`) and
  `VoiceSignalingRequest` (`:305`) — the methods are even named `…BAD`; a sync-over-async
  hazard, unchanged from upstream.
- `HangupReq`, `AudioBridgeConfigRoomReq`/`Resp` are defined but never constructed; `janus list
  rooms`/`janus info` console commands (`WebRtcJanusService.cs:391`–`:466`) are the only
  consumers of `AudioBridgeListRoomsReq`/`AudioBridgeListParticipantsReq`.
