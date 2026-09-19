"""Where the Janus API secret travels (ledger O-101), pinned by test.

The finding of slice 0.10a: every POST this repo makes already carries `apisecret` in the JSON body, and the
ONLY requests that put it in a URL are the long-poll GETs - because Janus offers no other way. Its HTTP
transport reads the secret for a GET with
    MHD_lookup_connection_value(connection, MHD_GET_ARGUMENT_KIND, "apisecret")
(`vendor/janus-gateway/src/transports/janus_http.c:1601`), i.e. query arguments only; `token` auth is read the
same way on the line below, so it is no escape either. A POST's secret, by contrast, is read out of the parsed
JSON body by the core (`vendor/janus-gateway/src/janus.c:978`).

So these tests are a ratchet, not a fix: they fail if a POST ever starts carrying the secret in the URL, and
they state the long-poll GET as the one documented exception. The mitigation for that exception is 0.8f's
rule - a transport error is logged by status alone, never by URL (`common/joincap.py:30`, `:39`).

Run from connectors/: `python -m pytest common/` or `python -m common.test_apisecret`.
"""

import asyncio
import sys
from urllib.parse import parse_qs, urlsplit

from common.janus import JanusHttp

SECRET = "unit-test-secret-never-real"


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def json(self):
        return self._payload


class RecordingSession:
    """Just enough aiohttp.ClientSession to record what each call put where."""

    def __init__(self):
        self.posts = []   # (url, json body)
        self.gets = []    # (url, params)

    def post(self, url, json=None):
        self.posts.append((url, json))
        return FakeResponse({"janus": "success", "data": {"id": 1}})

    def get(self, url, params=None):
        self.gets.append((url, params))
        return FakeResponse({"janus": "keepalive"})


def _drive():
    """Every POST the connector peer's Janus client can make, plus one long poll."""
    http = RecordingSession()
    j = JanusHttp("http://janus.test/voice", SECRET, http)

    async def go():
        await j.create()
        await j.attach()
        await j.message({"request": "join", "room": 7})
        await j.trickle_completed()
        await j.keepalive()
        await j.detach()
        await j.destroy()
        await j.poll()

    asyncio.run(go())
    return http


def test_every_post_carries_the_secret_in_the_body_and_never_in_the_url():
    http = _drive()
    assert len(http.posts) == 7, http.posts
    for url, body in http.posts:
        assert urlsplit(url).query == "", f"a POST put something in the query string: {url}"
        assert SECRET not in url, f"the secret is in a POST url: {url}"
        assert body.get("apisecret") == SECRET, f"the secret is not in the POST body: {body.get('janus')}"


def test_the_long_poll_get_is_the_one_documented_exception():
    """Janus reads a GET's secret only from query arguments (janus_http.c:1601), so this one is unavoidable
    without changing transport. The test pins it so it stays the ONLY one, and stays visible."""
    http = _drive()
    assert len(http.gets) == 1, http.gets
    url, params = http.gets[0]
    assert urlsplit(url).query == "", "the secret must be passed as params, not glued into the url"
    assert params["apisecret"] == SECRET
    assert params["maxev"] == "1"
    assert set(params) == {"apisecret", "maxev"}, params


def test_no_other_query_parameter_smuggles_it():
    http = _drive()
    for url, params in http.gets:
        query = parse_qs(urlsplit(url).query)
        assert not query, url
        assert [k for k, v in (params or {}).items() if v == SECRET] == ["apisecret"], params


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)):
        try:
            fn()
            print("PASS", name)
        except Exception as e:   # noqa: BLE001 - report every failure, not just the first
            failed += 1
            print("FAIL", name, "-", type(e).__name__, e)
    sys.exit(1 if failed else 0)
