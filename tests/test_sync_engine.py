"""SpotifySync / GrabberService with a fake Spotify client and a fake index: no network."""

import os

import pytest

from music_grabber.db import Database
from music_grabber.spotify import SpotifyAuthError, SpotifyError
from music_grabber.sync_engine import GrabberService, SpotifySync


def _t(sid, title, artist="Artist", position=0, isrc=""):
    return {"spotify_track_id": sid, "title": title, "artist": artist, "album": "Album",
            "album_artist": artist, "duration_ms": 200000, "isrc": isrc, "track_no": position + 1,
            "disc_no": 1, "year": 2020, "cover_url": "", "position": position, "added_at": "",
            "norm_title": title.lower(), "norm_artist": artist.lower()}


class FakeClient:
    def __init__(self, tracks, snapshot="s1", connected=True, fail=None):
        self.tracks, self.snapshot, self.connected, self.fail = tracks, snapshot, connected, fail
        self.fetches = 0
        self.disconnected = False

    def is_connected(self):
        return self.connected

    def get_playlist_meta(self, pid):
        if self.fail:
            raise self.fail
        return {"spotify_id": pid, "name": "Mix", "snapshot_id": self.snapshot, "image_url": "",
                "track_count": len(self.tracks), "owner": ""}

    def get_playlist_tracks(self, pid):
        self.fetches += 1
        return list(self.tracks)

    def disconnect(self):
        self.disconnected = True
        self.connected = False


class FakeIndex:
    def __init__(self, have=None, boom=False):
        self.have, self.boom, self.refreshed = dict(have or {}), boom, 0

    def refresh(self):
        self.refreshed += 1
        if self.boom:
            raise RuntimeError("share unmounted")

    def find_by_spotify_id(self, sid):
        return [{"file_path": self.have[sid]}] if sid in self.have else []

    def find_by_isrc(self, isrc):
        return []

    def find_candidates(self, na, nt):
        return []


class EmptyIndex(FakeIndex):
    def is_empty(self):
        return True


class Notifier:
    def __init__(self):
        self.sent = []

    def send_grabber(self, title, body, **kw):
        self.sent.append((title, body, kw))


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "mg.db"))


def _sync(db, client, index=None, notifier=None):
    return SpotifySync({"spotify_sync_minutes": 30}, db, index or FakeIndex(), client, notifier)


def test_sync_matches_have_and_auto_enqueues_missing(db):
    pid = db.add_playlist("PL1", "Mix")
    client = FakeClient([_t("s1", "Have It"), _t("s2", "Missing One", position=1)])
    sync = _sync(db, client, FakeIndex({"s1": "A/have.mp3"}))
    sync.sync_all()

    rows = {r["title"]: r for r in db.get_playlist_track_rows(pid)}
    assert rows["Have It"]["match_status"] == "have"
    assert rows["Have It"]["matched_file_path"] == "A/have.mp3"
    assert rows["Missing One"]["match_status"] == "missing"
    queue = db.get_queue()
    assert [q["spotify_track_id"] for q in queue] == ["s2"] and queue[0]["status"] == "pending"
    assert db.get_playlist(pid)["name"] == "Mix" and db.get_playlist(pid)["snapshot_id"] == "s1"


def test_unchanged_snapshot_does_not_refetch_but_rematches(db):
    pid = db.add_playlist("PL1", "Mix")
    client = FakeClient([_t("s1", "Song")])
    index = FakeIndex()
    sync = _sync(db, client, index)
    sync.sync_all()
    assert client.fetches == 1
    index.have["s1"] = "now/here.mp3"        # the library changed, the playlist did not
    sync.sync_all()
    assert client.fetches == 1 and index.refreshed == 2
    assert db.get_playlist_track_rows(pid)[0]["match_status"] == "have"


def test_a_brand_new_install_with_an_empty_library_still_queues(db):
    db.add_playlist("PL1", "Mix")
    sync = _sync(db, FakeClient([_t("s1", "One")]), EmptyIndex())
    sync.sync_all()
    assert len(db.get_queue()) == 1 and not sync.auto_queue_paused


