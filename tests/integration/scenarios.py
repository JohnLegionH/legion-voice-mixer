"""The churn scenarios S1-S8 -- the mixer's robustness contract (README.md in this directory).

Each scenario gets a fresh Ctx (its own control handle and room ids) and the runner tears it down
in a finally block. Every expectation is a poll of the oracle (Admin API handle_info, or the
plugin's list / listparticipants) with a bounded timeout; nothing sleeps blindly.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from tests.integration.harness import (POLL_TIMEOUT, BatchResender, Ctx, Fail, Skip, compose, displays,
                                       fields, new_display, pick, until, wait_mixer_up)


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


SCENARIOS = [
    Scenario("S1", "join/leave/rejoin", "O-42c presence, duplicate rows", s1_join_leave_rejoin),
    Scenario("S2", "crash without leave", "O-56", s2_crash_without_leave),
    Scenario("S3", "moderation mute persistence", "O-49, O-68", s3_mute_persistence),
    Scenario("S4", "mixer restart mid-session", "restart self-heal", s4_mixer_restart),
    Scenario("S5", "room switch = TP, grace destroy", "O-54", s5_room_switch_grace),
    Scenario("S6", "peer_ctl table full does not eat moderation", "O-49", s6_peer_ctl_full),
    Scenario("S7", "geometry persistence", "O-64", s7_geometry_persistence),
    Scenario("S8", "hangup then rejoin with the same display", "O-56, O-13", s8_hangup_rejoin_same_display),
]
