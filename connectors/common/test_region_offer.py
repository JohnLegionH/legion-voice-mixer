# Copyright (c) 2026 Legion Builds
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GNU General Public License, version 3 only; see the LICENSE file in the repository root.
"""Unit tests for the offer through a region (common/peer.py: provision_via_region, RegionOfferPeer). pytest-style; a
__main__ fallback runs them with plain python. Run from connectors/: `python -m pytest common/` or
`python -m common.test_region_offer`.

The region is a local aiohttp server that reads the same LLSD XML body the region's voice service reads and answers in
the shape ProvisionResponseBuilder builds. For the end-to-end test the server also plays the mixer: it answers the
offer with a real aiortc PeerConnection, so ICE really connects, on this host, with no Janus and no region."""

import asyncio
import logging

import aiohttp
from aiohttp import web
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

from common.peer import (RegionOfferError, RegionOfferPeer, llsd_dumps, llsd_loads, logout_body, logout_via_region,
                         provision_body, provision_via_region, signal_via_region)

OFFER = {"type": "offer", "sdp": "v=0\r\no=- 1 1 IN IP4 192.0.2.1\r\n"}
LLSD_XML = "application/llsd+xml"


def _log():
    log = logging.getLogger("test_region_offer")
    log.addHandler(logging.NullHandler())
    return log


def _llsd_response(value, status=200):
    return web.Response(body=llsd_dumps(value), status=status, content_type=LLSD_XML)


class FakeRegion:
    """The two capabilities of a region: /provision and /signal. `provision_reply` is a callable (body) -> response."""

    def __init__(self, provision_reply):
        self.provision_reply = provision_reply
        self.provisions: list = []
        self.signals: list = []
        self.logouts: list = []
        self._runner = None
        self.base = ""

    async def _provision(self, request):
        assert request.content_type == LLSD_XML, request.content_type
        body = llsd_loads(await request.read())
        if body.get("logout"):
            self.logouts.append(body)
            return _llsd_response({"response": "closed"})
        self.provisions.append(body)
        return await self.provision_reply(body)

    async def _signal(self, request):
        self.signals.append(llsd_loads(await request.read()))
        return web.Response(body=llsd_dumps(None), content_type=LLSD_XML)

    async def start(self):
        app = web.Application()
        app.router.add_post("/provision", self._provision)
        app.router.add_post("/signal", self._signal)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = self._runner.addresses[0][1]
        self.base = f"http://127.0.0.1:{port}"

    async def stop(self):
        await self._runner.cleanup()

    @property
    def provision_url(self):
        return f"{self.base}/provision"

    @property
    def signaling_url(self):
        return f"{self.base}/signal"


async def _answer_ok(body):
    return _llsd_response({"jsep": {"type": "answer", "sdp": "v=0\r\n"}, "viewer_session": "vs-1", "room": 42})


# ---- the LLSD codec and the bodies

def test_llsd_round_trips_every_type_the_requests_use():
    value = {"a": [1, -2, 2.5, True, False, None, "x < y & z"], "b": {"c": b"\x00\x01", "d": ""},
             "sdp": "v=0\r\ns=-\r\n"}
    assert llsd_loads(llsd_dumps(value)) == value


def test_llsd_xml_shape_is_what_a_region_parses():
    xml = llsd_dumps({"voice_server_type": "webrtc", "parcel_local_id": 3, "logout": True}).decode()
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?><llsd><map>')
    assert "<key>voice_server_type</key><string>webrtc</string>" in xml
    assert "<key>parcel_local_id</key><integer>3</integer>" in xml
    assert "<key>logout</key><boolean>true</boolean>" in xml


