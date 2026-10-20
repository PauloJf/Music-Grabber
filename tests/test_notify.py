"""ntfy pings: headers, failure tolerance, the wording that points at the CLI."""

import os

from fake_servers import FakeServer, Reply
from test_worker import FakeProvider, _config

from music_grabber.db import Database
from music_grabber.notify import Notifier
from music_grabber.worker import GrabPipeline


def test_notifier_only_exists_when_configured():
    assert Notifier.from_config({}) is None
    assert Notifier.from_config({"ntfy_url": "http://x"}) is None
    assert Notifier.from_config({"ntfy_url": "http://x", "ntfy_topic": "t"}) is not None


def test_ping_headers_and_body_over_real_http():
    with FakeServer(lambda r: Reply(200)) as srv:
        Notifier(srv.url + "/", "my-topic").send_grabber(
            "Needs review", "body text", click_url="http://ui/inbox/3", priority="high", tags="mag",
            actions="view, Open, http://ui/inbox/3")
    (req,) = srv.requests
    assert req.method == "POST" and req.path == "/my-topic" and req.body == b"body text"
    assert req.headers["Title"] == "Needs review" and req.headers["Priority"] == "high"
    assert req.headers["Tags"] == "mag" and req.headers["Click"] == "http://ui/inbox/3"
    assert req.headers["Actions"].startswith("view,")


def test_non_latin1_titles_are_encoded_instead_of_dropping_the_ping():
    with FakeServer(lambda r: Reply(200)) as srv:
        Notifier(srv.url, "t").send_grabber("Grabbed \u2013 Beyonc\u00e9 \u266b", "ok")
    (req,) = srv.requests                                   # a ping arrived at all
    assert req.headers["Title"].startswith("=?UTF-8?B?")


def test_ntfy_being_down_or_erroring_never_raises():
    Notifier("http://127.0.0.1:9", "t").send_grabber("x", "y")            # connection refused
    with FakeServer(lambda r: Reply(500)) as srv:
        Notifier(srv.url, "t").send_grabber("x", "y")                       # HTTP 500


class Recorder:
    def __init__(self):
        self.sent = []

    def send_grabber(self, title, body, **kw):
        self.sent.append((title, body, kw))


def _ambiguous_pipeline(tmp_path, **cfg):
    c = _config(tmp_path, **cfg)
    db = Database(c["db_path"])
    rec = Recorder()
    pipe = GrabPipeline(c, db, providers=[FakeProvider(quality_match=False)], notifier=rec)
    item = db.enqueue_grab({"spotify_track_id": "s1", "title": "Blinding Lights", "artist": "The Weeknd",
                            "album": "After Hours", "duration_ms": 200000})
    return pipe, db, rec, item


def test_ambiguity_ping_tells_you_the_cli_command_and_links_the_ui_when_known(tmp_path):
    pipe, db, rec, item = _ambiguous_pipeline(tmp_path, ui_public_url="https://mg.example/")
    assert pipe.process_item(db.get_grab_item(item)) == "awaiting_user"
    title, body, kw = rec.sent[0]
    assert title == "Needs review: The Weeknd – Blinding Lights"
    assert f"python -m music_grabber inbox show {item}" in body
    assert kw["click_url"] == f"https://mg.example/inbox/{item}" and kw["priority"] == "default"


def test_no_click_url_without_a_public_url(tmp_path):
    pipe, db, rec, item = _ambiguous_pipeline(tmp_path)
    pipe.process_item(db.get_grab_item(item))
    assert rec.sent[0][2]["click_url"] == "" and rec.sent[0][2]["actions"] == ""


def _done_pipeline(tmp_path, **cfg):
    c = _config(tmp_path, **cfg)
    db = Database(c["db_path"])
    rec = Recorder()
    pipe = GrabPipeline(c, db, providers=[FakeProvider()], notifier=rec)
    item = db.enqueue_grab({"spotify_track_id": "s1", "title": "Song", "artist": "A", "album": "L",
                            "album_artist": "A", "duration_ms": 200000, "track_no": 1})
    return pipe, db, rec, item


def test_done_pings_can_be_switched_off_but_failures_still_ping(tmp_path):
    pipe, db, rec, item = _done_pipeline(tmp_path)
    pipe.process_item(db.get_grab_item(item))
    assert [t for t, _b, _k in rec.sent] == ["↓ A – Song"]
    pipe, db, rec, item = _done_pipeline(tmp_path / "quiet", ntfy_notify_done=False)
    assert pipe.process_item(db.get_grab_item(item)) == "done" and rec.sent == []


def test_rejected_deezer_credentials_alert_once_with_the_fallback_named(tmp_path):
    from music_grabber.providers.base import ProviderAuthError

    class Rejected(FakeProvider):
        name = "deezer"

        def search(self, meta, limit=8):
            raise ProviderAuthError("Deezer rejected the ARL")

    c = _config(tmp_path)
    db = Database(c["db_path"])
    rec = Recorder()
    pipe = GrabPipeline(c, db, providers=[Rejected(), FakeProvider()], notifier=rec)
    for i in range(3):
        item = db.enqueue_grab({"spotify_track_id": f"s{i}", "title": "Song", "artist": "A", "album": "L",
                                "album_artist": "A", "duration_ms": 200000, "track_no": i + 1})
        pipe.process_item(db.get_grab_item(item))
    logins = [s for s in rec.sent if s[0] == "Deezer login failed"]
    assert len(logins) == 1 and "fall back to fake" in logins[0][1] and logins[0][2]["priority"] == "high"
    assert os.path.isdir(c["music_dir"])


def test_ledger_rel_path_is_relative_to_the_library_root_even_in_intake_mode(tmp_path):
    c = _config(tmp_path)
    intake = os.path.join(c["music_dir"], "Music-raw")
    from music_grabber.analysis import IntakeFolderAnalysis
    db = Database(c["db_path"])
    pipe = GrabPipeline(c, db, providers=[FakeProvider()], analysis=IntakeFolderAnalysis(intake))
    item = db.enqueue_grab({"spotify_track_id": "s1", "title": "Song", "artist": "A", "album": "L",
                            "album_artist": "A", "duration_ms": 200000, "track_no": 1})
    assert pipe.process_item(db.get_grab_item(item)) == "done"
    (row,) = db.find_grabbed_by_spotify_id("s1")
    assert row["rel_path"] == "Music-raw/A/L/01 - Song.mp3"
    assert row["abs_path"].startswith(intake)
