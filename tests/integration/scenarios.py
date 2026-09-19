"""The churn scenarios S1-S8 and S10-S14 -- the mixer's robustness contract (README.md in this directory).

Each scenario gets a fresh Ctx (its own control handle and room ids) and the runner tears it down
in a finally block. Every expectation is a poll of the oracle (Admin API handle_info, or the
plugin's list / listparticipants) with a bounded timeout; nothing sleeps blindly.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from tests.integration.harness import (POLL_TIMEOUT, BatchResender, Ctx, Fail, Heartbeater, Skip, TestPeer, displays,
                                       fields, mixer_exec, mixer_logs, mixer_restart, mixer_target, new_display, pick, until,
                                       wait_mixer_up, mint_cap)
from tests.integration.source_probe import own_candidates, parse_pair, verdict


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    covers: str
    fn: object


async def s1_join_leave_rejoin(ctx: Ctx) -> None:
    r = ctx.new_room()
    da, db = new_display(), new_display()
    a = await ctx.join("A", r, da)
    b = await ctx.join("B", r, db)
    await ctx.ready(a, b)

    await a.close()
    await ctx.until_participants(r, lambda rows: displays(rows) == [db], "only B remains after A's leave")

    a2 = await ctx.join("A2", r, da)
    await ctx.ready(a2)
    await ctx.until_info(b, lambda i: i.get("datachannel_open") is True and i.get("room") == r,
                         "B still datachannel_open after A's rejoin")
    await ctx.until_participants(r, lambda rows: sorted(displays(rows)) == sorted([da, db]),
                                 "exactly one row per display after A rejoined (no duplicate display rows)")


async def s2_crash_without_leave(ctx: Ctx) -> None:
    r = ctx.new_room()
    db = new_display()
    a = await ctx.join("A", r)
    b = await ctx.join("B", r, db)
    await ctx.ready(a, b)

    await b.crash()
    await ctx.until_participants(r, lambda rows: db not in displays(rows),
                                 "B's row gone within 5 s of its PeerConnection dying without a leave (O-56)")
    await ctx.until_info(a, lambda i: i.get("room_participants") == 1,
                         "room_participants back to 1 (B's mix slot released)")

    c = await ctx.join("C", r)
    await ctx.ready(c)
    await ctx.until_participants(r, lambda rows: sorted(displays(rows)) == sorted([a.display, c.display]),
                                 "third peer C admitted after B's slot was released")

    b2 = await ctx.join("B2", r, db)
    await ctx.ready(b2)
    await ctx.until_participants(r, lambda rows: displays(rows).count(db) == 1 and len(rows) == 3,
                                 "B rejoined cleanly with a new session (one row for its display)")


async def s3_mute_persistence(ctx: Ctx) -> None:
    r = ctx.new_room()
    da, db = new_display(), new_display()
    a = await ctx.join("A", r, da)   # the listener
    b = await ctx.join("B", r, db)   # the moderated source
    await ctx.ready(a, b)

    resender = BatchResender(ctx.admin, r, {da: [db]})
    ctx.background.append(resender)
    resender.start()
    await ctx.until_info(a, lambda i: i.get("mod_muted_entries") == 1,
                         "A mod_muted_entries 1 from the re-sent mute batch")

    # Pause across the rejoin so the fresh session can be read BEFORE the first batch reaches it:
    # nothing can be deferred for A while it is away, so any state there would be carried over.
    await resender.pause()
    await a.close()
    a2 = await ctx.join("A2", r, da)
    first = await ctx.info(a2)
    if first is None or first.get("mod_muted_entries") != 0 or first.get("excluded_entries") != 0:
        raise Fail("A's rejoined session has no mod-mute/excluded state before the first batch (O-68)", pick(first))

    resender.resume()
    await ctx.until_info(a2, lambda i: i.get("mod_muted_entries") == 1,
                         "the re-sent batch lands on A's fresh session within 1 s", timeout=1.0)

    await resender.stop()
    response = await ctx.admin.peer_ctl_batch(r, "replace", mute={da: []})
    if response.get("slvoice") != "applied":
        raise Fail("clearing batch applied", response)
    await ctx.until_info(a2, lambda i: i.get("mod_muted_entries") == 0, "clearing batch empties A's mod-mute set")


async def s4_mixer_restart(ctx: Ctx) -> None:
    if not ctx.cfg.restart:
        raise Skip("--no-restart")
    r = ctx.new_room()
    a = await ctx.join("A", r)
    b = await ctx.join("B", r)
    await ctx.ready(a, b)

    res = await mixer_restart(ctx.cfg)
    if res.returncode != 0:
        raise Fail(f"restart the mixer ({mixer_target(ctx.cfg)})", (res.stderr or res.stdout)[-400:])
    await wait_mixer_up(ctx.cfg, ctx.http)

    # The restart killed both Janus sessions; close what is left of them and start over.
    await a.close()
    await b.close()
    await ctx.reopen_control()
    a2 = await ctx.join("A2", r, a.display)
    b2 = await ctx.join("B2", r, b.display)
    await ctx.until_participants(r, lambda rows: sorted(displays(rows)) == sorted([a.display, b.display]),
                                 "room re-created after the restart with both peers present")
    for p in (a2, b2):
        first = await ctx.until_info(p, lambda i: (i.get("rtp_in_count") or 0) > 0,
                                     f"{p.name} rtp_in_count > 0 after the restart", timeout=10.0)
        base = first.get("rtp_in_count") or 0
        await ctx.until_info(p, lambda i, base=base: (i.get("rtp_in_count") or 0) > base,
                             f"{p.name} rtp_in_count climbing after the restart", timeout=10.0)


async def s5_room_switch_grace(ctx: Ctx) -> None:
    grace = ctx.cfg.grace
    r1, r2 = ctx.new_room(), ctx.new_room()
    da, db = new_display(), new_display()
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    a = await ctx.join("A", r1, da)
    b = await ctx.join("B", r1, db)
    await ctx.ready(a, b)

    # The teleport: leave R1, join R2 (a new session each, as the viewer re-provisions).
    await a.close()
    await b.close()
    emptied = time.monotonic()
    a2 = await ctx.join("A2", r2, da)
    b2 = await ctx.join("B2", r2, db)
    await ctx.ready(a2, b2)
    await ctx.until_participants(r2, lambda rows: sorted(displays(rows)) == sorted([da, db]), "R2 holds both peers")

    ids = await ctx.control.room_ids()
    if r1 not in ids and time.monotonic() - emptied < grace - 1:
        raise Fail("R1 still listed inside the grace period", {"rooms": sorted(i for i in ids if i >= r1)})
    await until(ctx.control.room_ids, lambda ids: r1 not in ids,
                f"R1 absent from list after the {grace} s grace (O-54)", timeout=grace + 15, step=1.0,
                show=lambda ids: {"r1_listed": r1 in ids, "waited_s": round(time.monotonic() - emptied, 1)})
    waited = time.monotonic() - emptied
    if waited < grace - 1:
        raise Fail("R1 destroyed before its grace expired", {"waited_s": round(waited, 1), "grace_s": grace})

    logs = await mixer_logs(ctx.cfg, since)
    m = re.search(rf"\[slvoice\] room {r1} destroyed after (\d+)s empty", logs)
    if m is None:
        raise Fail(f"log line '[slvoice] room {r1} destroyed after <n>s empty'",
                   {"read_from": mixer_target(ctx.cfg), "matches_for_other_rooms":
                    re.findall(r"\[slvoice\] room \d+ destroyed after \d+s empty", logs)[-3:]})
    if int(m.group(1)) < grace:
        raise Fail("logged empty time shorter than the grace", {"logged_s": int(m.group(1)), "grace_s": grace})

    c = await ctx.join("C", r1)
    await ctx.ready(c)
    await ctx.until_participants(r1, lambda rows: displays(rows) == [c.display],
                                 "a join into the destroyed R1 re-creates it")


async def s6_peer_ctl_full(ctx: Ctx) -> None:
    r = ctx.new_room()
    a = await ctx.join("A", r)
    await ctx.ready(a)
    sources = [new_display() for _ in range(34)]
    for s in sources[:33]:
        a.send({"ug": {s: 110}})   # one target per message, as setUserVolume sends it
    await ctx.until_info(a, lambda i: i.get("peer_ctl_entries") == 32 and i.get("peer_ctl_full_drops") == 1,
                         "33 per-source gains: peer_ctl_entries 32 and peer_ctl_full_drops 1")
    response = await ctx.admin.peer_ctl_batch(r, "replace", mute={a.display: [sources[33]]})
    if response.get("slvoice") != "applied":
        raise Fail("mute batch applied", response)
    await ctx.until_info(a, lambda i: i.get("mod_muted_entries") == 1,
                         "moderation mute of a 34th source still applies with peer_ctl full (O-49)")


async def s7_geometry_persistence(ctx: Ctx) -> None:
    r = ctx.new_room()
    b = await ctx.join("B", r)
    await ctx.ready(b)
    b.send_geometry()
    await ctx.until_info(b, lambda i: {"sp", "sh", "lp", "lh"} <= fields(i.get("last_msg_fields_seen")),
                         "geometry message carries sp/sh/lp/lh")
    b.send({"ug": {new_display(): 220}})
    await ctx.until_info(b, lambda i: i.get("last_msg_fields_seen") == "ug"
                         and {"sp", "lp"} <= fields(i.get("last_data_fields_seen")),
                         "ug-only message: last_msg_fields_seen 'ug', sp/lp kept in last_data_fields_seen (O-64)")


async def s8_hangup_rejoin_same_display(ctx: Ctx) -> None:
    r = ctx.new_room()
    db = new_display()
    a = await ctx.join("A", r)
    b = await ctx.join("B", r, db)
    await ctx.ready(a, b)

    # Make the overlap real. A rejoin started after the crash -- even one whose offer is ready at the
    # moment of the crash -- reaches the plugin only after Janus has processed the old PeerConnection's
    # DTLS close (measured 2026-09-13: no duplicate-display WARN either way). So B2 joins while B is
    # still up, which is the residue case O-13 is about (two rows for one display), and then B's
    # PeerConnection dies; the old row must go and the new handle must be the one left.
    b2 = await ctx.join("B2", r, db)
    await ctx.until_participants(r, lambda rows: displays(rows).count(db) == 2,
                                 "both of B's handles present before the crash (the overlap)")
    await b.crash()
    info = await ctx.until_info(b2, lambda i: i.get("room") == r and i.get("id"), "B2 joined the room")
    new_id = info.get("id")
    await ctx.until_participants(
        r, lambda rows: [row.get("id") for row in rows if row.get("display") == db] == [new_id],
        "within 5 s one row for B's display, and it is the new handle (O-13 residue)", timeout=POLL_TIMEOUT)
    old = await ctx.info(b)
    if old is not None and old.get("room") == r:
        raise Fail("B's crashed handle is still in the room", pick(old))
    await ctx.ready(b2)


async def s10_no_media_reap(ctx: Ctx) -> None:
    limit = ctx.cfg.join_timeout
    r = ctx.new_room()
    dg = new_display()
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    g = await ctx.join_without_media("G", r, dg)
    joined = time.monotonic()
    await ctx.until_participants(r, lambda rows: displays(rows) == [dg], "G present right after its join")
    first = await ctx.info(g)
    if first is None or first.get("room") != r or first.get("datachannel_open") is True:
        raise Fail("G's handle is in the room with no media (datachannel_open not true)", pick(first))

    await until(lambda: ctx.control.participants(r), lambda rows: dg not in displays(rows),
                f"G gone within the {limit} s join timeout + 5 s (O-75)",
                timeout=max(1.0, limit + 5 - (time.monotonic() - joined)), step=0.5,
                show=lambda rows: {"g_listed": dg in displays(rows), "waited_s": round(time.monotonic() - joined, 1)})
    waited = time.monotonic() - joined
    if waited < limit - 1:
        raise Fail("G reaped before its join timeout", {"waited_s": round(waited, 1), "join_timeout_s": limit})
    after = await ctx.info(g)
    if after is not None and after.get("room") == r:
        raise Fail("G's handle still reports the room after the reap", pick(after))

    logs = await mixer_logs(ctx.cfg, since)
    m = re.search(rf"\[slvoice\] {re.escape(dg)} reaped from room {r}: no media (\d+)s after join", logs)
    if m is None:
        raise Fail(f"log line '[slvoice] {dg} reaped from room {r}: no media <n>s after join'",
                   {"read_from": mixer_target(ctx.cfg),
                    "reap_lines": re.findall(r"\[slvoice\] \S+ reaped from room \d+: no media \d+s after join",
                                             logs)[-3:]})
    if int(m.group(1)) < limit:
        raise Fail("logged no-media time shorter than the join timeout", {"logged_s": int(m.group(1)), "join_timeout_s": limit})

    g2 = await ctx.join("G2", r, dg)
    await ctx.ready(g2)
    await ctx.until_participants(r, lambda rows: displays(rows) == [dg],
                                 "a fresh join by G's display is admitted and gets media (one row)")


async def s11_muted_source_dot_dark(ctx: Ctx) -> None:
    """SC-87: a source moderation-muted for one listener lights no dot for that listener, while an
    unmuted listener in the same room still sees it lit."""
    r = ctx.new_room()
    da, db, dc = new_display(), new_display(), new_display()
    a = await ctx.join("A", r, da)   # the listener the source is muted for
    b = await ctx.join("B", r, db)   # the talking source
    c = await ctx.join("C", r, dc)   # a listener it is not muted for
    await ctx.ready(a, b, c)

    async def lit(peer):
        return [e for e in peer.dots.get(db, []) if e[0] > 0]
    await until(lambda: lit(a), lambda xs: len(xs) > 0, "B's tone lights A's dot before the mute")

    resender = BatchResender(ctx.admin, r, {da: [db]})
    ctx.background.append(resender)
    resender.start()
    await ctx.until_info(a, lambda i: i.get("mod_muted_entries") == 1, "A mod_muted_entries 1 from the mute batch")

    # Judge only batches that arrive after the mute has landed.
    a.dots[db] = []
    c.dots[db] = []

    async def fresh(peer):
        return list(peer.dots.get(db, []))
    after_a = await until(lambda: fresh(a), lambda xs: len(xs) >= 10, "A receives 10 batches after the mute")
    after_c = await until(lambda: fresh(c), lambda xs: len(xs) >= 10, "C receives 10 batches after the mute")
    if any(p > 0 or v for p, v in after_a):
        raise Fail("A's dot for the moderation-muted B stays dark (p=0, v=false) in every batch", after_a)
    if not any(p > 0 for p, _ in after_c):
        raise Fail("C, for whom B is not muted, still sees B's dot lit", after_c)


async def s12_spatial_cull_and_pan(ctx: Ctx) -> None:
    """The spatial path runs: in a room created WITHOUT spatial_audio (spatial by default, O-83), a
    source inside the cull distance is audible to the listener and panned, the same source beyond
    the cutoff is culled, and moving back inside the re-add distance makes it audible again. Observed
    through the listener's handle_info mix levels, not by ear."""
    r = ctx.new_room()
    a = await ctx.join("A", r)   # the listener
    b = await ctx.join("B", r)   # the talking source
    await ctx.ready(a, b)

    def geometry_seen(i):
        return {"sp", "lp"} <= fields(i.get("last_data_fields_seen"))

    a.send_geometry(128.0, 128.0, 25.0)
    b.send_geometry(128.0, 138.0, 25.0)   # 10 m to A's side: full volume, hard-panned
    await ctx.until_info(a, geometry_seen, "A's geometry reached the mixer")
    await ctx.until_info(b, geometry_seen, "B's geometry reached the mixer")

    def panned(i):
        lvl, l, rr = i.get("last_mix_rms") or 0.0, i.get("last_mix_rms_l") or 0.0, i.get("last_mix_rms_r") or 0.0
        return lvl > 0.01 and max(l, rr) > 0.05 and min(l, rr) < 0.1 * max(l, rr)
    await ctx.until_info(a, panned, "B 10 m to the side is audible in A's mix and hard-panned (one channel near 0)")

    b.send_geometry(128.0, 238.0, 25.0)   # 110 m: beyond the 60 m cutoff
    await ctx.until_info(a, lambda i: i.get("last_mix_rms") == 0.0,
                         "B at 110 m is culled: A's mix level is exactly 0")

    b.send_geometry(128.0, 148.0, 25.0)   # 20 m: inside the 58 m re-add distance
    await ctx.until_info(a, lambda i: (i.get("last_mix_rms") or 0.0) > 0.01,
                         "B back at 20 m is re-added: A's mix is audible again")


