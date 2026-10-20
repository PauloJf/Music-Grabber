"""Post-import hooks: Mediarium and Navidrome, coalescing, error handling. Mediarium is
faked (a recording session, or a loopback server); nothing leaves the machine."""

import logging
import os

import pytest
import requests
from fake_servers import FakeServer, FakeTimers, Reply

from music_grabber import hooks as H
from music_grabber.analysis import PlacedFile
from music_grabber.db import Database
from music_grabber.wiring import ConfigError

KEY = "s3cr3t-api-key-value"


class Resp:
    def __init__(self, status=200, headers=None):
        self.status_code, self.headers = status, headers or {}


class Session:
    """A stand-in for `requests` whose answers are scripted; records each POST."""

    def __init__(self, *answers):
        self.answers, self.posts = list(answers), []

    def post(self, url, headers=None, timeout=None):
        self.posts.append((url, headers))
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


def _placed(i=0):
    return PlacedFile(path=f"/m/{i}.mp3", rel_path=f"{i}.mp3", meta={})


def _hook(ft, session, **kw):
    return H.MediariumHook("http://mediarium.test/", KEY, session=session, debounce=10,
                           max_wait=60, min_interval=30, clock=ft.clock,
                           timer_factory=ft.factory, **kw)


def test_album_of_tracks_becomes_one_scan_request():
    ft, s = FakeTimers(), Session(Resp(200))
    hook = _hook(ft, s)
    for i in range(12):
        hook.on_placed(_placed(i))
        ft.advance(2)
    assert s.posts == []                         # still within the debounce
    ft.advance(15)
    assert len(s.posts) == 1
    url, headers = s.posts[0]
    assert url == "http://mediarium.test/api/music/import/scan" and headers == {"X-Api-Key": KEY}


def test_files_placed_after_a_scan_get_exactly_one_follow_up():
    ft, s = FakeTimers(), Session(Resp(200))
    hook = _hook(ft, s)
    hook.on_placed(_placed())
    ft.advance(11)
    assert len(s.posts) == 1
    hook.on_placed(_placed(1))
    hook.on_placed(_placed(2))
    ft.advance(15)
    assert len(s.posts) == 1                     # min interval (30 s) still running
    ft.advance(30)
    assert len(s.posts) == 2


def test_404_means_music_module_off_pauses_without_retrying(caplog):
    caplog.set_level(logging.INFO)
    ft, s = FakeTimers(), Session(Resp(404))
    hook = _hook(ft, s)
    hook.on_placed(_placed())
    ft.advance(11)
    assert len(s.posts) == 1 and not hook.disabled
    assert any("music module looks off" in r.message and r.levelno == logging.INFO for r in caplog.records)
    assert not hook.coalescer.pending            # no retry loop
    hook.on_placed(_placed(1))
    ft.advance(200)
    assert len(s.posts) == 1                     # paused for the hour: no call, no error
    ft.advance(3700)
    hook.on_placed(_placed(2))
    ft.advance(15)
    assert len(s.posts) == 2                     # the pause expired


@pytest.mark.parametrize("status", [401, 403])
def test_401_403_log_one_clear_line_and_disable(status, caplog):
    caplog.set_level(logging.INFO)
    ft, s = FakeTimers(), Session(Resp(status))
    hook = _hook(ft, s)
    hook.on_placed(_placed())
    ft.advance(11)
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1
    msg = warns[0].getMessage()
    assert f"HTTP {status}" in msg and "/api/music/import/scan" in msg and "not allowed" in msg
    assert KEY not in msg
    assert hook.disabled and not hook.coalescer.pending
    hook.on_placed(_placed(1))
    ft.advance(500)
    assert len(s.posts) == 1                     # disabled until restart: not hammered


def test_5xx_and_network_errors_back_off_exponentially_then_recover(caplog):
    caplog.set_level(logging.INFO)
    ft = FakeTimers()
    s = Session(Resp(503), requests.ConnectionError("down"), Resp(200))
    hook = _hook(ft, s)
    hook.on_placed(_placed())
    ft.advance(11)
    assert len(s.posts) == 1 and hook.coalescer.pending      # failed, will retry
    ft.advance(61)
    assert len(s.posts) == 2                                 # 60 s later: network error
    ft.advance(100)
    assert len(s.posts) == 2                                 # next wait is 120 s
    ft.advance(30)
    assert len(s.posts) == 3 and not hook.coalescer.pending  # recovered
    assert all(KEY not in r.getMessage() for r in caplog.records)
    assert hook._failures == 0


def test_429_honours_retry_after():
    ft, s = FakeTimers(), Session(Resp(429, {"Retry-After": "90"}), Resp(200))
    hook = _hook(ft, s)
    hook.on_placed(_placed())
    ft.advance(11)
    assert len(s.posts) == 1
    ft.advance(80)
    assert len(s.posts) == 1
    ft.advance(15)
    assert len(s.posts) == 2


def test_authorization_header_style_sends_a_bearer_token():
    ft, s = FakeTimers(), Session(Resp(200))
    hook = _hook(ft, s, key_header="Authorization")
    hook.on_placed(_placed())
    ft.advance(11)
    assert s.posts[0][1] == {"Authorization": f"Bearer {KEY}"}


