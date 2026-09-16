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

from tests.integration.harness import (POLL_TIMEOUT, BatchResender, Ctx, Fail, Heartbeater, Skip, compose, displays,
                                       fields, mixer_logs, mixer_restart, new_display, pick, until, wait_mixer_up)
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

    res = await compose(ctx.cfg, "restart", "janus")
    if res.returncode != 0:
        raise Fail("docker compose restart janus", (res.stderr or res.stdout)[-400:])
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

    res = await compose(ctx.cfg, "logs", "--no-log-prefix", "--since", since, "janus")
    m = re.search(rf"\[slvoice\] room {r1} destroyed after (\d+)s empty", res.stdout or "")
    if m is None:
        raise Fail(f"log line '[slvoice] room {r1} destroyed after <n>s empty'",
                   {"compose_rc": res.returncode, "matches_for_other_rooms":
                    re.findall(r"\[slvoice\] room \d+ destroyed after \d+s empty", res.stdout or "")[-3:]})
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

    res = await compose(ctx.cfg, "logs", "--no-log-prefix", "--since", since, "janus")
    m = re.search(rf"\[slvoice\] {re.escape(dg)} reaped from room {r}: no media (\d+)s after join", res.stdout or "")
    if m is None:
        raise Fail(f"log line '[slvoice] {dg} reaped from room {r}: no media <n>s after join'",
                   {"compose_rc": res.returncode,
                    "reap_lines": re.findall(r"\[slvoice\] \S+ reaped from room \d+: no media \d+s after join",
                                             res.stdout or "")[-3:]})
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
        res = await compose(ctx.cfg, "exec", "-T", "janus", "legion-voice-selfcheck", "--session", str(hid), "--json",
                            timeout=30.0)
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
    """The sim's JoinCapability.Mint, in Python: v1.<b64url payload>.<b64url HMAC-SHA256>."""
    iat = int(time.time()) if iat is None else iat
    nonce = nonce or secrets.token_hex(16)
    payload = f"{agent}|{session}|{room}|{epoch}|{generation}|{iat}|{iat + lifetime}|{nonce}"
    signing = "v1." + _b64u(payload.encode())
    return signing + "." + _b64u(hmac.new(secret.encode(), signing.encode(), hashlib.sha256).digest())


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

    # No capability at all, in a declared room: counted cap_missing, and the join still succeeds.
    c = await ctx.join("C", r, vis_authority=True)
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

    await _refused(ctx, "REPLAY", r, new_display(), "cap_replayed", join_cap=good, session_id=session_a)
    await _refused(ctx, "MISSING", r, new_display(), "cap_missing")
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
    await _refused(ctx, "STALEGEN", r, db, "cap_stale_generation",
                   join_cap=_mint_cap(secret, db, session_b, r, epoch="0000018f00000001", generation=3),
                   session_id=session_b)

    # Not gated anywhere else: an undeclared room admits a join with no capability, with the knob on.
    r2 = ctx.new_room()
    plain = await ctx.join_without_media("PLAIN", r2)
    if (plain.join_reply or {}).get("audiobridge") != "joined":
        raise Fail("an undeclared room is never gated by the capability", plain.join_reply)
    info = await ctx.info(a)
    if _cap_state(info).get("enforced_refusals", 0) < 9:
        raise Fail("every refusal above was enforced and counted", _cap_state(info))


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
             "0.4 requirement off, §11.4", s22_join_capability_shadow),
    Scenario("S23", "join capability required: a valid one joins; every refusal names its reason",
             "0.4 requirement on, §11.4, O-46", s23_join_capability_required),
    Scenario("S24", "a pre-0.4 image ignores join_cap and session_id",
             "0.4 new sim / old mixer, §11.8", s24_old_mixer_ignores_capability),
]
