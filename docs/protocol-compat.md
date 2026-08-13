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

Taken from the OpenSim survey `docs/current-architecture.md` §3 (the message
table). These are the `body.request` shapes the C# `JanusMessages` classes
construct:

| Request (`request`) | Fields the C# side sends | Source (C# class) |
|---|---|---|
| `create`   | `room`, `is_private`, `permanent`, `sampling_rate`, `spatial_audio`, `denoise`, `record`, optional `description` | `AudioBridgeCreateRoomReq` |
| `destroy`  | `room`, `permanent` | `AudioBridgeDestroyRoomReq` |
| `join`     | `room`, `display` (+ JSEP `offer`) | `AudioBridgeJoinRoomReq` |
| `configure`| (audiobridge `configure` body) | `AudioBridgeConfigRoomReq` |
| `leave`    | `room`, `id` | `AudioBridgeLeaveRoomReq` |
| `list`     | — | `AudioBridgeListRoomsReq` |
| `listparticipants` | `room` | `AudioBridgeListParticipantsReq` |

Session-level ICE `trickle` is core Janus (not a plugin request) and is
unaffected by this constraint.

> When `docs/current-architecture.md` is dropped into this `docs/` directory,
> treat its §3 message table as the authoritative field list and reconcile the
> table above against it.

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