def test_llsd_reads_what_a_region_sends():
    reply = llsd_loads(b'<llsd><map><key>jsep</key><map><key>type</key><string>answer</string><key>sdp</key>'
                       b'<string>v=0</string></map><key>viewer_session</key><string>abc</string>'
                       b'<key>room</key><integer>12</integer><key>id</key><uuid>00000000-0000-0000-0000-000000000001'
                       b'</uuid><key>none</key><undef/></map></llsd>')
    assert reply["jsep"] == {"type": "answer", "sdp": "v=0"}
    assert reply["viewer_session"] == "abc" and reply["room"] == 12
    assert reply["id"] == "00000000-0000-0000-0000-000000000001" and reply["none"] is None


def test_provision_body_for_the_parcel_room_has_the_local_keys_only():
    body = provision_body(OFFER, channel_type="local", parcel_local_id=7)
    assert body == {"jsep": OFFER, "voice_server_type": "webrtc", "channel_type": "local", "parcel_local_id": 7}


def test_provision_body_for_a_call_carries_channel_and_credentials_under_those_names():
    body = provision_body(OFFER, channel_type="multiagent", channel="sess-1", credentials="cred-1")
    assert body["channel"] == "sess-1" and body["credentials"] == "cred-1" and body["channel_type"] == "multiagent"
    assert "channel_id" not in body and "parcel_local_id" not in body


def test_provision_body_keeps_only_the_offer_members():
    body = provision_body({"type": "offer", "sdp": "S", "extra": "dropped"})
    assert body["jsep"] == {"type": "offer", "sdp": "S"}


def test_logout_body_has_no_channel_type():
    assert logout_body("vs-9") == {"logout": True, "viewer_session": "vs-9", "voice_server_type": "webrtc"}


# ---- one POST each

def test_provision_via_region_sends_the_body_and_reads_the_answer_back():
    async def run():
        region = FakeRegion(_answer_ok)
        await region.start()
        try:
            async with aiohttp.ClientSession() as http:
                ans = await provision_via_region(http, region.provision_url, OFFER, channel_type="multiagent",
                                                 channel="sess-1", credentials="cred-1")
        finally:
            await region.stop()
        assert region.provisions == [provision_body(OFFER, channel_type="multiagent", channel="sess-1",
                                                    credentials="cred-1")]
        assert ans.ok and ans.status == 200
        assert ans.jsep == {"type": "answer", "sdp": "v=0\r\n"}
        assert ans.viewer_session == "vs-1" and ans.room == 42 and ans.error is None
    asyncio.run(run())


def test_a_refusal_the_region_answers_with_200_is_not_ok_and_keeps_its_error():
    async def refuse(body):
        return _llsd_response({"response": "failed", "error": "room selection failed"})

    async def run():
        region = FakeRegion(refuse)
        await region.start()
        try:
            async with aiohttp.ClientSession() as http:
                ans = await provision_via_region(http, region.provision_url, OFFER)
        finally:
            await region.stop()
        assert not ans.ok and ans.status == 200 and ans.jsep is None and ans.viewer_session is None
        assert ans.error == "room selection failed"
    asyncio.run(run())


def test_a_409_room_full_and_a_non_llsd_404_are_reported_by_status():
    async def full(body):
        return _llsd_response({"response": "failed", "error": "room is full", "error_code": 495}, status=409)

    async def not_found(body):
        return web.Response(status=404, text="no such capability")

    async def run():
        for reply, status, error in ((full, 409, "room is full"), (not_found, 404, "no such capability")):
            region = FakeRegion(reply)
            await region.start()
            try:
                async with aiohttp.ClientSession() as http:
                    ans = await provision_via_region(http, region.provision_url, OFFER)
            finally:
                await region.stop()
            assert not ans.ok and ans.status == status and ans.error == error
    asyncio.run(run())


def test_logout_and_signalling_go_through_their_capabilities():
    async def run():
        region = FakeRegion(_answer_ok)
        await region.start()
        try:
            async with aiohttp.ClientSession() as http:
                assert await logout_via_region(http, region.provision_url, "vs-1") == 200
                assert await signal_via_region(http, region.signaling_url, "vs-1", {"completed": True}) == 200
        finally:
            await region.stop()
        assert region.logouts == [logout_body("vs-1")]
        assert region.signals == [{"candidate": {"completed": True}, "viewer_session": "vs-1",
                                   "voice_server_type": "webrtc"}]
    asyncio.run(run())


