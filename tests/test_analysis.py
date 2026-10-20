"""PostDownloadAnalysis implementations: intake folder, BPM Tagger API; BPM tag reader."""

import logging

import pytest
from audio_fixtures import write_flac
from fake_servers import FakeServer, FakeTimers, Reply
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TBPM

from music_grabber import analysis as A
from music_grabber.analysis import PlacedFile

PW = "adm1n-password-value"


def _placed(path="/intake/a.mp3"):
    return PlacedFile(path=path, rel_path="a.mp3", meta={})


def test_no_analysis_files_under_music_dir():
    assert A.NoAnalysis().destination_root({"music_dir": "/m"}) == "/m"
    assert A.NoAnalysis().after_place(_placed()).ok


def test_intake_files_into_the_intake_dir_and_does_not_wait_by_default():
    a = A.IntakeFolderAnalysis("/music/Music-raw")
    assert a.destination_root({"music_dir": "/music"}) == "/music/Music-raw"
    res = a.after_place(_placed())
    assert res.ok and "BPM Tagger will analyse" in res.detail


def test_intake_wait_polls_until_the_bpm_tag_appears():
    clock = {"t": 0.0}
    reads = iter([None, None, 128.0])
    a = A.IntakeFolderAnalysis("/i", wait_seconds=30, poll_seconds=2, bpm_reader=lambda p: next(reads),
                               sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
                               clock=lambda: clock["t"])
    res = a.after_place(_placed())
    assert res.detail == "bpm: 128" and res.extra["bpm"] == 128.0 and clock["t"] == 4.0


def test_intake_wait_gives_up_without_failing():
    clock = {"t": 0.0}
    a = A.IntakeFolderAnalysis("/i", wait_seconds=5, poll_seconds=2, bpm_reader=lambda p: None,
                               sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
                               clock=lambda: clock["t"])
    res = a.after_place(_placed())
    assert res.ok and "not yet" in res.detail and clock["t"] >= 5


def test_read_bpm_tag_never_modifies_the_file(tmp_path):
    import os
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"\x00" * 64)
    tags = ID3()
    tags["TBPM"] = TBPM(encoding=3, text="123")
    tags.save(str(mp3))
    flac = tmp_path / "a.flac"
    write_flac(flac)
    audio = FLAC(str(flac))
    audio["BPM"] = "98"
    audio.save()
    os.utime(mp3, (5, 5))
    os.utime(flac, (5, 5))
    assert A.read_bpm_tag(str(mp3)) == 123.0 and A.read_bpm_tag(str(flac)) == 98.0
    assert os.stat(mp3).st_mtime == 5 and os.stat(flac).st_mtime == 5
    assert A.read_bpm_tag(str(tmp_path / "missing.mp3")) is None
    bare = tmp_path / "bare.flac"
    write_flac(bare)
    assert A.read_bpm_tag(str(bare)) is None


# ── API analysis ──────────────────────────────────────────────────────────────
def bpm_tagger(*, login=None, scan=None, csrf="tok123"):
    """A fake BPM Tagger: /api/login then /api/scan/start (X-CSRF-Token required)."""
    state = {"logged_in": False}

    def handler(req):
        if req.path == "/api/login":
            if login is not None:
                return login
            if req.json().get("password") != PW:
                return Reply(401, {"ok": False, "error": "invalid_password"})
            state["logged_in"] = True
            return {"ok": True, "role": "admin", "csrf_token": csrf}
        if req.path == "/api/scan/start":
            if not state["logged_in"] or req.headers.get("X-CSRF-Token") != csrf:
                return Reply(401 if not state["logged_in"] else 403, {"error": "unauthorized"})
            return scan if scan is not None else {"ok": True}
        return Reply(404)

    handler.state = state
    return handler


