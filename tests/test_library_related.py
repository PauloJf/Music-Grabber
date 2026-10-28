"""Related artist, album and track for an owned track. Deezer is faked."""

import pytest
from test_api import H, TOKEN
from test_ops import FakeClient

from music_grabber import deezer_catalog as dc
from music_grabber.api import create_app
from music_grabber.db import Database
from music_grabber.ops import Ops, OpsError
from music_grabber.sync_engine import GrabberService
from music_grabber.text import normalize_artist, normalize_title

CALLS = []
STATE = {"down": False}


def fake_get(path, params=None):
    CALLS.append(path)
    if STATE["down"]:
        raise ConnectionError("down")
    if path == "track/isrc:GBAHT2000001":
        return {"id": 101, "title": "Levitating", "artist": {"id": 7, "name": "Dua Lipa"}, "album": {"id": 55, "title": "Future Nostalgia"}}
    if path.startswith("track/isrc:"):
        return {"error": {"code": 800, "message": "no data"}}
    if path == "search/artist":
        q = params["q"]
        if q == "Prince":
            return {"data": [{"id": 8, "name": "Prince", "nb_fan": 10}, {"id": 9, "name": "Prince", "nb_fan": 5000}, {"id": 10, "name": "Prince Royce", "nb_fan": 99999}]}
        return {"data": [{"id": 12, "name": f"{q} Tribute", "nb_fan": 3}]}
    if path == "artist/7/related":
        return {"data": [{"id": 20, "name": "Dojja", "nb_fan": 5, "picture_medium": "p"}, {"id": 21, "name": "Third"}]}
    if path == "artist/9/related":
        return {"data": [{"id": 22, "name": "Other"}]}
    if path == "artist/77/related":
        return {"data": []}
    raise AssertionError(path)


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    CALLS.clear()
    STATE["down"] = False
    monkeypatch.setattr(dc, "_get", fake_get)
    monkeypatch.setattr("music_grabber.ratelimit.deezer_limiter.acquire", lambda: None)


def row(path, artist, title, isrc=None, album="LP"):
    return {"path": path, "change_token": "t", "source": "filesystem", "title": title, "artist": artist, "album": album,
            "album_artist": artist, "duration_ms": 1000, "isrc": isrc, "norm_title": normalize_title(title),
            "norm_artist": normalize_artist(artist)}


@pytest.fixture
def env(tmp_path):
    cfg = {"db_path": str(tmp_path / "mg.db"), "music_dir": str(tmp_path / "music"), "provider_order": "ytdlp",
           "output_format": "mp3-128", "auto_accept_threshold": 0.85, "ask_threshold": 0.55,
           "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}"}
    svc = GrabberService(cfg, Database(cfg["db_path"]))
    svc.client = FakeClient()
    svc.sync.client = svc.client
    svc.pool.pipeline.providers = []
    ops = Ops(svc, cfg)
    svc.db.library_upsert([row("a/1.mp3", "Dua Lipa", "Levitating", "GBAHT2000001"),
                           row("p/1.mp3", "Prince", "Kiss"),
                           row("x/1.mp3", "Nobody Known", "Song"),
                           row("m/1.mp3", "Mapped Band", "Tune", isrc="NOSUCHISRC001"),
                           row("n/1.mp3", "Dua Lipa", "No Isrc Here")])
    return svc, ops, create_app(ops, TOKEN).test_client()


def test_the_isrc_names_the_track_artist_and_album_and_related_artists_come_with_it(env):
    _, ops, _ = env
    d = ops.library_related("a/1.mp3")
    assert d["found"] and d["matched_by"] == "isrc"
    assert d["artist"] == {"dz_id": "7", "name": "Dua Lipa"}
    assert d["album"] == {"dz_id": "55", "title": "Future Nostalgia"} and d["track"] == {"dz_id": "101", "title": "Levitating"}
    assert [r["name"] for r in d["related"]] == ["Dojja", "Third"] and d["related"][0]["fans"] == 5


def test_without_an_isrc_the_artist_map_is_used_before_any_search(env):
    svc, ops, _ = env
    svc.db.save_artist_map(normalize_artist("Dua Lipa"), "Dua Lipa", "7", 1.0, "isrc")
    d = ops.library_related("n/1.mp3")
    assert d["matched_by"] == "artist_map" and d["artist"]["dz_id"] == "7" and d["album"] is None and d["track"] is None
    assert "search/artist" not in CALLS


def test_without_a_map_an_equal_name_wins_by_fans_and_a_lookalike_is_not_accepted(env):
    _, ops, _ = env
    d = ops.library_related("p/1.mp3")
    assert d["matched_by"] == "name" and d["artist"]["dz_id"] == "9"          # not 8 (fewer fans), not "Prince Royce"
    d = ops.library_related("x/1.mp3")
    assert d["found"] is False and d["artist"] is None and d["related"] == [] and d["matched_by"] is None


def test_an_isrc_deezer_does_not_know_falls_back_to_the_artist(env):
    svc, ops, _ = env
    svc.db.save_artist_map(normalize_artist("Mapped Band"), "Mapped Band", "77", 0.8, "name")
    d = ops.library_related("m/1.mp3")
    assert d["matched_by"] == "artist_map" and d["artist"]["dz_id"] == "77" and d["related"] == []


def test_unknown_paths_and_deezer_being_down(env):
    _, ops, _ = env
    with pytest.raises(OpsError) as e:
        ops.library_related("nope.mp3")
    assert e.value.http == 404
    STATE["down"] = True
    with pytest.raises(OpsError) as e:
        ops.library_related("a/1.mp3")
    assert e.value.code == "deezer_unreachable" and e.value.http == 502
    with pytest.raises(OpsError) as e:
        ops.library_related("p/1.mp3")
    assert e.value.code == "deezer_unreachable"


def test_the_route_and_keys(env):
    _, ops, client = env
    assert client.get("/api/library/related?path=a/1.mp3", headers=H).json["artist"]["name"] == "Dua Lipa"
    assert client.get("/api/library/related?path=nope", headers=H).status_code == 404
    assert client.get("/api/library/related", headers=H).status_code == 404
    key = client.post("/api/keys", json={"name": "k", "scopes": ["browse"]}, headers=H).json["token"]
    assert client.get("/api/library/related?path=a/1.mp3", headers={"Authorization": f"Bearer {key}"}).status_code == 403
