# Voice connectors

Headless WebRTC peers (Python 3.12 + aiortc) that join a `janus.plugin.slvoice`
room under a sim-registered connector NPC identity:

- [`recorder/`](recorder/README.md): S-CON-4, writes the room mixdown to WAV segments.
- [`injector/`](injector/README.md): S-CON-6, sends audio into the room.
- `common/`: the shared Janus session/join/poll/teardown plumbing and the segment writer.

`ROOM` and `DISPLAY` come from the sim's registration line:

    [CONNECTOR] registered <name> npc=<DISPLAY> room=<ROOM> inject=<bool> session=<...> identity=derived

Since slice 0.8c a connector no longer needs an avatar present for its room to exist: the sim creates the room it
records, and re-checks it at every capability fetch.

## Join capability (slice 0.7b)

A mixer with `JS_JOIN_CAP_REQUIRED=1` refuses a join into a declared room that carries no sim-minted capability, and
connector peers are not exempted. To join such a room, give the record a secret on the sim side and the peer the
matching pair:

- sim: `[VoiceConnector.<name>] CapabilitySecret = <at least 32 characters>` (shorter is refused with a WARN), plus
  `[WebRtcVoice] JoinCapabilityEnabled = true` and `[JanusWebRtcVoice] JoinCapabilitySecret` (= the mixer's
  `JS_JOIN_CAP_SECRET`);
- peer: `CONNECTOR_CAP_URL=http://<region host>:<region http port>/voice/connector/<name>/join-cap` and
  `CONNECTOR_CAP_SECRET=<the same CapabilitySecret>`. Set both or neither; one alone is FATAL at start.

With both set the peer fetches a capability before every join, joins with the display and room the sim returns (an
env `DISPLAY`/`ROOM` that disagrees loses, with a WARN), and on any failure retries with backoff and never joins
without one. With neither set the join is exactly as before. When the peer and the region are on different hosts the bearer crosses the network, so use TLS or a private network.

A peer never gives up on its room (slice 0.8f): after any failed join (a 485, a capability refusal, a transport
error) or a later loss of the room or session, it tears the session down, waits 2 s doubling to 60 s (back to 2 s
after a join that stayed up 60 s), fetches a fresh capability and joins again, logging one INFO line per attempt
with the reason and the next delay. A room destroyed under a joined peer sends it nothing, so the peer asks the
mixer for its room's participants every 5 s and treats a 485, or its own absence from the list, as a lost room.

## Position (slice 0.8h, ledger O-62)

A connector that has a position is mixed **spatially**: the mixer fades it with distance (full volume inside 10 m,
silent past 60 m) and pans it. Without one it is mixed **flat** - the same level to every listener in the room, at any
distance - and the peer says so once at start.

Two ways to have one:

- **With a capability** (the usual way): nothing to configure. The sim's grant carries `position` - the record's
  `Position` in its region, as GLOBAL centimetres - and the peer sends it as SLData once its data channel opens, and
  again after every rejoin. The sim re-computes it at every fetch, so moving the record's `Position` and re-fetching
  moves the connector.
- **Without one:** `CONNECTOR_POSITION_GLOBAL_CM=x,y,z`, the same frame - integers, `(region global origin +
  position in the region) x 100`. For a region at grid (1000, 1000), an NPC at `<128, 128, 22>` is
  `25612800,25612800,2200`. Region-local metres here would put the connector kilometres from every avatar, where the
  mixer culls it: this is the frame the viewer itself sends (`llvoicewebrtc.cpp:1108`, `:1241-1244`).

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