async def s13_relay_only_peer(ctx: Ctx) -> None:
    """The relay path end to end. A offers ONLY relay candidates (TURN from --turn-uri, e.g. the turn-test profile);
    B is a normal peer. A's media must cross the relay in both directions. The proof:
    - A's offer holds relay candidates only;
    - A's own ICE agent nominated pairs whose local candidate is its relay, with no non-relay socket left;
    - B hears A;
    - A receives audio back.
    The mixer's selected pair is logged as information and not asserted: on a published-port mixer the relay's packets
    reach Janus through the port publish, so Janus reports them as a prflx pair from the gateway, as it does for a
    direct peer."""
    cfg = ctx.cfg
    if not cfg.turn_uri:
        raise Skip("no TURN for the harness: `docker compose --profile turn-test up -d turn-test`, then pass "
                   "--turn-uri 'turn:127.0.0.1:3478?transport=tcp' --turn-secret turn-test-secret")
    r = ctx.new_room()
    a = await ctx.join_relay_only("A", r)
    b = await ctx.join("B", r)
    offered = a.offered_candidates()
    if not offered or {kind for kind, _ in offered} != {"relay"}:
        raise Fail("A's offer carries only relay candidates", offered)
    await ctx.ready(a, b)

    async def relay_proof():
        return a.relay_proof()

    proof = await until(relay_proof, lambda p: bool(p["nominated_local_types"]), "A's ICE agent nominated a pair")
    if set(proof["nominated_local_types"]) != {"relay"} or proof["non_relay_sockets"]:
        raise Fail("A's nominated pairs use its relay candidate and no non-relay socket is left", proof)
    ice = await ctx.admin.handle_ice(a) or {}
    print(f"      S13 info: A relay candidates {sorted({addr for _, addr in offered})}; A's ICE {proof}; "
          f"mixer selected pair {ice.get('selected-pair')}", flush=True)
    await ctx.until_info(b, lambda i: (i.get("last_mix_rms") or 0.0) > 0.01,
                         "A's tone, sent through the relay, is audible in B's mix")
    await until(a.packets_received, lambda n: n > 25, "A receives the mixer's audio back through the relay")


async def s14_path_verdict_matches_source(ctx: Ctx) -> None:
    """A.6: the ICE diagnostics path verdict agrees with where the peer's packets actually came from.
    Ground truth is the source probe's comparison: the remote end of Janus's selected pair is, or is not, one of the
    addresses the peer itself holds (its own offer's candidates). After A's session ends, its diagnostics record must
    read `undetermined` when the source was rewritten on the way (a Docker Desktop port publish), never direct or relay,
    and `direct` when the source survived (a Linux published port or host networking). Only one branch can run on a
    given deployment; the info line says which."""
    r = ctx.new_room()
    a = await ctx.join("A", r)
    await ctx.ready(a)
    sid, hid = a.ids
    ice = await until(lambda: ctx.admin.handle_ice(a), lambda i: bool(i and i.get("selected-pair")),
                      "A has a selected candidate pair")
    truth = verdict(own_candidates(a._pc.localDescription.sdp), parse_pair(ice.get("selected-pair")))
    await a.close()

    async def record():
        res = await mixer_exec(ctx.cfg, "legion-voice-selfcheck", "--session", str(hid), "--json", timeout=30.0)
        try:
            data = json.loads(res.stdout or "{}")
        except ValueError:
            return None
        return data.get("session") if data.get("found") else None

    rec = await until(record, lambda s: s is not None and s.get("live") is False,
                      "A's ICE diagnostics record exists and has ended", timeout=20.0, step=1.0)
    path = rec.get("path") or {}
    print(f"      S14 info: Janus pair {ice.get('selected-pair')}; A's own addresses {truth.get('peer_addresses')}; "
          f"source {truth['verdict']}; diagnostics path {path.get('verdict')}", flush=True)
    expected = {"rewritten": "undetermined", "preserved": "direct"}.get(truth["verdict"])
    if expected is None:
        raise Fail("A's selected pair can be parsed", {"selected-pair": ice.get("selected-pair")})
    if path.get("verdict") != expected:
        raise Fail(f"a {truth['verdict']} source address reads {expected} in the diagnostics record",
                   {"selected-pair": ice.get("selected-pair"), "peer_addresses": truth.get("peer_addresses"),
                    "path": path})


# ---- Phase 0 slice 0.3: the visibility authority (docs/voice/nonspatial-phase0-design.md §9) -------------------------
# The harness is the sim here: it declares rooms, arms, heartbeats and reads the replies. S15 needs a mixer with
# fail-closed OFF (the shipped default), S16-S20 one with it ON (a scratch mixer, never the live grid's), and S21 a
# pre-0.3 image; each skips on the wrong mixer, so one full run proves which mode it ran against.

_epoch_base = (int(time.time() * 1000) << 16) | 0x5a00


def _epoch(n: int) -> str:
    """The n-th authority epoch of this run, as the sim formats it (16 lowercase hex digits); larger n is newer."""
    return "%016x" % (_epoch_base + n)


def _vis(info) -> dict:
    return (info or {}).get("visibility") or {}


def _audible(info) -> bool:
    return (info.get("last_mix_rms") or 0.0) > 0.01


def _silent(info) -> bool:
    return info.get("last_mix_rms") == 0.0


async def _mode(ctx: Ctx, peer, want_fail_closed: bool) -> dict:
    """The mixer's Phase 0 mode from the peer's handle_info; Skip when it is not the mode this scenario needs."""
    info = await ctx.until_info(peer, lambda i: isinstance(i.get("visibility"), dict), f"{peer.name} reports a visibility block")
    vis = _vis(info)
    if "fail_closed" not in vis:
        raise Skip("the mixer reports no visibility authority (a pre-0.3 image)")
    if vis["fail_closed"] is not want_fail_closed:
        raise Skip(f"this scenario needs JS_VIS_FAIL_CLOSED={1 if want_fail_closed else 0}; the mixer reports "
                   f"fail_closed={vis['fail_closed']}")
    return vis


