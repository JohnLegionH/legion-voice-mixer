# Copyright (c) 2026 Legion Builds
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GNU General Public License, version 3 only; see the LICENSE file in the repository root.
"""Shared plumbing for the voice connector peers (recorder and
injector).

Import via the package (``from common.peer import ConnectorPeer``) with the
connectors/ directory on sys.path — the containers run ``python -m
<peer>.<peer>`` from a WORKDIR that holds both packages. Nothing here imports
aiortc/aiohttp at package-import time: ``segments`` stays stdlib-only so the
writer tests run without the WebRTC stack installed.
"""
