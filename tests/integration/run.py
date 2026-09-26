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
import ipaddress
import itertools
import json
import logging
import sys
import time
import traceback
from pathlib import Path

import aiohttp

from tests.integration.harness import REPO, ROOM_BASE, Admin, Config, Ctx, Fail, Skip, read_env, read_secret_file
from tests.integration.scenarios import SCENARIOS


def _ipv4(text: str) -> str:
    """--media-address: an IPv4 literal (aiortc's candidates need one), or empty for Janus's own candidates."""
    if not text:
        return ""
    try:
        return str(ipaddress.IPv4Address(text.strip()))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an IPv4 address: {text!r}") from None


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
    p.add_argument("--join-cap-secret", default="",
                   help="S22-S24 (slice 0.4): the mixer's JS_JOIN_CAP_SECRET, so the harness can mint join "
                        "capabilities as the sim does; without it those scenarios are skipped")
    p.add_argument("--stale-ms-started-with", type=int, default=0,
                   help="S31 (slice 0.5, §9 27): the JS_VIS_STALE_MS this mixer was STARTED with, when that is "
                        "below the §5 minimum, so S31 can check the clamp took effect. Without it S31 skips")
    p.add_argument("--prove-fail", action="store_true",
                   help="slice 0.5: report a SKIP as a FAIL. Only for the 'behaviour absent' proof runs against "
                        "an older image, where a scenario that merely skips proves nothing. Never for a "
                        "reporting run")
    p.add_argument("--container", default="",
                   help="a mixer started with `docker run` (a scratch mixer): every scenario reads its logs, execs into it "
                        "and restarts it by this container name instead of the compose service (O-94)")
    p.add_argument("--join-cap-secret-file", default="",
                   help="slice 0.8e: read the mixer's JS_JOIN_CAP_SECRET from a FILE (a bare value, or a dotenv file "
                        "with a JS_JOIN_CAP_SECRET= line) instead of a command line, so it never reaches the shell "
                        "history, the process list or this run's output. With a key, every ordinary join into a "
                        "declared room carries a freshly minted capability, which is what a board against a mixer "
                        "with JS_JOIN_CAP_REQUIRED=1 needs")
    p.add_argument("--media-address", default="", type=_ipv4,
                   help="send every peer's media to this IPv4 instead of the addresses in Janus's candidates. CI "
                        "passes the runner's own host address, so S14 reaches the published RTP port without the "
                        "loopback docker-proxy and runs its preserved-source branch")
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
        join_cap_secret=(read_secret_file(args.join_cap_secret_file) if args.join_cap_secret_file
                         else args.join_cap_secret),
        stale_ms_started_with=args.stale_ms_started_with,
        prove_fail=args.prove_fail,
        media_address=args.media_address,
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
                # --prove-fail: a scenario that skips against a build lacking the behaviour proves nothing, so
                # the proof runs turn that into the failure it really is.
                status, detail = ("FAIL", f"skipped, and --prove-fail: {e}") if cfg.prove_fail else ("SKIP", str(e))
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