def test_client_logs_in_once_and_requests_a_scan():
    h = bpm_tagger()
    with FakeServer(h) as srv:
        c = A.BpmTaggerClient(srv.url, PW)
        assert c.trigger_scan() == "scan started"
        assert c.trigger_scan() == "scan started"
        assert len(srv.calls("/api/login")) == 1 and len(srv.calls("/api/scan/start")) == 2


def test_client_treats_already_running_as_success():
    with FakeServer(bpm_tagger(scan={"ok": False, "error": "scan already running"})) as srv:
        assert A.BpmTaggerClient(srv.url, PW).trigger_scan() == "scan already running"


def test_client_relogs_in_when_the_session_expired():
    h = bpm_tagger()
    with FakeServer(h) as srv:
        c = A.BpmTaggerClient(srv.url, PW)
        c.trigger_scan()
        h.state["logged_in"] = False                  # the session expired server-side
        assert c.trigger_scan() == "scan started"
        assert len(srv.calls("/api/login")) == 2


def test_totp_and_bad_password_are_permanent_auth_errors():
    with FakeServer(bpm_tagger(login=Reply(401, {"ok": False, "error": "totp_required"}))) as srv:
        with pytest.raises(A.BpmTaggerAuthError, match="two-factor"):
            A.BpmTaggerClient(srv.url, PW).trigger_scan()
    with FakeServer(bpm_tagger()) as srv:
        with pytest.raises(A.BpmTaggerAuthError, match="rejected the admin password"):
            A.BpmTaggerClient(srv.url, "wrong").trigger_scan()


def test_lockout_is_transient_not_permanent():
    with FakeServer(bpm_tagger(login=Reply(429, {"ok": False, "error": "locked_out"}))) as srv:
        with pytest.raises(RuntimeError, match="locked out") as e:
            A.BpmTaggerClient(srv.url, PW).trigger_scan()
        assert not isinstance(e.value, A.BpmTaggerAuthError)


def _api(srv_url, ft, **kw):
    return A.ApiAnalysis("/music/Music-raw", A.BpmTaggerClient(srv_url, PW), debounce=10, max_wait=60,
                         min_interval=30, clock=ft.clock, timer_factory=ft.factory, **kw)


def test_api_analysis_coalesces_an_album_into_one_scan_request():
    ft = FakeTimers()
    with FakeServer(bpm_tagger()) as srv:
        a = _api(srv.url, ft)
        assert a.destination_root({}) == "/music/Music-raw"
        for i in range(8):
            res = a.after_place(_placed(f"/music/Music-raw/{i}.mp3"))
            ft.advance(1)
        assert "scan requested (coalesced)" in res.detail
        assert srv.requests == []
        ft.advance(15)
        assert len(srv.calls("/api/scan/start")) == 1


def test_api_analysis_disables_itself_on_auth_failure_with_one_clear_warning(caplog):
    caplog.set_level(logging.INFO)
    ft = FakeTimers()
    with FakeServer(bpm_tagger(login=Reply(401, {"ok": False, "error": "totp_required"}))) as srv:
        a = _api(srv.url, ft)
        a.after_place(_placed())
        ft.advance(15)
        assert a.disabled
        a.after_place(_placed())
        ft.advance(500)
        assert len(srv.calls("/api/login")) == 1                 # not hammered
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1 and "two-factor" in warns[0] and PW not in warns[0]


def test_api_analysis_retries_transient_failures_with_backoff(caplog):
    ft = FakeTimers()
    replies = iter([Reply(500), None])
    h = bpm_tagger()

    def flaky(req):
        if req.path == "/api/scan/start":
            r = next(replies)
            if r is not None:
                return r
        return h(req)

    with FakeServer(flaky) as srv:
        a = _api(srv.url, ft)
        a.after_place(_placed())
        ft.advance(15)
        assert len(srv.calls("/api/scan/start")) == 1 and a.coalescer.pending
        ft.advance(70)
        assert len(srv.calls("/api/scan/start")) == 2 and not a.coalescer.pending
