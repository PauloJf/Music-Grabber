"""Browsing Deezer's catalogue and queueing from it, with the Deezer API faked."""

import pytest
from test_api import H, TOKEN
from test_ops import FakeClient

from music_grabber import deezer_catalog as dc
from music_grabber.api import create_app
from music_grabber.db import Database
from music_grabber.ops import Ops, OpsError
from music_grabber.sync_engine import GrabberService


def dz_track(i, title=None, artist="Dua Lipa", album="Future Nostalgia", **kw):
    return {"id": 100 + i, "title": title or f"Song {i}", "duration": 200 + i, "preview": f"https://cdnt-preview.dzcdn.net/{i}.mp3",
            "artist": {"id": 7, "name": artist},
            "album": {"id": 55, "title": album, "cover_medium": "https://cdn-images.dzcdn.net/c.jpg", "release_date": "2020-03-27"},
            "explicit_lyrics": False, **kw}


ARTIST = {"id": 7, "name": "Dua Lipa", "picture_medium": "https://cdn-images.dzcdn.net/a.jpg", "nb_fan": 1000, "nb_album": 3}
ALBUM = {"id": 55, "title": "Future Nostalgia", "release_date": "2020-03-27", "nb_tracks": 3, "record_type": "album",
         "cover_medium": "https://cdn-images.dzcdn.net/c.jpg", "artist": {"id": 7, "name": "Dua Lipa"},
         "tracks": {"data": [{"id": 101, "title": "Levitating", "duration": 203, "preview": "p", "artist": {"id": 7, "name": "Dua Lipa"}, "track_position": 5},
                             {"id": 102, "title": "Physical", "duration": 194, "artist": {"id": 7, "name": "Dua Lipa"}},
                             {"id": 103, "title": "Hallucinate", "duration": 208, "artist": {"id": 7, "name": "Dua Lipa"}}]}}

CALLS = []


def fake_get(path, params=None):
    CALLS.append((path, params))
    if path == "search":
        return {"data": [dz_track(1, "Levitating"), dz_track(2, "Physical")]}
    if path == "search/artist":
        return {"data": [ARTIST]}
    if path == "search/album":
        return {"data": [{**ALBUM, "tracks": None}]}
    if path == "artist/7":
        return ARTIST
    if path == "artist/7/top":
        return {"data": [dz_track(1, "Levitating")]}
    if path == "artist/7/albums":
        return {"data": [{"id": 55, "title": "Future Nostalgia", "release_date": "2020-03-27", "cover_medium": "c", "record_type": "album"}]}
    if path == "artist/7/related":
        return {"data": [{"id": 8, "name": "Dojja"}]}
    if path == "album/55":
        return ALBUM
    if path.startswith("track/"):
        return {"isrc": "GBAHT2000001"}
    if path == "artist/404":
        return {"error": {"type": "DataException", "message": "no data", "code": 800}}
    raise AssertionError(path)


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    CALLS.clear()
    monkeypatch.setattr(dc, "_get", fake_get)


@pytest.fixture
def ops(tmp_path):
    cfg = {"db_path": str(tmp_path / "mg.db"), "music_dir": str(tmp_path / "music"), "provider_order": "ytdlp",
           "output_format": "mp3-128", "auto_accept_threshold": 0.85, "ask_threshold": 0.55,
           "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}"}
    svc = GrabberService(cfg, Database(cfg["db_path"]))
    svc.client = FakeClient(connected=False)
    svc.sync.client = svc.client
    return Ops(svc, cfg)


def test_views_convert_units_and_ids():
    t = dc.track_view(dz_track(1))
    assert t["dz_id"] == "101" and t["duration_ms"] == 201000 and t["year"] == 2020 and t["artist_id"] == "7"
    assert t["preview_url"].startswith("https://cdnt-preview") and t["cover_url"].endswith("c.jpg")
    assert dc.album_view(ALBUM)["track_count"] == 3 and dc.artist_view(ARTIST)["fans"] == 1000


def test_search_all_three_kinds_and_bad_kind(ops):
    assert [t["title"] for t in ops.browse_search("dua", "track")["results"]] == ["Levitating", "Physical"]
    assert ops.browse_search("dua", "artist")["results"][0]["name"] == "Dua Lipa"
    assert ops.browse_search("dua", "album")["results"][0]["title"] == "Future Nostalgia"
    with pytest.raises(OpsError) as e:
        ops.browse_search("  ")
    assert e.value.code == "query_required"
    with pytest.raises(OpsError) as e:
        ops.browse_search("x", "playlist")
    assert e.value.http == 502


def test_artist_page_has_top_albums_related_and_degrades(ops, monkeypatch):
    p = ops.browse_artist("7")
    assert p["artist"]["name"] == "Dua Lipa" and p["top"][0]["state"] == {"kind": "none"}
    assert p["albums"][0]["title"] == "Future Nostalgia" and p["related"][0]["name"] == "Dojja"
    real = fake_get

    def flaky(path, params=None):
        if path.endswith("/related"):
            raise RuntimeError("boom")
        return real(path, params)

    monkeypatch.setattr(dc, "_get", flaky)
    assert ops.browse_artist("7")["related"] == []                 # one section failing is not fatal
    with pytest.raises(OpsError) as e:
        ops.browse_artist("404")
    assert e.value.code == "deezer_unreachable" and "no data" in str(e.value)