def test_an_empty_library_index_queues_nothing_automatically(db, caplog):
    pid = db.add_playlist("PL1", "Mix")
    db.bump_grabbed_total()                                         # it has grabbed before (or imported)
    client = FakeClient([_t("s1", "One"), _t("s2", "Two", position=1)])
    sync = _sync(db, client, EmptyIndex())
    sync.sync_all()
    assert db.get_queue() == [] and sync.auto_queue_paused
    assert {r["match_status"] for r in db.get_playlist_track_rows(pid)} == {"missing"}
    assert sum("Library index is empty" in r.message for r in caplog.records) == 1
    sync.sync_all()                                                 # warns once, not on every pass
    assert sum("Library index is empty" in r.message for r in caplog.records) == 1
    sync.index = FakeIndex()                                        # files appear: back to normal
    sync.sync_all()
    assert len(db.get_queue()) == 2 and not sync.auto_queue_paused


def test_failed_or_skipped_items_are_not_auto_requeued(db):
    db.add_playlist("PL1", "Mix")
    client = FakeClient([_t("s1", "Song")])
    sync = _sync(db, client)
    sync.sync_all()
    (item,) = db.get_queue()
    db.transition(item["id"], "failed", "no match")
    sync.sync_all()
    assert len(db.get_queue()) == 1          # only an explicit user action retries


def test_disconnected_client_syncs_nothing(db):
    db.add_playlist("PL1", "Mix")
    client = FakeClient([_t("s1", "Song")], connected=False)
    _sync(db, client).sync_all()
    assert db.get_queue() == [] and client.fetches == 0


def test_index_refresh_failure_degrades_to_cached_matching(db):
    db.add_playlist("PL1", "Mix")
    index = FakeIndex({"s1": "x.mp3"}, boom=True)
    _sync(db, FakeClient([_t("s1", "Song")]), index).sync_all()
    assert db.get_queue() == []              # still matched against the cache: nothing to grab


def test_auth_error_disconnects_notifies_and_does_not_kill_the_loop(db):
    db.add_playlist("PL1", "Mix")
    n = Notifier()
    client = FakeClient([], fail=SpotifyAuthError("invalid_grant"))
    sync = _sync(db, client, notifier=n)
    with pytest.raises(SpotifyAuthError):
        sync.sync_all()                      # run() catches and logs this
    assert client.disconnected and sync.last_error == "invalid_grant"
    assert n.sent and n.sent[0][0] == "Spotify disconnected" and n.sent[0][2]["priority"] == "high"


def test_other_spotify_errors_are_recorded_and_the_next_playlist_still_syncs(db):
    db.add_playlist("PL1", "One")
    db.add_playlist("PL2", "Two")

    class Flaky(FakeClient):
        def get_playlist_meta(self, pid):
            if pid == "PL1":
                raise SpotifyError("503")
            return super().get_playlist_meta(pid)

    client = Flaky([_t("s9", "Song")])
    sync = _sync(db, client)
    sync.sync_all()
    assert sync.last_synced_epoch > 0
    assert [q["spotify_track_id"] for q in db.get_queue()] == ["s9"]


def test_service_seeds_watch_playlists_without_network(tmp_path):
    db = Database(str(tmp_path / "mg.db"))
    cfg = {"db_path": db.db_path, "music_dir": str(tmp_path / "music"),
           "watch_playlists": ["https://open.spotify.com/playlist/AAA111?si=x", "spotify:playlist:BBB222", "", "AAA111"]}
    service = GrabberService(cfg, db)
    assert service.seed_playlists() == 2
    assert service.seed_playlists() == 0     # idempotent
    assert sorted(p["spotify_id"] for p in db.list_playlists()) == ["AAA111", "BBB222"]


def test_service_uses_library_dir_for_the_filesystem_index(tmp_path):
    db = Database(str(tmp_path / "mg.db"))
    lib = tmp_path / "lib"
    lib.mkdir()
    service = GrabberService({"db_path": db.db_path, "music_dir": str(tmp_path / "raw"),
                              "library_dir": str(lib)}, db)
    assert service.index.root == str(lib) and service.index.source.root == str(lib)
    assert not os.path.exists(tmp_path / "raw")      # constructing creates nothing
