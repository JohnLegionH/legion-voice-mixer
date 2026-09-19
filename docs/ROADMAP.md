# Roadmap

Work that is wanted, understood and not yet scheduled. Defects and findings live in
`docs/voice/voice-programme-ledger.md`; this file is for deliberate improvements. One row per item, newest
first, each naming what it buys and what it costs.

| Item | Why | What it takes | Filed |
|---|---|---|---|
| **Speak the Janus WebSocket transport instead of HTTP long polling** | It is the only way to stop the API secret travelling in a URL. On the HTTP transport a long poll must carry `?apisecret=`, because Janus reads a GET's secret only from query arguments (`vendor/janus-gateway/src/transports/janus_http.c:1601`); over WebSockets the secret rides in the first message's JSON like any other field, so no proxy, gateway or access log can capture it from a URL. It also removes one HTTP round trip per event and the 30 s poll churn | Both clients have to learn it: the connector peers (`connectors/common/janus.py`, whose `_post`/`poll` pair becomes one duplex connection) and the sim's `JanusSession`. The mixer already exposes it - `JS_WS_ENABLED=true`, port 8188 - so no mixer change. Keep HTTP as the fallback for one release, and the harness needs a transport switch to run its board over both | 2026-09-19, slice 0.10b, ledger O-101 |
