"""A throwaway loopback HTTP server to act as a fake peer (Mediarium, BPM Tagger,
Navidrome/OpenSubsonic). Tests give it a handler; it records every request."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class Request:
    def __init__(self, method, path, query, headers, body, query_all=None):
        self.method, self.path, self.query, self.headers, self.body = method, path, query, headers, body
        self.query_all = query_all or {k: [v] for k, v in query.items()}     # repeated keys (songId=1&songId=2)

    def json(self):
        return json.loads(self.body or b"{}")


class Reply:
    def __init__(self, status=200, data=None, headers=None, raw=None):
        self.status, self.data, self.headers, self.raw = status, data, headers or {}, raw


class FakeServer:
    """``handler(request) -> Reply`` (or a dict for a 200 JSON reply)."""

    def __init__(self, handler):
        self.requests: list[Request] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def _do(self):
                u = urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                allq = parse_qs(u.query, keep_blank_values=True)
                req = Request(self.command, u.path, {k: v[0] for k, v in allq.items()},
                              dict(self.headers), self.rfile.read(length) if length else b"", allq)
                outer.requests.append(req)
                rep = handler(req)
                if not isinstance(rep, Reply):
                    rep = Reply(200, rep)
                body = rep.raw if rep.raw is not None else json.dumps(rep.data if rep.data is not None else {}).encode()
                self.send_response(rep.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for k, v in rep.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _do

            def log_message(self, *a):
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}"
        self._thread = threading.Thread(target=lambda: self._srv.serve_forever(poll_interval=0.01), daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *a):
        self._srv.shutdown()
        self._srv.server_close()

    def calls(self, path):
        return [r for r in self.requests if r.path == path]


class FakeTimers:
    """A manual clock plus a stand-in for threading.Timer, so debounce logic is tested
    without sleeping. ``advance(dt)`` moves time and fires due timers in order."""

    def __init__(self):
        self.now = 1000.0
        self.timers = []

    def clock(self):
        return self.now

    def factory(self, delay, fn):
        t = _Timer(self, self.now + delay, fn)
        self.timers.append(t)
        return t

    def advance(self, dt):
        end = self.now + dt
        while True:
            due = sorted((t for t in self.timers if t.active and t.at <= end), key=lambda t: t.at)
            if not due:
                break
            t = due[0]
            t.active = False
            self.now = max(self.now, t.at)
            t.fn()
        self.now = end

    @property
    def armed(self):
        return [t for t in self.timers if t.active]


class _Timer:
    def __init__(self, owner, at, fn):
        self.owner, self.at, self.fn, self.active, self.daemon = owner, at, fn, False, False

    def start(self):
        self.active = True

    def cancel(self):
        self.active = False