# ---- the peer, end to end on this host

def test_the_peer_offers_through_the_region_reaches_ice_connected_and_logs_out():
    async def run():
        mixer_pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))

        async def answer_with_a_real_peer(body):
            await mixer_pc.setRemoteDescription(RTCSessionDescription(sdp=body["jsep"]["sdp"], type="offer"))
            await mixer_pc.setLocalDescription(await mixer_pc.createAnswer())
            d = mixer_pc.localDescription
            return _llsd_response({"jsep": {"type": d.type, "sdp": d.sdp}, "viewer_session": "vs-7", "room": 5})

        region = FakeRegion(answer_with_a_real_peer)
        await region.start()
        peer = RegionOfferPeer({"provision_url": region.provision_url, "signaling_url": region.signaling_url,
                                "channel_type": "local", "parcel_local_id": 3, "ice_servers": []}, _log())
        try:
            ans = await peer.connect(timeout=20)
            assert ans.ok and peer.viewer_session == "vs-7" and peer.room == 5
            assert peer.ice_states and peer.ice_states[-1] in ("connected", "completed"), peer.ice_states
            status = await peer.hangup()
            assert status == 200
        finally:
            await mixer_pc.close()
            await region.stop()
        sent = region.provisions[0]
        assert sent["channel_type"] == "local" and sent["parcel_local_id"] == 3 and sent["voice_server_type"] == "webrtc"
        assert sent["jsep"]["type"] == "offer" and "m=audio" in sent["jsep"]["sdp"]
        assert region.signals and region.signals[0]["viewer_session"] == "vs-7"
        assert region.logouts == [logout_body("vs-7")]
    asyncio.run(run())


def test_the_peer_raises_when_the_region_refuses_and_keeps_the_answer():
    async def refuse(body):
        return _llsd_response({"response": "failed", "error": "JoinFailed"})

    async def run():
        region = FakeRegion(refuse)
        await region.start()
        peer = RegionOfferPeer({"provision_url": region.provision_url, "ice_servers": []}, _log())
        try:
            try:
                await peer.connect(timeout=5)
                raise AssertionError("connect() should have raised")
            except RegionOfferError as e:
                assert "JoinFailed" in str(e) and region.base not in str(e)
            assert peer.answer is not None and peer.answer.error == "JoinFailed"
            assert peer.viewer_session is None
            assert await peer.hangup() is None      # nothing was provisioned: no logout is sent
        finally:
            await region.stop()
        assert region.logouts == []
    asyncio.run(run())


def test_the_peer_raises_when_ice_does_not_connect():
    async def run():
        mixer_pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))

        async def answer_nobody_can_reach(body):
            await mixer_pc.setRemoteDescription(RTCSessionDescription(sdp=body["jsep"]["sdp"], type="offer"))
            await mixer_pc.setLocalDescription(await mixer_pc.createAnswer())
            # The answer is real, but its sender is gone before the peer starts its checks: nobody answers them.
            sdp = mixer_pc.localDescription.sdp
            await mixer_pc.close()
            return _llsd_response({"jsep": {"type": "answer", "sdp": sdp}, "viewer_session": "vs-8", "room": 1})

        region = FakeRegion(answer_nobody_can_reach)
        await region.start()
        peer = RegionOfferPeer({"provision_url": region.provision_url, "ice_servers": []}, _log())
        try:
            try:
                await peer.connect(timeout=3)
                raise AssertionError("connect() should have raised")
            except RegionOfferError as e:
                assert "ICE" in str(e), str(e)
            assert peer.viewer_session == "vs-8"
            assert await peer.hangup() == 200
        finally:
            await mixer_pc.close()
            await region.stop()
        assert region.logouts == [logout_body("vs-8")]
    asyncio.run(run())


if __name__ == "__main__":
    import sys
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)
