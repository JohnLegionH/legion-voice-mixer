# Parcel Voice Semantics — Sim-Side Baseline

**Purpose.** This document is a read-only inventory of the parcel/estate/presence/voice
state that already exists in the OpenSim-Tranquillity server tree. It is the compatibility
baseline that a future WebRTC per-listener voice implementation will be verified against.
It proposes no redesign. Where the code is ambiguous, that is called out explicitly rather
than guessed.

## Citation baseline

| | |
|---|---|
| Tree | `D:\tranquillity-develop` |
| Branch | `feature/membership-tiers` |
| Commit | `0bdeb0bf08cb1f5c9921f28b0293a5d82eba9cea` (`0bdeb0bf08`) |
| Commit date | 2026-08-12 17:57:42 -0500 |
| Working tree | clean at survey time (`git status --short` returned 0 entries) |

All `path:line` citations below are **relative to the tree root above** and were read at
this commit. Line numbers will drift if the tree is re-based or edited. The `.NET8`
Tranquillity layout places framework code under `Source/OpenSim.Framework/`, scene code
under `Source/OpenSim.Region.Framework/`, core modules under
`Source/OpenSim.Region.CoreModules/`, and the integrated WebRTC voice add-on under
`Addons/os-webrtc-janus/`.

**`ParcelFlags` is external.** A whole-tree search for `enum ParcelFlags` returns nothing;
the enum comes from the **OpenMetaverse (libomv)** dependency and is used via
`using OpenMetaverse;` (`Source/OpenSim.Framework/LandData.cs:30`). All `ParcelFlags.*`
references (`AllowVoiceChat`, `UseEstateVoiceChan`, `UseBanList`, `UseAccessList`,
`UseAccessGroup`, `UsePassList`, `HideAvatars` unused, etc.) resolve to that enum. The raw
flag word is `LandData.Flags` (`Source/OpenSim.Framework/LandData.cs:410`), and consumers
test bits directly against `land.Flags`.

---

## SECTION 1 — Parcel state the sim already has

### 1.1 Ban and access-restriction evaluation

Interface — `Source/OpenSim.Framework/ILandObject.cs`:

- `:85` `bool IsEitherBannedOrRestricted(UUID avatar);`
- `:86` `bool IsBannedFromLand(UUID avatar);`
- `:87` `bool CanBeOnThisLand(UUID avatar, float posHeight);`
- `:88` `bool IsRestrictedFromLand(UUID avatar);`
- `:89` `bool IsInLandAccessList(UUID avatar);`
- `:92` `List<LandAccessEntry> CreateAccessListArrayByFlag(AccessList flag);`
- `:94` `void UpdateAccessList(...);`

`HasGroupAccess` is **not** on the interface — it is a public method on the concrete
`LandObject` only.

Implementation — `Source/OpenSim.Region.CoreModules/World/Land/LandObject.cs`:

**Common exemption preamble.** `IsEitherBannedOrRestricted`, `IsBannedFromLand`,
`IsRestrictedFromLand`, and `CanBeOnThisLand` all begin with the same four exemptions, in
this order (quoting `IsEitherBannedOrRestricted`, `:722`–`:734`):

```
if (m_estateSettings.TaxFree)              // region access control only  -> :724
    return false;
if (m_scenePermissions.IsAdministrator(avatar))                          // :727
    return false;
if (m_estateSettings.IsEstateManagerOrOwner(avatar))                     // :730
    return false;
if (avatar.Equals(LandData.OwnerID))                                     // :733
    return false;
```

`m_estateSettings.TaxFree` is a **misnomer** — see §1.4; it means "estate/region access
control only," so when set it short-circuits *all* parcel-level ban/restrict/voice checks.

`IsEitherBannedOrRestricted` then returns `true` if `IsBannedFromLand_inner` (`:736`) or
`IsRestrictedFromLand_inner` (`:739`) is true.

**Ban logic — `IsBannedFromLand_inner` (`:826`–`:838`), gated by `UseBanList`:**

```
if ((LandData.Flags & (uint) ParcelFlags.UseBanList) > 0)          // :828
{
    int now = Util.UnixTimeSinceEpoch();
    foreach (LandAccessEntry e in LandData.ParcelAccessList)       // :831
        if (e.Flags == AccessList.Ban && e.AgentID.Equals(avatar)) // :833
            return e.Expires == 0 || e.Expires > now;              // :834
}
return false;
```

Ban entries live in `LandData.ParcelAccessList` with `e.Flags == AccessList.Ban`;
`Expires == 0` is permanent, else compared to Unix now.

**Restriction/access logic — `IsRestrictedFromLand_inner` (`:857`–`:911`).** Two modes:

- **`UseAccessList` OFF (`:859`):** parcel is not whitelist-restricted, but age/anonymous
  denial still applies (`:861`–`:882`) — `DenyAgeUnverified` / `DenyAnonymous` combined
  with the estate `DoDenyMinors`/`DoDenyAnonymous` and the avatar's `ProfileFlags`. NPCs
  are exempt (`:870`–`:871`). Returns `false` (not restricted) if neither denial trips.
- **`UseAccessList` ON:** access granted by group **or** explicit access-list entry —
  `if (HasGroupAccess(avatar)) return false;` (`:885`), then
  `if (IsInLandAccessList(avatar)) return false;` (`:888`). An avatar not present in the
  region is treated as restricted (`:892`–`:893`). NPCs inherit their owner's access
  (`:895`–`:910`, `return !IsInLandAccessList(owner);`).

**Group access — `HasGroupAccess` (`:768`–`:807`), gated by `UseAccessGroup` + non-zero
group:**

```
if (LandData.GroupID.IsNotZero() && (LandData.Flags & (uint)ParcelFlags.UseAccessGroup) != 0) // :770
```

Uses `m_groupMemberCache` with `GROUPMEMBERCACHETIMEOUT`; falls back to
`sp.ControllingClient.IsGroupMember(LandData.GroupID)` for present avatars (`:777`) or
`IGroupsModule.GetMembershipData` otherwise (`:787`). Returns `false` when the flag is unset
or the group is zero (`:806`).

**Explicit access list — `IsInLandAccessList` (`:913`–`:922`).** NOT gated by any flag; it
always scans `ParcelAccessList` for `e.Flags == AccessList.Access` with the same expiry rule
(`:918`–`:919`).

**Pass list.** Purchased passes are written as timed `AccessList.Access` entries — i.e. into
the same list `IsInLandAccessList` reads. In
`Source/OpenSim.Region.CoreModules/World/Land/LandManagementModule.cs`: `TaxFree` skips
passes (`:577`); group-owned parcels disallow passes (`:581`–`:585`);
`if((ldata.Flags & (uint)ParcelFlags.UsePassList) == 0) return;` (`:587`); expiry is
`(int)(3600.0 * ldata.PassHours + 0.5f)` (`:600`). So `UsePassList` feeds `UseAccessList`
grants through timed access entries.

**Height nuance — `CanBeOnThisLand` (`:745`–`:766`).** After the four exemptions, ban is
enforced **only below `BanLineSafeHeight`** (`:759`, you can fly over a ban line), and the
restriction check is an `else if` (`:762`) — a banned avatar above the safe height therefore
**skips the restriction check entirely**. This asymmetry matters if voice audibility is ever
tied to `CanBeOnThisLand` rather than to `IsBannedFromLand`/`IsRestrictedFromLand` directly.

> Semantic summary for voice: the canonical "is X excluded from parcel P" predicates are
> `IsBannedFromLand` (`:809`), `IsRestrictedFromLand` (`:840`), and the combined
> `IsEitherBannedOrRestricted` (`:722`). The WebRTC module already calls the first two on
> provisioning (§3.1). None of these is per-listener — they answer "may *this* avatar be on
> parcel P," not "may listener L hear speaker S."

### 1.2 "Hide avatars from other parcels" mechanism

The driving property is **`LandData.SeeAVs`** — a plain bool, **not** a `ParcelFlags` bit
(`Source/OpenSim.Framework/LandData.cs:96`; siblings `AnyAVSounds` `:97`, `GroupAVSounds`
`:98`, which govern *sound*, not visibility). Default is `true` (avatars visible). When
`SeeAVs == false`, the parcel hides its occupants from avatars standing on other parcels.

