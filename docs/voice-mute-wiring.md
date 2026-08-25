# Firestorm WebRTC voice mute / gain wiring — source trace

Read-only investigation of the Firestorm fork at `/d/phoenix-firestorm`
(branch matching the local checkout). All line numbers below are quoted from
**that** tree. Goal: determine whether any stock-viewer user action causes the
`"m"` (mute) or `"ug"` (gain) SLData messages to be emitted on the WebRTC voice
data channel, which our server-side mixer reads.

## TL;DR / Bottom line (answer to Q4)

**Yes — stock Firestorm _does_ send `"m"` and `"ug"` on the WebRTC data
channel.** The crux hypothesis ("FS voice mute is client-side playback-only and
never notifies the server") is **false at the source level**: the per-listener
mute path is wired straight through the viewer's **block/mute-list**
(`LLMuteList`), not a separate voice toggle, and it emits `"m"` whenever an
`AGENT` mute entry changes while a WebRTC voice session is up.

- **`"m"` is emitted** by *any* change to `LLMuteList` for an avatar — i.e.
  right-click **Block/Unblock** (People list, Radar, mini-map, profile,
  in-world right-click "Mute"), the **Mute Voice** items in chat/IM name menus,
  and the mute button in the voice-volume popup. All of these route through
  `LLMuteList` → `LLWebRTCVoiceClient::onChangeDetailed()` → `setUserMute()`.
- **`"ug"` is emitted** by the per-avatar **voice volume slider**, which lives in
  the `floater_voice_volume` popup (opened by clicking the **voice level /
  output-monitor icon** next to a participant), plus the avatar-list-item and
  inspect-avatar sliders.

So the reason our mixer's `peer_ctl` map stays empty is **not** that the viewer
lacks the wiring. Either the exact action wasn't one of the emitting paths, or
there's a runtime/build gap (see "Why you may be seeing nothing" below). The one
real **UX gap** is that there is **no general right-click "Mute Voice" context
item** — voice-specific mute/gain is only in the output-monitor popup; the plain
right-click "Mute"/"Block" is a *full* `LLMuteList` block (which still emits
`"m":true`).

There is **no `LLVoiceClient::setUserMute` public wrapper at all** — `setUserMute`
exists only inside `LLWebRTCVoiceClient` and is driven exclusively by the mute-list
observer.

---

## 1. Who calls `setUserMute()` / `setUserVolume()`? (Q1)

### `setUserMute` — emits `{"m": {"<uuid>": <bool>}}`

The message is built here:

- `llvoicewebrtc.cpp:2706` `LLVoiceWebRTCConnection::setUserMute()`
  ```cpp
  boost::json::object root = { { "m", { { id.asString(), mute } } } };
  ... mWebRTCDataInterface->sendData(json_data, false);   // line 2708-2712
  ```

Call chain up from there (single path — no UI calls it directly):

- `llvoicewebrtc.cpp:2019` `sessionState::setUserMute()` → for each connection → `connection->setUserMute()` (2027)
- `llvoicewebrtc.cpp:1940` `predSetUserMute()` → `session->setUserMute()` (1942)
- `llvoicewebrtc.cpp:1922` `LLWebRTCVoiceClient::onChangeDetailed(const LLMute&)`
  — the **`LLMuteListObserver`** callback:
  ```cpp
  if (mute.mType == LLMute::AGENT) {
      bool muted = ((mute.mFlags & LLMute::flagVoiceChat) == 0);
      sessionState::for_each(boost::bind(predSetUserMute, _1, mute.mID, muted));
  }
  ```
- Observer registration: `llvoicewebrtc.cpp:515`
  `LLMuteList::getInstance()->addObserver(this);` inside `voiceConnectionCoro()`
  (runs for the life of the voice coroutine).

**=> `setUserMute` has exactly one trigger: a change to `LLMuteList` for an
`AGENT`.** It is *not* wired to any dedicated "voice mute" call site.

### `setUserVolume` — emits `{"ug": {"<uuid>": <uint>}}`

Message built here:

- `llvoicewebrtc.cpp:2696` `LLVoiceWebRTCConnection::setUserVolume()`
  ```cpp
  { { "ug", { { id.asString(), (uint32_t)(volume * PEER_GAIN_CONVERSION_FACTOR) } } } };
  ```

Chain:
- `llvoicewebrtc.cpp:2007` `sessionState::setUserVolume()` → per connection (2015)
- `llvoicewebrtc.cpp:1909` `predSetUserVolume()`
- `llvoicewebrtc.cpp:1882` `LLWebRTCVoiceClient::setUserVolume()` → `for_each(predSetUserVolume)`
- `llvoiceclient.cpp:836` `LLVoiceClient::setUserVolume()` (public facade; also forwards to Vivox)

UI callers of `LLVoiceClient::setUserVolume` (**all wired to sliders**):
- `llfloatervoicevolume.cpp:195` `onVolumeChange()` — the voice-volume popup slider
- `llavatarlistitem.cpp:197` — per-row slider in avatar lists (`show_voice_volume`)
- `llinspectavatar.cpp:540` — avatar inspector slider
- `fsfloatervoicecontrols.cpp:364` — FS Voice Controls floater slider

---

## 2. What runs on right-click → "Mute"? (Q2)

There are two distinct "mute" verbs in the UI, and it matters which one the user
clicked:

### (a) General right-click "Mute" / "Block" = *full* mute-list block

Every general avatar context menu routes to `LLAvatarActions::toggleBlock` (full
block) or an equivalent `LLMuteList::add`:

- People panel: `llpanelpeoplemenus.cpp:88` `"Avatar.BlockUnblock"` → `toggleBlock`
- Radar: `fsradarmenu.cpp:78` → `toggleBlock`
- Avatar search: `fsavatarsearchmenu.cpp:61` → `toggleBlock`
- Name-list menu: `fsnamelistavatarmenu.cpp:62` → `toggleBlock`
- Mini-map right-click: `llnetmap.cpp:2329` → `toggleBlock`
- Profile "Block": `llpanelprofile.cpp:391/400/2083` → `toggleBlock`
- **In-world right-click "Mute"** (the pie/context "Avatar.Mute"): registered at
  `llviewermenu.cpp:13256`, handler `LLObjectMute::handleEvent`
  (`llviewermenu.cpp:4515-4574`) → `LLMuteList::getInstance()->add(mute)` with
  default flags.

`toggleBlock` (`llavataractions.cpp:1396`) does `LLMuteList::add(mute)` with
`flags = 0`. In `LLMuteList::add` (`llmutelist.cpp:435-444`) `flags == 0` stores
`mFlags = 0` (mute everything). `add`/`remove` **do** fire the detailed observer:
`notifyObserversDetailed()` at `llmutelist.cpp:395,457,578,595`.

Back in `onChangeDetailed` (`llvoicewebrtc.cpp:1926`):
`muted = ((0 & flagVoiceChat) == 0) = true` → **emits `"m":true`.**

**So even a plain full Block emits `"m":true` while WebRTC voice is up.** This
directly contradicts the "client-side playback only" hypothesis.

### (b) Voice-specific "Mute Voice" = `toggleMuteVoice`

- `llavataractions.cpp:1436` `toggleMuteVoice(id)` → `toggleMute(id, flagVoiceChat)`
  (`:1416`) → `LLMuteList::add(mute, flagVoiceChat)`.
- In `add` (`llmutelist.cpp:429,438`): new entry starts `mFlags = flagAll` then
  `&= ~flagVoiceChat` → `mFlags = 0xD`. `onChangeDetailed`:
  `(0xD & flagVoiceChat) == 0` → `true` → **emits `"m":true`.**
- Unmute (`remove`, `llmutelist.cpp:536`) sets the `flagVoiceChat` bit back →
  `muted = false` → **emits `"m":false`.**

`toggleMuteVoice` is reached only from:
- Voice-controls participant list: `fsparticipantlist.cpp:530` `"Avatar.BlockUnblock"`
  → `toggleMuteVoice` (in the *voice* list, "Block" is actually voice-mute)
- Voice-volume popup mute button: `llfloatervoicevolume.cpp:188` `onClickMuteVolume`
- Chat/IM name menus "Mute Voice": `llchathistory.cpp:496`, `fschathistory.cpp:535`,
  `llfloaterimcontainer.cpp:1334` → `toggleMute(..., flagVoiceChat)`

### Mute-flag semantics (important, inverted)

`llmutelist.h:54-59`:
```
flagTextChat  = 0x01,  // If set, don't mute text
flagVoiceChat = 0x02,  // If set, don't mute voice
flagAll       = 0x0F   // mask
```
Flags are **exceptions**: a set bit means "*don't* mute this aspect"; `mFlags==0`
means mute everything. That is why `onChangeDetailed` computes
`muted = (mFlags & flagVoiceChat) == 0`.

**Answer to Q2:** right-click "Mute"/"Block" runs `LLMuteList::add` (AGENT), which
*does* reach `setUserMute` → emits `"m"`. It is **not** playback-only. The catch
is that the general menu item is a *full block*, not a voice-only toggle.

---

## 3. Per-avatar VOICE volume control (Q3)

Yes, it exists, but it is **not** in the general right-click menu — it is a popup:

- `LLFloaterVoiceVolume` (`llfloatervoicevolume.cpp`) is the per-avatar voice
  popup. It contains a **mute button** (`onClickMuteVolume` → `toggleMuteVoice` →
  `"m"`, line 188) **and a volume slider** (`onVolumeChange` →
  `setUserVolume` → `"ug"`, line 195). Registered as `floater_voice_volume`
  (`llfloatervoicevolume.cpp:218`; XML `floater_voice_volume.xml`).
- **How it opens:** `lloutputmonitorctrl.cpp:279-287` `handleMouseUp()` — clicking
  the **voice output-monitor icon** (the little speaking-level indicator next to a
  participant) calls
  `LLFloaterReg::showInstance("floater_voice_volume", {"avatar_id": id})`.
  Also opened from the conversation floater (`llconversationview.cpp:366`).
- The popup only shows the slider/mute button when
  `getVoiceEnabled(avatar)` is true and it isn't yourself
  (`llfloatervoicevolume.cpp:142-182`).

This is distinct from the **general audio/master voice gain**
(`AudioLevelVoice`, `llvieweraudio.cpp:542`), which is a client-side master
volume and never touches the data channel.

There are also inline per-row voice sliders in avatar lists when the list has
`show_voice_volume="true"` (`llavatarlist.cpp:145`, e.g. default-skin
`floater_fs_voice_controls.xml:114`) → `llavatarlistitem.cpp:197` → `"ug"`. The
**Vintage skin sets `show_voice_volume="false"`** (`floater_fs_voice_controls.xml`
vintage:96), which would hide those inline sliders.

**Answer to Q3:** the per-avatar voice-volume slider exists and *does* emit
`"ug"`, but it is reached by clicking the **voice level icon** next to a
participant (opening `floater_voice_volume`), not from the avatar right-click
menu. If you can't find it, check that you're on a voice-enabled list, on the
default skin (`show_voice_volume` true), and that you clicked the level icon.

---

## 4. Exactly what to click to emit `"m"` / `"ug"`

While connected to a **WebRTC** voice session on the same region:

| Message | Action |
|---|---|
| `"m":true`  | Right-click an avatar → **Block** (People/Radar/mini-map/profile/in-world "Mute"), or **Mute Voice** in a chat/IM name menu, or the mute button in the voice-volume popup. |
| `"m":false` | Unblock / un-mute-voice the same way. |
| `"ug":<n>`  | Click the **voice level icon** next to a participant to open the voice-volume popup, then drag the **volume slider** (or the inline per-row slider in the Voice Controls / People voice list). |

## Why you may be seeing nothing at the server (runtime caveats)

The source clearly emits, so the divergence is runtime, not wiring. Likely causes,
in order of suspicion:

1. **Not actually on WebRTC.** `setUserMute`/`setUserVolume` iterate
   `LLWebRTCVoiceClient` sessions/connections only. If the region/session is on
   **Vivox**, nothing is emitted on the WebRTC channel. Confirm the active session
   is WebRTC.
2. **Emit gated on a live data interface, no retry.** The connection-level emit
   (`llvoicewebrtc.cpp:2700-2703, 2710-2712`) only sends if
   `mWebRTCDataInterface != nullptr`, and there is **no queue/retry**. A mute
   toggled a moment before the data channel is up is silently dropped. (Position
   updates use the same interface but recur every tick, so they mask this.)
3. **You clicked a non-emitting control** — e.g. a client-side master voice
   volume, or a menu on a non-`AGENT` target.

**To confirm at runtime**, set a breakpoint / add a temporary `LL_INFOS("Voice")`
at `llvoicewebrtc.cpp:2708` (`setUserMute`) and `:2698` (`setUserVolume`) and
watch whether `mWebRTCDataInterface` is non-null when you toggle. That will tell
you definitively whether the viewer is building the JSON and whether the channel
was up.

---

## 5. Smallest change to expose an explicit per-avatar "Mute Voice" (proposal only — not implemented)

The `"m"` path already fires from the mute list, so no code change is required to
*emit* it. But if the goal is a discoverable, voice-specific right-click item
(instead of a full block) that emits `"m"`, the minimal wiring reuses the existing
`LLAvatarActions::toggleMuteVoice` (already emits `"m"` via the observer):

1. **Register the callback** in the People context menu, alongside the existing
   block entry at `llpanelpeoplemenus.cpp:88`:
   ```cpp
   registrar.add("Avatar.MuteVoice",
                 boost::bind(&LLAvatarActions::toggleMuteVoice, id));
   // enable-check:
   enable_registrar.add("Avatar.MuteVoiceCheck",
                 boost::bind(&LLAvatarActions::isVoiceMuted, id));   // :1902
   ```
   (Both `toggleMuteVoice` (`llavataractions.cpp:1436`) and `isVoiceMuted`
   (`:1902`) already exist and are `static` — no new logic needed.)

2. **Add the menu item** to the People/Nearby avatar context menu XML
   (`skins/default/xui/en/menu_people_nearby.xml` and the radar equivalent),
   e.g.:
   ```xml
   <menu_item_check label="Mute Voice" name="Mute Voice">
     <on_click function="Avatar.MuteVoice"/>
     <on_check function="Avatar.MuteVoiceCheck"/>
   </menu_item_check>
   ```

That is the whole change: one registrar line (+ enable line) and one XML item per
menu. Nothing in `llvoice*` needs touching — `toggleMuteVoice` already flows
through `LLMuteList` → `onChangeDetailed` → `setUserMute` → `"m"`.

*If instead the runtime investigation (caveat #2 above) shows the emit is being
dropped because the data channel wasn't up, the smallest fix is on the emit side:
queue the last-known mute/gain per participant and flush it when
`mWebRTCDataInterface` becomes available (around `llvoicewebrtc.cpp:2700`), rather
than sending fire-and-forget. Scope that separately once the breakpoint confirms
which case you're in.*

---

### Files/lines referenced

- `indra/newview/llvoicewebrtc.cpp` — 515, 1882-1912, 1918-1943, 2007-2028, 2696-2716
- `indra/newview/llvoiceclient.cpp` — 836-839
- `indra/newview/llavataractions.cpp` — 1396-1438, 1902-1904
- `indra/newview/llmutelist.cpp` — 344-460, 523-595
- `indra/newview/llmutelist.h` — 54-59
- `indra/newview/llfloatervoicevolume.cpp` — 142-195, 218
- `indra/newview/lloutputmonitorctrl.cpp` — 279-294
- `indra/newview/llviewermenu.cpp` — 4515-4574, 13256
- context-menu registrars: `llpanelpeoplemenus.cpp:88`, `fsradarmenu.cpp:78`,
  `fsavatarsearchmenu.cpp:61`, `fsnamelistavatarmenu.cpp:62`, `llnetmap.cpp:2329`,
  `fsparticipantlist.cpp:530`
- volume-slider callers: `llavatarlistitem.cpp:197`, `llinspectavatar.cpp:540`,
  `fsfloatervoicecontrols.cpp:364`
