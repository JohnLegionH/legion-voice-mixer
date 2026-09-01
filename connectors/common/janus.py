"""Minimal Janus HTTP transport client, shared by the connector peers.

Moved verbatim from the S-CON-4 recorder (deliberately small — build plan D5: a
later port should move little). Janus HTTP API shapes used (mixer plugin verbs
cited from src/janus_slvoice.c):
  create   POST {base}                       {"janus":"create", ...}
  attach   POST {base}/{session}             {"janus":"attach","plugin":"janus.plugin.slvoice"}
  message  POST {base}/{session}/{handle}    {"janus":"message","body":...,"jsep":...}
  events   GET  {base}/{session}?maxev=1     long-poll; "joined" event carries the answer
  trickle  POST {"janus":"trickle","candidate":...}
  keepalive POST {"janus":"keepalive"} every KEEPALIVE_SECONDS
  detach/destroy on shutdown.
"""

from __future__ import annotations

import uuid

import aiohttp

KEEPALIVE_SECONDS = 30
PLUGIN = "janus.plugin.slvoice"


class JanusHttp:
    def __init__(self, base: str, api_secret: str, http: aiohttp.ClientSession):
        self._base = base
        self._secret = api_secret
        self._http = http
        self.session_id: int | None = None
        self.handle_id: int | None = None

    def _body(self, janus: str, **extra) -> dict:
        body = {"janus": janus, "transaction": uuid.uuid4().hex[:12], **extra}
        if self._secret:
            body["apisecret"] = self._secret
        return body

    async def _post(self, path: str, body: dict) -> dict:
        async with self._http.post(f"{self._base}{path}", json=body) as resp:
            resp.raise_for_status()
            data = await resp.json()
        if data.get("janus") == "error":
            raise RuntimeError(f"janus error on {body['janus']}: {data.get('error')}")
        return data

    async def create(self) -> None:
        data = await self._post("", self._body("create"))
        self.session_id = data["data"]["id"]

    async def attach(self) -> None:
        data = await self._post(f"/{self.session_id}", self._body("attach", plugin=PLUGIN))
        self.handle_id = data["data"]["id"]

    async def message(self, body: dict, jsep: dict | None = None) -> dict:
        msg = self._body("message", body=body)
        if jsep is not None:
            msg["jsep"] = jsep
        return await self._post(f"/{self.session_id}/{self.handle_id}", msg)

    async def trickle_completed(self) -> None:
        await self._post(f"/{self.session_id}/{self.handle_id}",
                         self._body("trickle", candidate={"completed": True}))

    async def keepalive(self) -> None:
        await self._post(f"/{self.session_id}", self._body("keepalive"))

    async def detach(self) -> None:
        await self._post(f"/{self.session_id}/{self.handle_id}", self._body("detach"))

    async def destroy(self) -> None:
        await self._post(f"/{self.session_id}", self._body("destroy"))

    async def poll(self) -> dict | None:
        """One long-poll turn. Returns the event, or None on the transport's own
        keepalive timeout answer."""
        params = {"maxev": "1"}
        if self._secret:
            params["apisecret"] = self._secret
        async with self._http.get(f"{self._base}/{self.session_id}", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return None if data.get("janus") == "keepalive" else data
