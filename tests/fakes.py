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


# ---- TURN (slice A.3) ---------------------------------------------------------------------------------------------

TURN_REALM = "fake-realm"
TURN_NONCE = b"fake-nonce-0123"


def _stun_attr(atype, value):
    return struct.pack(">HH", atype, len(value)) + value + b"\x00" * (-len(value) % 4)


def _xor_attr(atype, ip, port):
    raw = struct.unpack(">I", socket.inet_aton(ip))[0] ^ STUN_MAGIC
    return _stun_attr(atype, struct.pack(">BBHI", 0, 1, port ^ (STUN_MAGIC >> 16), raw))


def rest_password(secret, username):
    import base64
    import hashlib
    import hmac
    return base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()


class FakeTurn(object):
    """A TURN server on 127.0.0.1 answering Allocate and Refresh only, over udp or tcp.
    - An Allocate without MESSAGE-INTEGRITY gets 401 with REALM and NONCE.
    - The authenticated retry must carry a USERNAME that `users` ({name: password}) knows, or, with `secret`, a
      coturn shared-secret password. Its MESSAGE-INTEGRITY is checked with its own HMAC code, independent of
      addr_probe.
    - A successful Allocate is granted the `relay` address.
    mode "silent" never answers. `requests` records (message type, username) for each authenticated request."""

    def __init__(self, users=None, secret=None, transport="udp", relay=("203.0.113.50", 49152), mode="ok"):
        import hashlib
        import hmac
        self._hashlib, self._hmac = hashlib, hmac
        self.users, self.secret, self.transport, self.relay, self.mode = dict(users or {}), secret, transport, relay, mode
        self.requests = []
        self.stopped = False
        kind = socket.SOCK_DGRAM if transport == "udp" else socket.SOCK_STREAM
        self.sock = socket.socket(socket.AF_INET, kind)
        if transport != "udp":
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        if transport != "udp":
            self.sock.listen(8)
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.conns = []
        self.thread = threading.Thread(target=self._serve_udp if transport == "udp" else self._serve_tcp, daemon=True)
        self.thread.start()

    def _password_for(self, username):
        if username in self.users:
            return self.users[username]
        if self.secret is not None:
            return rest_password(self.secret, username)
        return None

    def _error(self, mtype, txid, code, challenge=False):
        value = b"\x00\x00" + bytes([code // 100, code % 100]) + b"Unauthorized"
        attrs = _stun_attr(0x0009, value)
        if challenge:
            attrs += _stun_attr(0x0014, TURN_REALM.encode()) + _stun_attr(0x0015, TURN_NONCE)
        return struct.pack(">HHI", mtype | 0x0110, len(attrs), STUN_MAGIC) + txid + attrs

    def reply_for(self, data):
        if self.mode == "silent" or len(data) < 20:
            return None
        mtype, mlen, magic = struct.unpack(">HHI", data[:8])
        txid = data[8:20]
        attrs, pos = [], 20
        while pos + 4 <= 20 + mlen:
            atype, alen = struct.unpack(">HH", data[pos:pos + 4])
            attrs.append((atype, data[pos + 4:pos + 4 + alen], pos))
            pos += 4 + alen + (-alen % 4)
        integrity = next(((value, offset) for atype, value, offset in attrs if atype == 0x0008), None)
        if integrity is None:
            return self._error(mtype, txid, 401, challenge=True)
        user = next((value for atype, value, _ in attrs if atype == 0x0006), b"").decode()
        password = self._password_for(user)
        if password is None:
            return self._error(mtype, txid, 401, challenge=True)
        key = self._hashlib.md5(("%s:%s:%s" % (user, TURN_REALM, password)).encode()).digest()
        value, offset = integrity
        header = struct.pack(">HHI", mtype, offset - 20 + 24, STUN_MAGIC) + txid
        if self._hmac.new(key, header + data[20:offset], self._hashlib.sha1).digest() != value:
            return self._error(mtype, txid, 401, challenge=True)
        self.requests.append((mtype, user))
        if mtype == 0x0003:
            attrs_out = _xor_attr(0x0016, *self.relay) + _stun_attr(0x000D, struct.pack(">I", 600))
        else:
            attrs_out = _stun_attr(0x000D, struct.pack(">I", 0))
        return struct.pack(">HHI", mtype | 0x0100, len(attrs_out), STUN_MAGIC) + txid + attrs_out

    def _serve_udp(self):
        while not self.stopped:
            try:
                data, peer = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            reply = self.reply_for(data)
            if reply is not None:
                try:
                    self.sock.sendto(reply, peer)
                except OSError:
                    return

    def _serve_tcp(self):
        while not self.stopped:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.conns.append(conn)
            threading.Thread(target=self._serve_conn, args=(conn,), daemon=True).start()

    def _serve_conn(self, conn):
        buffer = b""
        conn.settimeout(0.2)
        while not self.stopped:
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while len(buffer) >= 20:
                length = 20 + struct.unpack(">H", buffer[2:4])[0]
                if len(buffer) < length:
                    break
                frame, buffer = buffer[:length], buffer[length:]
                reply = self.reply_for(frame)
                if reply is not None:
                    try:
                        conn.sendall(reply)
                    except OSError:
                        return

    def close(self):
        self.stopped = True
        self.thread.join(1.0)
        for conn in self.conns:
            conn.close()
        self.sock.close()


class FakeTurnRest(object):
    """A TURN REST API backend on 127.0.0.1 (the shape Janus's turnrest.c expects). It checks the key (api= or key=)
    and answers {username, password, ttl, uris} with coturn shared-secret credentials for `secret`. `requests`
    records (method, {param: value}) with the query and any POST body merged. mode "bad_json" answers garbage."""

    def __init__(self, secret, uris, key="rest-key", ttl=3600, mode="ok"):
        import time as _time
        import urllib.parse as _parse
        self.secret, self.uris, self.key, self.ttl, self.mode = secret, list(uris), key, ttl, mode
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self):
                split = _parse.urlsplit(self.path)
                params = dict(_parse.parse_qsl(split.query))
                if self.command == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    params.update(dict(_parse.parse_qsl(self.rfile.read(length).decode())))
                outer.requests.append((self.command, params))
                if split.path != "/turn":
                    return self.send_error(404)
                if params.get("key", params.get("api")) != outer.key:
                    return self.send_error(401)
                if outer.mode == "bad_json":
                    body = b"not json"
                else:
                    username = "%d:%s" % (int(_time.time()) + outer.ttl, params.get("username", "janus"))
                    body = json.dumps({"username": username, "password": rest_password(outer.secret, username),
                                       "ttl": outer.ttl, "uris": outer.uris}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _answer
            do_POST = _answer

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return "http://127.0.0.1:%d/turn" % self.port

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FakeJanusAdmin(object):
    """A Janus Admin API on 127.0.0.1 for the candidate report: list_sessions, list_handles and handle_info.
    `handles` is {session: {handle: info}}; admin_secret must match or Janus's 403 error is returned."""

    def __init__(self, handles, secret="admin-s3cret", base="/admin"):
        self.handles, self.secret, self.base = handles, secret, base
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
                if req.get("admin_secret") != outer.secret:
                    return self._json({"janus": "error", "error": {"code": 403, "reason": "Unauthorized request"}})
                parts = [p for p in self.path[len(outer.base):].split("/") if p]
                if not parts and req.get("janus") == "list_sessions":
                    return self._json({"janus": "success", "sessions": [int(s) for s in outer.handles]})
                if len(parts) == 1 and req.get("janus") == "list_handles":
                    return self._json({"janus": "success", "handles": [int(h) for h in outer.handles.get(int(parts[0]), {})]})
                if len(parts) == 2 and req.get("janus") == "handle_info":
                    info = outer.handles.get(int(parts[0]), {}).get(int(parts[1]))
                    if info is None:
                        return self._json({"janus": "error", "error": {"code": 459, "reason": "No such handle"}})
                    return self._json({"janus": "success", "info": info})
                self._json({"janus": "error", "error": {"code": 400, "reason": "unexpected"}})

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