def test_deezer_down_is_a_502_with_a_readable_message(ops, monkeypatch):
    def down(path, params=None):
        raise ConnectionError("nope")
    monkeypatch.setattr(dc, "_get", down)
    with pytest.raises(OpsError) as e:
        ops.browse_search("x")
    assert e.value.http == 502 and "unreachable" in str(e.value)


def test_album_page_numbers_tracks_and_inherits_album_fields(ops):
    p = ops.browse_album("55")
    assert [t["track_no"] for t in p["tracks"]] == [5, 2, 3]       # explicit position wins, else order
    assert all(t["album"] == "Future Nostalgia" and t["year"] == 2020 and t["artist"] == "Dua Lipa" for t in p["tracks"])


def test_states_reflect_queue_progress_inbox_failure_and_library(ops):
    def put(title, status, **kw):
        i = ops.db.enqueue_grab({"title": title, "artist": "Dua Lipa", "spotify_track_id": title})
        if status != "pending":
            ops.db.transition(i, status, "t")
        ops.db.update_grab(i, **kw)
        return i
    put("Levitating", "downloading", progress=0.4)
    put("Physical", "failed", error="boom")
    res = {t["title"]: t["state"] for t in ops.browse_search("d")["results"]}
    assert res["Levitating"]["kind"] == "active" and res["Levitating"]["progress"] == 0.4
    assert res["Physical"]["kind"] == "failed" and res["Physical"]["error"] == "boom"
    ops.db.transition(ops.db.get_queue("failed")[0]["id"], "done", "x")
    assert {t["title"]: t["state"]["kind"] for t in ops.browse_search("d")["results"]}["Physical"] == "library"
    put("Hallucinate", "awaiting_user")
    assert {t["title"]: t["state"]["kind"] for t in ops.browse_album("55")["tracks"]}["Hallucinate"] == "review"


def test_library_files_show_as_in_library(ops, tmp_path):
    from audio_fixtures import write_flac
    from mutagen.flac import FLAC
    f = tmp_path / "music" / "Dua Lipa" / "FN" / "05 - Levitating.flac"
    f.parent.mkdir(parents=True)
    write_flac(f, seconds=203)
    a = FLAC(str(f))
    a["title"], a["artist"], a["album"] = "Levitating", "Dua Lipa", "Future Nostalgia"
    a.save()
    ops.svc.index.refresh()
    states = {t["title"]: t["state"]["kind"] for t in ops.browse_album("55")["tracks"]}
    assert states["Levitating"] == "library"
    assert states["Physical"] == "none"


def test_queue_tracks_resolves_isrc_skips_owned_and_dedupes(ops):
    tracks = ops.browse_search("d")["results"]
    r = ops.browse_queue(tracks)
    assert r["queued"] == 2 and all(x["result"] == "queued" for x in r["results"])
    item = ops.db.get_grab_item(r["item_ids"][0])
    assert item["isrc"] == "GBAHT2000001" and item["album"] == "Future Nostalgia" and item["status"] == "pending"
    again = ops.browse_queue(ops.browse_search("d")["results"])
    assert again["queued"] == 0
    with pytest.raises(OpsError):
        ops.browse_queue([])
    with pytest.raises(OpsError) as e:
        ops.browse_queue([{"title": "x", "dz_id": str(i)} for i in range(101)])
    assert e.value.code == "too_many"
    assert ops.browse_queue([{"title": "", "dz_id": "1"}])["results"][0]["result"] == "invalid"


def test_queue_album_and_undo(ops):
    r = ops.browse_queue_album("55")
    assert r["queued"] == 3 and r["album"] == "Future Nostalgia"
    assert ops.undo_queue(r["item_ids"][:2])["removed"] == 2
    ops.db.transition(r["item_ids"][2], "downloading", "x")
    assert ops.undo_queue(r["item_ids"])["removed"] == 0           # started: no longer undoable
    assert len(ops.db.get_queue()) == 1


def test_activity_lists_active_inbox_and_recent(ops):
    a = ops.db.enqueue_grab({"title": "A", "artist": "X", "spotify_track_id": "a"})
    ops.db.transition(a, "downloading", "x")
    b = ops.db.enqueue_grab({"title": "B", "artist": "X", "spotify_track_id": "b"})
    ops.db.transition(b, "awaiting_user", "x")
    c = ops.db.enqueue_grab({"title": "C", "artist": "X", "spotify_track_id": "c"})
    ops.db.transition(c, "done", "x")
    d = ops.activity()
    assert [i["title"] for i in d["active"]] == ["A"] and d["inbox"] == 1
    assert d["recent"][0]["title"] == "C"


def test_api_routes(ops):
    app = create_app(ops, TOKEN).test_client()
    assert app.get("/api/browse/search?q=dua&type=track", headers=H).json["results"][0]["title"] == "Levitating"
    assert app.get("/api/browse/search?q=", headers=H).status_code == 400
    assert app.get("/api/browse/artist/7", headers=H).json["related"]
    assert app.get("/api/browse/artist/404", headers=H).status_code == 502
    assert app.get("/api/browse/album/55", headers=H).json["tracks"]
    r = app.post("/api/browse/queue", json={"album_id": "55"}, headers=H).json
    assert r["queued"] == 3
    r2 = app.post("/api/browse/queue", json={"tracks": [{"title": "X", "dz_id": "9", "artist": "Y"}]}, headers=H).json
    assert r2["queued"] == 1
    assert app.post("/api/browse/undo", json={"item_ids": r2["item_ids"]}, headers=H).json["removed"] == 1
    assert "active" in app.get("/api/activity", headers=H).json
    assert app.get("/api/browse/search?q=x", headers={}).status_code == 401