async def _hold(ctx: Ctx, peer, pred, what: str, seconds: float) -> None:
    """pred holds at every poll for `seconds` (a continuous check, not a blind sleep)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        info = await ctx.info(peer)
        if info is None or not pred(info):
            raise Fail(what, pick(info))
        await asyncio.sleep(0.1)


def _check_reply(reply: dict, what: str, **expect) -> None:
    for key, want in expect.items():
        if reply.get(key) != want:
            raise Fail(what, reply)


async def _declared_pair(ctx: Ctx, declared: bool = True):
    r = ctx.new_room()
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=declared)
    a = await ctx.join("A", r)   # the listener
    b = await ctx.join("B", r)   # the talking source
    await ctx.ready(a, b)
    return r, a, b


async def s15_shadow_mode(ctx: Ctx) -> None:
    """Fail-closed OFF: the full protocol against a declared room changes nothing audible, and would_silence counts."""
    r, a, b = await _declared_pair(ctx)
    vis = await _mode(ctx, a, want_fail_closed=False)
    if vis.get("vis_authority") is not True or vis.get("enforced") is not False:
        raise Fail("a room created with vis_authority reports vis_authority true and enforced false", vis)
    await ctx.until_info(a, _audible, "never armed: A hears B's tone, exactly as before (shadow mode)")
    await ctx.until_info(a, lambda i: _vis(i).get("would_silence_listeners") == 2 and _vis(i).get("would_silence_pairs") == 2
                         and i.get("vis_row") == 1, "never armed: would_silence_listeners 2, would_silence_pairs 2, A in row 1")

    old = await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: []}, mute={a.display: []})
    if old.get("slvoice") != "applied" or old.get("vis_protocol") != 2 or not re.fullmatch(r"[0-9a-f]{16}", old.get("mixer_instance") or "") \
            or "status" in old:
        raise Fail("an unstamped (old-sim) batch is applied; the reply advertises vis_protocol 2 and a mixer_instance, no status", old)

    e1 = _epoch(1)
    armed = await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: [], b.display: []},
                                           mute={a.display: [], b.display: []}, epoch=e1, generation=1)
    _check_reply(armed, "the arming replace: applied, status ok, authority_epoch E1, policy_generation 1",
                 slvoice="applied", status="ok", authority_epoch=e1, policy_generation=1)
    hb = await ctx.admin.heartbeat(e1, {r: {"policy_generation": 1, "listeners": {a.display: 1, b.display: 1}}})
    room = (hb.get("rooms") or {}).get(str(r)) or {}
    if hb.get("slvoice") != "heartbeat" or hb.get("vis_protocol") != 2 or hb.get("mixer_instance") != old.get("mixer_instance") \
            or room.get("status") != "ok" or room.get("unarmed_listeners") or room.get("stale_listeners"):
        raise Fail("a matching heartbeat: status ok, nothing stale or unarmed, the same mixer_instance", hb)
    await ctx.until_info(a, lambda i: _vis(i).get("would_silence_listeners") == 0 and _vis(i).get("would_silence_pairs") == 0
                         and i.get("vis_row") == 4, "armed: would_silence 0 and A in row 4")
    await ctx.until_info(a, _audible, "armed: A still hears B")

    e2 = _epoch(2)
    hb = await ctx.admin.heartbeat(e2, {r: {"policy_generation": 1, "listeners": {a.display: 1, b.display: 1}}})
    room = (hb.get("rooms") or {}).get(str(r)) or {}
    if sorted(room.get("unarmed_listeners") or []) != sorted([a.display, b.display]) or room.get("authority_epoch") != e2:
        raise Fail("a new-epoch heartbeat lists both unarmed and reports E2", hb)
    await ctx.until_info(a, lambda i: _vis(i).get("would_silence_listeners") == 2, "disarmed by E2: counted again")
    await _hold(ctx, a, _audible, "disarmed in shadow mode: A keeps hearing B", 1.5)
    excl = await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: [b.display]}, mute={a.display: []}, epoch=e2, generation=1)
    _check_reply(excl, "an exclusion replace in E2 is applied", slvoice="applied", status="ok")
    await ctx.until_info(a, _silent, "the exclusion silences B for A, as before")


async def s16_fail_closed_decision_table(ctx: Ctx) -> None:
    """Fail-closed ON: rows 1, 3 and 4 and the pair rule, heard; heartbeats keep policy fresh; the window; recovery; a new
    epoch neither revalidates nor blocks re-arming."""
    r, a, b = await _declared_pair(ctx)
    vis = await _mode(ctx, a, want_fail_closed=True)
    stale_s = (vis.get("stale_ms") or 8000) / 1000.0

    await ctx.until_info(a, lambda i: _silent(i) and i.get("vis_row") == 1, "row 1: A, unarmed, has an exactly silent mix")
    a.dots.clear()
    await until(lambda: _count(a.dots.get(a.display)), lambda n: n >= 10, "A receives 10 power batches while unarmed")
    if b.display in a.dots or any(d == b.display for d, _ in a.presence):
        raise Fail("row 1: A gets no power or presence entry for B", {"dots_for_B": a.dots.get(b.display), "presence": a.presence})

    e1 = _epoch(1)
    hb = Heartbeater(ctx.admin, e1, r, {a.display: 1}).start()
    ctx.background.append(hb)
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: []}, mute={a.display: []}, epoch=e1, generation=1),
                 "arming A", slvoice="applied", status="ok")
    await ctx.until_info(a, lambda i: i.get("vis_row") == 4, "A armed: row 4")
    await _hold(ctx, a, _silent, "pair rule: armed A does not hear unarmed B", 1.5)

    armed_at = time.monotonic()
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={b.display: []}, mute={b.display: []}, epoch=e1, generation=2),
                 "arming B", slvoice="applied", status="ok")
    hb.listeners = {a.display: 1, b.display: 2}
    await ctx.until_info(a, _audible, "row 4: A hears B once both are armed")
    print(f"      S16 info: audible {time.monotonic() - armed_at:.2f} s after B's arming reply (poll-limited)", flush=True)
    await until(lambda: _joined(a, b.display), lambda ok: ok, "row 4: A gets B's join presence")

    await _hold(ctx, a, _audible, "heartbeats alone keep A audible (10 s, no batches)", 10.0)

    last = await hb.pause()
    until_s = last + stale_s - 1.0
    await _hold(ctx, a, _audible, f"window: still audible {stale_s - 1.0:.1f} s after the last heartbeat", max(0.0, until_s - time.monotonic()))
    await ctx.until_info(a, lambda i: _silent(i) and i.get("vis_row") == 3, "row 3: silent once the window has passed",
                         timeout=max(1.0, last + stale_s + 1.0 - time.monotonic()))
    print(f"      S16 info: silent {time.monotonic() - last:.2f} s after the last heartbeat (window {stale_s:.1f} s)", flush=True)
    await until(lambda: _left(a, b.display), lambda ok: ok, "row 3: A gets B's leave presence")

    hb.resume()
    await ctx.until_info(a, _audible, "recovery: resumed heartbeats make A audible again without a new arming")

    await hb.stop()
    e2 = _epoch(2)
    reply = await ctx.admin.heartbeat(e2, {r: {"policy_generation": 2, "listeners": {a.display: 1, b.display: 2}}})
    room = (reply.get("rooms") or {}).get(str(r)) or {}
    if a.display not in (room.get("unarmed_listeners") or []) or room.get("authority_epoch") != e2:
        raise Fail("a new-epoch heartbeat at matching generations lists A unarmed and reports E2", reply)
    await ctx.until_info(a, lambda i: _silent(i) and i.get("vis_row") == 1, "a new-epoch heartbeat does not revalidate: A silent")
    hb2 = Heartbeater(ctx.admin, e2, r, {a.display: 1, b.display: 1}).start()
    ctx.background.append(hb2)
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: [], b.display: []},
                                                mute={a.display: [], b.display: []}, epoch=e2, generation=1),
                 "arming in E2", slvoice="applied", status="ok", authority_epoch=e2)
    await ctx.until_info(a, _audible, "new-epoch arming makes A audible")


async def _count(xs) -> int:
    return len(xs or [])


async def _joined(peer, display: str) -> bool:
    return (display, "j") in peer.presence


async def _left(peer, display: str) -> bool:
    return (display, "l") in peer.presence


async def s17_fail_closed_undeclared_room(ctx: Ctx) -> None:
    """Fail-closed ON: a room created without vis_authority is not enforced, and the mixer says so once."""
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r, a, b = await _declared_pair(ctx, declared=False)
    vis = await _mode(ctx, a, want_fail_closed=True)
    if vis.get("vis_authority") is not False or vis.get("enforced") is not False:
        raise Fail("an undeclared room reports vis_authority false and enforced false", vis)
    await ctx.until_info(a, _audible, "undeclared: A hears B with no arming and no heartbeat")
    await _hold(ctx, a, lambda i: _audible(i) and _vis(i).get("would_silence_listeners") == 0, "undeclared: audible, nothing counted", 2.0)
    logs = await mixer_logs(ctx.cfg, since)
    line = f"fail-closed enabled but room {r} has no vis_authority: NOT enforced"
    if logs.count(line) != 1:
        raise Fail(f"the mixer logs '{line}' exactly once", {"count": logs.count(line)})


async def s18_fail_closed_epochs_base_omission_stop(ctx: Ctx) -> None:
    """Fail-closed ON: a lower epoch is refused while fresh; delta base checks; omission disarms; a graceful stop silences
    at once; a lower epoch then takes over."""
    r, a, b = await _declared_pair(ctx)
    await _mode(ctx, a, want_fail_closed=True)
    e1, e2 = _epoch(1), _epoch(2)
    hb = Heartbeater(ctx.admin, e2, r, {a.display: 1, b.display: 1}).start()
    ctx.background.append(hb)
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: [], b.display: []},
                                                mute={a.display: [], b.display: []}, epoch=e2, generation=1),
                 "arming in E2", status="ok")
    await ctx.until_info(a, _audible, "armed in E2: A hears B")

    lower = await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: [b.display]}, mute={a.display: []}, epoch=e1, generation=9)
    _check_reply(lower, "a lower epoch while E2 is fresh: refused as stale_epoch", slvoice="error", reason="stale_epoch",
                 status="stale_epoch", authority_epoch=e2)
    await _hold(ctx, a, lambda i: _audible(i) and _vis(i).get("authority_epoch") == e2, "stale_epoch changed nothing", 1.0)

    bad = await ctx.admin.peer_ctl_batch(r, "add", excl={a.display: [b.display]}, epoch=e2, generation=2, base={a.display: 99})
    if bad.get("slvoice") != "applied" or a.display not in (bad.get("stale_listeners") or []):
        raise Fail("an add with a wrong base lists A in stale_listeners", bad)
    await ctx.until_info(a, lambda i: _silent(i) and i.get("vis_row") == 3 and i.get("excluded_entries") == 0,
                         "base mismatch: A's entry not applied, A silenced")
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: []}, mute={a.display: []}, epoch=e2, generation=3),
                 "re-arming A", status="ok")
    hb.listeners = {a.display: 3, b.display: 1}
    await ctx.until_info(a, _audible, "re-armed: A audible")
    good = await ctx.admin.peer_ctl_batch(r, "add", excl={a.display: [b.display]}, epoch=e2, generation=4, base={a.display: 3})
    if good.get("stale_listeners"):
        raise Fail("an add with the right base is not stale", good)
    hb.listeners = {a.display: 4, b.display: 1}
    await ctx.until_info(a, lambda i: _silent(i) and i.get("excluded_entries") == 1 and i.get("vis_listener_generation") == 4,
                         "base match: the exclusion applies and A's generation is 4")
    await ctx.admin.peer_ctl_batch(r, "remove", excl={a.display: [b.display]}, epoch=e2, generation=5, base={a.display: 4})
    hb.listeners = {a.display: 5, b.display: 1}
    await ctx.until_info(a, _audible, "the remove restores B for A")

    hb.listeners = {b.display: 1}
    await ctx.until_info(a, lambda i: _silent(i) and i.get("vis_row") == 1, "omission: a heartbeat without A disarms and silences it")
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: []}, mute={a.display: []}, epoch=e2, generation=6),
                 "re-arming A", status="ok")
    hb.listeners = {a.display: 6, b.display: 1}
    await ctx.until_info(a, _audible, "re-armed after omission: A audible")

    await hb.stop()
    stop = await ctx.admin.heartbeat(e2, {r: {"policy_generation": 6, "listeners": {a.display: 6, b.display: 1}}}, stopping=True)
    stopped_at = time.monotonic()
    if ((stop.get("rooms") or {}).get(str(r)) or {}).get("status") != "ok":
        raise Fail("the stopping heartbeat is accepted", stop)
    await ctx.until_info(a, lambda i: _silent(i) and i.get("vis_row") == 3, "graceful stop: A silent without waiting for the window",
                         timeout=2.0)
    print(f"      S18 info: silent {time.monotonic() - stopped_at:.2f} s after the stopping heartbeat (poll-limited)", flush=True)
    take = await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: [], b.display: []},
                                          mute={a.display: [], b.display: []}, epoch=e1, generation=1)
    _check_reply(take, "after the stop a lower epoch takes over at once", slvoice="applied", status="ok", authority_epoch=e1)
    hb1 = Heartbeater(ctx.admin, e1, r, {a.display: 1, b.display: 1}).start()
    ctx.background.append(hb1)
    await ctx.until_info(a, _audible, "the takeover's arming makes A audible")


async def s19_fail_closed_reconnect_fanout(ctx: Ctx) -> None:
    """Fail-closed ON: the arming record belongs to the avatar in the room. A second session is audible at once and follows
    the same record; it stays audible after the first leaves; a rejoin keeps the armed columns; another room is separate."""
    r, a, b = await _declared_pair(ctx)
    await _mode(ctx, a, want_fail_closed=True)
    e1 = _epoch(1)
    hb = Heartbeater(ctx.admin, e1, r, {a.display: 1, b.display: 1}).start()
    ctx.background.append(hb)
    await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: [], b.display: []}, mute={a.display: [], b.display: []},
                                   epoch=e1, generation=1)
    await ctx.until_info(a, _audible, "armed: A hears B")

    a2 = await ctx.join("A2", r, a.display)
    await ctx.ready(a2)
    await ctx.until_info(a2, lambda i: _audible(i) and i.get("vis_row") == 4, "reconnect: A's second session is audible at once")
    hb.listeners = {a.display: 7, b.display: 1}
    await ctx.until_info(a, _silent, "fan-out: a wrong generation for A's display silences the first session")
    await ctx.until_info(a2, _silent, "fan-out: and the second")
    await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: []}, mute={a.display: []}, epoch=e1, generation=2)
    hb.listeners = {a.display: 2, b.display: 1}
    await ctx.until_info(a, _audible, "fan-out: one replace restores the first session")
    await ctx.until_info(a2, _audible, "fan-out: and the second")

    await a.close()
    await _hold(ctx, a2, _audible, "the second session stays audible after the first leaves", 2.0)

    await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: [b.display]}, mute={a.display: []}, epoch=e1, generation=3)
    hb.listeners = {a.display: 3, b.display: 1}
    await ctx.until_info(a2, lambda i: _silent(i) and i.get("excluded_entries") == 1, "the exclusion reaches A's session")
    await a2.close()
    a3 = await ctx.join("A3", r, a.display)
    await ctx.ready(a3)
    await ctx.until_info(a3, lambda i: i.get("vis_row") == 4 and i.get("excluded_entries") == 1,
                         "rejoin: a new session takes the armed columns at join (open question 2)")
    await _hold(ctx, a3, _silent, "rejoin: so it never hears the excluded source", 1.5)

    r2 = ctx.new_room()
    await ctx.control.create_room(r2, f"integration {ctx.name}", vis_authority=True)
    a4 = await ctx.join("A4", r2, a.display)
    c = await ctx.join("C", r2)
    await ctx.ready(a4, c)
    await _hold(ctx, a4, lambda i: _silent(i) and i.get("vis_row") == 1, "another room: A's avatar is unarmed there", 1.5)
    hb2 = Heartbeater(ctx.admin, e1, r2, {a.display: 1, c.display: 1}).start()
    ctx.background.append(hb2)
    await ctx.admin.peer_ctl_batch(r2, "replace", excl={a.display: [], c.display: []}, mute={a.display: [], c.display: []},
                                   epoch=e1, generation=1)
    await ctx.until_info(a4, _audible, "another room: audible once armed there")


async def s20_fail_closed_mixer_restart(ctx: Ctx) -> None:
    """Fail-closed ON: a restart gives a new mixer_instance; re-created rooms start with no epoch and silent joiners; a sim
    that re-arms on the instance change restores audio."""
    if not ctx.cfg.restart:
        raise Skip("--no-restart")
    r, a, b = await _declared_pair(ctx)
    await _mode(ctx, a, want_fail_closed=True)
    e1 = _epoch(1)
    before = await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: [], b.display: []},
                                            mute={a.display: [], b.display: []}, epoch=e1, generation=1)
    await ctx.until_info(a, _audible, "armed before the restart")

    res = await mixer_restart(ctx.cfg)
    if res.returncode != 0:
        raise Fail("restart the mixer", (res.stderr or res.stdout)[-400:])
    await wait_mixer_up(ctx.cfg, ctx.http)
    await a.close()
    await b.close()
    await ctx.reopen_control()
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=True)
    a2 = await ctx.join("A2", r, a.display)
    b2 = await ctx.join("B2", r, b.display)
    await ctx.ready(a2, b2)
    await ctx.until_info(a2, lambda i: _silent(i) and _vis(i).get("authority_epoch") == "0" * 16 and i.get("vis_row") == 1,
                         "after the restart: the re-created room holds no epoch and A is silent")

    after = await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: [], b.display: []},
                                           mute={a.display: [], b.display: []}, epoch=e1, generation=1)
    old_i, new_i = before.get("mixer_instance"), after.get("mixer_instance")
    if not old_i or not new_i or old_i == new_i:
        raise Fail("the restart changed mixer_instance", {"before": old_i, "after": new_i})
    print(f"      S20 info: mixer_instance {old_i} -> {new_i}", flush=True)
    hb = Heartbeater(ctx.admin, e1, r, {a.display: 1, b.display: 1}).start()
    ctx.background.append(hb)
    await ctx.until_info(a2, _audible, "re-arming on the instance change restores audio")


async def s21_old_image_ignores_stamp(ctx: Ctx) -> None:
    """Against a pre-0.3 image: vis_authority on create, and room_epoch, policy_generation and base on batches, are ignored,
    so a new sim's batches apply exactly as unstamped ones; no vis_protocol is advertised, so a 0.2 sim never heartbeats."""
    r, a, b = await _declared_pair(ctx)
    probe = await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: []}, mute={a.display: []})
    if "vis_protocol" in probe:
        raise Skip("this mixer advertises vis_protocol: S21 runs against a pre-0.3 image")
    await ctx.until_info(a, _audible, "A hears B in a room created with vis_authority (the old create parser ignored the key)")
    stamped = await ctx.admin.peer_ctl_batch(r, "add", mute={a.display: [b.display]}, epoch=_epoch(1), generation=2,
                                             base={a.display: 1})
    if stamped.get("slvoice") != "applied" or "vis_protocol" in stamped or "status" in stamped:
        raise Fail("a stamped add is applied and answered without Phase 0 keys", stamped)
    await ctx.until_info(a, lambda i: i.get("mod_muted_entries") == 1 and _silent(i), "the stamped mute applied as an unstamped one would")
    await ctx.admin.peer_ctl_batch(r, "replace", mute={a.display: []}, epoch=_epoch(0), generation=1)
    await ctx.until_info(a, lambda i: i.get("mod_muted_entries") == 0 and _audible(i),
                         "a stamped replace with a LOWER epoch still applies: the old image checks nothing")
    hb = await ctx.admin.heartbeat(_epoch(1), {r: {"policy_generation": 1, "listeners": {a.display: 1}}})
    if hb.get("reason") != "unknown_request" or "vis_protocol" in hb:
        raise Fail("peer_ctl_heartbeat is an unknown request to the old image", hb)


