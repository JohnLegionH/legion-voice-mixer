# Voice connectors

Headless WebRTC peers (Python 3.12 + aiortc) that join a `janus.plugin.slvoice`
room under a sim-registered connector NPC identity:

- [`recorder/`](recorder/README.md): S-CON-4, writes the room mixdown to WAV segments.
- [`injector/`](injector/README.md): S-CON-6, sends audio into the room.
- `common/`: the shared Janus session/join/poll/teardown plumbing and the segment writer.

`ROOM` and `DISPLAY` come from the sim's registration line:

    [CONNECTOR] registered <name> npc=<DISPLAY> room=<ROOM> inject=<bool> session=<...> identity=derived

## Receiving SLData from the mixer

The mixer's SLData (presence, power batches) does **not** come back on the `SLData` data channel a
peer creates:
1. The plugin relays SLData with no channel label.
2. Janus substitutes its default label, `JanusDataChannel`, and finds no open channel by that name.
3. It opens a new channel toward the peer and sends on that
   (`vendor/janus-gateway/src/sctp.c`, `janus_sctp_send_data`).

A connector that wants the mixer's SLData must therefore listen on channels the far side opens.
`common/peer.py` registers `pc.on("datachannel")` and routes those messages to `_on_sldata`.

The integration harness's S11 failed until it did this: the test peer received nothing while the
mixer was sending correctly (ledger O-86).

## One-time migration (regionserver build 1.1.392+)

Connector NPC ids are now derived and stable; on the first restart after upgrading,
each NPC's id changes once — re-edit DISPLAY in recorder.env / injector.env one last
time.

Before build `1.1.392-alpha+d347102272` the sim generated a new NPC UUID at every
start. From that build it derives the UUID (UUIDv5 of grid, region and connector
record name), so the first start on the new build replaces the last random id with
the derived one. Copy the new `npc=` value from the registration line above into
`DISPLAY`, then restart the peer (`docker compose --profile recorder up -d recorder`,
or `--profile injector ... injector`). Later regionserver restarts keep the same id.

Until you re-edit, the peer is joined under an id the sim no longer knows. It is not
the NPC the sim moderation-muted and disclosed at registration, so a stale injector
plays unmuted and undisclosed (ledger O-63).
