"""OpenSubsonicSource against a loopback fake of BPM Tagger's / Navidrome's Subsonic API."""

import hashlib

import pytest
from fake_servers import FakeServer, Reply

from music_grabber.db import Database
from music_grabber.library import LibraryIndex, LibrarySourceError
from music_grabber.library_opensubsonic import OpenSubsonicSource, song_to_track, track_token
from music_grabber.matching import library_match


def _song(i, **kw):
    s = {"id": f"so-{i}", "title": f"Song {i}", "artist": "The Band", "album": "LP",
         "albumArtist": "The Band", "track": i, "discNumber": 1, "year": 2021, "duration": 200 + i,
         "path": f"The Band/LP/{i:02d} - Song {i}.flac", "isrc": [f"GBAAA00000{i:02d}"]}
    s.update(kw)
    return s


def ok(**body):
    return {"subsonic-response": {"status": "ok", "version": "1.16.1", **body}}


def err(code, message):
    return {"subsonic-response": {"status": "failed", "error": {"code": code, "message": message}}}


def library_server(songs, *, empty_query_ok=True, key="K", cap=1000):
    """search3 pages through all songs on an empty query, like both real servers."""
    albums = {}
    for s in songs:
        albums.setdefault(s["album"], []).append(s)

    def handler(req):
        q = req.query
        if q.get("apiKey") != key and not q.get("t"):
            return err(40, "Wrong username or password")
        if req.path == "/rest/search3":
            if not empty_query_ok:
                return ok(searchResult3={})
            off, n = int(q.get("songOffset", 0)), min(int(q.get("songCount", 20)), cap)
            return ok(searchResult3={"song": songs[off:off + n]})
        if req.path == "/rest/getAlbumList2":
            names = sorted(albums)
            off, n = int(q.get("offset", 0)), int(q.get("size", 10))
            return ok(albumList2={"album": [{"id": a} for a in names[off:off + n]]})
        if req.path == "/rest/getAlbum":
            return ok(album={"song": albums[q["id"]]})
        return Reply(404)

    return handler


def test_song_mapping_handles_isrc_list_seconds_and_paths():
    t = song_to_track(_song(3, path="\\The Band\\LP\\03.flac", isrc=["gb1", "gb2"]))
    assert t.path == "The Band/LP/03.flac" and t.isrc == "gb1" and t.duration_ms == 203000
    assert (t.track_no, t.disc_no, t.year, t.album_artist) == (3, 1, 2021, "The Band")
    assert song_to_track({"title": "no path"}) is None
    assert song_to_track(_song(1, isrc="plain")).isrc == "plain"
    assert song_to_track({"path": "a.mp3", "duration": "x"}).duration_ms is None


def test_token_changes_with_the_stored_fields_only():
    a = song_to_track(_song(1))
    assert track_token(a) == track_token(song_to_track(_song(1, id="other-id", playCount=9)))
    assert track_token(a) != track_token(song_to_track(_song(1, title="Renamed")))


def test_walk_pages_through_everything_and_loads_the_cache(tmp_path):
    songs = [_song(i) for i in range(1, 8)]
    with FakeServer(library_server(songs)) as srv:
        db = Database(str(tmp_path / "mg.db"))
        idx = LibraryIndex(db, OpenSubsonicSource(srv.url, api_key="K", page=3))
        stats = idx.refresh()
        assert stats["seen"] == 7 and stats["updated"] == 7
        assert [r.query["songOffset"] for r in srv.calls("/rest/search3")] == ["0", "3", "6"]
        assert srv.requests[0].query["query"] == "" and srv.requests[0].query["songCount"] == "3"
        assert db.library_count() == 7
        assert idx.find_by_isrc("gbaaa0000003")[0]["path"] == "The Band/LP/03 - Song 3.flac"
        # A second pass changes nothing: tokens match, so nothing is rewritten.
        assert idx.refresh()["updated"] == 0


def test_matching_through_the_http_index(tmp_path):
    with FakeServer(library_server([_song(1)])) as srv:
        db = Database(str(tmp_path / "mg.db"))
        idx = LibraryIndex(db, OpenSubsonicSource(srv.url, api_key="K"))
        idx.refresh()
        assert library_match({"title": "x", "artist": "y", "isrc": "GBAAA0000001"}, idx) == \
            "The Band/LP/01 - Song 1.flac"
        sp = {"title": "Song 1", "artist": "The Band", "album": "LP", "duration_ms": 201000, "isrc": ""}
        assert library_match(sp, idx) == "The Band/LP/01 - Song 1.flac"
        assert library_match({"title": "Nope", "artist": "Nobody", "isrc": ""}, idx) is None