# ---- Phase 0 slice 0.4: the sim-issued join capability (design §11, ledger O-46) -------------------------------
# The harness mints capabilities exactly as the sim does, so it needs the mixer's JS_JOIN_CAP_SECRET
# (--join-cap-secret). S22 needs a mixer with the requirement OFF, S23 one with it ON (a scratch mixer, never the
# grid's), and S24 a pre-0.4 image. Each skips on the wrong mixer.

_NO_EPOCH = "0" * 16


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _mint_cap(secret: str, agent: str, session: str, room: int, epoch: str = _NO_EPOCH, generation: int = 0,
              iat: int | None = None, lifetime: int = 60, nonce: str | None = None) -> str:
    """The sim's JoinCapability.Mint. Slice 0.8e: one implementation, in harness.mint_cap, shared with the ordinary
    joins the harness now mints for itself."""
    return mint_cap(secret, agent, session, room, epoch=epoch, generation=generation, iat=iat, lifetime=lifetime,
                    nonce=nonce)


def _cap_state(info) -> dict:
    return (_vis(info) or {}).get("join_cap") or {}


async def _cap_mode(ctx: Ctx, peer, want_required: bool) -> dict:
    """The mixer's capability mode, or Skip when it is the wrong one (or a pre-0.4 image)."""
    if not ctx.cfg.join_cap_secret:
        raise Skip("--join-cap-secret not given: the harness cannot mint what this mixer would accept")
    info = await ctx.until_info(peer, lambda i: isinstance(_vis(i), dict), f"{peer.name} reports a visibility block")
    state = _cap_state(info)
    if "required" not in state:
        raise Skip("the mixer reports no join_cap state (a pre-0.4 image)")
    if state["required"] is not want_required:
        raise Skip(f"this scenario needs JS_JOIN_CAP_REQUIRED={1 if want_required else 0}; the mixer reports "
                   f"required={state['required']}")
    if not state.get("key_set"):
        raise Fail("the mixer has a JS_JOIN_CAP_SECRET set", state)
    return state


async def _refused(ctx: Ctx, name: str, room: int, display: str, reason: str, **kw) -> None:
    """One join that must be refused with exactly `reason`."""
    peer = await ctx.join_without_media(name, room, display, vis_authority=True, expect_join=False, **kw)
    reply = peer.join_reply or {}
    if reply.get("audiobridge") == "joined":
        raise Fail(f"a join with {reason} is refused, not admitted", reply)
    if reply.get("error_code") != 496 or reply.get("reason") != reason:
        raise Fail(f"the refusal names {reason} with error_code 496", reply)


async def s22_join_capability_shadow(ctx: Ctx) -> None:
    """Requirement OFF: capabilities are verified and counted, and no join is ever refused for one."""
    r = ctx.new_room()
    secret = ctx.cfg.join_cap_secret
    da, session_a = new_display(), new_display()
    a = await ctx.join("A", r, da, vis_authority=True,
                       join_cap=_mint_cap(secret, da, session_a, r), session_id=session_a)
    await ctx.ready(a)
    state = await _cap_mode(ctx, a, want_required=False)
    if state.get("accepted", 0) < 1 or state.get("seen", 0) < 1:
        raise Fail("a valid capability is verified and counted", state)

    # A capability for another agent: still admitted (shadow), and counted under its own reason.
    db, session_b = new_display(), new_display()
    b = await ctx.join("B", r, db, vis_authority=True,
                       join_cap=_mint_cap(secret, da, session_b, r), session_id=session_b)
    await ctx.ready(b)
    await ctx.until_info(b, lambda i: (_cap_state(i).get("refused") or {}).get("cap_wrong_agent", 0) >= 1,
                         "shadow: a capability bound to another agent is counted cap_wrong_agent")

    # No capability at all, in a declared room: counted cap_missing, and the join still succeeds. bare=True because
    # 0.8e otherwise mints one for an ordinary join into a declared room, which is exactly what this leg must not have.
    c = await ctx.join("C", r, vis_authority=True, bare=True)
    await ctx.ready(c)
    info = await ctx.until_info(c, lambda i: (_cap_state(i).get("refused") or {}).get("cap_missing", 0) >= 1,
                                "shadow: a join with no capability to a declared room is counted cap_missing")
    if _cap_state(info).get("enforced_refusals", 0) != 0:
        raise Fail("shadow mode refuses nothing", _cap_state(info))

    # An undeclared room is never gated, so a capability-less join there counts nothing.
    before = (_cap_state(info).get("refused") or {}).get("cap_missing", 0)
    r2 = ctx.new_room()
    d = await ctx.join("D", r2)
    await ctx.ready(d)
    after = (_cap_state(await ctx.info(d)).get("refused") or {}).get("cap_missing", 0)
    if after != before:
        raise Fail("an undeclared room counts no missing capability", {"before": before, "after": after})

    # O-91: the PER-SESSION marker. Everything checked above is process-wide and cannot say whether a GIVEN
    # join carried a capability -- which is why a soak could not attribute what it counted. These two fields
    # can. Checked on all four sessions after the fact, so this also shows the marker persists rather than
    # being a transient of the join. A session that never joined reads "none"; no harness peer can be in that
    # state (every peer joins), so that default is covered by construction, not by this check.
    for peer, want_present, want_verdict in ((a, True, "ok"), (b, True, "cap_wrong_agent"),
                                             (c, False, "cap_missing"), (d, False, "cap_missing")):
        i = await ctx.info(peer)
        if i.get("join_cap_present") is not want_present or i.get("join_cap_verdict") != want_verdict:
            raise Fail(f"O-91: {peer.name}'s session records join_cap_present={want_present} and "
                       f"join_cap_verdict={want_verdict!r}",
                       {"peer": peer.name, "join_cap_present": i.get("join_cap_present"),
                        "join_cap_verdict": i.get("join_cap_verdict")})


async def s23_join_capability_required(ctx: Ctx) -> None:
    """Requirement ON: a valid capability joins a declared room, and every refusal names its own reason."""
    r = ctx.new_room()
    secret = ctx.cfg.join_cap_secret
    da, session_a = new_display(), new_display()
    good = _mint_cap(secret, da, session_a, r)
    a = await ctx.join_without_media("A", r, da, vis_authority=True, join_cap=good, session_id=session_a)
    await _cap_mode(ctx, a, want_required=True)
    if (a.join_reply or {}).get("audiobridge") != "joined":
        raise Fail("a valid capability is admitted", a.join_reply)

    # The replay must present the SAME agent and session as the capability binds, or the §11.4 order refuses it at
    # cap_wrong_agent before the nonce is ever consulted. Only the Janus session differs, which is what a replay is.
    await _refused(ctx, "REPLAY", r, da, "cap_replayed", join_cap=good, session_id=session_a)
    await _refused(ctx, "MISSING", r, new_display(), "cap_missing", bare=True)
    await _refused(ctx, "MALFORMED", r, new_display(), "cap_malformed",
                   join_cap="v1.not-a-payload.not-a-signature", session_id=new_display())

    db, session_b = new_display(), new_display()
    await _refused(ctx, "BADSIG", r, db, "cap_bad_signature",
                   join_cap=_mint_cap("not-the-mixers-secret", db, session_b, r), session_id=session_b)
    await _refused(ctx, "EXPIRED", r, db, "cap_expired",
                   join_cap=_mint_cap(secret, db, session_b, r, iat=int(time.time()) - 3600), session_id=session_b)
    await _refused(ctx, "WRONGAGENT", r, db, "cap_wrong_agent",
                   join_cap=_mint_cap(secret, new_display(), session_b, r), session_id=session_b)
    await _refused(ctx, "WRONGSESSION", r, db, "cap_wrong_session",
                   join_cap=_mint_cap(secret, db, new_display(), r), session_id=session_b)
    await _refused(ctx, "WRONGROOM", r, db, "cap_wrong_room",
                   join_cap=_mint_cap(secret, db, session_b, r + 7), session_id=session_b)
    # Slice 0.8b (O-96): cap_stale_generation now means ONE thing -- an epoch BELOW the room's adopted one. The
    # room must therefore have adopted one before this leg: before 0.8b any epoch at all was refused here,
    # because the room had adopted none, which is exactly the false refusal O-96 removes.
    e_hi, e_lo = _epoch(2), _epoch(1)
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={db: []}, mute={db: []},
                                                epoch=e_hi, generation=1),
                 "S23: the room adopts a high epoch, so a lower one is provably an older authority",
                 slvoice="applied", status="ok")
    await _refused(ctx, "STALEGEN", r, db, "cap_stale_generation",
                   join_cap=_mint_cap(secret, db, session_b, r, epoch=e_lo, generation=3),
                   session_id=session_b)

    # Not gated anywhere else: an undeclared room admits a join with no capability, with the knob on.
    r2 = ctx.new_room()
    plain = await ctx.join_without_media("PLAIN", r2)
    if (plain.join_reply or {}).get("audiobridge") != "joined":
        raise Fail("an undeclared room is never gated by the capability", plain.join_reply)
    # Read the counters off the peer that joined last: they are process-wide, and A's Janus session is the oldest
    # in this run, so it is the one that can have been reaped by the time the refusals are done.
    await ctx.until_info(plain, lambda i: _cap_state(i).get("enforced_refusals", 0) >= 9,
                         "every refusal above was enforced and counted")


async def s24_old_mixer_ignores_capability(ctx: Ctx) -> None:
    """A pre-0.4 image ignores join_cap and session_id, so a minting sim's joins land exactly as before."""
    r = ctx.new_room()
    da, session_a = new_display(), new_display()
    a = await ctx.join("A", r, da, vis_authority=True,
                       join_cap=_mint_cap(ctx.cfg.join_cap_secret or "any-secret", da, session_a, r),
                       session_id=session_a)
    await ctx.ready(a)
    if "required" in _cap_state(await ctx.info(a)):
        raise Skip("this mixer understands join capabilities: S24 runs against a pre-0.4 image")
    b = await ctx.join("B", r)
    await ctx.ready(b)
    await ctx.until_info(a, _audible, "the old mixer admitted a stamped join and mixes it exactly as before")


# ---- Phase 0 slice 0.5: the remaining §9 assertions ------------------------------------------------------------
# S25-S30 and S32 need fail-closed ON (a scratch mixer, never the grid's); S31 needs a mixer STARTED below the §5
# minimum (scratch.sh up-clamp). Each skips on the wrong mixer, so one run proves which mode it ran against.
#
# Fail-first (the standing finding): 0.5 adds no mixer code, so these pass against the pre-0.5 image. Their proof is
# a run against legion-voice-mixer:rollback-pre-03 -- no vis_protocol, no peer_ctl_heartbeat, no vis_authority, no
# stale_generation, no takeover -- with --prove-fail, so the skip these would otherwise report counts as the failure
# it is; plus a mutation proof that inverts the one condition each scenario is about.


async def s25_arming_with_source_excluded(ctx: Ctx) -> None:
    """§9 6. L armed with S excluded and T armed: L hears T, never S, and S is absent from the roster the mixer
    builds for L at join. query_session has no roster field -- the joined event is the only place that view exists,
    which is why L joins AFTER the arming."""
    r = ctx.new_room()
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=True)
    s = await ctx.join("S", r)          # the excluded source
    t = await ctx.join("T", r)          # the source L may hear
    await ctx.ready(s, t)
    await _mode(ctx, s, want_fail_closed=True)

    dl = new_display()
    e1 = _epoch(1)
    hb = Heartbeater(ctx.admin, e1, r, {dl: 1, s.display: 1, t.display: 1}).start()
    ctx.background.append(hb)
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace",
                                                excl={dl: [s.display], s.display: [], t.display: []},
                                                mute={dl: [], s.display: [], t.display: []},
                                                epoch=e1, generation=1),
                 "arming L with S excluded, and S and T", slvoice="applied", status="ok")

    l = await ctx.join("L", r, dl)
    await ctx.ready(l)
    # Assertion ORDER matters here, and getting it wrong cost this scenario its proof once. The roster check is
    # what §9 6 is about, so nothing else that depends on the same exclusion may run before it: with the columns
    # asserted first, a mutation that removes the exclusion fails at THAT line instead and the roster claim is
    # never exercised at all. Row 4 is safe to assert first -- it holds whether or not S is excluded.
    await ctx.until_info(l, lambda i: i.get("vis_row") == 4, "L joins already armed (row 4)")
    roster = l.roster()
    if s.display in roster:
        raise Fail("§9 6: S is absent from L's initial roster", {"roster": roster, "S": s.display, "T": t.display})
    if t.display not in roster:
        raise Fail("§9 6: T IS in L's initial roster", {"roster": roster, "S": s.display, "T": t.display})
    info = await ctx.info(l)
    if info.get("excluded_entries") != 1:
        raise Fail("§9 6: L joined carrying its authority's one exclusion", pick(info))
    await ctx.until_info(l, _audible, "§9 6: L hears T")
    if s.display in l.dots or any(d == s.display for d, _ in l.presence):
        raise Fail("§9 6: L gets no dot and no presence for the excluded S",
                   {"dots_for_S": l.dots.get(s.display), "presence": l.presence})