**Derivation into presence state — `Source/OpenSim.Region.Framework/Scenes/ScenePresence.cs`:**

```
m_currentParcelHide = !land.LandData.SeeAVs;   // :224 (currentParcelUUID setter)
m_currentParcelHide = !landover.LandData.SeeAVs; // :2259 (root-agent / teleport completion)
```

Exposed read-only as:

```
public bool ParcelHideThisAvatar { get { return m_currentParcelHide; } }  // :275–:281
```

The recurring enforcement predicate is
`ParcelHideThisAvatar && currentParcelUUID != peer.currentParcelUUID && !peer.IsViewerUIGod`
→ *skip the send*. **Every enforcement site in `ScenePresence.cs` (exhaustive, from
`grep ParcelHideThisAvatar`):**

| Line | Context |
|---|---|
| `2342`, `2385` | movement/visibility scan blocks |
| `3974` | appearance/data send guard (`SendOtherAgentsAvatarFullToMe` path) |
| `4207` | `SendOtherAgentsAvatarFullToMe` region scan |
| `4262`, `4279` | initial-avatar-data neighborhood (kill vs view) |
| `4293` | **`SendAvatarDataToAgent`** — canonical full-update guard |
| `4334` | `SendAppearanceToAgent` |
| `4349`, `4360`, `4387`, `4405` | anim / attachment sends |
| `5531`, `5633`, `5694`, `5726` | additional update-scan guards |
| `6674` | `parcelGodCheck` — sends `SendViewTo` (god) vs `SendKillTo` |
| `6861` | inside `ParcelCrossCheck` helper |

`SendAvatarDataToAgentNF` (`:4298`) is the deliberate **no-filter** variant that bypasses
the hide check. `ParcelCrossCheck` (`:6685`, invoked from the `currentParcelUUID` setter at
`:227`) is the transition engine: when a presence changes parcel it diffs
`currentParcelHide`/`previusParcelHide` to send kill-object vs re-show packets to the right
peers.

**Gap relevant to voice — coarse locations are NOT hidden.** `GetCoarseLocations`
(`Source/OpenSim.Region.Framework/Scenes/SceneGraph.cs:232`–`:267`) iterates all presences,
skipping only child agents (`:256`), with **no `SeeAVs`/parcel filter**. So map/coarse dots
already cross the hide boundary today; only full avatar data/appearance/anim/attachments are
suppressed. A voice-hide feature deriving strictly from `SeeAVs` would match the
*full-update* behavior, not the coarse-location behavior.

> Voice seam: `SeeAVs` (`LandData.cs:96`) → `m_currentParcelHide` → `ParcelHideThisAvatar`
> (`ScenePresence.cs:275`), with the peer test
> `currentParcelUUID != peer.currentParcelUUID && !peer.IsViewerUIGod`. This is the exact
> flag and predicate voice hiding must derive from to stay consistent with the visual
> behavior.

### 1.3 Per-parcel voice settings

Two OpenMetaverse `ParcelFlags`, both read off `land.Flags` (no dedicated `LandData` voice
accessor):

- **`ParcelFlags.AllowVoiceChat`** — parcel voice enable. Set by default on new parcels
  (`Source/OpenSim.Framework/LandData.cs:64`).
- **`ParcelFlags.UseEstateVoiceChan`** — when set, the parcel shares the estate/region voice
  channel instead of its own.

Where read today (all three voice modules share the same contract — see §3):

- WebRTC — `Addons/os-webrtc-janus/WebRtcVoiceRegionModule/WebRtcVoiceRegionModule.cs:274`
  (`AllowVoiceChat`), `:282` (`UseEstateVoiceChan`).
- Vivox — `Source/OpenSim.Region.OptionalModules/Avatar/Voice/VivoxVoice/VivoxVoiceModule.cs:690`
  (`AllowVoiceChat`), `:767` (`UseEstateVoiceChan`).
- FreeSwitch — `Source/OpenSim.Region.OptionalModules/Avatar/Voice/FreeSwitchVoice/FreeSwitchVoiceModule.cs:464`
  (`AllowVoiceChat`), `:822` (`UseEstateVoiceChan`, plus a `LocalID != 1` guard).

### 1.4 Estate-level state relevant to voice

`Source/OpenSim.Framework/EstateSettings.cs`; exposed to modules as
`scene.RegionInfo.EstateSettings`.

- `:140`–`:144` `public bool AllowVoice` (default `true`) — estate voice master switch; all
  three voice modules gate on it (§3.1).
- `:205`–`:209` `public bool TaxFree` — comment on `:205` states
  `// this is now !AllowAccessOverride, keeping same name to reuse DB entries`. **This is a
  named-vs-meaning trap:** `TaxFree == true` means the estate does *not* override access
  control, which is why it short-circuits `IsBanned*`/`IsRestricted*`/parcel-voice checks
  (`LandObject.cs:724,747,811,842`; `WebRtcVoiceRegionModule.cs:274`). Do not read it
  literally.
- `:374`–`:377` `IsEstateManagerOrOwner(UUID)` — owner or in `l_EstateManagers`.
- `:379`–`:382` `IsEstateOwner(UUID)`.
- `:384`–`:395` `IsBanned(UUID)` — estate managers/owners exempt (`:386`), then scans
  `l_EstateBans`.
- `:397` `IsBanned(UUID, int userFlags)` — adds minors/anonymous denial via user flags.

**No voice module currently consults `EstateSettings.IsBanned`.** Voice modules check
`AllowVoice` and (WebRTC only) parcel `IsBanned/IsRestricted`. Estate-ban-aware voice
suppression, if wanted, has its data source here (`IsBanned` `:384`, exemption mirror
`IsEstateManagerOrOwner` `:374`) but no current consumer.

---

## SECTION 2 — Events and presence

### 2.1 Intra-region parcel-crossing event

`Source/OpenSim.Region.Framework/Scenes/EventManager.cs`:

```
public delegate void AvatarEnteringNewParcel(ScenePresence avatar, int localLandID, UUID regionID); // :401
public event AvatarEnteringNewParcel OnAvatarEnteringNewParcel;                                      // :402
public void TriggerAvatarEnteringNewParcel(ScenePresence avatar, int localLandID, UUID regionID)     // :1927
```

Arguments: `ScenePresence avatar`, `int localLandID` (**local** land ID, not a UUID),
`UUID regionID`.

**Sole producer** — `Source/OpenSim.Region.CoreModules/World/Land/LandManagementModule.cs`,
inside `EventManagerOnClientMovement` (`:536`):

```
if (avatar.IsChildAgent)                                              // :538 — child guard
    return;
...
if(over != newover || avatar.currentParcelUUID.NotEqual(newover.LandData.GlobalID)) // :548
    m_scene.EventManager.TriggerAvatarEnteringNewParcel(avatar,
            newover.LandData.LocalID, m_scene.RegionInfo.RegionID);   // :550–:551
```

- **(1) Root agents only.** The child guard (`:538`–`:539`) means child agents never fire
  this event. Upstream, `ScenePresence.CheckForSignificantMovement` also returns early for
  child agents (`ScenePresence.cs:3851`–`:3852`, `if (IsChildAgent || IsInTransit) return;`),
  so children never even generate the `OnClientMovement` that would trigger it. This is the
  documented cross-region model — child-agent voice participation must be driven by direct
  enumeration (§2.3), **not** by this event. It is not a defect.
- **(2) Internal crossings, not first assignment.** On region entry the parcel is assigned
  directly, bypassing the event: `ScenePresence.cs:2260` (root-agent activation sets
  `m_currentParcelUUID` directly) and `LandManagementModule.cs:503`
  (`sendClientInitialLandInfo` sets `avatar.currentParcelUUID`). The event therefore models
  root-agent movement across a parcel boundary within the region. Crucially, it fires on
  **any** boundary crossing that changes `currentParcelUUID` — including crossings between
  two parcels on the *same* voice channel (e.g. both on the estate channel), because the
  condition compares `GlobalID`, not channel identity. This is the sim-side hook for the
  "movement between parcels on the same channel is invisible to the viewer" case, **but only
  for root agents.**

### 2.2 How ScenePresence tracks the current parcel

`Source/OpenSim.Region.Framework/Scenes/ScenePresence.cs`:

