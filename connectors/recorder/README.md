# Voice connector — recorder peer (S-CON-4)

A headless WebRTC peer (Python 3.12 + aiortc) that joins one `janus.plugin.slvoice`
room as a sim-registered connector NPC identity and writes the received room
mixdown to rolling WAV segments. Design and authority:
`docs/voice/connector-build-plan.md` S-CON-4, `docs/voice/connector-assessment-20260831.md` §2,
`docs/voice/connector-design-brief.md` Amendment 2.

It **never sends audio or SLData** — it is the silent participant the assessment
describes, and its recording is subject to parcel/estate exclusions because the sim
registered the NPC identity it joins under (the mixer fans the NPC's exclusion
column out to this peer by `display`).

## Getting ROOM and DISPLAY

Both come from the sim, not from this repo. At region start (or `voice connector
start <name>` on the region console) the sim logs, at INFO:

    [CONNECTOR] registered <name> npc=<DISPLAY> room=<ROOM> inject=<bool> session=<...>

Copy `npc=` into `DISPLAY` and `room=` into `ROOM`. Do **not** invent a DISPLAY:
the sim's policy record owns the identity; an unregistered UUID records without
exclusions (and is exactly the O-46 shape the ledger tracks).

## Running

    cp connectors/recorder/recorder.env.example connectors/recorder/recorder.env
    # fill in JANUS_API_SECRET (= JS_API_SECRET in the mixer .env), ROOM, DISPLAY
    docker compose --profile recorder up -d recorder

The service sits behind `profiles: [recorder]`, so a plain `docker compose up -d`
never starts it. WAV segments land in `./recordings/` on the host
(`<DISPLAY first 8>_<UTC yyyymmdd-HHMMSS>.wav`, one per `SEGMENT_SECONDS`,
48 kHz, channel count as received — the mixer sends stereo).

## What healthy looks like

- recorder log: `joined room <ROOM> as <DISPLAY>` → `webrtcup` → `audio track
  received; writing segments`.
- Janus admin handle-walk (`/voiceAdmin`, or the sim console `janus list rooms`):
  a participant whose `display` is the NPC UUID, `setup:true`,
  `datachannel_open:true`, never `audible`.
- In-world: the NPC visible, its roster row present, and (for `MayInject=false`)
  `mod_muted_entries=1` on every listener's handle.

## Stopping cleanly

    docker compose --profile recorder stop recorder

SIGTERM triggers the clean teardown: `{"request":"leave"}` → detach → destroy →
the open WAV segment is finalised (valid RIFF header). Killing the container
instead leaves the last segment with a stale header — the earlier, closed
segments are unaffected.

## Tests

    # from the connectors/ directory (the shared common/ package must resolve)
    python -m py_compile common/segments.py common/janus.py common/peer.py recorder/recorder.py
    python -m pytest recorder/        # or: python -m recorder.test_segments

The segment writer (rollover, format-change roll, RIFF validity) is stdlib-only
and tests without aiortc installed.

## Layout note (S-CON-6)

The Janus session/join/poll/keepalive/teardown plumbing and the segment writer
moved to `connectors/common/`, shared with the S-CON-6 injector peer; this
directory keeps only the recorder's own media behaviour. Behaviour is
unchanged. The compose build context is `connectors/` so the image carries
`common/`.