async def s26_per_listener_staleness(ctx: Ctx) -> None:
    """§9 14. A heartbeat naming L at a generation above the stored one silences L ONLY: M in the same room stays
    audible, the reply lists L in stale_listeners, and a replace for L restores it. Also §9 25's last clause: the
    reply's policy_generation echoes the highest applied value."""
    r = ctx.new_room()
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=True)
    l = await ctx.join("L", r)
    m = await ctx.join("M", r)
    src = await ctx.join("SRC", r)
    await ctx.ready(l, m, src)
    await _mode(ctx, l, want_fail_closed=True)

    e1 = _epoch(1)
    # Slice 0.7d: as_of rides along as the 0.7d sim sends it, updated in the same step as the listener map, so a heartbeat
    # built before a batch applied is outdated at the mixer instead of racing it (O-95).
    hb = Heartbeater(ctx.admin, e1, r, {l.display: 1, m.display: 1, src.display: 1}, as_of=0).start()
    ctx.background.append(hb)
    armed = await ctx.admin.peer_ctl_batch(r, "replace",
                                           excl={l.display: [], m.display: [], src.display: []},
                                           mute={l.display: [], m.display: [], src.display: []},
                                           epoch=e1, generation=1)
    _check_reply(armed, "arming L, M and SRC", slvoice="applied", status="ok", policy_generation=1)
    hb.as_of = 1
    await ctx.until_info(l, _audible, "L hears SRC")
    await ctx.until_info(m, _audible, "M hears SRC")

    # §9 25: three applied generations in sequence, each echoed back as the highest applied.
    for gen in (2, 3):
        reply = await ctx.admin.peer_ctl_batch(r, "replace",
                                               excl={l.display: [], m.display: [], src.display: []},
                                               mute={l.display: [], m.display: [], src.display: []},
                                               epoch=e1, generation=gen)
        _check_reply(reply, f"§9 25: the reply echoes the highest applied generation ({gen})",
                     slvoice="applied", status="ok", policy_generation=gen)
    hb.listeners = {l.display: 3, m.display: 3, src.display: 3}
    hb.as_of = 3
    await ctx.until_info(l, _audible, "still audible at generation 3")

    # L alone is named at a generation ABOVE what the mixer stored: L goes stale, M is untouched.
    hb.listeners = {l.display: 99, m.display: 3, src.display: 3}
    await ctx.until_info(l, lambda i: _silent(i) and i.get("vis_row") == 3,
                         "§9 14: a generation above the stored one silences L (row 3)")
    await _hold(ctx, m, _audible, "§9 14: M in the same room stays audible", 2.0)
    reply = hb.last_reply or {}
    room = (reply.get("rooms") or {}).get(str(r)) or {}
    if l.display not in (room.get("stale_listeners") or []):
        raise Fail("§9 14: the heartbeat reply lists L in stale_listeners", reply)
    if m.display in (room.get("stale_listeners") or []):
        raise Fail("§9 14: and does NOT list M", reply)

    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_send = time.monotonic()
    rearm = await ctx.admin.peer_ctl_batch(r, "replace", excl={l.display: []}, mute={l.display: []},
                                           epoch=e1, generation=100)
    t_reply = time.monotonic()
    _check_reply(rearm, "re-arming L", slvoice="applied", status="ok")
    hb.listeners = {l.display: 100, m.display: 3, src.display: 3}
    hb.as_of = 100
    t_listeners = time.monotonic()
    try:
        await ctx.until_info(l, _audible, "§9 14: a replace for L restores it")
    except Fail as failure:
        await _capture_o95(ctx, r, l, m, hb, rearm, since, t_send, t_reply, t_listeners, failure)
        raise


async def _capture_o95(ctx, r, l, m, hb, rearm, since, t_send, t_reply, t_listeners, failure) -> None:
    """O-95: everything needed to classify a failed restore, taken at the moment of failure. Written to
    $O95_CAPTURE_DIR (default: the current directory) as o95-<room>.json, and summarised on stdout."""
    import os
    t_fail = time.monotonic()
    rel = lambda t: round(t - t_send, 3)
    info_l = await ctx.info(l)
    info_m = await ctx.info(m)
    logs = await mixer_logs(ctx.cfg, since)
    capture = {
        "room": r, "L": l.display, "M": m.display,
        "timing_s_from_replace_send": {"replace_reply": rel(t_reply), "heartbeat_listeners_updated": rel(t_listeners),
                                       "assertion_failed": rel(t_fail)},
        "replace_reply": rearm,
        "L_handle_info": info_l, "M_vis_row": (info_m or {}).get("vis_row"),
        "heartbeats_near_replace": [{"sent": rel(s), "replied": rel(rp), "L_generation_sent": ls.get(l.display),
                                     "room_reply": rr} for s, rp, ls, rr in hb.history if s >= t_send - 2.0],
        "mixer_log_for_room": [ln for ln in logs.splitlines() if str(r) in ln or l.display in ln],
        "failure": str(failure),
    }
    path = os.path.join(os.environ.get("O95_CAPTURE_DIR", "."), f"o95-{r}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(capture, f, indent=2, default=str)
    print(f"      S26 O-95 capture written: {path}", flush=True)


async def s27_out_of_order_generation(ctx: Ctx) -> None:
    """§9 17. After a replace at generation 10, a delayed add at generation 9 is rejected and leaves L's set
    unchanged. Also §9 1's remaining half: an exclusion replace carrying NO epoch fields still silences."""
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r, a, b = await _declared_pair(ctx)
    await _mode(ctx, a, want_fail_closed=True)
    e1 = _epoch(1)
    hb = Heartbeater(ctx.admin, e1, r, {a.display: 10, b.display: 10}).start()
    ctx.background.append(hb)
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: [], b.display: []},
                                                mute={a.display: [], b.display: []}, epoch=e1, generation=10),
                 "arming at generation 10", slvoice="applied", status="ok", policy_generation=10)
    await ctx.until_info(a, lambda i: _audible(i) and i.get("excluded_entries") == 0, "armed at 10: A hears B")
    before = _vis(await ctx.info(a)).get("stale_generation_rejects") or 0

    late = await ctx.admin.peer_ctl_batch(r, "add", excl={a.display: [b.display]}, epoch=e1, generation=9,
                                          base={a.display: 10})
    if late.get("status") != "stale_generation" and late.get("reason") != "stale_generation":
        raise Fail("§9 17: a generation below the stored one is refused as stale_generation", late)
    await _hold(ctx, a, lambda i: _audible(i) and i.get("excluded_entries") == 0,
                "§9 17: the out-of-order add left A's set unchanged", 1.5)
    after = _vis(await ctx.info(a)).get("stale_generation_rejects") or 0
    if after <= before:
        raise Fail("§9 17: stale_generation_rejects climbs", {"before": before, "after": after})
    logs = await mixer_logs(ctx.cfg, since)
    if f"policy_generation 9 is not above 10 (out of order)" not in logs:
        raise Fail("§9 17: the mixer logs the out-of-order refusal naming both generations",
                   {"searched_for": "policy_generation 9 is not above 10 (out of order)"})

    # §9 1's remaining half, in an UNDECLARED room so the authority does not apply: an exclusion replace with no
    # epoch fields at all still silences, exactly as before Phase 0.
    r2 = ctx.new_room()
    await ctx.control.create_room(r2, f"integration {ctx.name}", vis_authority=False)
    c = await ctx.join("C", r2)
    d = await ctx.join("D", r2)
    await ctx.ready(c, d)
    await ctx.until_info(c, _audible, "§9 1: un-batched listener hears the source")
    plain = await ctx.admin.peer_ctl_batch(r2, "replace", excl={c.display: [d.display]}, mute={c.display: []})
    if plain.get("slvoice") != "applied" or "status" in plain:
        raise Fail("§9 1: an unstamped exclusion replace is applied, with no Phase 0 status", plain)
    await ctx.until_info(c, lambda i: _silent(i) and i.get("excluded_entries") == 1,
                         "§9 1: an exclusion replace with no epoch fields still silences")


async def s28_pre_join_arming_survives_deferral(ctx: Ctx) -> None:
    """§9 18. An EMPTY arming replace sent before L joins is replayed at join: L is audible on joining with no
    heartbeat in between. Empty columns are what makes this hard -- a deferred record with both channels empty was
    dropped before the §2 fix, so the joiner would have waited for a heartbeat round trip."""
    r = ctx.new_room()
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=True)
    b = await ctx.join("B", r)
    await ctx.ready(b)
    await _mode(ctx, b, want_fail_closed=True)

    dl = new_display()
    e1 = _epoch(1)
    # Arm BOTH the absent L and the present B, then send no heartbeat at all for the rest of the scenario.
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={dl: [], b.display: []},
                                                mute={dl: [], b.display: []}, epoch=e1, generation=1),
                 "an empty arming replace for a listener not yet in the room", slvoice="applied", status="ok")
    l = await ctx.join("L", r, dl)
    await ctx.ready(l)
    await ctx.until_info(l, lambda i: i.get("vis_row") == 4,
                         "§9 18: the pre-join arming was replayed at join (row 4), with no heartbeat")
    await ctx.until_info(l, _audible, "§9 18: and L is audible on joining")


async def s29_declared_room_no_listeners(ctx: Ctx) -> None:
    """§9 23. A declared room with no listeners gets no heartbeat: it is grace-destroyed exactly as today, and a
    fresh join plus an arming replace is audible again."""
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    grace = ctx.cfg.grace
    r = ctx.new_room()
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=True)
    a = await ctx.join("A", r)
    b = await ctx.join("B", r)
    await ctx.ready(a, b)
    await _mode(ctx, a, want_fail_closed=True)
    await a.close()
    await b.close()
    emptied = time.monotonic()

    await until(ctx.control.room_ids, lambda ids: r not in ids,
                f"§9 23: the declared room is grace-destroyed after {grace} s with no listeners and no heartbeat",
                timeout=grace + 20, step=1.0)
    waited = time.monotonic() - emptied
    if waited < grace - 1:
        raise Fail("§9 23: destroyed no earlier than its grace", {"waited_s": round(waited, 1), "grace_s": grace})
    logs = await mixer_logs(ctx.cfg, since)
    if f"room {r} destroyed after" not in logs:
        raise Fail("§9 23: the mixer logs the grace destroy for this room",
                   {"searched_for": f"[slvoice] room {r} destroyed after <n>s empty"})

    await ctx.reopen_control()
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=True)
    a2 = await ctx.join("A2", r)
    b2 = await ctx.join("B2", r)
    await ctx.ready(a2, b2)
    e1 = _epoch(1)
    hb = Heartbeater(ctx.admin, e1, r, {a2.display: 1, b2.display: 1}).start()
    ctx.background.append(hb)
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={a2.display: [], b2.display: []},
                                                mute={a2.display: [], b2.display: []}, epoch=e1, generation=1),
                 "arming the re-created room", slvoice="applied", status="ok")
    await ctx.until_info(a2, _audible, "§9 23: a new join plus an arming replace is audible again")


async def s30_recorder_in_declared_room(ctx: Ctx) -> None:
    """§9 24. A recording tap in a declared room is silent until armed, like any other participant (open question 3
    documents that decision). SC-96's "recorder" join key is old; what is new is asserting the tap is gated."""
    r = ctx.new_room()
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=True)
    src = await ctx.join("SRC", r)
    await ctx.ready(src)
    await _mode(ctx, src, want_fail_closed=True)

    rec = await ctx.join("REC", r, recorder=True)
    await ctx.ready(rec)
    info = await ctx.info(rec)
    if info.get("recorder") is not True:
        raise Fail("§9 24: the mixer records this participant as a recorder tap", pick(info))
    await ctx.until_info(rec, lambda i: _silent(i) and i.get("vis_row") == 1,
                         "§9 24: an unarmed recorder tap is silent in a declared room")
    await _hold(ctx, rec, _silent, "§9 24: and stays silent while unarmed", 2.0)

    e1 = _epoch(1)
    hb = Heartbeater(ctx.admin, e1, r, {rec.display: 1, src.display: 1}).start()
    ctx.background.append(hb)
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={rec.display: [], src.display: []},
                                                mute={rec.display: [], src.display: []}, epoch=e1, generation=1),
                 "arming the recorder and the source", slvoice="applied", status="ok")
    await ctx.until_info(rec, _audible, "§9 24: armed, the recorder tap hears the room")