def test_real_http_round_trip_with_a_loopback_server():
    """One end-to-end pass over real sockets (loopback), real timers, tiny debounce."""
    import time
    with FakeServer(lambda r: Reply(200, {"running": True})) as srv:
        hook = H.MediariumHook(srv.url, KEY, debounce=0.05, max_wait=1, min_interval=0)
        try:
            for i in range(5):
                hook.on_placed(_placed(i))
            deadline = time.time() + 5
            while not srv.requests and time.time() < deadline:
                time.sleep(0.02)
            time.sleep(0.2)
        finally:
            hook.close()
        assert len(srv.requests) == 1
        req = srv.requests[0]
        assert req.method == "POST" and req.path == "/api/music/import/scan"
        assert req.headers["X-Api-Key"] == KEY


class SubsonicSession:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return r


class SubResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


def test_navidrome_hook_coalesces_and_uses_token_auth():
    ft = FakeTimers()
    sess = SubsonicSession([SubResp(200, {"subsonic-response": {"status": "ok"}})])
    hook = H.NavidromeHook("http://nd.test", "u", "pw", session=sess, debounce=10, max_wait=60,
                           min_interval=30, clock=ft.clock, timer_factory=ft.factory)
    for i in range(6):
        hook.on_placed(_placed(i))
    ft.advance(11)
    assert len(sess.calls) == 1
    url, params = sess.calls[0]
    assert url == "http://nd.test/rest/startScan"
    assert params["u"] == "u" and params["t"] and params["s"] and "pw" not in params.values()


def test_navidrome_hook_retries_after_an_error():
    ft = FakeTimers()
    sess = SubsonicSession([SubResp(500), SubResp(200, {"subsonic-response": {"status": "ok"}})])
    hook = H.NavidromeHook("http://nd.test", "u", "pw", session=sess, debounce=1, max_wait=5,
                           min_interval=0, clock=ft.clock, timer_factory=ft.factory)
    hook.on_placed(_placed())
    ft.advance(2)
    assert len(sess.calls) == 1 and hook.coalescer.pending
    ft.advance(61)
    assert len(sess.calls) == 2 and not hook.coalescer.pending


# ── configuration ─────────────────────────────────────────────────────────────
def _cfg(**kw):
    return {"post_import_hooks": [], "analysis": "none", **kw}


def test_no_hooks_by_default():
    assert H.build_hooks(_cfg()).names == []


def test_mediarium_listed_but_unconfigured_is_a_clear_error():
    with pytest.raises(ConfigError, match="MEDIARIUM_URL and MEDIARIUM_API_KEY"):
        H.build_hooks(_cfg(post_import_hooks=["mediarium"], mediarium_url="http://m"))


def test_unknown_hook_name_is_rejected():
    with pytest.raises(ConfigError, match="unknown post-import hook 'plex'"):
        H.build_hooks(_cfg(post_import_hooks=["plex"]))


def test_hooks_are_built_with_the_configured_timings():
    runner = H.build_hooks(_cfg(post_import_hooks=["mediarium", "navidrome"],
                                mediarium_url="http://m", mediarium_api_key=KEY,
                                navidrome_url="http://n", navidrome_user="u", navidrome_pass="p",
                                hook_debounce_seconds=7, hook_max_wait_seconds=70,
                                hook_min_interval_seconds=9))
    try:
        assert runner.names == ["mediarium", "navidrome"]
        assert runner.hooks[0].coalescer._debounce == 7 and runner.hooks[0].coalescer._max_wait == 70
    finally:
        runner.close()


def test_navidrome_hook_with_intake_analysis_warns(caplog):
    caplog.set_level(logging.WARNING)
    runner = H.build_hooks(_cfg(post_import_hooks=["navidrome"], analysis="intake",
                                navidrome_url="http://n", navidrome_user="u", navidrome_pass="p"))
    runner.close()
    assert any("before the BPM tag exists" in r.getMessage() for r in caplog.records)


def test_a_failing_hook_never_stops_the_others_or_the_grab():
    class Boom:
        name = "boom"

        def on_placed(self, p):
            raise RuntimeError("x")

        def close(self):
            pass

    seen = []

    class Ok(Boom):
        name = "ok"

        def on_placed(self, p):
            seen.append(p)

    H.PostImportHooks([Boom(), Ok()]).on_placed(_placed())
    assert len(seen) == 1


def test_worker_fires_hooks_once_per_album_through_the_pipeline(tmp_path):
    """Five tracks filed by the real pipeline → one Mediarium request."""
    from test_worker import FakeProvider, _config

    from music_grabber.worker import GrabPipeline
    ft, s = FakeTimers(), Session(Resp(200))
    hook = _hook(ft, s)
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    pipe = GrabPipeline(cfg, db, providers=[FakeProvider()], on_placed=[hook.on_placed])
    for i in range(5):
        item = db.enqueue_grab({"spotify_track_id": f"s{i}", "title": f"Song {i}", "artist": "A",
                                "album": "Album", "album_artist": "A", "duration_ms": 200000,
                                "track_no": i + 1})
        assert pipe.process_item(db.get_grab_item(item)) == "done"
        ft.advance(1)
    assert s.posts == []
    ft.advance(15)
    assert len(s.posts) == 1
    assert len(os.listdir(os.path.join(cfg["music_dir"], "A", "Album"))) == 5
