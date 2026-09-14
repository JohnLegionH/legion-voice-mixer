"""Loopback fakes for the A.1/A.2 Python tests: a STUN server, a minimal Janus HTTP API and a silent TCP
listener. Everything binds 127.0.0.1, so the image build runs these tests with no network."""

import json
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

STUN_MAGIC = 0x2112A442


def xor_mapped(ip, port):
    raw = struct.unpack(">I", socket.inet_aton(ip))[0] ^ STUN_MAGIC
    value = struct.pack(">BBHI", 0, 1, port ^ (STUN_MAGIC >> 16), raw)
    return struct.pack(">HH", 0x0020, len(value)) + value


class FakeStun(object):
    """Answers Binding requests. mapping(src_ip, src_port) returns the (ip, port) to report, or None to stay
    silent."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.stopped = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def address(self):
        return "127.0.0.1:%d" % self.port

    def _serve(self):
        while not self.stopped:
            try:
                data, peer = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            if len(data) < 20:
                continue
            answer = self.mapping(peer[0], peer[1])
            if answer is None:
                continue
            attr = xor_mapped(*answer)
            reply = struct.pack(">HHI", 0x0101, len(attr), STUN_MAGIC) + data[8:20] + attr
            try:
                self.sock.sendto(reply, peer)
            except OSError:
                return

    def close(self):
        self.stopped = True
        self.thread.join(1.0)
        self.sock.close()


class FakeJanusHTTP(object):
    """A minimal Janus HTTP transport on 127.0.0.1.
      GET  <base>/info                   server_info (mode ok), without slvoice (no_slvoice), or not Janus (not_janus)
      GET  <base>                        a janus:"error" reply (what the admin API answers to a GET)
      POST <base> create, <base>/111 attach/destroy, <base>/111/222 message {"request":"list"} -> ack
      GET  <base>/111?maxev=1            the list event: one room per entry of `rooms`
    Every POST and the long poll need `secret` (apisecret); a wrong one gets Janus's 403 error."""

    def __init__(self, base="/voice", secret="s3cret", mode="ok", rooms=(2, 1)):
        self.base, self.secret, self.mode, self.rooms = base, secret, mode, list(rooms)
        self.pending = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, obj):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _forbidden(self, tx):
                self._json({"janus": "error", "transaction": tx,
                            "error": {"code": 403, "reason": "Unauthorized request (wrong or missing secret/token)"}})

            def do_GET(self):
                path, _, query = self.path.partition("?")
                if path == outer.base + "/info":
                    if outer.mode == "not_janus":
                        return self._json({"hello": "world"})
                    plugins = {"janus.plugin.echotest": {}} if outer.mode == "no_slvoice" else {"janus.plugin.slvoice": {}}
                    return self._json({"janus": "server_info", "name": "Fake Janus", "version_string": "1.4.1",
                                       "plugins": plugins})
                if path == outer.base + "/111":
                    params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                    if params.get("apisecret") != outer.secret:
                        return self._forbidden(None)
                    tx = outer.pending.pop("list", None)
                    rooms = [{"room": i + 1, "num_participants": n} for i, n in enumerate(outer.rooms)]
                    events = [] if tx is None else [{"janus": "event", "transaction": tx, "sender": 222, "plugindata": {
                        "plugin": "janus.plugin.slvoice", "data": {"audiobridge": "success", "list": rooms}}}]
                    return self._json(events)
                if path == outer.base:
                    return self._json({"janus": "error", "error": {"code": 457, "reason": "Unhandled request 'GET'"}})
                self.send_error(404)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    req = json.loads(self.rfile.read(length).decode("utf-8"))
                except ValueError:
                    return self._json({"janus": "error", "error": {"code": 454, "reason": "Failed to parse JSON"}})
                tx = req.get("transaction")
                if req.get("apisecret") != outer.secret:
                    return self._forbidden(tx)
                if self.path == outer.base and req.get("janus") == "create":
                    return self._json({"janus": "success", "transaction": tx, "data": {"id": 111}})
                if self.path == outer.base + "/111" and req.get("janus") == "attach":
                    return self._json({"janus": "success", "transaction": tx, "data": {"id": 222}})
                if self.path == outer.base + "/111" and req.get("janus") == "destroy":
                    return self._json({"janus": "success", "transaction": tx})
                if self.path == outer.base + "/111/222" and req.get("janus") == "message":
                    outer.pending["list"] = tx
                    return self._json({"janus": "ack", "transaction": tx})
                self.send_error(404)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return "http://127.0.0.1:%d%s" % (self.port, self.base)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class SilentTCP(object):
    """Accepts TCP connections and never answers."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.conns = []
        self.stopped = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stopped:
            try:
                conn, _ = self.sock.accept()
                self.conns.append(conn)
            except socket.timeout:
                continue
            except OSError:
                return

    def close(self):
        self.stopped = True
        self.thread.join(1.0)
        for conn in self.conns:
            conn.close()
        self.sock.close()


def closed_tcp_port():
    """A 127.0.0.1 TCP port with nothing listening (connection refused)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port
