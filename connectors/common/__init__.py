"""Shared plumbing for the voice connector peers (S-CON-4 recorder, S-CON-6
injector; docs/voice/connector-build-plan.md).

Import via the package (``from common.peer import ConnectorPeer``) with the
connectors/ directory on sys.path — the containers run ``python -m
<peer>.<peer>`` from a WORKDIR that holds both packages. Nothing here imports
aiortc/aiohttp at package-import time: ``segments`` stays stdlib-only so the
writer tests run without the WebRTC stack installed.
"""