async def s31_stale_ms_clamp(ctx: Ctx) -> None:
    """§9 27. A mixer STARTED with JS_VIS_STALE_MS below the §5 constraint logs the clamp, and the effective window
    it reports equals the constraint -- not the value it was given."""
    started_with = ctx.cfg.stale_ms_started_with
    if not started_with:
        raise Skip("--stale-ms-started-with not given: this needs a mixer started below the §5 minimum "
                   "(scratch.sh up-clamp)")
    r, a, b = await _declared_pair(ctx)
    # The clamp is orthogonal to enforcement: §9 27 is about the window the mixer ACCEPTED at startup, which it
    # reports and logs whether or not fail-closed is on. Only the pre-0.3 guard applies here.
    info = await ctx.until_info(a, lambda i: isinstance(i.get("visibility"), dict), "A reports a visibility block")
    vis = _vis(info)
    if "fail_closed" not in vis:
        raise Skip("the mixer reports no visibility authority (a pre-0.3 image)")
    reported = vis.get("stale_ms")
    minimum = 2 * 1000 + 5000 + 250        # §5: 2 x heartbeat + admin timeout + tick slip
    if started_with >= minimum:
        raise Skip(f"--stale-ms-started-with {started_with} is not below the §5 minimum {minimum}")
    if reported != minimum:
        raise Fail(f"§9 27: the effective window equals the §5 constraint ({minimum} ms), not the value given",
                   {"started_with": started_with, "reported_stale_ms": reported})
    logs = await mixer_logs(ctx.cfg, "2000-01-01T00:00:00Z")
    want = f"JS_VIS_STALE_MS={started_with} is below the minimum {minimum} ms"
    if want not in logs:
        raise Fail("§9 27: the mixer logs the clamp at startup", {"searched_for": want})


async def s32_takeover_after_the_window(ctx: Ctx) -> None:
    """§9 13 second half. S18 reaches a takeover through a graceful stop; this is the path the design specifies:
    E2 simply goes stale for the window, and then E1 -- LOWER than E2 -- is adopted, disarming every record."""
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r, a, b = await _declared_pair(ctx)
    vis = await _mode(ctx, a, want_fail_closed=True)
    stale_s = (vis.get("stale_ms") or 8000) / 1000.0
    e1, e2 = _epoch(1), _epoch(2)
    hb = Heartbeater(ctx.admin, e2, r, {a.display: 1, b.display: 1}).start()
    ctx.background.append(hb)
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: [], b.display: []},
                                                mute={a.display: [], b.display: []}, epoch=e2, generation=1),
                 "arming in E2", slvoice="applied", status="ok", authority_epoch=e2)
    await ctx.until_info(a, _audible, "armed in E2: A hears B")

    # While E2 is FRESH a lower epoch is refused -- the other half of §9 13, asserted here as the control.
    fresh = await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: []}, mute={a.display: []},
                                           epoch=e1, generation=1)
    _check_reply(fresh, "§9 13a: while E2 is fresh, E1 is stale_epoch", slvoice="error", status="stale_epoch",
                 authority_epoch=e2)

    last = await hb.pause()
    await ctx.until_info(a, _silent, "E2 stops confirming: A goes silent at the window",
                         timeout=max(2.0, last + stale_s + 2.0 - time.monotonic()))
    # Past the window the SAME lower epoch is adopted as a takeover.
    take = await ctx.admin.peer_ctl_batch(r, "replace", excl={a.display: [], b.display: []},
                                          mute={a.display: [], b.display: []}, epoch=e1, generation=1)
    _check_reply(take, "§9 13b: once E2 is stale, the lower E1 takes over", slvoice="applied", status="ok",
                 authority_epoch=e1)
    logs = await mixer_logs(ctx.cfg, since)
    if "takeover" not in logs or f"room {r}: authority epoch" not in logs:
        raise Fail("§9 13b: the mixer logs the takeover and how many records it disarmed",
                   {"searched_for": f"room {r}: authority epoch <E2> -> <E1> (takeover, via peer_ctl_batch); "
                                    f"<n> listener record(s) disarmed"})
    hb1 = Heartbeater(ctx.admin, e1, r, {a.display: 1, b.display: 1}).start()
    ctx.background.append(hb1)
    await ctx.until_info(a, _audible, "§9 13b: the takeover's own arming makes A audible again")


# ---- Phase 0 slice 0.7a: connector / recorder arming (ledger O-88) ----------------------------------------------
# The sim arms a voice connector exactly as it arms an avatar: its NPC id is the peer's display, the room is the
# estate room, and unregistering it drops it from the population, so the next heartbeat OMITS it. S33 checks the
# mixer end of that ruling, for a recorder tap as a listener and for a non-recorder connector as a source. Every
# disarm is asserted inside OMIT_DISARM_S, far below the §5 window (7250 ms minimum), so a mixer that let omission
# fall through to staleness fails here rather than passing late.

OMIT_DISARM_S = 3.0


async def s33_connector_arming_and_omission(ctx: Ctx) -> None:
    """O-88 / slice 0.7a. In a declared room under fail-closed, with SRC talking: a recorder tap is silent unarmed,
    hears the room once an arming replace names its display, and is silent again after a heartbeat that omits it. A
    non-recorder connector-style peer as a SOURCE is inaudible to an armed listener while unarmed, audible armed, and
    inaudible again after omission. Both disarms are the omission itself, not the staleness window."""
    r = ctx.new_room()
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=True)
    src = await ctx.join("SRC", r)                       # an avatar, talking
    rec = await ctx.join("REC", r, recorder=True)        # a recording connector: display = its NPC id at the sim
    lis = await ctx.join("L", r)                         # an avatar listening to the connector
    con = await ctx.join("CON", r)                       # an injecting connector, talking
    await ctx.ready(src, rec, lis, con)
    await _mode(ctx, src, want_fail_closed=True)

    e1 = _epoch(1)
    hb = Heartbeater(ctx.admin, e1, r, {src.display: 1}).start()
    ctx.background.append(hb)
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={src.display: []}, mute={src.display: []},
                                                epoch=e1, generation=1),
                 "arming the talking avatar SRC", slvoice="applied", status="ok")

    # -- the recorder tap as a listener --
    info = await ctx.info(rec)
    if info.get("recorder") is not True:
        raise Fail("S33: REC is recorded as a recorder tap", pick(info))
    await ctx.until_info(rec, lambda i: _silent(i) and i.get("vis_row") == 1, "S33: the unarmed recorder tap is silent (row 1)")
    await _hold(ctx, rec, _silent, "S33: and stays silent while unarmed", 1.5)
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={rec.display: []}, mute={rec.display: []},
                                                epoch=e1, generation=2),
                 "an arming replace naming the recorder's display", slvoice="applied", status="ok")
    hb.listeners = {src.display: 1, rec.display: 2}
    await ctx.until_info(rec, lambda i: _audible(i) and i.get("vis_row") == 4, "S33: armed, the recorder tap hears the room")
    hb.listeners = {src.display: 1}                      # the connector was unregistered: the sim omits it
    await ctx.until_info(rec, lambda i: _silent(i) and i.get("vis_row") == 1,
                         "S33: a heartbeat omitting the recorder disarms it at once (row 1, not the staleness window)",
                         timeout=OMIT_DISARM_S)

    # -- a non-recorder connector as a source --
    # L excludes SRC and REC, so the only thing L can hear is CON.
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={lis.display: [src.display, rec.display]},
                                                mute={lis.display: []}, epoch=e1, generation=3),
                 "arming L with SRC and REC excluded", slvoice="applied", status="ok")
    hb.listeners = {src.display: 1, lis.display: 3}
    await ctx.until_info(lis, lambda i: i.get("vis_row") == 4 and i.get("excluded_entries") == 2, "S33: L armed (row 4)")
    await ctx.until_info(lis, _silent, "S33: the unarmed connector source is inaudible to armed L")
    await _hold(ctx, lis, _silent, "S33: and stays inaudible while unarmed", 1.5)
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={con.display: []}, mute={con.display: []},
                                                epoch=e1, generation=4),
                 "an arming replace naming the connector's display", slvoice="applied", status="ok")
    hb.listeners = {src.display: 1, lis.display: 3, con.display: 4}
    await ctx.until_info(lis, _audible, "S33: armed, the connector source is audible to L")
    hb.listeners = {src.display: 1, lis.display: 3}      # the connector was unregistered: the sim omits it
    await ctx.until_info(con, lambda i: i.get("vis_row") == 1,
                         "S33: a heartbeat omitting the connector disarms it at once (row 1, not the staleness window)",
                         timeout=OMIT_DISARM_S)
    await ctx.until_info(lis, _silent, "S33: omitted, the connector source is inaudible to L again", timeout=OMIT_DISARM_S)
    info = await ctx.info(lis)
    if info.get("vis_row") != 4:
        raise Fail("S33: L itself stays armed through the connector's omission", pick(info))


# ---- Phase 0 slice 0.7b: the connector join capability (ledger O-88, design §11.10) ----------------------------
# A connector peer is not exempted from JS_JOIN_CAP_REQUIRED: it fetches a sim-minted capability before every join.
# S34 drives the REAL connector join code (common/peer.py + common/joincap.py, through TestPeer) against a stub of the
# sim's endpoint that mints with the harness's own minter (_mint_cap, the sim's JoinCapability.Mint in Python).


class CapStub:
    """The sim's POST /voice/connector/<name>/join-cap, on loopback. mode: "ok" mints a fresh capability per request,
    "replay" answers with the last one it granted, "404" answers 404 with an empty body (the sim's pre-auth answer)."""

    BEARER = "s34-connector-bearer-secret-0123456789"

    def __init__(self, key: str, display: str, room: int, session: str, name: str = "S34"):
        self._key, self.display, self.room, self.session = key, display, room, session
        self.name = name
        self.mode = "ok"
        self.hits = {"ok": 0, "replay": 0, "404": 0, "unauthorised": 0}
        #: slice 0.8f: every capability this stub minted, in order (S37 leg c: each is used at most once)
        self.issued: list = []
        self._last = None
        self._runner = None
        self.url = ""

    async def start(self) -> "CapStub":
        from aiohttp import web

        async def handle(request):
            if request.headers.get("Authorization") != "Bearer " + self.BEARER:
                self.hits["unauthorised"] += 1
                return web.Response(status=404)
            self.hits[self.mode] += 1
            if self.mode == "404":
                return web.Response(status=404)
            if self.mode == "replay" and self._last is not None:
                return web.json_response(self._last)
            now = int(time.time())
            self._last = {"display": self.display, "room": self.room, "session_id": self.session,
                          "join_cap": _mint_cap(self._key, self.display, self.session, self.room, iat=now),
                          "expires": now + 60}
            self.issued.append(self._last["join_cap"])
            return web.json_response(self._last)

        app = web.Application()
        app.router.add_post(f"/voice/connector/{self.name}/join-cap", handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/voice/connector/{self.name}/join-cap"
        return self

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    def config(self, backoff=(0.2, 0.5)) -> dict:
        return {"cap_url": self.url, "cap_secret": self.BEARER, "cap_backoff": backoff}


async def _connector(ctx: Ctx, name: str, room: int, display: str, connector_cap) -> TestPeer:
    """A connector peer started without Ctx.join's refusal check, so a scenario can read what the join got."""
    peer = TestPeer(ctx.cfg, name, room, display, connector_cap=connector_cap)
    ctx.peers.append(peer)
    peer._task = asyncio.create_task(peer.run(), name=f"peer-{name}")
    return peer


async def _join_answer(peer: TestPeer, what: str, timeout: float = 20.0) -> dict:
    done, _ = await asyncio.wait({peer._task, peer._join_result}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    if peer._join_result.done():
        return peer._join_result.result()
    if peer._task in done:
        raise Fail(f"{what}: the peer died first", repr(peer._task.exception()))
    raise Fail(f"{what}: no join answer within {timeout:.0f} s", None)


async def s34_connector_join_capability(ctx: Ctx) -> None:
    """O-88 / slice 0.7b, JS_JOIN_CAP_REQUIRED=1, declared room. A connector peer with no CONNECTOR_CAP_* is refused
    cap_missing; configured against a healthy endpoint it joins; a capability replayed on a second join is refused
    cap_replayed; and with the endpoint answering 404 the peer keeps retrying and sends no join at all."""
    if not ctx.cfg.join_cap_secret:
        raise Skip("--join-cap-secret not given: the harness cannot mint what this mixer would accept")
    r = ctx.new_room()
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=True)
    da, sa = new_display(), new_display()
    anchor = await ctx.join("A", r, da, vis_authority=True, join_cap=_mint_cap(ctx.cfg.join_cap_secret, da, sa, r),
                            session_id=sa)
    await ctx.ready(anchor)
    await _cap_mode(ctx, anchor, want_required=True)

    npc, session = new_display(), new_display()     # what the sim's record would hold: the NPC id, its ViewerSessionId
    stub = await CapStub(ctx.cfg.join_cap_secret, npc, r, session).start()
    ctx.background.append(stub)

    bare = await _connector(ctx, "BARE", r, npc, None)
    answer = await _join_answer(bare, "S34: an unconfigured connector's join")
    if answer.get("audiobridge") == "joined" or answer.get("error_code") != 496 or answer.get("reason") != "cap_missing":
        raise Fail("S34: a connector with no CONNECTOR_CAP_* is refused with error_code 496 cap_missing", answer)
    if sum(stub.hits.values()) != 0:
        raise Fail("S34: an unconfigured connector never calls the capability endpoint", stub.hits)

    good = await _connector(ctx, "CON", r, npc, stub.config())
    answer = await _join_answer(good, "S34: a configured connector's join")
    if answer.get("audiobridge") != "joined":
        raise Fail("S34: configured, with the endpoint healthy, the connector joins", answer)
    if stub.hits["ok"] != 1:
        raise Fail("S34: exactly one capability was fetched for that one join", stub.hits)
    await ctx.until_info(good, lambda i: i.get("join_cap_present") is True and i.get("join_cap_verdict") == "ok",
                         "S34: the connector's session records a present capability verified ok")

    stub.mode = "replay"
    again = await _connector(ctx, "REPLAY", r, npc, stub.config())
    answer = await _join_answer(again, "S34: a second join handed the same capability")
    if answer.get("audiobridge") == "joined" or answer.get("error_code") != 496 or answer.get("reason") != "cap_replayed":
        raise Fail("S34: a capability replayed on a second join is refused with error_code 496 cap_replayed", answer)

    stub.mode = "404"
    retrying = await _connector(ctx, "RETRY", r, npc, stub.config(backoff=(0.2, 0.4)))
    await asyncio.sleep(3.0)
    if retrying._join_result.done() or retrying.janus is not None:
        raise Fail("S34: with the capability endpoint answering 404 the peer sends no join at all, never a bare one",
                   {"join_answer": retrying._join_result.result() if retrying._join_result.done() else None,
                    "janus_session": retrying.janus.session_id if retrying.janus else None})
    if stub.hits["404"] < 4:
        raise Fail("S34: with the capability endpoint answering 404 the peer keeps retrying", stub.hits)
    if retrying._task.done():
        raise Fail("S34: and it is still running, not given up", repr(retrying._task.exception()))


# ---- Phase 0 slice 0.7d: heartbeat ordering against batches (ledger O-95) --------------------------------------------
# A heartbeat and a batch are separate flights, so a heartbeat the sim built before a batch succeeded can arrive after it.
# S35 CONSTRUCTS both orderings instead of racing them: the background heartbeater is paused, the batch is applied, and a
# heartbeat built "before" it (as_of one below) is sent by hand.


async def s35_heartbeat_ordering(ctx: Ctx) -> None:
    """O-95 / slice 0.7d. Fail-closed on, declared room, SRC talking throughout.
    Leg A: L armed and hearing; a replace for L at N+1; then a heartbeat built at N naming L at N. L is not stale, stays
    at row 4 and keeps hearing for 2 s. Leg B: L2 armed by a replace; then a heartbeat built before that replace, which
    omits L2. L2 stays armed and hears."""
    r = ctx.new_room()
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=True)
    src = await ctx.join("SRC", r)
    l = await ctx.join("L", r)
    await ctx.ready(src, l)
    await _mode(ctx, src, want_fail_closed=True)

    e1 = _epoch(1)
    hb = Heartbeater(ctx.admin, e1, r, {src.display: 1, l.display: 1}, as_of=1).start()
    ctx.background.append(hb)
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={src.display: [], l.display: []},
                                                mute={src.display: [], l.display: []}, epoch=e1, generation=1),
                 "arming SRC and L", slvoice="applied", status="ok")
    await ctx.until_info(l, lambda i: _audible(i) and i.get("vis_row") == 4, "S35: L armed and hearing SRC")

    # ---- leg A ----
    await hb.pause()
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={l.display: []}, mute={l.display: []},
                                                epoch=e1, generation=2),
                 "leg A: the replace for L at generation 2", slvoice="applied", status="ok")
    late = await ctx.admin.heartbeat(e1, {r: {"policy_generation": 1, "as_of": 1,
                                              "listeners": {src.display: 1, l.display: 1}}})
    room = (late.get("rooms") or {}).get(str(r)) or {}
    if l.display in (room.get("stale_listeners") or []):
        raise Fail("S35 leg A: a heartbeat built before L's replace does not list L in stale_listeners", late)
    hb.listeners = {src.display: 1, l.display: 2}
    hb.as_of = 2
    hb.resume()
    await _hold(ctx, l, lambda i: _audible(i) and i.get("vis_row") == 4,
                "S35 leg A: L stays at row 4 and its audio does not drop for 2 s after the late heartbeat", 2.0)

    # ---- leg B ----
    l2 = await ctx.join("L2", r)
    await ctx.ready(l2)
    await hb.pause()
    _check_reply(await ctx.admin.peer_ctl_batch(r, "replace", excl={l2.display: []}, mute={l2.display: []},
                                                epoch=e1, generation=3),
                 "leg B: the arming replace for L2 at generation 3", slvoice="applied", status="ok")
    await ctx.admin.heartbeat(e1, {r: {"policy_generation": 2, "as_of": 2,
                                       "listeners": {src.display: 1, l.display: 2}}})
    hb.listeners = {src.display: 1, l.display: 2, l2.display: 3}
    hb.as_of = 3
    hb.resume()
    await ctx.until_info(l2, lambda i: i.get("vis_row") == 4 and _audible(i),
                         "S35 leg B: a heartbeat built before L2's arming leaves L2 armed, and L2 hears SRC")
    await _hold(ctx, l2, lambda i: _audible(i) and i.get("vis_row") == 4,
                "S35 leg B: and L2 keeps hearing for 2 s", 2.0)


