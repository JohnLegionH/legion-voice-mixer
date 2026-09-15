"""Source-address probe (slice A.6): does a peer's real address survive to Janus?

One viewer-shaped peer (the harness's TestPeer: a real Opus tone and the SLData channel) joins a fresh test room and
brings media up. The probe then puts two things side by side:
  - the addresses the peer itself holds: the candidates in its own SDP offer;
  - the remote end of Janus's selected candidate pair, from Admin API handle_info.
If that remote address is one of the peer's own, the source address survived the path to Janus. If not, something on
the path rewrote it: a published-port proxy shows its own address (Docker Desktop: the network gateway), and Janus
types the pair prflx, because it learned the address from connectivity checks rather than from the peer's signalling.

    python -m tests.integration.source_probe [--janus-url URL] [--admin-url URL] [--env PATH] [--label TEXT] [--json]

It prints the Janus handle id, so the ICE diagnostics record can be read afterwards on the mixer:
    legion-voice-selfcheck --session <handle>
It uses one room at 990 000 000 and up (clear of the harness's blocks) and tears it down. Exit 0 when a pair was
selected (preserved or rewritten, the answer is in the output); 2 when media never came up; 64 on bad arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from pathlib import Path

import aiohttp

from .harness import REPO, ROOM_BASE, Config, Control, TestPeer, new_display, read_env

_CANDIDATE = re.compile(r"^a=candidate:\S+ \d+ (udp|tcp) \d+ (\S+) (\d+) typ (\w+)", re.M)
_PAIR = re.compile(r"^(.+):(\d+) \[(\w+),(\w+)\] <-> (.+):(\d+) \[(\w+),(\w+)\]$")
_TYPE = re.compile(r"\btyp (host|srflx|relay|prflx)\b")


def own_candidates(sdp: str) -> list:
    return [{"address": m.group(2), "port": int(m.group(3)), "type": m.group(4), "transport": m.group(1)}
            for m in _CANDIDATE.finditer(sdp or "")]


def parse_pair(text: str | None):
    m = _PAIR.match((text or "").strip())
    if not m:
        return None
    g = m.groups()
    return {"local": {"address": g[0], "port": int(g[1]), "type": g[2], "transport": g[3]},
            "remote": {"address": g[4], "port": int(g[5]), "type": g[6], "transport": g[7]}}


def verdict(own: list, pair) -> dict:
    if pair is None:
        return {"verdict": "no-pair", "remote_address_is_peers_own": None}
    addresses = sorted({c["address"] for c in own})
    remote = pair["remote"]["address"]
    return {"verdict": "preserved" if remote in addresses else "rewritten",
            "remote_address_is_peers_own": remote in addresses, "peer_addresses": addresses}


async def _admin(http, url: str, secret: str, path: str, body: dict) -> dict:
    body = dict(body, transaction=uuid.uuid4().hex[:12], admin_secret=secret)
    async with http.post(url + path, json=body) as resp:
        return await resp.json()


async def probe(args) -> int:
    env = read_env(Path(args.env)) if args.env and Path(args.env).exists() else {}
    cfg = Config(janus_url=args.janus_url, admin_url=args.admin_url,
                 api_secret=args.api_secret or env.get("JS_API_SECRET", ""),
                 admin_secret=args.admin_secret or env.get("JS_ADMIN_SECRET", ""),
                 compose_file=REPO / "docker-compose.yml", grace=60, restart=False)
    room = ROOM_BASE + 90_000_000 + int(time.time()) % 1_000_000
    result = {"label": args.label, "janus_url": cfg.janus_url, "room": room}
    async with aiohttp.ClientSession() as http:
        control = await Control(cfg, http).open()
        peer = None
        try:
            await control.create_room(room, "source probe")
            peer = TestPeer(cfg, "P", room, new_display())
            await peer.start()
            await peer.wait_channel_open()
            sid, hid = peer.ids
            result.update(session=sid, handle=hid, display=peer.display)
            info = {}
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                reply = await _admin(http, cfg.admin_url, cfg.admin_secret, f"/{sid}/{hid}", {"janus": "handle_info"})
                info = reply.get("info") or {}
                ice = (info.get("webrtc") or {}).get("ice") or {}
                if ice.get("selected-pair") and ((info.get("plugin_specific") or {}).get("rtp_in_count") or 0) > 0:
                    break
                await asyncio.sleep(0.2)
            ice = (info.get("webrtc") or {}).get("ice") or {}
            own = own_candidates(peer._pc.localDescription.sdp if peer._pc.localDescription else "")
            pair = parse_pair(ice.get("selected-pair"))
            remote_types = {}
            for line in ice.get("remote-candidates") or []:
                m = _TYPE.search(str(line))
                if m:
                    remote_types[m.group(1)] = remote_types.get(m.group(1), 0) + 1
            result.update(peer_own_candidates=own, janus_selected_pair=ice.get("selected-pair"), janus_pair=pair,
                          janus_remote_candidate_types=remote_types,
                          rtp_in_count=(info.get("plugin_specific") or {}).get("rtp_in_count"), **verdict(own, pair))
        finally:
            if peer is not None:
                await peer.close()
            try:
                await control.destroy_room(room)
            except Exception:
                pass
            await control.close()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        tag = "[source-probe]"
        print(f"{tag} {args.label or cfg.janus_url}: room {room}, Janus session {result.get('session')}, "
              f"handle {result.get('handle')}")
        print(f"{tag} peer's own candidates: " + ", ".join(f"{c['address']}:{c['port']} {c['type']}"
                                                          for c in result.get("peer_own_candidates") or []))
        print(f"{tag} Janus selected pair:  {result.get('janus_selected_pair')}")
        print(f"{tag} Janus remote candidate types: {result.get('janus_remote_candidate_types')}; "
              f"rtp_in_count {result.get('rtp_in_count')}")
        pair = result.get("janus_pair")
        if pair is None:
            print(f"{tag} verdict: NO PAIR — media did not come up within {args.timeout:g} s")
        else:
            remote = pair["remote"]
            what = "is" if result["remote_address_is_peers_own"] else "is NOT"
            print(f"{tag} verdict: {result['verdict'].upper()} — the pair's remote address {remote['address']} {what} "
                  f"one of the peer's own addresses {result['peer_addresses']} (Janus types it {remote['type']})")
        print(f"{tag} ICE diagnostics record: legion-voice-selfcheck --session {result.get('handle')}")
    return 0 if result.get("janus_pair") else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.integration.source_probe", description=__doc__.split("\n\n")[0])
    parser.add_argument("--janus-url", default="http://localhost:24223/voice")
    parser.add_argument("--admin-url", default="http://localhost:24225/voiceAdmin")
    parser.add_argument("--env", default=str(REPO / ".env"), help="where JS_API_SECRET / JS_ADMIN_SECRET are read from")
    parser.add_argument("--api-secret", default="")
    parser.add_argument("--admin-secret", default="")
    parser.add_argument("--label", default="")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 64 if exc.code else 0
    return asyncio.run(probe(args))


if __name__ == "__main__":
    sys.exit(main())