```
private UUID m_previusParcelUUID = UUID.Zero;   // :196
private UUID m_currentParcelUUID = UUID.Zero;   // :197
private bool m_previusParcelHide = false;       // :198
private bool m_currentParcelHide = false;       // :199
private readonly object parcelLock = new();      // :200
public UUID currentParcelUUID { get; set; }      // :203–:230  (setter is the recompute)
```

The `currentParcelUUID` **setter** (`:206`–`:229`) is the update mechanism: under
`parcelLock` it records previous parcel/hide, recomputes `m_currentParcelHide` from the land
under `AbsolutePosition` (`:222`–`:224`), and calls `ParcelCrossCheck` (`:227`).

**Staleness / threading caveats:**

- The value is **pushed in** by the land subsystem, not self-recomputed on every position
  tick. Writers: `LandManagementModule.cs:463` (crossing event handler), `:503` (initial
  land info), `:231`/`:1619` (reset / force-review); `LandObject.cs:959`/`:983`
  (`SendLandUpdateToAvatarsOverMe`/`SendLandUpdateToAvatars`); `ScenePresence.cs:2260`
  (region entry), `:1699`/`:2249` (reset to Zero — direct field writes).
- The crossing detection driving the setter runs off `OnClientMovement`, gated by the
  significant-movement threshold (`CheckForSignificantMovement`, `ScenePresence.cs:4462`–
  `:4470`), so it updates on movement events, not continuously.
- **Child agents never update it via movement** (guard at `:3851`) — for a child presence
  `currentParcelUUID` reflects whatever was last set (often `UUID.Zero`) and must be treated
  as unreliable; a voice module needing a child's parcel must resolve it from
  `AbsolutePosition` via `LandChannel.GetLandObject`.
- Several paths write `m_currentParcelUUID` **directly without `parcelLock`** (`:1699`,
  `:2249`, `:2260`). Treat cross-thread reads as eventually-consistent.

`m_currentParcelHide` (§1.2) rides alongside on the same setter; `previusParcelHide`
supports `ParcelCrossCheck` transition diffing (`:6685`).

### 2.3 Child vs root presence and enumeration

- `public bool IsChildAgent { get; set; }` — `ScenePresence.cs:933` (set true at
  construct/close `:1117`/`:1690`; false on promotion to root `:1396`).
- Land-module handlers early-return for child agents: `EventManagerOnClientMovement`
  (`LandManagementModule.cs:538`), `EventManagerOnSignificantClientMovement` (`:525`),
  `sendClientInitialLandInfo`/`SendLandUpdate` (`:512`).
- Enumeration APIs (positions via `ScenePresence.AbsolutePosition`,
  `Source/OpenSim.Region.Framework/Scenes/ScenePresence.cs:732`):
  - `Scene.GetScenePresences()` — `Scene.cs:5330` (root **and** child; returns shared list
    marked "DO NOT MODIFY").
  - `Scene.ForEachRootScenePresence(Action<ScenePresence>)` — `Scene.cs:5342` →
    `SceneGraph.cs:1440`, skips child/deleted (`:1445`).
  - `Scene.ForEachScenePresence(Action<ScenePresence>)` — `Scene.cs:5352` →
    `SceneGraph.cs:1463`, skips only deleted (`:1468`) — **includes child agents.**

> Cross-region voice requires child-agent participation, so a voice module should enumerate
> with `Scene.ForEachScenePresence` / `GetScenePresences` (children included) and resolve
> each child's parcel from `AbsolutePosition`, since children receive neither the crossing
> event (§2.1) nor movement-driven `currentParcelUUID` updates (§2.2).

### 2.4 Parcel property-change events (cache invalidation)

`Source/OpenSim.Region.Framework/Scenes/EventManager.cs`:

```
public delegate void LandObjectAdded(ILandObject newParcel);  public event ... OnLandObjectAdded;   // :395–:396
public delegate void LandObjectRemoved(UUID globalID);        public event ... OnLandObjectRemoved;  // :398–:399
```

Triggers: `TriggerLandObjectAdded` (`:1880`), `TriggerLandObjectRemoved` (`:1901`), and
`TriggerLandObjectUpdated(uint localParcelID, ILandObject newParcel)` (`:1922`) — which
**simply forwards to `TriggerLandObjectAdded`** (`:1924`). There is no distinct "updated"
event; subscribing to `OnLandObjectAdded` alone catches both add and update.

Propagation of edits (flags / access list):

- `LandManagementModule.UpdateLandProperties` (`:1590`) → `LandObject.UpdateLandProperties`
  (`LandObject.cs:528`) → `TriggerLandObjectUpdated` (`LandObject.cs:2085`); module then
  re-sends land data and forces a flags review via `avatar.currentParcelUUID = parcelID;`
  (`LandManagementModule.cs:1618`–`:1619`).
- Client entry: `ClientOnParcelPropertiesUpdateRequest` (`LandManagementModule.cs:1624`,
  wired `:212`).
- Access-list edits / buy-pass: `TriggerLandObjectUpdated` (`LandManagementModule.cs:659`,
  `:668`); expiry: `LandObject.cs:2085`.
- Create/join/subdivide/remove: `TriggerLandObjectAdded` (`LandManagementModule.cs:813`),
  `TriggerLandObjectRemoved` (`:854`, `:882`).
- `SendLandUpdateToAvatarsOverMe` (`LandObject.cs:938`/`:943`) iterates **root** presences
  only (`ForEachRootScenePresence`, `:946`) and sets `avatar.currentParcelUUID` for avatars
  physically over the parcel (`:959`) — presence-scoped, root-only. For region-global,
  presence-independent invalidation prefer the `OnLandObjectAdded`/`OnLandObjectRemoved`
  events.

---

## SECTION 3 — Existing voice channel logic (semantic reference)

Modules present:

- Vivox (legacy, canonical rule) —
  `Source/OpenSim.Region.OptionalModules/Avatar/Voice/VivoxVoice/VivoxVoiceModule.cs`
- FreeSwitch (legacy) —
  `Source/OpenSim.Region.OptionalModules/Avatar/Voice/FreeSwitchVoice/FreeSwitchVoiceModule.cs`
- Integrated WebRTC/Janus —
  `Addons/os-webrtc-janus/WebRtcVoiceRegionModule/WebRtcVoiceRegionModule.cs`, with
  `Addons/os-webrtc-janus/Janus/*.cs` (esp. `JanusAudioBridge.cs`, `JanusRoom.cs`,
  `WebRtcJanusService.cs`, `JanusMessages.cs`).

### 3.1 Channel/room derivation rule

**Enable gate — identical across all three modules:**

1. `if (!EstateSettings.AllowVoice)` → no channel (empty / HTTP error).
2. `else if (!EstateSettings.TaxFree && (land.Flags & ParcelFlags.AllowVoiceChat) == 0)`
   → no channel.
3. else → derive a channel.

Citations: Vivox `:684`/`:690`; FreeSwitch `:456`/`:464`; WebRTC `:233`/`:274`.

**Parcel-vs-estate selection — keyed on `ParcelFlags.UseEstateVoiceChan`:**

- **Vivox** (`RegionGetOrCreateChannel`, `:767`–`:780`): channel name = **parcel `GlobalID`**
  string when `UseEstateVoiceChan` is clear; = **region `RegionID`** string when set.
  ```
  if ((land.Flags & (uint)ParcelFlags.UseEstateVoiceChan) == 0) {
      landUUID = land.GlobalID.ToString();          // per-parcel channel  :770
  } else {
      landUUID = scene.RegionInfo.RegionID.ToString(); // estate/region channel :777
  }
  ```
  Land is taken from **avatar position**: `scene.GetLandData(avatar.AbsolutePosition)`
  (`:672`). Response carries `parcel_local_id`, `region_name`, `channel_uri` (`:706`–`:709`).
- **FreeSwitch** (`ChannelUri`, `:822`–`:839`): same rule, plus `land.LocalID != 1` — the
  whole-region default parcel (LocalID 1) always uses the region channel. Identity is
  `sip:conf-x{base64(landUUID)}@realm` (`:834`).
