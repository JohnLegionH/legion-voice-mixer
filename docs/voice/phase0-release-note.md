# Phase 0 release note - the sim half (2026-09-19)

Operator-facing companion to the mixer's `docs/RELEASES.md` entry "Mixer 1.2.0: Phase A + Phase 0,
enforcement ON". This file is mirrored between `tranq-ais:Docs/voice/` and `legion-voice-mixer:docs/voice/`
(ledger O-28); the authoritative record of each deploy stays the ledger row and the commit message.

## What the sim half is

**Live build:** `WebRtcJanusService` and `WebRtcVoiceRegionModule` `1.1.451-alpha+04fe27f323`; `WebRtcVoice`,
`VoiceVisibility` and `WebRtcVoiceServiceModule` `1.1.442-alpha+0b9c1b0a6c`. Proposed version to pair with
mixer 1.2.0: **voice 1.2.0** - tagging is the operator's call.

The sim is the authority that Phase 0 enforces. It arms every listener per room with an epoch and a monotonic
`policy_generation`, heartbeats that arming once a second, mints a short-lived join capability for every join
into a room it created with `vis_authority`, registers connector NPCs, and now tells the mixer where each
connector stands.

## Config keys, with defaults

| Key | Section | Default | With the default |
|---|---|---|---|
| `VisibilityArmingEnabled` | `[WebRtcVoice]` | `false` | no arming, no epochs, no heartbeats - the pre-Phase-0 build |
| `VisibilityFeederEnabled` | `[WebRtcVoice]` | `true` | as shipped since 1.1.392: the per-region feeder runs |
| `VisibilityEmitEnabled` | `[WebRtcVoice]` | `true` | as shipped since 1.1.392: batches are emitted to the mixer |
| `JoinCapabilityEnabled` | `[WebRtcVoice]` | `false` | no capability is minted; joins are exactly as before |
| `JoinCapabilitySecret` | `[JanusWebRtcVoice]` | *(unset)* | nothing is minted even when enabled, and the sim says so once |
| `CapabilitySecret` | `[VoiceConnector.<name>]` | *(unset)* | that connector serves no join-capability endpoint |
| `Position`, `Scope`, `MayInject` | `[VoiceConnector.<name>]` | *(none)* | a record without them does not load |

**Every default reproduces the previous build.** The two switches that change behaviour are
`VisibilityArmingEnabled` and `JoinCapabilityEnabled`, and both belong on *before* the mixer's knobs, never
after.

## Upgrade order

1. Mixer image first, both mixer knobs off (shadow).
2. **Then this build**, with arming and capabilities enabled and the shared secret matching the mixer's
   `JS_JOIN_CAP_SECRET`.
3. Soak in shadow until the mixer's counters are quiet.
4. Then the mixer knobs, through the pre-flip gate in the mixer's release note.

**Ship the voice assemblies together.** `WebRtcVoice` defines `IWebRtcVoiceService`; `WebRtcJanusService`,
`WebRtcVoiceRegionModule` and `WebRtcVoiceServiceModule` implement or name it. A publish carrying some but not
all of them loads a new interface against an old implementer, and voice fails to load rather than degrading
(ledger O-100). The region publish now emits all four and asserts it at publish time.

## Removing a connector without a restart

    voice connector stop <name>     # tears down the voice session, then deletes the NPC
    voice connector start <name>    # starts or restarts a loaded record

Remove the record from `OpenSim.ini` as well to keep it gone across restarts. The mixer disarms the peer on
the next heartbeat, which under enforcement means it goes silent rather than lingering audible.

## Known open rows

**O-102** (a position-less participant is mixed flat to the whole room), **O-87** (the recorder's disclosure
is the sim's responsibility), **O-77** (a child agent's room still comes from the viewer's own
`parcel_local_id`, so O-46 is narrowed rather than closed). The mixer's release note describes each from the
operator's side.
