# Voice connector — injector peer (S-CON-6)

A headless WebRTC peer (Python 3.12 + aiortc) that joins one `janus.plugin.slvoice`
room as a sim-registered connector NPC identity — exactly as the S-CON-4 recorder
does — and **sends** audio into the room from a PCM/WAV pipe or file. Design and
authority: `docs/voice/connector-build-plan.md` S-CON-6,
`docs/voice/connector-design-brief.md` Amendment 2.

The source is deliberately dumb: an s16 mono/stereo WAV stream read from
`SOURCE` (a regular file or a named pipe). A TTS engine (S-CON-7) or a live mic
are just different writers to the same pipe. Whatever the input rate/layout, the
peer sends 20 ms mono 48 kHz frames, paced in real time; an exhausted or idle
source produces **silence frames, not a stop** — the participant stays joined.
It also receives the room mixdown so it remains a normal participant (discarded
by default; `RECORD=1` writes it to WAV segments with the recorder's writer).
The data channel is open and never written to.

## Sim-side prerequisite (read this first)

The `[VoiceConnector.<name>]` record on the sim MUST carry **`MayInject=true`**.
With `MayInject=false` the sim pushes a moderation mute at registration and
**nobody hears the injector** — every listener's handle carries
`mod_muted_entries=1` and the mix gates the source out. That silence is the
S-CON-5(e) enforcement test working, not a defect here. The NPC's own matrix
row constrains the injected audio like any avatar's: parcel-excluded listeners
do not hear it.

`ROOM` and `DISPLAY` come from the sim's registration line, as for the recorder:

    [CONNECTOR] registered <name> npc=<DISPLAY> room=<ROOM> inject=true session=<...>

## Running

    cp connectors/injector/injector.env.example connectors/injector/injector.env
    # fill in JANUS_API_SECRET (= JS_API_SECRET in the mixer .env), ROOM, DISPLAY
    mkdir -p injector-in
    docker compose --profile injector up -d injector

The service sits behind `profiles: [injector]`, so a plain `docker compose up -d`
never starts it. The host directory `./injector-in/` is mounted at `/injector`;
the default `SOURCE=/injector/in.wav` reads `./injector-in/in.wav`.

## One-line test recipe

Generate 10 s of 440 Hz tone as the source (or drop any spoken-word s16 WAV in
as `injector-in/in.wav`):

    python -c "import math,wave; w=wave.open('injector-in/in.wav','wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000); w.writeframes(b''.join(int(12000*math.sin(2*math.pi*440*i/48000)).to_bytes(2,'little',signed=True) for i in range(480000))); w.close()"

What the room should hear: ten seconds of a clean 440 Hz tone (or the spoken
WAV), **spatialised at the NPC's position** — attenuating with distance, panning
with the listener's orientation — with the NPC's name marker on the speaking
dot, then silence (the peer stays joined). With `LOOP=1` the file replays
end-to-end instead. Restart the service after replacing the file with `LOOP=0`
(the once-through has already ended), or use a FIFO and just write to it again.

## What healthy looks like

- injector log: `joined room <ROOM> as <DISPLAY>` → `webrtcup` →
  `source open: /injector/in.wav (...)`.
- Janus admin handle-walk: a participant whose `display` is the NPC UUID,
  `setup:true`, `datachannel_open:true`, and — unlike the recorder — `audible`
  true while real (non-silent) audio is flowing.
- In-world: the NPC's speaking dot active while the tone plays; the proximity
  notice (D3(iii)) fires for an avatar approaching the voiced NPC.

## Stopping cleanly

    docker compose --profile injector stop injector

SIGTERM triggers the clean teardown: `{"request":"leave"}` → detach → destroy
(→ the open WAV segment finalised, when `RECORD=1`).

## Tests

    # from the connectors/ directory (the shared common/ package must resolve)
    python -m pytest injector/        # or: python -m injector.test_source

The framer/WAV-stream parsing (`source.py`) is stdlib-only and tests without
aiortc installed: exact 960-sample frames, stereo downmix, 16 k/44.1 k
resampling, chunk-seam identity, silence-when-idle (no junk frames), tail
padding, and the pipe-shaped header (unseekable stream, data size 0).
