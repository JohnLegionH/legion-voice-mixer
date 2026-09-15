# TURN delivery to viewers: finding

**Slice A.4 Part 1, 2026-09-14. Finding (b): no mechanism exists.** No stock viewer takes TURN
servers or TURN credentials from the sim. Part 2 (sim-side TURN delivery) was **not built**, as the
brief directs. No code, build, deploy or restart was part of this slice. The result is recorded as
**O-87, BLOCKED** under the stock-viewer compatibility constraint (ledger §4). This document is
mirrored byte-identical at `D:\legion-voice-mixer\docs\voice\` (O-28).

---

## 0. Sources read

| label | tree | identity |
|---|---|---|
| **FS-local** | `D:\phoenix-firestorm` | `git describe`: `Firestorm_Beta_7.2.5.81383-15-g895f65ab43` (15 local commits) |
| **FS-rel** | `D:\AI Tools\Firestorm Viewer working Code\phoenix-firestorm` | `git describe`: `Firestorm_Release_7.2.2.79439-279-gf36da6af09` |
| **FS-copy** | `D:\AI Tools\Firstorm Viewer Source Code` | no git; the voice files match FS-rel line for line at every line cited |
| **LL** | `D:\AI Tools\Second Life Viewer Source Code\viewer-develop` | no git |
| **SIM** | `D:\tranq-ais` | `feature/ais-v3` at `33adc16dc7` |

`D:\SLViewer-Source` is empty. **libwebrtc source is not on this machine.** The viewers link it as
a prebuilt package, so nothing below says what libwebrtc itself does with a configuration (§3).

Viewer paths are under `indra/`. Every `path:line` was read at the identities above.

---

## 1. How the viewer builds its ICE configuration

### 1.1 The structure can carry credentials

`llwebrtc/llwebrtc.h` declares `InitOptions::IceServers` with URLs, a user name and a password:
LL `:255` `struct IceServers {`, `:262` `std::string mUserName;`, `:263` `std::string mPassword;`.
The same struct is at FS-local `:269`/`:276`/`:277` and FS-rel and FS-copy `:265`/`:272`/`:273`.

### 1.2 The wrapper passes them to libwebrtc

`llwebrtc/llwebrtc.cpp` copies each entry into `webrtc::PeerConnectionInterface::IceServer`:
- LL: `:900` `for (auto server : options.mServers)`; `:905` `ice_server.urls.push_back(url);`;
  `:907` `ice_server.username = server.mUserName;`; `:908` `ice_server.password = server.mPassword;`.
- FS-rel and FS-copy: `:1115`, `:1117`, `:1118`.
- FS-local: `:1141`, `:1155`, `:1164`, `:1165`. It also skips an entry with no URLs (`:1160`
  `if (ice_server.urls.empty())`). That is the local commit `26e74c311f` (2026-08-15, "Voice: skip
  empty ICE server URIs…"), not upstream Firestorm.

### 1.3 Nothing fills the credentials

The only producer of `InitOptions` is `getConnectionOptions()` in `newview/llvoicewebrtc.cpp`.

- **LL:** `:2806` declares `IceServers servers;`. After `// TODO: Pull these from login`, it pushes
  hardcoded URLs, `llformat("stun:stun%d.%s.secondlife.io:3478", i, grid.c_str())`, and at `:2820`
  `options.mServers.push_back(servers);`. It reads nothing from the sim. On an OpenSim grid the host
  names are built from that grid's login id under `secondlife.io`.
- **FS-rel and FS-copy:** the same function (`:2868` definition, `:2871` declaration, `:2873` the
  TODO). It reads nothing from the sim.
- **FS-local:** `:2966` declares `servers`. The `<FS:TJ> [FIRE-36421] Add OpenSim STUN server
  support` block (`:2971`–`:2983`), when not in Second Life, sets `:2977`
  `servers.mUrls = regionp->getStunServers();` and pushes the entry (`:2979`). The Second Life path
  pushes at `:2993`.

**Only `mUrls` is ever set.** No line in any tree assigns `mUserName` or `mPassword` (§2).

### 1.4 What the viewer reads from `SimulatorFeatures`

- **FS-local only:** `newview/llviewerregion.cpp:2720`
  `if (mSimulatorFeatures.has("stun-servers"))`, then `:2722`
  `LLStringUtil::getTokens(mSimulatorFeatures["stun-servers"].asString(), mStunServers, ",");`.
  The value is one string, split on commas into URL strings. The accessor is `llviewerregion.h:384`
  `getStunServers()` and the member is `:631`. Nothing else is read from that key: no map, no array,
  no user name.
- **LL and FS-rel:** there is no reader of any ICE-related feature (§2).
- **Where the key came from:** the reader is upstream Firestorm, not a local change. Commit
  `d72e47dc71` (2026-03-15, Hecklezz, "[FIRE-36421] Add custom WebRTC STUN server support for
  OpenSim") is first contained in tag `Firestorm_Beta_7.2.4.80323`. It is not in Release 7.2.2, and
  the LL viewer has no equivalent.

**The viewer logs the whole features map at INFO.** FS-local `llviewerregion.cpp:2615`
`LLSDSerialize::toPrettyXML(sim_features, str);` and `:2616`
`LL_INFOS() << "region " << getName() << " " << str.str() << LL_ENDL;`, and the same at LL
`:2443`–`:2444`. **Any credential placed in `SimulatorFeatures` would be written to the user's
viewer log.** §5 accounts for this.

### 1.5 What the viewer reads from the provision response

The provision response is checked for `viewer_session` and `jsep` only. `result.has("viewer_session")`
at LL `:2782`, FS-local `:2942`, and FS-rel and FS-copy `:2847`. `result["jsep"].has("sdp")` at LL
`:2786`, FS-local `:2946`, and FS-rel and FS-copy `:2851`. No ICE-server key is read.

### 1.6 The ICE configuration is fixed before the provision request

In the connection state machine, the PeerConnection is created with its ICE servers before the
offer exists, and the offer goes out in the provision request.

| step | LL | FS-local |
|---|---|---|
| `case VOICE_STATE_START_SESSION:` | `:2838` | `:3011` |
| `initializeConnection(getConnectionOptions())` | `:2852` | `:3025` |
| `case VOICE_STATE_WAIT_FOR_SESSION_START:` (offer arrives: `OnOfferAvailable`, LL `:2497`) | `:2859` | `:3032` |
| `case VOICE_STATE_REQUEST_CONNECTION:` (provision request carries the offer) | `:2868` | `:3041` |
| `case VOICE_STATE_CONNECTION_WAIT:` (answer from the provision response) | `:2883` | `:3056` |

**Consequence:** even a viewer that parsed ICE servers from the provision response would get them
after its PeerConnection was configured. Adding them there would need a configuration change and
an ICE restart in the viewer, not only a new field.

---

## 2. Greps that returned zero

Every command was run against every tree named in its row.

| search | trees | result |
|---|---|---|
| `grep -c -E '\.mUserName *=\|\.mPassword *=\|IceServers'` over `newview/llvoicewebrtc.cpp`, `newview/llvoicewebrtc.h`, `newview/llviewerregion.cpp` | all four | **1 per tree**: the `IceServers servers;` declaration (LL `:2806`, FS-local `:2966`, FS-rel `:2871`, FS-copy `:2871`). **0 assignments.** |
| `grep -rn --include='*.cpp' --include='*.h' -E 'IceServers\|mServers\b\|\.mUserName\|\.mPassword'` over all of `indra/`, excluding the struct in `llwebrtc.h` | LL, FS-local | only the `llwebrtc.cpp` copy lines (§1.2) and the `llvoicewebrtc.cpp` declaration and push lines (§1.3). **No other writer.** |
| case-sensitive `turns?:\|"turn"\|\bTURN\b\|turn_server\|ice_servers\|iceServers\|turn_user\|turn_pwd\|"credential"\|"username"` over `newview/llvoicewebrtc.cpp/.h`, `newview/llviewerregion.cpp/.h`, `newview/llvoicechannel.cpp`, `llwebrtc/` | all four | **0 matches** |
| `grep -rn --include='*.cpp' --include='*.h' --include='*.xml' -E '"turns?:\|turns?:[a-z0-9]\|"turn-servers"\|"turn_servers"\|"ice-servers"\|"ice_servers"\|iceServers'` over all of `indra/` | LL, FS-local | **0 matches** |
| `grep -rn --include='*.cpp' --include='*.h' -E 'stun-servers\|stun_servers'` over all of `indra/` | LL, FS-local | **LL: 0 matches.** FS-local: `llviewerregion.cpp:2720` and `:2722` only |

A case-insensitive search for `turn` is useless here, because it matches `return`.

The whole-tree sweeps (rows 2, 4 and 5) were run on LL and FS-local only. FS-rel and FS-copy got
the voice-file sweeps (rows 1 and 3), where their `getConnectionOptions` is the LL shape.

---

## 3. A `turn:` URL in `stun-servers`: not a workaround (unverified part marked)

**From viewer source:** the FIRE-36421 path accepts any comma-separated string as URLs. A
`turn:host:3478?transport=udp` entry would reach libwebrtc as an `IceServer` whose `username` and
`password` are empty strings (§1.2, §1.3). A TURN URI has no place for credentials (RFC 7065), so
they cannot ride inside the URL.

**Not verified:** what libwebrtc does with a TURN entry that has empty credentials. It might reject
the whole configuration, which would make `CreatePeerConnection` fail and voice dead. Or it might
drop that one server. The decision is made in libwebrtc's ICE-server parsing, which is not on this
machine. From memory, upstream libwebrtc refuses a TURN server with an empty user name or password
as an invalid parameter. **That is recollection, not a read.**

**Either way the sim must not do it.** At best the entry is useless, since coturn refuses an
unauthenticated Allocate. At worst it breaks voice for every Firestorm 7.2.4+ viewer in the region.
The existing module comment already records that a bad ICE entry fails `CreatePeerConnection`
(SIM `WebRtcVoiceRegionModule.cs:243`–`:244`).

---

## 4. The sim side as it stands

- **Config and emission:** `Addons/os-webrtc-janus/WebRtcVoiceRegionModule/WebRtcVoiceRegionModule.cs`
  reads `:126` `m_StunServers = m_Config.GetString("StunServers", string.Empty);`. It emits the key
  once per region, when the region is added (`:237` gets `ISimulatorFeaturesModule`; `:245`–`:248`
  `simFeatures?.AddFeature("stun-servers", OSD.FromString(m_StunServers));`). The config lines are
  `os-webrtc-janus.ini:20` and `os-webrtc-janus.ini.example:26`,
  `StunServers = stun:stun.l.google.com:19302`.
- **A per-agent hook already exists:**
  `Source/OpenSim.Region.Framework/Interfaces/ISimulatorFeaturesModule.cs:33`
  `public delegate void SimulatorFeaturesRequestDelegate(UUID agentID, ref OSDMap features);`,
  event `:40`. It is raised per request at
  `Source/OpenSim.Region.ClientStack.LindenCaps/SimulatorFeaturesModule.cs:299`–`:301` and used by,
  for example, `Source/OpenSim.Region.OptionalModules/ViewerSupport/GodNamesModule.cs:122`/`:126`.
  So credentials could be bound to an agent without new sim plumbing. §1.4's logging point is why
  §5 still does not recommend this route for secrets.

---

## 5. Proposed upstream change (not built)

The change needed is in the **viewer**, and it has to land upstream (Firestorm first, since
FIRE-36421 is the existing OpenSim hook, then LL) before any sim work is worth doing.

**Recommended: a per-agent capability read at session start.**

1. **Viewer:** request a new region capability, for example `VoiceIceServers`, in
   `VOICE_STATE_START_SESSION`, before `initializeConnection(getConnectionOptions())`
   (LL `:2852`, FS-local `:3025`). Each (re)connection then fetches fresh, short-lived credentials,
   which is the only placement that satisfies "regenerated per provision" given §1.6.
   - **Response:** an LLSD array of maps
     `{ "urls": [string, …], "username": string, "credential": string }`. The names follow W3C
     `RTCIceServer`.
   - **Handling:** `getConnectionOptions()` pushes one `IceServers` per map, setting `mUrls`,
     `mUserName` and `mPassword`.
   - **Fallback:** when the cap is absent or fails, the current `stun-servers` behaviour.
   - **Logging:** the cap response must not be logged. A capability response, unlike
     `SimulatorFeatures` (§1.4), is not already dumped at INFO.
2. **Sim, once a viewer ships it:** the brief's Part 2 through that cap.
   - TURN config beside `StunServers`, static and shared-secret.
   - An expiry past 2^31 is refused.
   - Credentials are bound to the requesting agent and short-lived (`expiry:agent`,
     `base64(HMAC-SHA1(secret, username))`, the coturn REST scheme already tested mixer-side in A.3).
   - No credential is logged at any level.
   - The `SimulatorFeatures` payload stays byte-identical when TURN is not configured.

**Simpler, weaker alternative:** a `SimulatorFeatures` key `ice-servers` with the same array shape,
parsed in `LLViewerRegion::setSimulatorFeatures` beside `:2720`–`:2722`. Two problems follow from
source. The map is logged at INFO (§1.4), so credentials would reach the user's log file unless
that dump is also changed. And features are fetched on region entry, so a credential would have to
outlive the whole stay in the region instead of one connection.

**Not proposed:** ICE servers in the provision response. They would arrive after the PeerConnection
is configured (§1.6).

---

## 6. Status and what still works

- **O-87: BLOCKED**, a stock-viewer compatibility gap (ledger §4.1). It unblocks when an upstream
  viewer release reads ICE credentials from the sim before `VOICE_STATE_START_SESSION`.
- **A viewer that can reach the mixer's UDP ports** needs no TURN and is unaffected. That is
  host, srflx or prflx with the A.1 address discovery.
- **Mixer-side TURN (A.3)** remains useful for the mixer's own reachability. As recorded in A.3, it
  gives a UDP-blocked viewer no path.
- **Firestorm 7.2.4+ on this grid** keeps using the STUN list the sim already sends.