# ---- Phase 0 slice 0.8b: the join capability's staleness rule (O-96) ------------------------------------------
# The sim publishes a room's (epoch, generation) when the generation is ALLOCATED (VisAuthority.NextGeneration ->
# JoinCapabilityAuthority.Publish, read at JanusRoom.JoinRoom); the mixer holds what it has APPLIED. A capability is
# therefore normally AHEAD of the mixer -- always for a fresh room, a room whose batch was dropped or is in flight,
# and a room re-created after a grace destroy. The 0.8 soak lost two of five joins to exactly that. After 0.8b the
# generation refuses nothing and only an epoch BELOW the room's adopted one does.


async def s36_join_cap_generation_staleness(ctx: Ctx) -> None:
    """O-96. A capability ahead of the mixer joins; only an older authority's epoch is refused."""
    secret = ctx.cfg.join_cap_secret
    # The mode probe joins an UNDECLARED room, which the capability never gates: on a mixer that still refuses a
    # capability ahead of it, leg a's own join is refused and would leave nothing to read the mode from.
    probe = await ctx.join_without_media("PROBE", ctx.new_room())
    probe_state = await _cap_mode(ctx, probe, want_required=True)
    # The capability counters are PROCESS-WIDE, so on a full board other scenarios have already refused things.
    # Leg d's claim is about what THIS scenario added, so it is measured as a delta from here (slice 0.8e).
    base_stale = (probe_state.get("refused") or {}).get("cap_stale_generation", 0)

    # (a) A FRESH room the mixer has adopted no epoch for, and a capability minted at generation 5 under an
    # authority the mixer has never heard from. This is the first join into any room the sim has already armed
    # elsewhere -- the commonest shape there is, and the one the soak lost.
    r = ctx.new_room()
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=True)
    da, sa = new_display(), new_display()
    a = await ctx.join_without_media("A", r, da, vis_authority=True, expect_join=False,
                                     join_cap=_mint_cap(secret, da, sa, r, epoch=_epoch(5), generation=5),
                                     session_id=sa)
    if (a.join_reply or {}).get("audiobridge") != "joined":
        raise Fail("S36 leg a: a capability at generation 5 joins a fresh room the mixer holds no epoch for",
                   a.join_reply)
    info = await ctx.info(a)
    if info.get("join_cap_verdict") != "ok":
        raise Fail("S36 leg a: the O-91 verdict for that join is ok", pick(info))
    if _cap_state(info).get("cap_epoch_ahead", 0) < 1:
        raise Fail("S36 leg a: a capability ahead of the mixer is counted cap_epoch_ahead", _cap_state(info))

    # (b) A room armed, emptied, grace-destroyed and re-created: the re-created room has adopted nothing again,
    # while the sim's generation for it has only gone up. A join with a generation ahead must still land. The
    # occupant that holds the room open carries a capability with NO epoch, which every mixer since 0.4 admits,
    # so the grace clock here is honest whatever the mixer does with leg a.
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    grace = ctx.cfg.grace
    rb = ctx.new_room()
    await ctx.control.create_room(rb, f"integration {ctx.name}", vis_authority=True)
    do, so = new_display(), new_display()
    occupant = await ctx.join_without_media("O", rb, do, vis_authority=True,
                                            join_cap=_mint_cap(secret, do, so, rb), session_id=so)
    _check_reply(await ctx.admin.peer_ctl_batch(rb, "replace", excl={do: []}, mute={do: []},
                                                epoch=_epoch(5), generation=6),
                 "S36 leg b: the room adopts an epoch before it is emptied", slvoice="applied", status="ok")
    await occupant.close()
    emptied = time.monotonic()
    await until(ctx.control.room_ids, lambda ids: rb not in ids,
                f"S36 leg b: the declared room is grace-destroyed after {grace} s empty",
                timeout=grace + 20, step=1.0)
    if time.monotonic() - emptied < grace - 1:
        raise Fail("S36 leg b: destroyed no earlier than its grace", {"grace_s": grace})
    logs = await mixer_logs(ctx.cfg, since)
    if f"room {rb} destroyed after" not in logs:
        raise Fail("S36 leg b: the mixer logs the grace destroy for this room",
                   {"searched_for": f"[slvoice] room {rb} destroyed after <n>s empty"})
    await ctx.reopen_control()
    await ctx.control.create_room(rb, f"integration {ctx.name}", vis_authority=True)
    db, sb = new_display(), new_display()
    b = await ctx.join_without_media("B", rb, db, vis_authority=True, expect_join=False,
                                     join_cap=_mint_cap(secret, db, sb, rb, epoch=_epoch(5), generation=7),
                                     session_id=sb)
    if (b.join_reply or {}).get("audiobridge") != "joined":
        raise Fail("S36 leg b: a capability with a generation ahead joins the re-created room", b.join_reply)

    # (c) The one refusal left. A room that HAS adopted E2, and a capability minted under E1 < E2: an authority
    # that is provably gone. Nothing else in this scenario may be refused, and this must be.
    r2 = ctx.new_room()
    await ctx.control.create_room(r2, f"integration {ctx.name}", vis_authority=True)
    e2, e1 = _epoch(9), _epoch(8)
    dc, sc = new_display(), new_display()
    _check_reply(await ctx.admin.peer_ctl_batch(r2, "replace", excl={dc: []}, mute={dc: []},
                                                epoch=e2, generation=1),
                 "S36 leg c: the room adopts E2", slvoice="applied", status="ok")
    await _refused(ctx, "OLDEPOCH", r2, dc, "cap_stale_generation",
                   join_cap=_mint_cap(secret, dc, sc, r2, epoch=e1, generation=99), session_id=sc)

    # A capability under E2 itself, with a generation far ahead of the single batch applied, still joins: the
    # refusal above was about the epoch, and nothing about the generation.
    dd, sd = new_display(), new_display()
    d = await ctx.join_without_media("D", r2, dd, vis_authority=True, expect_join=False,
                                     join_cap=_mint_cap(secret, dd, sd, r2, epoch=e2, generation=99),
                                     session_id=sd)
    if (d.join_reply or {}).get("audiobridge") != "joined":
        raise Fail("S36 leg c: under the adopted epoch a generation far ahead still joins", d.join_reply)
    state = _cap_state(await ctx.info(d))
    if state.get("cap_generation_ahead", 0) < 1:
        raise Fail("S36 leg c: that join is counted cap_generation_ahead", state)

    # (d) Slice 0.8b ruling change: a capability carrying NO epoch at all, against a room that HAS adopted one.
    # No epoch means the sim held no authority state for that room when it minted; it is not evidence of an older
    # authority, and it is ordinary - the sim forgets a room while the mixer still holds its epoch through the
    # empty-room grace, and the same avatar rejoins. It must be admitted, and counted as its own case.
    before = _cap_state(await ctx.info(d)).get("cap_no_epoch", 0)
    de, se = new_display(), new_display()
    e = await ctx.join_without_media("NOEPOCH", r2, de, vis_authority=True, expect_join=False,
                                     join_cap=_mint_cap(secret, de, se, r2), session_id=se)
    if (e.join_reply or {}).get("audiobridge") != "joined":
        raise Fail("S36 leg d: a capability with NO epoch joins a room that has adopted one (0.8b)", e.join_reply)
    info = await ctx.info(e)
    if info.get("join_cap_verdict") != "ok":
        raise Fail("S36 leg d: the O-91 verdict for the no-epoch join is ok", pick(info))
    state = _cap_state(info)
    if state.get("cap_no_epoch", 0) <= before:
        raise Fail("S36 leg d: that join is counted cap_no_epoch", state)
    added_stale = (state.get("refused") or {}).get("cap_stale_generation", 0) - base_stale
    if added_stale != 1:
        raise Fail("S36 leg d: leg c's refusal is the only one this scenario added",
                   {"added_cap_stale_generation": added_stale, "join_cap": state})


# ---- Phase 0 slice 0.8f: a connector peer survives a failed join (ledger O-99) ---------------------------------------
# 0.8d live: both injectors fetched a capability, joined, got "485 No such room", and sat on a room-less Janus session
# for nine hours. S37 drives the REAL injector (connectors/injector/injector.py over common/peer.py and joincap.py)
# against a loopback stub of the sim's capability endpoint, on a mixer that requires a capability.


def _s37_tone_wav(path: str) -> None:
    """One second of 440 Hz, 48 kHz mono s16: the injector's SOURCE, looped."""
    import math
    import struct
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"".join(struct.pack("<h", int(0.3 * 32767 * math.sin(2 * math.pi * 440 * i / 48000)))
                               for i in range(48000)))


class _S37Watch:
    """What S37 observes of the real injector: every join it put on the wire (through a recording JanusHttp swapped
    into common.peer for the scenario's duration) and every plugin answer it got."""

    def __init__(self, display: str):
        self.display = display
        self.joins: list = []        # the join_cap each join carried (None = a bare join)
        self.sessions: list = []     # the Janus session id each join went out on
        self.answers: list = []      # plugin event data, in arrival order

    def refused_485(self) -> int:
        return sum(1 for a in self.answers if a.get("error_code") == 485)

    def joined(self) -> int:
        return sum(1 for a in self.answers if a.get("audiobridge") == "joined")