def test_api_key_and_token_auth_are_never_combined(tmp_path):
    with FakeServer(library_server([_song(1)], key="K")) as srv:
        OpenSubsonicSource(srv.url, api_key="K").scan({}).__next__()
        q = srv.requests[-1].query
        assert q["apiKey"] == "K" and "u" not in q and "t" not in q
        OpenSubsonicSource(srv.url, user="neo", password="pw").scan({}).__next__()
        q = srv.requests[-1].query
        assert q["u"] == "neo" and "apiKey" not in q
        assert q["t"] == hashlib.md5(("pw" + q["s"]).encode()).hexdigest()
        assert "pw" not in q.values()


def test_empty_query_refused_falls_back_to_the_album_walk(tmp_path):
    songs = [_song(i, album=f"LP{i % 2}") for i in range(1, 6)]
    with FakeServer(library_server(songs, empty_query_ok=False)) as srv:
        db = Database(str(tmp_path / "mg.db"))
        idx = LibraryIndex(db, OpenSubsonicSource(srv.url, api_key="K", page=10))
        assert idx.refresh()["seen"] == 5
        assert srv.calls("/rest/getAlbumList2") and len(srv.calls("/rest/getAlbum")) == 2


def test_server_error_raises_and_leaves_the_cache_alone(tmp_path):
    with FakeServer(library_server([_song(1), _song(2)])) as srv:
        db = Database(str(tmp_path / "mg.db"))
        idx = LibraryIndex(db, OpenSubsonicSource(srv.url, api_key="K"))
        idx.refresh()
    bad = OpenSubsonicSource(srv.url, api_key="K", timeout=1)     # server is gone now
    idx.source = bad
    with pytest.raises(LibrarySourceError, match="request failed"):
        idx.refresh()
    assert db.library_count() == 2


def test_wrong_key_is_a_library_error_without_leaking_the_key(tmp_path):
    with FakeServer(library_server([_song(1)], key="right")) as srv:
        idx = LibraryIndex(Database(str(tmp_path / "mg.db")),
                           OpenSubsonicSource(srv.url, api_key="wrong-secret-key"))
        with pytest.raises(LibrarySourceError) as e:
            idx.refresh()
        assert "Wrong username or password" in str(e.value) and "wrong-secret-key" not in str(e.value)


def test_http_500_and_html_replies_are_library_errors(tmp_path):
    for reply in (Reply(500), Reply(200, raw=b"<html>login</html>")):
        with FakeServer(lambda r, reply=reply: reply) as srv:
            idx = LibraryIndex(Database(str(tmp_path / f"{id(reply)}.db")),
                               OpenSubsonicSource(srv.url, api_key="K"))
            with pytest.raises(LibrarySourceError):
                idx.refresh()


def test_an_empty_listing_never_prunes_a_populated_cache(tmp_path):
    songs = [_song(i) for i in range(1, 31)]
    with FakeServer(library_server(songs)) as srv:
        db = Database(str(tmp_path / "mg.db"))
        idx = LibraryIndex(db, OpenSubsonicSource(srv.url, api_key="K", page=100))
        idx.refresh()
        assert db.library_count() == 30
    with FakeServer(library_server([], empty_query_ok=True)) as srv2:    # server lost its data
        idx.source = OpenSubsonicSource(srv2.url, api_key="K")
        stats = idx.refresh()
    assert stats["pruned"] is False and db.library_count() == 30


def test_removed_songs_are_pruned_on_a_healthy_walk(tmp_path):
    songs = [_song(i) for i in range(1, 31)]
    with FakeServer(library_server(songs)) as srv:
        db = Database(str(tmp_path / "mg.db"))
        idx = LibraryIndex(db, OpenSubsonicSource(srv.url, api_key="K", page=100))
        idx.refresh()
    with FakeServer(library_server(songs[:28])) as srv2:
        idx.source = OpenSubsonicSource(srv2.url, api_key="K", page=100)
        assert idx.refresh()["removed"] == 2
    assert db.library_count() == 28
