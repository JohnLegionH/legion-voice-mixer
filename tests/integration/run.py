"""Entry point: python -m tests.integration.run [--only S3[,S5]] [--grace N] [--join-timeout N] [--no-restart]

Runs the scenarios against a LIVE mixer and prints one PASS/FAIL/SKIP line per scenario (the
observed oracle values on a FAIL), then a summary. Exit code 1 if any scenario failed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import itertools
import json
import logging
import sys
import time
import traceback
from pathlib import Path

import aiohttp

from tests.integration.harness import REPO, ROOM_BASE, Admin, Config, Ctx, Fail, Skip, read_env
from tests.integration.scenarios import SCENARIOS


def parse_args(argv):
    p = argparse.ArgumentParser(prog="python -m tests.integration.run", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--janus-url", default="http://localhost:24223/voice", help="client API base")
    p.add_argument("--admin-url", default="http://localhost:24225/voiceAdmin", help="Admin API base")
    p.add_argument("--env", default=str(REPO / ".env"),
                   help="compose .env to read JS_API_SECRET / JS_ADMIN_SECRET from")
    p.add_argument("--api-secret", default=None, help="overrides JS_API_SECRET from --env")
    p.add_argument("--admin-secret", default=None, help="overrides JS_ADMIN_SECRET from --env")
    p.add_argument("--compose-file", default=str(REPO / "docker-compose.yml"),
                   help="used by S4 (restart), S5 and S10 (log lines)")
    p.add_argument("--only", action="append", default=[],
                   help="scenario id(s) to run, e.g. --only S3 or --only S1,S7 (repeatable)")
    p.add_argument("--grace", type=int, default=60,
                   help="the mixer's JS_EMPTY_ROOM_GRACE_S, which S5 waits out (default 60)")
    p.add_argument("--join-timeout", type=int, default=30,
                   help="the mixer's JS_JOIN_MEDIA_TIMEOUT_S, which S10 waits out (default 30)")
    p.add_argument("--no-restart", action="store_true", help="skip S4 (docker compose restart janus)")
    p.add_argument("--turn-uri", default="",
                   help="S13: TURN server for the relay-only peer, e.g. 'turn:127.0.0.1:3478?transport=tcp' "
                        "(the turn-test profile); without it S13 is skipped")
    p.add_argument("--turn-secret", default="",
                   help="S13: coturn shared secret; credentials are derived as a TURN REST API would")
    p.add_argument("--turn-user", default="", help="S13: static TURN username (instead of --turn-secret)")
    p.add_argument("--turn-pwd", default="", help="S13: static TURN password")
    p.add_argument("--container", default="",
                   help="S17/S20: a mixer started with `docker run` (the 0.3 fail-closed scratch mixer): read its logs "
                        "and restart it by this container name instead of the compose service")
    p.add_argument("-v", "--verbose", action="store_true", help="peer/harness logs and tracebacks")
    return p.parse_args(argv)


def fmt(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except Exception:
        return repr(value)


async def main_async(args) -> int:
    env = read_env(Path(args.env))
    turn_user, turn_pwd = args.turn_user, args.turn_pwd
    if args.turn_secret and not turn_user:
        # coturn shared-secret credentials, valid for an hour: '<expiry>:<user>', base64(HMAC-SHA1(secret, user)).
        turn_user = "%d:legion-voice-harness" % (int(time.time()) + 3600)
        turn_pwd = base64.b64encode(hmac.new(args.turn_secret.encode(), turn_user.encode(), hashlib.sha1).digest()).decode()
    cfg = Config(
        janus_url=args.janus_url.rstrip("/"),
        admin_url=args.admin_url.rstrip("/"),
        api_secret=args.api_secret if args.api_secret is not None else env.get("JS_API_SECRET", ""),
        admin_secret=args.admin_secret if args.admin_secret is not None else env.get("JS_ADMIN_SECRET", ""),
        compose_file=Path(args.compose_file),
        grace=args.grace,
        restart=not args.no_restart,
        join_timeout=args.join_timeout,
        turn_uri=args.turn_uri,
        turn_user=turn_user,
        turn_pwd=turn_pwd,
        container=args.container,
    )
    only = {s.strip().upper() for arg in args.only for s in arg.split(",") if s.strip()}
    unknown = only - {s.id for s in SCENARIOS}
    if unknown:
        print(f"unknown scenario id(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    selected = [s for s in SCENARIOS if not only or s.id in only]

    # A fresh block of room ids per run, far above real rooms; each scenario draws its own.
    nonce = int(time.time()) % 100_000
    counter = itertools.count()

    def alloc_room() -> int:
        return ROOM_BASE + nonce * 100 + next(counter)

    results = []
    started = time.monotonic()
    async with aiohttp.ClientSession() as http:
        admin = Admin(cfg, http)
        try:
            pong = await admin.ping()
            if pong.get("janus") != "pong":
                raise RuntimeError(pong)
        except Exception as e:
            print(f"Admin API not usable at {cfg.admin_url}: {e!r}", file=sys.stderr)
            return 2

        for sc in selected:
            ctx = Ctx(cfg, http, admin, sc.id, alloc_room)
            t0 = time.monotonic()
            status, detail = "PASS", ""
            try:
                await ctx.open()
                await sc.fn(ctx)
            except Skip as e:
                status, detail = "SKIP", str(e)
            except Fail as e:
                status, detail = "FAIL", f"{e.what}; observed: {fmt(e.observed)}"
            except Exception as e:
                status, detail = "FAIL", f"harness error: {e!r}"
                if args.verbose:
                    traceback.print_exc()
            finally:
                try:
                    await ctx.teardown()
                except Exception as e:
                    if args.verbose:
                        print(f"  teardown of {sc.id} raised {e!r}", file=sys.stderr)
            elapsed = time.monotonic() - t0
            results.append((sc, status, elapsed, detail))
            line = f"{status:<4}  {sc.id}  {sc.title} [{sc.covers}]  ({elapsed:.1f} s)"
            if status != "PASS":
                line += f"\n      {detail}"
            print(line, flush=True)

    total = time.monotonic() - started
    counts = {k: sum(1 for _, s, _, _ in results if s == k) for k in ("PASS", "FAIL", "SKIP")}
    rooms = f"{ROOM_BASE + nonce * 100}..{ROOM_BASE + nonce * 100 + max(next(counter) - 1, 0)}"
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped "
          f"in {total:.1f} s (grace {cfg.grace} s, join timeout {cfg.join_timeout} s, rooms {rooms})")
    return 1 if counts["FAIL"] else 0


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.CRITICAL,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
