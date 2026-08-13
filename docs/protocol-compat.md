# Protocol compatibility constraint (audiobridge superset)

**Status:** ACTIVE (Phase 0 / bring-up). **Planned expiry:** after the
flat-mix parity milestone (see below).

## The constraint

`janus.plugin.slvoice`'s `handle_message()` MUST start life as a **superset of
`janus.plugin.audiobridge`'s request protocol** — specifically the request
shapes the OpenSimulator C# side sends today. The requests and their field
names must be **byte-for-byte the same** as what the region/Robust code already
emits, so that:

> During bring-up, the OpenSim C# side can flip between `janus.plugin.audiobridge`
> and `janus.plugin.slvoice` **by configuration alone** (the `SpatialVoiceService`
> / plugin package it attaches to), with no code change, to A/B isolate faults.

This is why the scaffold recognises the audiobridge verbs and nothing else yet.

## The requests we must accept (same field names)

Reconciled against the (now vendored) survey `docs/voice/current-architecture.md`
§3.2. These are the `body.request` shapes the C# `JanusMessages` classes
construct:

| Request (`request`) | Fields the C# side sends | Source (C# class) |
|---|---|---|
| `create`   | `room` (dynamic, `CalcRoomNumber`) + fixed `is_private:false`, `permanent:false`, `sampling_rate:48000`, `spatial_audio:<bool>`, `denoise:false`, `record:false`, optional `description` | `AudioBridgeCreateRoomReq` |
| `destroy`  | `room`, `permanent:true` | `AudioBridgeDestroyRoomReq` |
| `join`     | `room`, `display` (agent UUID; **no** mute/allow/hide) + JSEP `offer` | `AudioBridgeJoinRoomReq` |
| `leave`    | `room`, `id` | `AudioBridgeLeaveRoomReq` |
| `list`     | — (console only) | `AudioBridgeListRoomsReq` |
| `listparticipants` | `room` (console only) | `AudioBridgeListParticipantsReq` |
| `configure`| — | `AudioBridgeConfigRoomReq` — **dead code**, never constructed (§2.5/§3.2) |

Session-level ICE `trickle` is core Janus (not a plugin request) and is
unaffected by this constraint.

> **Reconciled (Phase 1).** The plugin implements exactly these shapes and the
> audiobridge **response** envelopes (top-level `audiobridge` key, `error_code`
> matching audiobridge's numeric codes — incl. `create` on an existing room
> returning **486**, which `JanusAudioBridge.CreateRoom` treats as success,
> §3.3). `configure` is accepted defensively but the C# side never sends it.
> Note (§4): the OpenSim side does **not** touch the data channel — it forwards
> the viewer SDP (incl. `m=application`) verbatim — so the plugin answers the
> data channel and parses SLData itself.

## Rules while the constraint is active

1. **Do not rename** any existing request or field. The names above are frozen.
2. **Additions go in NEW fields only.** slvoice-specific needs (spatial
   position, per-listener gain, region id, orientation, …) are added as new
   optional fields on the existing requests, or as new request verbs — never by
   repurposing an existing field.
3. **Unknown fields are ignored, not rejected**, so an audiobridge-shaped
   message continues to work verbatim.
4. Responses must remain shaped so the C# response parsers
   (`AudioBridgeResp`, `AudioBridgeJoinRoomResp`, …) keep working during A/B.

## Expiry

The constraint is a bring-up scaffold, not a permanent contract. It is lifted at
the **flat-mix parity milestone**: the point at which `janus.plugin.slvoice`
produces a correct flat (non-spatial) mix at parity with `janus.plugin.audiobridge`
for the OpenSim spatial-voice path, verified end-to-end, so audiobridge is no
longer needed as an A/B fallback.

After that milestone:
- the OpenSim C# side no longer needs to flip back to audiobridge, so the
  wire protocol may diverge from audiobridge (still additive/versioned, but no
  longer bound to audiobridge's shapes);
- the spatial extensions (data-channel position, per-listener mixing) become
  first-class rather than "new optional fields."

Until this document's status says otherwise, assume the constraint holds.
