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