- **WebRTC** (`WebRtcVoiceRegionModule.cs:228`–`:294`): the estate-vs-parcel decision uses
  the **same flags**, but the mechanism differs. Instead of substituting the region UUID, it
  **removes `parcel_local_id` from the request** when `UseEstateVoiceChan` is set:
  ```
  if ((land.Flags & (uint)ParcelFlags.UseEstateVoiceChan) != 0)
      map.Remove("parcel_local_id"); // estate channel                    :282–:284
  else if(parcel.IsRestrictedFromLand(agentID) || parcel.IsBannedFromLand(agentID))
      ... return Forbidden;                                                :286–:293
  ```
  Also, WebRTC **does not trust the viewer's parcel** in the same way — it reads the
  client-supplied `parcel_local_id` via `scene.LandChannel.GetLandObject(parcelID)` (`:256`–
  `:258`; comment `//do fully not trust viewers voice parcel requests` `:230`), whereas
  Vivox/FreeSwitch derive the parcel from server-side avatar position.

  Downstream the room number is a deterministic hash in
  `JanusAudioBridge.CalcRoomNumber(pRegionId, pChannelType, pParcelLocalID, pChannelID)`
  (`Addons/os-webrtc-janus/Janus/JanusAudioBridge.cs:141`–`:167`): for `"local"` it hashes
  `(regionId, "local", parcelLocalID)` with `BHasherMdjb2`, then `Math.Abs(...)` (`:165`).
  When `parcel_local_id` was removed, `WebRtcJanusService` defaults it to
  `REGION_ROOM_ID = -999` (`JanusAudioBridge.cs:134`; default applied in
  `WebRtcJanusService.cs:227`), collapsing everyone in the region onto one room.

**Compatibility baseline (the rule a new implementation must preserve):**

> voice ON ⇔ `EstateSettings.AllowVoice` AND (`EstateSettings.TaxFree` OR parcel
> `AllowVoiceChat`). Channel identity is **per-region-parcel** keyed on the parcel
> (`GlobalID` in Vivox, `parcelLocalID` hash in WebRTC) **unless** `UseEstateVoiceChan` is
> set, in which case it is **one region/estate channel** keyed on the region. All parcels
> that resolve to the estate channel therefore share a single room.

### 3.2 Per-listener filtering — confirmed NONE

Channel/room membership is all-or-nothing and symmetric; there is no per-listener audibility
control anywhere in the current voice code.

- The Janus join request carries only room id + display name — no per-participant mute/hide/
  allow list: `AudioBridgeJoinRoomReq(int pRoomId, string pAgentName)` sets only `request`,
  `room`, `display` (`Addons/os-webrtc-janus/Janus/JanusMessages.cs:524`–`:533`).
- Room creation sets no allow/deny list (`is_private=false`, `spatial_audio`,
  `sampling_rate`, `denoise`, `record`) — `JanusMessages.cs:492`–`:511`.
- `JanusRoom.JoinRoom` (`Addons/os-webrtc-janus/Janus/JanusRoom.cs:55`–`:90`) issues the
  identical join for every session; no per-listener divergence, mute map, or suppression.
- The only ban/restrict logic (`WebRtcVoiceRegionModule.cs:286`) decides whether the
  **requesting agent may join the room at all** — a whole-session gate, not per-listener
  hiding. And note it is **skipped entirely** when `UseEstateVoiceChan` is set (it is the
  `else` branch of `:282`), so on a shared estate channel there is currently no parcel
  ban/restrict enforcement in voice.
- Vivox/FreeSwitch likewise return a single `channel_uri` with no per-listener data
  (`VivoxVoiceModule.cs:706`–`:709`; `FreeSwitchVoiceModule.cs:478`+).

> This is the crux for the WebRTC work: the symmetric per-listener hiding described in the
> project brief (banned avatars neither hear nor are heard within a shared estate channel;
> `SeeAVs` hiding applied to voice) has **no existing implementation**. All current code
> treats a room as a flat, symmetric membership set.

### 3.3 Room lifecycle (WebRTC/Janus)

- Lookup/create: `JanusAudioBridge.SelectRoom(...)` (`JanusAudioBridge.cs:168`) computes
  `CalcRoomNumber` (`:170`) and consults an in-memory
  `Dictionary<int, JanusRoom> _rooms` (`:135`, `:178`–`:181`).
- Create-on-Janus: `CreateRoom` (`:80`–`:116`) is idempotent (reuses on Janus error 486);
  a create race is resolved by destroying the loser and adopting the existing room
  (`:186`–`:210`).
- Teardown: `DestroyRoom` (`:118`–`:131`); leave-on-logout in `WebRtcJanusService.cs:214`–
  `:217`. Rooms are **not reference-counted** — `WebRtcJanusService.cs:209` carries a TODO
  ("need to keep count of users in a room to know when to close a room"), and `_rooms` is a
  per-instance in-memory cache, not persisted.

**Ambiguity flagged.** The region module calls the **3-arg**
`voiceService.ProvisionVoiceAccountRequest(map, agentID, RegionID)`
(`WebRtcVoiceRegionModule.cs:299`), but in `WebRtcJanusService` that 3-arg overload throws
`NotImplementedException` (`WebRtcJanusService.cs:364`–`:367`); the functional logic is the
4-arg `ViewerSession` overload (`WebRtcJanusService.cs:191`). In practice the working path
runs via `WebRtcVoiceServiceConnector` (JSON-RPC to a Robust-side service,
`Addons/os-webrtc-janus/WebRtcVoice/WebRtcVoiceServiceConnector.cs:94`). The parcel/estate
**gate** in §3.1 executes in the region module regardless of which service path handles
provisioning. Confirm the active deployment path before relying on any specific
`ProvisionVoiceAccountRequest` overload.

---

## SECTION 4 — Derived requirements table

Legend: **Data source** = where the sim answers the rule today; **Invalidated by** = the
event a voice cache should watch; **OPEN** = no sim-side source exists — new plumbing
required. OPEN rows are the actionable gaps.

