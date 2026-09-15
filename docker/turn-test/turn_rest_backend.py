#!/usr/bin/env python3
"""TEST FIXTURE ONLY (compose profile `turn-test`). NOT FOR PRODUCTION.

A minimal TURN REST API backend for the turn-test coturn, in the shape Janus's turnrest.c calls:
- the request carries `service=turn`, the key as `api=` and/or `key=`, and `username=`, as a GET query or POST form;
- the answer is {username, password, ttl, uris}, with coturn shared-secret credentials: username
  '<expiry>:<username>', password base64(HMAC-SHA1(secret, username)), computed by addr_probe.rest_credentials.

The legion-voice product ships no TURN server and no REST backend: operators run their own. This exists so the
mixer's REST path (JS_TURN_REST_API) and the self-check's C6 can be exercised against a real coturn.

Environment (throwaway test values; see docker-compose.yml):
  TURN_TEST_SECRET     coturn's static-auth-secret
  TURN_TEST_API_KEY    the key this backend expects
  TURN_TEST_URIS       comma-separated TURN URIs to hand out
  TURN_TEST_TTL        credential lifetime in seconds (default 3600)
  TURN_TEST_REST_PORT  listening port (default 8089)
"""

import json
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/usr/local/lib/legion-voice")
import addr_probe  # noqa: E402

SECRET = os.environ.get("TURN_TEST_SECRET", "turn-test-secret")
API_KEY = os.environ.get("TURN_TEST_API_KEY", "turn-test-api-key")
URIS = [uri.strip() for uri in os.environ.get("TURN_TEST_URIS", "").split(",") if uri.strip()]
TTL = int(os.environ.get("TURN_TEST_TTL", "3600"))
PORT = int(os.environ.get("TURN_TEST_REST_PORT", "8089"))


class Handler(BaseHTTPRequestHandler):
    def _params(self):
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
        if self.command == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            params.update(dict(urllib.parse.parse_qsl(self.rfile.read(length).decode("utf-8", "replace"))))
        return params

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _answer(self):
        params = self._params()
        if params.get("service") != "turn":
            return self._send(400, {"error": "service=turn is required"})
        if params.get("key", params.get("api")) != API_KEY:
            return self._send(401, {"error": "unknown key"})
        username, password = addr_probe.rest_credentials(SECRET, params.get("username", "janus"), int(time.time()) + TTL)
        self._send(200, {"username": username, "password": password, "ttl": TTL, "uris": URIS})

    do_GET = _answer
    do_POST = _answer

    def log_message(self, fmt, *args):
        # The path only: never the query, which carries the key.
        sys.stdout.write("turn-test-rest: %s %s -> %s\n" % (self.command, urllib.parse.urlsplit(self.path).path,
                                                           args[1] if len(args) > 1 else "?"))
        sys.stdout.flush()


if __name__ == "__main__":
    sys.stdout.write("turn-test-rest: TEST FIXTURE listening on :%d, handing out %s\n" % (PORT, ", ".join(URIS) or "no URIs"))
    sys.stdout.flush()
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