async def s37_connector_rejoins_after_failed_join(ctx: Ctx) -> None:
    """O-99 / slice 0.8f, JS_JOIN_CAP_REQUIRED=1. (a) The injector's room does not exist: every join gets 485, and the
    peer keeps retrying, fetching a fresh capability each time; once the harness creates the room the peer joins and
    its audio reaches the mixer. (b) The joined peer's room is destroyed under it: the peer notices, and rejoins once
    the room exists again. (c) Every capability the stub issued was put on the wire at most once, and no join was
    bare."""
    import tempfile

    import common.peer as peer_mod
    from common.janus import JanusHttp
    from injector.injector import Injector

    if not ctx.cfg.join_cap_secret:
        raise Skip("--join-cap-secret not given: the harness cannot mint what this mixer would accept")
    anchor = await ctx.join("A", ctx.new_room(), vis_authority=True)
    await ctx.ready(anchor)
    await _cap_mode(ctx, anchor, want_required=True)

    r = ctx.new_room()                                   # allocated, and deliberately NOT created yet
    npc, session = new_display(), new_display()
    stub = await CapStub(ctx.cfg.join_cap_secret, npc, r, session, name="S37").start()
    ctx.background.append(stub)
    watch = _S37Watch(npc)

    class RecordingJanus(JanusHttp):
        async def message(self, body, jsep=None):
            if body.get("request") == "join" and body.get("display") == watch.display:
                watch.joins.append(body.get("join_cap"))
                watch.sessions.append(self.session_id)
            return await super().message(body, jsep=jsep)

    class WatchedInjector(Injector):
        name = "INJ"

        @property
        def ids(self) -> tuple:
            return (self.janus.session_id, self.janus.handle_id) if self.janus else (None, None)

        def on_plugin_event(self, data: dict) -> None:
            super().on_plugin_event(data)
            if data.get("audiobridge") == "joined" or "error_code" in data:
                watch.answers.append(data)

    tmp = tempfile.mkdtemp(prefix="s37-")
    wav = f"{tmp}/tone.wav"
    _s37_tone_wav(wav)
    cfg = {"janus_url": ctx.cfg.janus_url, "api_secret": ctx.cfg.api_secret, "room": r, "display": npc,
           "source": wav, "loop": True, "record": False, "out_dir": tmp, "segment_seconds": 600,
           **stub.config(backoff=(0.2, 0.5)),
           # 0.8f tuning so the scenario runs in seconds; production keeps 2 s doubling to 60 s and a 5 s room probe
           "rejoin_backoff": (0.3, 1.0), "room_probe_s": 0.5}
    real_janus = peer_mod.JanusHttp
    peer_mod.JanusHttp = RecordingJanus
    inj = WatchedInjector(cfg)
    task = asyncio.create_task(inj.run(), name="peer-S37-injector")

    class Running:
        async def stop(self):
            inj.stop()
            try:
                await asyncio.wait_for(task, 15.0)
            except BaseException:
                pass
            peer_mod.JanusHttp = real_janus

    ctx.background.append(Running())

    async def state():
        return {"fetched": stub.hits["ok"], "joins_sent": len(watch.joins), "refused_485": watch.refused_485(),
                "joined": watch.joined(), "janus_sessions": len(set(watch.sessions)), "peer_task_done": task.done()}

    # (a) no room yet: 485, and again, and again -- each attempt its own fetch and its own Janus session.
    await until(state, lambda s: s["refused_485"] >= 3 and s["fetched"] >= 3 and s["janus_sessions"] >= 3,
                "S37 leg a: after '485 No such room' the peer tears down, re-fetches a capability and joins again "
                "(3 refusals, 3 fetches, 3 Janus sessions)", timeout=20.0)
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=True)
    await until(state, lambda s: s["joined"] >= 1, "S37 leg a: once the room exists the retrying peer joins it",
                timeout=20.0)
    await ctx.until_info(inj, lambda i: i.get("room") == r and (i.get("rtp_in_count") or 0) > 0,
                         "S37 leg a: and its audio reaches the mixer (room R, rtp_in_count > 0)", timeout=15.0)

    # (b) the room is destroyed under the joined peer. The mixer evicts it silently (no event), so the peer has to
    # find out for itself, tear down, and come back once the room exists again.
    lost_ids = inj.ids
    fetched_before, joins_before, joined_before = stub.hits["ok"], len(watch.joins), watch.joined()
    await ctx.control.destroy_room(r)
    await until(state, lambda s: s["fetched"] > fetched_before and s["joins_sent"] > joins_before,
                "S37 leg b: the peer notices its room was destroyed, re-fetches a capability and joins again",
                timeout=20.0)
    lost = await ctx.admin.handle_info(type("Lost", (), {"ids": lost_ids})())
    if lost is not None:
        raise Fail("S37 leg b: the session that lost its room was torn down (its handle is gone)", pick(lost))
    await ctx.control.create_room(r, f"integration {ctx.name}", vis_authority=True)
    await until(state, lambda s: s["joined"] > joined_before,
                "S37 leg b: once the room exists again the peer rejoins it", timeout=20.0)
    await ctx.until_info(inj, lambda i: i.get("room") == r and (i.get("rtp_in_count") or 0) > 0,
                         "S37 leg b: and its audio reaches the mixer again", timeout=15.0)

    # (c) one capability per join: never twice, never one the stub did not issue, never none.
    bare = watch.joins.count(None)
    reused = max((watch.joins.count(c) for c in watch.joins), default=0)
    foreign = sum(1 for c in watch.joins if c is not None and c not in stub.issued)
    if bare or reused > 1 or foreign:
        raise Fail("S37 leg c: every capability the stub issued was used at most once, and no join was bare",
                   {"joins": len(watch.joins), "issued": len(stub.issued), "bare": bare, "max_uses": reused,
                    "not_issued_by_the_stub": foreign})
    if task.done():
        raise Fail("S37: the peer is still running at the end", repr(task.exception()))


# ---- S39: the Janus constraint that retired the sim's same-handle retry (slice 0.8i, O-98) ----------------------
# 0.8g PROOF D, live 2026-09-19 01:33:42Z: the sim's viewer join (carrying the viewer's JSEP offer) drew 485 from the
# plugin, the sim re-created the room and re-sent the same join on the SAME handle, and Janus core refused it with 490
# "Error setting ICE locally" - core builds the handle's ICE agent from the offer BEFORE the plugin looks at the room,
# so the agent survives the 485 and a second JSEP join on that handle collides with it. The sim now creates before it
# joins and never retries on the handle. This scenario pins the rule against a REAL Janus, so nobody re-adds the retry.
async def s39_jsep_join_cannot_be_resent_on_the_same_handle(ctx: Ctx) -> None:
    from aiortc import RTCPeerConnection

    from tests.integration.harness import ERR_NO_SUCH_ROOM, Control

    room = ctx.new_room()                                 # allocated, deliberately NOT created yet
    display = new_display()
    pc = RTCPeerConnection()
    same = fresh = None
    try:
        pc.addTransceiver("audio", direction="sendrecv")
        await pc.setLocalDescription(await pc.createOffer())   # a real offer, so core really builds an ICE agent
        jsep = {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp}
        join = {"request": "join", "room": room, "display": display}

        same = await Control(ctx.cfg, ctx.http).open()
        first = await same.request(dict(join), jsep=jsep)
        if first.get("error_code") != ERR_NO_SUCH_ROOM:
            raise Fail("leg a: a JSEP join into a room that does not exist answers 485", first)

        await ctx.control.create_room(room, f"integration {ctx.name}")

        try:
            again = await same.request(dict(join), jsep=jsep)
        except RuntimeError as e:                         # JanusHttp raises on a top-level {"janus":"error"}
            if "490" not in str(e):
                raise Fail("leg b: the same JSEP join re-sent on the SAME handle is refused by Janus core with 490",
                           str(e))
        else:
            raise Fail("leg b: the same JSEP join re-sent on the SAME handle must NOT reach the plugin - Janus core "
                       "refuses it (490) - but it was answered", again)

        fresh = await Control(ctx.cfg, ctx.http).open()
        joined = await fresh.request(dict(join), jsep=jsep)
        if joined.get("audiobridge") != "joined":
            raise Fail("leg c: the same join on a FRESH handle joins", joined)
    finally:
        for c in (same, fresh):
            if c is not None:
                try:
                    await c.close()
                except Exception:
                    pass
        await pc.close()
        try:
            await ctx.control.destroy_room(room)
        except Exception:
            pass


SCENARIOS = [
    Scenario("S1", "join/leave/rejoin", "O-42c presence, duplicate rows", s1_join_leave_rejoin),
    Scenario("S2", "crash without leave", "O-56", s2_crash_without_leave),
    Scenario("S3", "moderation mute persistence", "O-49, O-68", s3_mute_persistence),
    Scenario("S4", "mixer restart mid-session", "restart self-heal", s4_mixer_restart),
    Scenario("S5", "room switch = TP, grace destroy", "O-54", s5_room_switch_grace),
    Scenario("S6", "peer_ctl table full does not eat moderation", "O-49", s6_peer_ctl_full),
    Scenario("S7", "geometry persistence", "O-64", s7_geometry_persistence),
    Scenario("S8", "hangup then rejoin with the same display", "O-56, O-13", s8_hangup_rejoin_same_display),
    Scenario("S10", "join whose PeerConnection never comes up is reaped", "O-75", s10_no_media_reap),
    Scenario("S11", "a moderation-muted source lights no dot for that listener", "SC-87", s11_muted_source_dot_dark),
    Scenario("S12", "spatial path: in-range source audible and panned, beyond the cutoff culled",
             "O-80, O-83, spatial coverage", s12_spatial_cull_and_pan),
    Scenario("S13", "relay path: a peer offering only relay candidates carries media both ways",
             "A.3 TURN, relay path end to end", s13_relay_only_peer),
    Scenario("S14", "the diagnostics path verdict agrees with where the peer's packets came from",
             "A.6 source address, SC-126", s14_path_verdict_matches_source),
    Scenario("S15", "shadow mode: the full protocol in a declared room changes nothing audible; would_silence counts",
             "0.3 fail-closed off, §9 1, 2, 25", s15_shadow_mode),
    Scenario("S16", "fail-closed: rows 1, 3, 4 and the pair rule heard; heartbeats, the window, recovery, a new epoch",
             "0.3 fail-closed on, §9 4, 5, 7-12", s16_fail_closed_decision_table),
    Scenario("S17", "fail-closed: a room without vis_authority is not enforced, and says so once",
             "0.3 fail-closed on, §9 3", s17_fail_closed_undeclared_room),
    Scenario("S18", "fail-closed: stale_epoch, delta base, omission, graceful stop, takeover",
             "0.3 fail-closed on, §9 13, 15, 16, 22", s18_fail_closed_epochs_base_omission_stop),
    Scenario("S19", "fail-closed: reconnect, fan-out, rejoin columns, another room",
             "0.3 fail-closed on, §9 19, 20", s19_fail_closed_reconnect_fanout),
    Scenario("S20", "fail-closed: a mixer restart changes mixer_instance; re-arming restores audio",
             "0.3 fail-closed on, §9 21", s20_fail_closed_mixer_restart),
    Scenario("S21", "a pre-0.3 image applies stamped batches as unstamped and never advertises vis_protocol",
             "0.3 new sim / old mixer, §9 26", s21_old_image_ignores_stamp),
    Scenario("S22", "join capability shadow: verified and counted, and no join refused",
             "0.4 requirement off, §11.4; O-91 per-session marker", s22_join_capability_shadow),
    Scenario("S23", "join capability required: a valid one joins; every refusal names its reason",
             "0.4 requirement on, §11.4, O-46", s23_join_capability_required),
    Scenario("S24", "a pre-0.4 image ignores join_cap and session_id",
             "0.4 new sim / old mixer, §11.8", s24_old_mixer_ignores_capability),
    Scenario("S25", "arming with a source excluded: L hears T, never S, and S is absent from L's roster",
             "0.5 fail-closed on, §9 6", s25_arming_with_source_excluded),
    Scenario("S26", "per-listener staleness: a generation above the stored one silences that listener only",
             "0.5 fail-closed on, §9 14, 25", s26_per_listener_staleness),
    Scenario("S27", "out of order: an add below the stored generation is refused and changes nothing",
             "0.5 fail-closed on, §9 17, 1", s27_out_of_order_generation),
    Scenario("S28", "pre-join arming survives deferral: an empty replace before the join is replayed at it",
             "0.5 fail-closed on, §9 18", s28_pre_join_arming_survives_deferral),
    Scenario("S29", "a declared room with no listeners is grace-destroyed, and re-arms after a fresh join",
             "0.5 fail-closed on, §9 23", s29_declared_room_no_listeners),
    Scenario("S30", "a recorder tap in a declared room is silent until armed",
             "0.5 fail-closed on, §9 24, O-88", s30_recorder_in_declared_room),
    Scenario("S31", "a window below the §5 constraint is clamped, logged, and reported as the constraint",
             "0.5 §9 27", s31_stale_ms_clamp),
    Scenario("S32", "a lower epoch takes over once the fresh one has been stale for the window",
             "0.5 fail-closed on, §9 13", s32_takeover_after_the_window),
    Scenario("S33", "connector arming: a recorder tap and a connector source are silent unarmed, heard armed, silent on omission",
             "0.7a fail-closed on, O-88", s33_connector_arming_and_omission),
    Scenario("S34", "connector join capability: bare refused, configured joins, replay refused, 404 retries without joining",
             "0.7b join capability required, O-88, §11.10", s34_connector_join_capability),
    Scenario("S35", "heartbeat ordering: a heartbeat built before a replace neither stales nor disarms what it applied",
             "0.7d fail-closed on, O-95", s35_heartbeat_ordering),
    Scenario("S36", "join capability staleness: a capability ahead of the mixer joins; only an older epoch is refused",
             "0.8b join capability required, O-96, §11.5", s36_join_cap_generation_staleness),
    Scenario("S37", "connector rejoin: 485 retried with a fresh capability; a destroyed room noticed and rejoined",
             "0.8f join capability required, O-99", s37_connector_rejoins_after_failed_join),
    Scenario("S39", "a JSEP join cannot be re-sent on the same handle: 485, then core refuses it 490; a fresh handle joins",
             "0.8i, O-98 (why the sim creates before it joins)", s39_jsep_join_cannot_be_resent_on_the_same_handle),
]