| # | Semantic rule the voice layer must honor | (a) Sim-side data source | (b) Invalidating event | Status |
|---|---|---|---|---|
| 1 | Is speaker/listener banned from parcel P? | `LandObject.IsBannedFromLand` (`LandObject.cs:809`) / `IsEitherBannedOrRestricted` (`:722`); ban entries in `LandData.ParcelAccessList` gated by `UseBanList` (`:828`) | `OnLandObjectAdded` (covers updates via forward, `EventManager.cs:1924`); access-list edits `LandManagementModule.cs:659/668` | Source exists |
| 2 | Is avatar access-restricted from parcel P (access list / group / pass)? | `IsRestrictedFromLand` (`:840`), `HasGroupAccess` (`:768`), `IsInLandAccessList` (`:913`); flags `UseAccessList`/`UseAccessGroup`/`UsePassList` | `OnLandObjectAdded`; `TriggerLandObjectUpdated` on access edits | Source exists |
| 3 | Estate-manager / owner / admin / parcel-owner exemption | `EstateSettings.IsEstateManagerOrOwner` (`EstateSettings.cs:374`), `m_scenePermissions.IsAdministrator`, `LandData.OwnerID` (exemption preamble `LandObject.cs:724`–`734`) | estate-settings change (no dedicated fine-grained event — see #12) | Source exists |
| 4 | Is avatar estate-banned? | `EstateSettings.IsBanned` (`EstateSettings.cs:384`) | estate-settings change (see #12) | Source exists (no current voice consumer) |
| 5 | "Hide avatars" for parcel P (visual → voice) | `LandData.SeeAVs` (`LandData.cs:96`) → `ScenePresence.ParcelHideThisAvatar` (`:275`); predicate `currentParcelUUID != peer.currentParcelUUID && !peer.IsViewerUIGod` | `OnLandObjectAdded` (flag/`SeeAVs` change); presence parcel change via `ParcelCrossCheck` | Source exists (not applied to voice today) |
| 6 | Voice enabled for parcel P? | `EstateSettings.AllowVoice` (`EstateSettings.cs:140`) + `ParcelFlags.AllowVoiceChat` on `land.Flags` (read `WebRtcVoiceRegionModule.cs:274`) | `OnLandObjectAdded`; estate-settings change | Source exists |
| 7 | Parcel channel vs estate/region channel | `ParcelFlags.UseEstateVoiceChan` on `land.Flags` (Vivox `:767`; WebRTC `:282`); channel identity = parcel `GlobalID` / `parcelLocalID` hash vs region `RegionID` | `OnLandObjectAdded` (flag change) | Source exists |
| 8 | Which parcel is a **root** avatar on (for same-channel crossings)? | `ScenePresence.currentParcelUUID` (`:203`); movement-driven | `OnAvatarEnteringNewParcel` (root only, `EventManager.cs:401`; fired `LandManagementModule.cs:550`) | Source exists (root only) |
| 9 | Which parcel is a **child** avatar on (cross-region audibility)? | Must resolve from `AbsolutePosition` (`ScenePresence.cs:732`) via `LandChannel.GetLandObject`; `currentParcelUUID` is unreliable for children (§2.2) | **no event** — child agents get no crossing event (`LandManagementModule.cs:538`) | **OPEN** — voice module must poll child positions itself (expected/required, not a defect) |
| 10 | Detect intra-region movement between two parcels **on the same voice channel** | For root agents: `OnAvatarEnteringNewParcel` fires on any `GlobalID` change incl. same-channel (`LandManagementModule.cs:548`) | that event | Source exists for root; **OPEN** for child agents (ties to #9) |
| 11 | **Per-listener audibility** — banned/hidden avatar neither hears nor is heard, symmetric hiding, within a shared room | none — all room membership is flat/symmetric (§3.2: `JanusRoom.cs:55`, `JanusMessages.cs:524`) | n/a | **OPEN** — core new capability; no per-listener mechanism exists |
| 12 | Cache invalidation on estate-settings change (bans, managers, `AllowVoice`) | `EstateSettings` mutators (`AddBan`/`AddEstateManager`, `EstateSettings.cs:360`+) | **no granular event** found; `OnLandObjectAdded` covers parcel data only, not estate settings | **OPEN** — no estate-settings-changed event for voice to subscribe to |
| 13 | Apply parcel ban/restrict to voice while on the **estate/shared** channel | data from #1/#2 exists, but current voice code **skips** the ban check on the estate channel (`WebRtcVoiceRegionModule.cs:282` `else` branch) | as #1/#2 | **OPEN** — enforcement gap; requires per-listener logic (#11) since one room spans multiple parcels |
| 14 | Coarse-location (map dot) consistency with voice hiding | `GetCoarseLocations` applies **no** `SeeAVs` filter (`SceneGraph.cs:232`–`:267`) | n/a | **OPEN/known-divergence** — decide whether voice hiding should match full-update hiding (which *is* `SeeAVs`-gated) or the unfiltered coarse behavior |

### OPEN items — the plumbing a WebRTC implementation must add

- **#11 Per-listener audibility filtering (symmetric hiding).** No existing mechanism; every
  current room is a flat symmetric membership set (§3.2). This is the central new capability.
- **#9 / #10 Child-agent parcel resolution and same-channel crossing detection.** Child
  agents receive no crossing event and have unreliable `currentParcelUUID`; the voice module
  must enumerate child presences (`Scene.ForEachScenePresence`) and resolve parcels from
  `AbsolutePosition`. Required for cross-region audibility, by design.
- **#13 Estate-channel ban/restrict enforcement.** Data exists (#1/#2) but is bypassed on the
  shared estate channel today; enforcing it there is inseparable from #11.
- **#12 Estate-settings-changed invalidation event.** No granular event exists for estate
  ban/manager/`AllowVoice` changes; a voice cache has nothing to subscribe to.
- **#14 Coarse-location vs voice-hiding policy.** A pre-existing divergence (coarse dots
  ignore `SeeAVs`); the voice design must pick which behavior to match.

---

## Explicit ambiguities / caveats carried from the code

- `EstateSettings.TaxFree` is a misnomer for `!AllowAccessOverride`
  (`EstateSettings.cs:205`); it short-circuits ban/restrict/voice-parcel checks. Do not read
  it literally.
- `CanBeOnThisLand` enforces ban only below `BanLineSafeHeight` and uses `else if`, so a
  banned avatar above the safe height skips the restriction check
  (`LandObject.cs:759`–`:763`). Voice logic keyed on `IsBannedFromLand`/`IsRestrictedFromLand`
  directly avoids this height coupling.
- Coarse locations are not `SeeAVs`-filtered (`SceneGraph.cs:256`), so "hidden" avatars
  already appear as map dots.
- `TriggerLandObjectUpdated` forwards to `TriggerLandObjectAdded` (`EventManager.cs:1924`) —
  there is no distinct parcel-updated event.
- `currentParcelUUID` is written from several paths without `parcelLock`
  (`ScenePresence.cs:1699`, `:2249`, `:2260`); treat concurrent reads as
  eventually-consistent.
- WebRTC provisioning has two `ProvisionVoiceAccountRequest` overloads; the 3-arg one throws
  `NotImplementedException` (`WebRtcJanusService.cs:364`). Confirm the live service path
  before depending on a specific overload (§3.3).

---

## ADDENDUM — Re-baseline verification (2026-08-16)

**Scope.** This addendum records a re-verification of the citations above against the current
tree; it does **not** rewrite the body. The body's `path:line` citations remain relative to the
original baseline commit `0bdeb0bf08`.

| | |
|---|---|
| Verified against | `1696a6ecff` (`Voice: coalesce concurrent room creation…`) |
| Branch | `fix/room-selection-race` |
| Working tree | clean at survey time |
| Commits since baseline | `be94487b1b` (session-id parse), `1fb63713c5` (PluginName), `dc252686e0` (provision guard), `b3c680d625` (stun-servers), `1696a6ecff` (room-selection race) |

**Semantic result: every rule in §1–§4 still holds.** All *sim-side* files cited (`LandObject.cs`,
`ILandObject.cs`, `LandData.cs`, `LandManagementModule.cs`, `ScenePresence.cs`, `SceneGraph.cs`,
`Scene.cs`, `EventManager.cs`, `EstateSettings.cs`) and the Vivox/FreeSwitch modules are
**untouched** by the five commits — their line numbers are unchanged. Only the four WebRTC/Janus
files moved, and only line numbers drifted; the logic (gates, hashing, flat symmetric membership,
the estate-channel ban bypass) is unchanged. Two items need a substantive note (C, D below).

### A. Line-number drift — `WebRtcVoiceRegionModule.cs` (`b3c680d625` inserted ≈+15 lines above the gate)

| Cited in doc | Was | Now |
|---|---|---|
| §3.1 AllowVoice estate gate | `:233` | `:248` |
| §1.3/§1.4/§3.1 parcel `AllowVoiceChat` gate (`TaxFree` read) | `:274` | `:289` |
| §1.3/§3.1/§3.2 `UseEstateVoiceChan` test | `:282` | `:297` |
| §3.1 `map.Remove("parcel_local_id")` (estate branch) | `:282`–`:284` | `:299` |
| §3.1/§3.2 `else if` ban/restrict (`IsRestrictedFromLand`/`IsBannedFromLand`) | `:286`–`:293` | `:301`–`:308` |
| §3.1 `GetLandObject(parcelID)` (client-supplied parcel) | `:256`–`:258` | `:273` |
| §3.1 `//do fully not trust viewers voice parcel requests` | `:230` | `:245` |
| §3.3 3-arg `ProvisionVoiceAccountRequest(map, agentID, RegionID)` call | `:299` | `:314` |

### B. Line-number drift — Janus files

`JanusAudioBridge.cs` (`1fb6371` + `1696a6e`, heavy):

| Cited | Was | Now |
|---|---|---|
| §3.1/§3.3 `CalcRoomNumber` | `:141`–`:167` | `:194`–`:220` |
| §3.1 `REGION_ROOM_ID = -999` | `:134` | `:175` |
| §3.1 `Math.Abs(hash)` | `:165` | `:218` |
| §3.3 `SelectRoom` | `:168`–`:181` | `:221`–`:238` |
| §3.3 `CreateRoom` (486-reuse retained) | `:80`–`:116` | `:92`–`:155` |
| §3.3 `DestroyRoom` | `:118`–`:131` | `:157` |

`JanusMessages.cs` (`be94487`): §3.2 `AudioBridgeJoinRoomReq` `:524`–`:533` → `:533`–`:541`
(still only `request`/`room`/`display`); §3.2 room-create params `:492`–`:511` →
`AudioBridgeCreateRoomReq` `:506`–`:515` (`is_private=false`, `spatial_audio`, `sampling_rate`,
`denoise`, `record` all present; a `"permanent":false` field was added — no allow/deny list).

`WebRtcJanusService.cs` (`1fb6371` + `1696a6e`): §3.3 4-arg `ViewerSession` overload `:191` →
`:200` (sync wrapper; async body `ProvisionVoiceAccountRequestBAD` at `:205`); §3.3 3-arg
`NotImplementedException` `:364`–`:367` → `:377`–`:379`; §3.1/§3.3 `REGION_ROOM_ID` default
`:227` → `:236`. The §3.3 ambiguity flag still stands verbatim.

Note: `dc252686e0` (provision guard) touched `WebRtcVoiceServiceModule.cs`, which the body does
**not** cite. It changes create-vs-lookup handling of a zero/empty `viewer_session`; it does **not**
touch the parcel/estate/ban gate. No effect on §1–§4.

### C. §3.3 room-race description is superseded (behaviour changed, not just line numbers)

The body's §3.3 line "*a create race is resolved by destroying the loser and adopting the existing
room (:186–210)*" describes the **pre-`1696a6ecff`** logic. Current behaviour: same-process racers
are coalesced through `_roomCreateLocks`/`_knownRooms` (`JanusAudioBridge.cs:187`–`:188`,
`SelectRoomCoalesced` `:240`) so N racers collapse to one Janus `create`; the cross-process race is
covered by `CreateWithRecheck` re-attempting and reusing on Janus error 486 (`:104`–`:114`,
`:130`); stale hints are cleared via `ForgetRoom`. The room *identity* rule (deterministic
`CalcRoomNumber` hash; `-999` for the estate channel) is unchanged, so no §4 requirement is
affected — this note exists only so a reader doesn't look for the old destroy-the-loser path.

### D. Correction to #12 — an estate-change event DOES exist (contradicts the OPEN-items claim)

Row 12 / the OPEN-items list states *"no granular event found … a voice cache has nothing to
subscribe to."* **The second clause is wrong.** `IEstateModule.OnEstateInfoChange`
(`Source/OpenSim.Region.Framework/Interfaces/IEstateModule.cs:41`, delegate
`ChangeDelegate(UUID regionID)` at `:35`) exists and fires on estate **access deltas — including
bans and managers** — from `ExecDeltaRequests` (`EstateManagementModule.cs:871`), as well as
estate owner/name/region-link/settings changes (`:370`, `:414`, `:443`, `:2221`, `:2250`). A voice
cache *can* subscribe today via `scene.RequestModuleInterface<IEstateModule>()`.

What is genuinely missing is **granularity**: the delegate carries only the region UUID, not what
changed, so a subscriber must re-read `RegionInfo.EstateSettings` and diff. The `AllowVoice`
estate-flags path is **confirmed** to reach a fire site on both viewer transports — legacy UDP
`HandleEstateChangeInfo` sets `AllowVoice` (`EstateManagementModule.cs:2205`/`:2207`) → store
`:2214` → `TriggerEstateInfoChange()` `:2221`; CAP `handleEstateChangeInfoCap` sets it (`:2239`) →
store `:2246` → `TriggerEstateInfoChange()` `:2250`. So #12 should read: *"a coarse region-scoped
event exists (`OnEstateInfoChange`) and it does fire for voice-enable, bans, and managers; the gap
is a payload-carrying event."* This is the correction that reshapes the #12 design in
`mixer-feed-protocol.md`.

### E. Second structural defect — under `TaxFree` the provision gate consults NO per-parcel voice/ban/restrict deny

This addendum records the second reportable finding explicitly (§1.4 and §1.1 already carry the raw
material; this spotlights it). Estate **`TaxFree`** (the `!AllowAccessOverride` misnomer, §1.4)
voids every per-parcel *deny* in the WebRTC provision path:

- **Parcel voice-disable is overridden.** `WebRtcVoiceRegionModule.cs:289` (was `:274` at baseline)
  gates the parcel-voice check behind `!EstateSettings.TaxFree`:
  `if (!TaxFree && (land.Flags & ParcelFlags.AllowVoiceChat) == 0) → deny`. When `TaxFree` is set
  the clause is false, so a parcel that has **explicitly cleared `AllowVoiceChat`** (voice off) is
  still provisioned voice. The parcel owner's deny-voice is never consulted.
- **Parcel ban/restrict self-nullifies.** Even on the per-parcel channel where the ban/restrict
  check *is* reached (`:301`), `IsBannedFromLand` / `IsRestrictedFromLand` **return `false` under
  `TaxFree`** via their common exemption preamble (`LandObject.cs:724`, `:842`; §1.1). So the `:301`
  check is a no-op whenever `TaxFree` is set.
- **Net:** under estate `TaxFree`, anyone who can be present in the estate is provisioned voice
  regardless of per-parcel voice setting, parcel ban, or parcel restriction.

**Scope / honesty:** this is the **shared cross-module contract** — the identical enable gate and
the same `TaxFree` preamble govern Vivox and FreeSwitch too (§3.1), so it is a *design consequence
of the `TaxFree` misnomer*, **not** a WebRTC-specific regression. It is reportable because (a) the
flag's name hides that it silently disables all per-parcel voice/ban control estate-wide, and
(b) it is **distinct from #13**: #13 bypasses the `:301` check on the *estate/shared channel*
irrespective of `TaxFree`, whereas this voids the deny even on *per-parcel channels*, specifically
when `TaxFree` is set. A per-listener visibility matrix (see `mixer-feed-protocol.md`) that derives
from the sim predicates would inherit the `TaxFree` exemption *by construction* — so honoring or
overriding it becomes an explicit policy choice rather than a silent gate.

### F. Phase-3a implementation decisions (VoiceStateFeeder)

Recorded here so the shipped behavior is traceable to a decision, not an accident. Both are
implemented in `Addons/os-webrtc-janus/Visibility/` (`VisibilityRules`) with tests in
`Tests/WebRtcJanusService.Tests/VoiceStateFeederTests.cs`.

- **SeeAVs hiding is implemented SYMMETRIC — pending SL verification.** The visual model is
  one-way at the source (§1.2); for voice the feeder excludes a pair if *either* party is on a
  `SeeAVs=false` parcel (and they are on different parcels), so a listener never hears someone
  they cannot see. This is **implemented-symmetric-pending-SL-verification**: whether stock SL
  voice hides symmetrically is not yet confirmed in-world. The rule is isolated behind
  `VisibilityRules.SeeAvsHidesSymmetric` with an ambiguity comment; if SL proves one-way, drop the
  second disjunct there and this reverts to source-only. (Decision 1b.)
- **The `TaxFree` ban void is FIXED in the feeder — overriding the "inherit by construction"
  default described in §E above.** The matrix evaluates parcel ban/restrict on ban-list
  *membership* (position-independent: a banned avatar physically inside the banning parcel is still
  excluded — tested) and, in production, **without** the `TaxFree` short-circuit, so a parcel ban
  is honored in voice even on a `TaxFree` estate. The admin/EM/owner/self exemptions are retained.
  Note this deliberately introduces a voice-vs-visual divergence under `TaxFree` (voice enforces a
  parcel ban that the visual/access layer does not); it was chosen over §E's parity default per
  explicit decision. The parcel voice-**enable** `TaxFree` override (§E first bullet) is **not**
  changed — only the ban/restrict void. (Decision 2b.)

### G. Neighbour-region voice rooms — a semantics gap (child-agent room, 2026-08-16)

Recorded here because an in-world baseline capture (Ebony + Transylvania, two avatars) surfaced a
case §1–§4 does **not** model. Nothing above is amended; this documents where the room model and
reality diverge, and pins the open decision.

**The finding.** A single avatar occupies **one voice room per adjacent region**, not one room:

- The **viewer** establishes one WebRTC voice connection per neighbouring region *by design* —
  `LLWebRTCVoiceClient::updateNeighboringRegions` (`indra/newview/llvoicewebrtc.cpp:665`), comment
  *"Estate voice requires connection to neighboring regions"* (`:674`), with a Firestorm opt-out
  `FSDisableNeighbourRegionConnections` (`:681`, off by default). It **renders** neighbour audio:
  `mPrimary` gates only the **microphone** (`setMute(mMuted || !mPrimary)` at `:3116`, which acts on
  the peer connection's **senders** — `indra/llwebrtc/llwebrtc.cpp:1342`, `track->set_enabled`), and
  **not** the receive path (`setReceiveVolume(mSpeakerVolume)` is set for every spatial connection at
  `:3065`, acting on the **receivers** — `llwebrtc.cpp:1388`). So neighbour-room audio is played, not
  suppressed; the participant-roster dedup for non-primary servers (`:3289`–`:3294`, `:3319`) is
  metadata only, not the audio path.
- The **sim** does not gate provisioning on child vs root: `ProvisionVoiceAccountRequest` fetches the
  presence but never checks `sp.IsChildAgent` (`Addons/os-webrtc-janus/WebRtcVoiceRegionModule.cs:299`).
  So a **child agent** in the neighbour region provisions a **distinct room** there
  (`CalcRoomNumber(neighbourRegionId, "local", -999)` — a different room from the root region's).

**Consequence — the §3.1 room model does not match reality.** §3.1 (`:444`–`:446`) frames voice as
"one region/estate channel keyed on the region … all parcels that resolve to the estate channel
share a single room," and §2.3 (`:339`) frames cross-region voice as *children enumerated into this
region's matrix*. Both describe a **single** estate room per region with children folded in. In fact
an avatar near a region border is a live participant in **each adjacent region's own room**, and
**each region's feeder governs only its own room** (one `VoiceVisibilityService` per `Scene`, keyed on
that Scene's `RegionID`). The "fold children in" mental model is intra-region; it says nothing about
the second room the child simultaneously joins in the neighbour.

**OPEN QUESTION (UNRESOLVED — pending SL verification).** When avatar X is banned from a parcel in
region **A**, and X and Y *also* share region **B**'s neighbour room (both present there as
root/child), should **B**'s matrix exclude the X–Y pair?

- **Reading 1 — ban is land-scoped.** B's rules are B's. A parcel ban in A has no jurisdiction over
  B's parcels; B's matrix should exclude the pair only if B's own land/estate predicates say so.
  Under the **current** design this is what happens — each region's feeder derives from its own land,
  and a neighbour room is not adjusted for another region's bans.
- **Reading 2 — ban intent is to silence the pair.** The operator banning X intends X and Y not to
  hear each other; a neighbour room that carries their audio un-excluded is a bypass of that intent,
  regardless of which land the room is keyed to.

This is left **UNRESOLVED pending in-world SL verification**, the same treatment as the
symmetric-`SeeAVs` decision in §F: record both readings, change no normative rule, and isolate the
choice for when SL behaviour is confirmed.

**Enforcement requirement that follows (independent of the open question).** Whatever the ban answer,
the feeder/sender must be **enabled in every loaded region**. Because each region governs only its own
room, a region whose emission is disabled leaves its room — including any neighbour room an adjacent
region's avatars occupy — as an **unenforced path**: exclusions computed elsewhere never reach it.
Per-region emission is therefore a coverage requirement, not a per-region convenience.

---

## ADDENDUM 2 — Phase 3a live verification (2026-08-17)

**Scope.** Records live in-world observations from the Phase 3a acceptance run. It does **not**
rewrite the body or the 2026-08-16 addendum, and resolves no open question. Subsection letters
continue the A–G sequence of the first addendum so that bare-letter references (`§F`, `§H`) remain
unambiguous.

| | |
|---|---|
| Tree | `D:\tranquillity-develop` |
| Branch | `feature/voice-visibility-matrix` |
| Commit | `6935d941f8` (post-merge of `fix/visibility-emission-stall`) |
| Build configuration | **Debug** — load-bearing; the `Debug.Assert` tick-thread guard (`VoiceStateFeeder.cs:112`) compiles out in Release |
| Mixer | `legion-voice-mixer-janus-1`, plugin 0.7.0 |
| Region | Ebony `c44606b1-43e1-45fb-8ae8-201545dc2f6a`, estate room `226001844` |

> Note: a §I "neighbour-room data point" was drafted for this addendum and **deleted before
> recording** — at the 15:35 reading Aleric was present only in Ebony's room (`226001844`), not in
> Transylvania's (`1578726032`), so Legion's neighbour-room `excluded_entries=0` was trivially
> expected and said nothing about §G. §G remains UNRESOLVED. Subsections were renumbered so H/I/J
> stay contiguous.

### H. Ban-derived exclusion is applied symmetrically end-to-end — OBSERVED (2026-08-17)

**This does not verify §F Decision 1b.** Decision 1b concerns `SeeAVs` hiding, and its pending item
is *stock SL* behaviour. That remains unverified; observing our own symmetric implementation behave
symmetrically is not evidence about SL. The observation below is on the **ban/restrict** path and
confirms only that symmetry computed in the feeder survives serialization and mixer application.

**Conditions.** Ban entry added on Ebony parcel `LocalLandID 2` naming Aleric Fenwood only. Legion
Hienrichs is estate owner and therefore ban-exempt via the §1.1 exemption preamble; no reciprocal
entry existed or could exist.

**Evidence chain, one continuous sequence:**

```
feeder: seq=2170 @ 15:35:12,848  listeners=2 exclPairs=2 delta=+2/-0
sender:          @ 15:35:12,852  path=delta op=add result=Ok entries=2
mixer:           @ 15:35:57      excluded_entries=1 on BOTH Ebony handles
                                 room 226001844, last_mode=add, epoch 47
```

`last_mode=add` establishes a live delta on a running room rather than a bootstrap snapshot. The room
number matches `CalcRoomNumber(Ebony)`, which closes the §3.3.1 false-positive loophole in which an
unknown room returns `applied` regardless.

**Confirmed by ear:** mutual silence, two machines on separate network paths, stock Firestorm 7.2.2
both sides.

**What is established:** a one-sided ban entry yields exclusion on both listeners' handles. A pipeline
that computed symmetric pairs but applied only one direction at the mixer would have shown
`excluded_entries=1` on one handle and `0` on the other.

### I. Parcel-ban scope — observed behaviour (2026-08-17)

A parcel ban excludes the banned avatar from **all occupants of that parcel**, not only from the
parcel/estate owner who added the entry.

**Evidence.** A third avatar (unbanned, voice on) entered Ebony and stood on the banning parcel while
the §H ban was in force:

| Listener | Result |
|---|---|
| Third avatar | `frames_mixed=96 / rtp_out=96` — receiving Legion's audio |
| Aleric (banned) | `frames_mixed=0 / rtp_out=0` — same talker, same room |

One speaker, two listeners, different outcomes: per-listener culling demonstrated, and not a room that
has gone dead.

Resulting topology: pair set `{Legion↔Aleric, third↔Aleric}`, `exclPairs=4`, per-listener `EXCL`
1/2/1.

This follows from §F Decision 2b evaluating ban-list **membership** per listener pair rather than per
ban-entry author, and is internally consistent with symmetric exclusion. Stated here as observed
behaviour; it is **not** promoted to a normative rule. If it should be normative, that belongs in the
body under a revision.

### J. Instrument notes

- **`exclPairs` counts directed pairs.** §H's single unordered pair reads as `2`; §I's two unordered
  pairs read as `4`. Confirmed by §I's per-listener `EXCL` 1/2/1 summing to 4. Do not read it as a
  count of ban entries.
- **`excluded_entries` on a mixer handle is the visibility-exclusion signal.** `peer_ctl_entries` is
  the viewer mute/gain set and is **not** a visibility exclusion. Conflating the two has cost time.
- **`last_mode`:** `add`/`remove` = live delta; `replace` = snapshot (bootstrap or join-resync).
- **`frames_mixed`/`rtp_out` are cumulative** and advance only while someone is actually talking. A
  static counter during silence is indistinguishable from a broken path. N=1 rooms mix to silence by
  design.
- **`Ok` ≠ applied.** An unknown room returns `{"slvoice":"applied"}` with `entries` = the *parsed*
  count. Application is confirmable only via `excluded_entries` on a live handle whose room matches
  `CalcRoomNumber`.

### Provenance caveat added 2026-08-18 — §H and §I evidence is not attributable to a commit

The `tick-built` / `sendresult` / `exclPairs` telemetry cited in §H and §I does not
exist in any commit. A search of the full history (`git log -S`) finds those strings
nowhere in `Addons/os-webrtc-janus/`; they exist only on the local, never-merged
`diag/visibility-tick-heartbeat` branch. The region server process that produced the
15:35 and 15:53 readings started at 15:25:38 and was running a locally-instrumented
build whose source was never committed.

The Commit row above (`6935d941f8`) is therefore **wrong for this evidence**. Those
binaries were written at 17:39:39 and did not go live until the 17:41:07 restart —
after both readings. The build that produced §H and §I cannot be identified: its dlls
were overwritten by the 17:39 rebuild, and no per-plugin version line is logged.

Consequences, per section:

- **§H** — the feeder-side chain (`seq`, `exclPairs`, `path=delta op=add`) is
  unattributable. The mixer-side observation (`excluded_entries=1` on both handles,
  `last_mode=add`, room `226001844`) and the by-ear result do not depend on the
  instrumented build and stand as observations, but not as observations *of a known
  commit*. Re-verified against a known commit on 2026-08-18 — see §K.
- **§I** — the mixer-side evidence (`frames_mixed`/`rtp_out` diverging between two
  listeners of one talker) is independent of the instrumented build and stands. The
  `exclPairs=4` and per-listener `EXCL` 1/2/1 figures are feeder-side and are
  unattributable. **The third-avatar culling test has not been re-run against a known
  commit.**

Nothing in §H–§J is deleted. This caveat records what the evidence can and cannot
support.

---

## ADDENDUM 3 — Phase 3a re-verification and a mixer defect (2026-08-18)

**Scope.** Re-verifies §H against a known commit, records a neighbour-room data point
that §G's open question can actually use, and documents a mixer-side defect found in
the process. Append-only; no normative rule is changed and §G remains unresolved.

| | |
|---|---|
| Tree | `D:\tranquillity-develop` |
| Branch | `feature/voice-visibility-matrix` |
| Commit | `3b0e03bcd1` |
| Build configuration | **Debug** — load-bearing; the `Debug.Assert` tick-thread guard (`VoiceStateFeeder.cs:112`) compiles out in Release |
| Deployed artifact | `WebRtcVoiceRegionModule.dll`, SHA256 `016FE179…`, built 21:50:20, deployed 21:50:20, region server restart 21:54:14 (2026-08-17) |
| Mixer | `legion-voice-mixer-janus-1`, plugin 0.7.0 |
| Region | Ebony `c44606b1-43e1-45fb-8ae8-201545dc2f6a`, estate room `226001844` |

### K. Symmetric ban enforcement re-verified against a known commit (2026-08-18)

**Conditions.** Estate `TaxFree = 0` and Ebony parcel `LocalLandID 2` carries
`UseBanList` (`LandFlags & 1024`), both confirmed by direct query before the run — the
two preconditions that would otherwise void the test via the §1.1 exemption preamble.
Ban entry names Aleric Fenwood only; Legion Hienrichs is estate owner and ban-exempt.
Stock Firestorm both sides.

**Enforced state.** Room `226001844`, exactly two handles, one per avatar, both
`ice_state: connected`:

| Handle | Display | `excluded_entries` | epoch | `last_mode` |
|---|---|---|---|---|
| `6155889928500904` | Legion `4fbdfd2a…` | **1** | 33 | `replace` |
| `5851260946832861` | Aleric `4dc144cb…` | **1** | 33 | `replace` |

**By ear:** neither avatar could hear the other. Microphone waveform active on both,
confirming capture and transmission — the silence is enforcement, not a dead audio
path.

**Removal as a live delta.** The ban entry was removed and the same two handles
re-read:

| Handle | `excluded_entries` | epoch | `last_mode` |
|---|---|---|---|
| `6155889928500904` | **0** | 34 | `remove` |
| `5851260946832861` | **0** | 34 | `remove` |

`last_mode: remove` at an advanced epoch establishes a live delta on a running room.
Legion could hear Aleric again after removal. The reverse direction was **not**
confirmed by ear: Legion's viewer lost its microphone (greyed, receive path intact) at
this point and re-provisioned, so audibility of Legion by Aleric post-removal is
unrecorded.

**What this establishes and what it does not.** Symmetric enforcement is confirmed
against a known commit, from a snapshot (`replace`, epoch 33) and released as a live
delta (`remove`, epoch 34). **The `add` delta path was not re-verified** — the session
ended before the ban was re-applied. §H's `add` observation remains the only one, and
it is unattributable per the caveat above.

### L. §G neighbour-room data point — question REMAINS OPEN (2026-08-18)

Throughout §K's enforced state, both avatars simultaneously held handles in
Transylvania's room `1578726032` — Aleric `7842546127144060`, Legion
`7796732499225250` — and **both read `excluded_entries: 0` at epoch 32**, unchanged
across Ebony's transitions to epochs 33 and 34.

This is a usable data point on §G, unlike the 2026-08-17 reading (which was discarded
because the second avatar held no handle in the neighbour room). Both parties to the
excluded pair were present in the neighbour room, and the neighbour room carried no
exclusion. That is **Reading 1** — ban is land-scoped — observed in the current
implementation.

It is **not** a resolution. §G asks what the semantics *should* be, and that remains
UNRESOLVED pending in-world SL verification. This records what the implementation
currently does, which was previously undocumented.

### M. Mixer resolves exclusions by display string and silently drops one participant on collision

**Observed 2026-08-18, before the §K run.** Enforcement failed in one direction: the
banned avatar could not hear the talker, the talker heard the banned avatar normally.

Room `226001844` held **three** handles for two avatars:

| Handle | Display | ICE | `rtp_in_count` | `excluded_entries` |
|---|---|---|---|---|
| `2992794682240457` | Aleric | connected | 1142 | 1 |
| `1984643388050503` | Legion | **disconnected** | 0 | **1** |
| `3957655785919019` | Legion | connected | 0 | **0** |

All three at epoch 24 from one snapshot: the exclusion was computed, delivered, and
applied — to Legion's orphaned handle rather than his live one. The orphan survived a
full avatar relog.

**Mechanism.** `janus_slvoice_apply_visbatch` indexes the room into a temporary
`by_display` table keyed on `p->display`
(`D:\legion-voice-mixer\src\janus_slvoice.c:945`–`:953`) and resolves each entry's
listener against it (`:957`). `display` is the avatar UUID string (`:262`), which is
**not** the participant identity — `room->participants` is keyed on `user_id` (`:219`,
`:260`). The exclusion path resolves through a value not guaranteed unique within a
room. `g_hash_table_insert` replaces on duplicate key, so two sessions sharing a
`display` collapse to one; the other silently receives nothing. Which survives is last
insert wins in GLib hash-iteration order, **which GLib does not define**.

**Why it is silent.** The overwrite during index build (`:952`) emits no log, and the
surviving entry is applied and counted normally — `excluded_entries` advances on a
handle and every counter reads healthy. Nothing distinguishes "applied to the right
handle" from "applied to the wrong one."

**This is the failure mode §H named as its control:** *a pipeline that computed
symmetric pairs but applied only one direction at the mixer would have shown
`excluded_entries=1` on one handle and `0` on the other.* §H's run had no duplicate
handle present; this run did. It is the realization of that case, not a contradiction
of §H.

**Confirmed by elimination.** Clearing mixer session state so each avatar held exactly
one handle restored correct symmetric enforcement (§K) with no change to the sim, the
feeder, or the matrix. The matrix was correct throughout; the mixer applied its output
to the wrong participant.

**Origin of the duplicate — cross-reference only.** The orphaned handle is consistent
with the unwired `OnRemovePresence` teardown recorded in `KnownDefects.md`, which
leaves sim-side presence removals without a corresponding `LeaveRoom`. That defect is
C#-side and **was not confirmed against mixer code**; nothing in the mixer repository
corroborates the origin. Treat as a pointer, not a finding.

**Suggested first step.** Decide whether the wire format should carry participant
identity rather than display, or whether an entry should apply to **all** participants
matching a display. At minimum, detect the collision at `janus_slvoice.c:952` and log
it — a silent overwrite in an enforcement path should never be silent.

### N. Instrument note — `epoch`

`epoch` on a handle's `visibility` block increments per applied batch and is the
field that distinguishes "the feeder never recomputed" from "it recomputed and the
result was wrong." Two uses proven on 2026-08-18: identical epochs across handles with
differing `excluded_entries` isolated the fault to application rather than derivation
(§M); and an unexpected jump of twelve epochs revealed repeated join/leave churn during
a viewer re-provision that was otherwise invisible.
