"""New releases: mapping library artists to Deezer, polling, the per-user feed. Deezer is faked."""

from datetime import date, timedelta

import pytest
from test_api import H, TOKEN
from test_ops import FakeClient

from music_grabber import deezer_catalog as dc
from music_grabber.api import create_app
from music_grabber.db import Database
from music_grabber.ops import Ops, OpsError
from music_grabber.releases import ReleaseTracker
from music_grabber.sync_engine import GrabberService
from music_grabber.text import normalize_artist, normalize_title


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


# Deezer's side: artist 7 is Dua Lipa (reachable by ISRC), 9 is "Prince" (name only, two namesakes),
# 11 is followed but not owned.
ALBUMS = {
    "7": [{"id": 701, "title": "New Album", "release_date": days_ago(10), "record_type": "album", "cover_medium": "c1"},
          {"id": 702, "title": "Old Album", "release_date": days_ago(400), "record_type": "album", "cover_medium": "c2", "nb_tracks": 10},
          {"id": 703, "title": "Summer Single", "release_date": days_ago(45), "record_type": "single", "cover_medium": "c3"}],
    "9": [{"id": 901, "title": "Purple Thing", "release_date": days_ago(5), "record_type": "ep", "cover_medium": "c4"}],
    "11": [{"id": 1101, "title": "Followed Release", "release_date": days_ago(3), "record_type": "album", "cover_medium": "c5"}],
}
CALLS = []
STATE = {"down": False}


def fake_get(path, params=None):
    CALLS.append(path)
    if STATE["down"]:
        raise ConnectionError("down")
    if path == "track/isrc:GBAHT2000001":
        return {"id": 1, "isrc": "GBAHT2000001", "artist": {"id": 7, "name": "Dua Lipa"}}
    if path.startswith("track/isrc:"):
        return {"error": {"code": 800, "message": "no data"}}
    if path == "search/artist":
        q = params["q"]
        if q == "Prince":
            return {"data": [{"id": 8, "name": "Prince", "nb_fan": 10}, {"id": 9, "name": "Prince", "nb_fan": 5000},
                             {"id": 10, "name": "Prince Royce", "nb_fan": 99999}]}
        if q == "Obscure Band":
            return {"data": [{"id": 12, "name": "Obscure Band Tribute", "nb_fan": 3}]}
        return {"data": []}
    if path == "artist/7/related":
        return {"data": [{"id": 20, "name": "Dojja", "nb_fan": 1}, {"id": 9, "name": "Prince"}, {"id": 21, "name": "Third"}]}
    if path == "artist/9/related":
        return {"data": [{"id": 20, "name": "Dojja"}, {"id": 22, "name": "Other"}]}
    if path.startswith("artist/") and path.endswith("/related"):
        return {"data": []}
    if path.startswith("artist/") and path.endswith("/albums"):
        aid = path.split("/")[1]
        return {"data": ALBUMS.get(aid, []), "total": len(ALBUMS.get(aid, []))}
    if path.startswith("artist/"):
        return {"id": int(path.split("/")[1]), "name": "Followed Artist"}
    raise AssertionError(path)


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    CALLS.clear()
    STATE["down"] = False
    monkeypatch.setattr(dc, "_get", fake_get)
    monkeypatch.setattr("music_grabber.ratelimit.deezer_limiter.acquire", lambda: None)


def lib_row(i, artist, isrc=None):
    return {"path": f"{artist}/{i}.mp3", "change_token": "t", "source": "filesystem", "title": f"Song {i}",
            "artist": artist, "album": "A", "album_artist": artist, "duration_ms": 1000, "isrc": isrc,
            "norm_title": normalize_title(f"Song {i}"), "norm_artist": normalize_artist(artist)}


@pytest.fixture
def env(tmp_path):
    cfg = {"db_path": str(tmp_path / "mg.db"), "music_dir": str(tmp_path / "music"), "provider_order": "ytdlp",
           "output_format": "mp3-128", "auto_accept_threshold": 0.85, "ask_threshold": 0.55,
           "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}", "releases_enabled": True,
           "releases_refresh_hours": 24}
    svc = GrabberService(cfg, Database(cfg["db_path"]))
    svc.client = FakeClient()
    svc.sync.client = svc.client
    svc.pool.pipeline.providers = []
    svc.releases = ReleaseTracker(cfg, svc.db)
    ops = Ops(svc, cfg)
    db = svc.db
    db.library_upsert([lib_row(i, "Dua Lipa", "GBAHT2000001" if i == 0 else None) for i in range(4)]
                      + [lib_row(i, "Prince") for i in range(3)]
                      + [lib_row(0, "Seldom Heard")]                   # one track: not "yours"
                      + [lib_row(i, "Obscure Band") for i in range(3)])
    return svc, ops, create_app(ops, TOKEN).test_client()


def titles(ops, uid=0, days=0):
    return [r["title"] for r in ops.releases_feed(uid, days)["releases"]]


def test_mapping_by_isrc_then_by_exact_name_and_unsure_is_left_out(env):
    svc, ops, _ = env
    out = svc.releases.refresh()
    assert out["ok"] and out["artists"] == 3                            # Seldom Heard has only one track
    m = {r: svc.db.get_artist_map(normalize_artist(r)) for r in ("Dua Lipa", "Prince", "Obscure Band")}
    assert (m["Dua Lipa"]["dz_artist_id"], m["Dua Lipa"]["source"]) == ("7", "isrc")
    assert (m["Prince"]["dz_artist_id"], m["Prince"]["source"]) == ("9", "name")   # exact name, most fans (not "Prince Royce")
    assert m["Obscure Band"]["dz_artist_id"] is None                    # only a look-alike: no guess
    assert svc.db.get_artist_map(normalize_artist("Seldom Heard")) is None


def test_feed_has_recent_releases_only_newest_first(env):
    svc, ops, _ = env
    svc.releases.refresh()
    assert titles(ops) == ["Purple Thing", "New Album", "Summer Single"]       # 5, 10, 45 days; the 400-day one is not cached
    assert titles(ops, days=20) == ["Purple Thing", "New Album"]
    assert ops.releases_feed(0)["status"]["mapped"] == 2


def test_nothing_is_asked_twice_inside_the_refresh_window(env):
    svc, _, _ = env
    svc.releases.refresh()
    first = len(CALLS)
    svc.releases.refresh()
    assert len(CALLS) == first                                          # mapped, "no match" cached, albums fresh
    svc.releases.refresh(force=True)
    assert len(CALLS) == first + 4                                      # force re-reads the two mapped artists and their related


def test_hide_is_per_user_and_reversible(env):
    svc, ops, _ = env
    svc.releases.refresh()
    ops.artist_pref_set(1, "9", {"hide_releases": True, "name": "Prince"})
    assert "Purple Thing" not in titles(ops, uid=1) and "Purple Thing" in titles(ops, uid=2)
    assert ops.releases_hidden(1)["artists"][0]["name"] == "Prince"
    ops.artist_pref_set(1, "9", {"hide_releases": False})
    assert "Purple Thing" in titles(ops, uid=1)
    assert ops.artist_prefs(1)["prefs"] == []                           # all flags off: the row is gone


def test_dismissing_one_release_and_undoing_it(env):
    svc, ops, _ = env
    svc.releases.refresh()
    ops.release_dismiss(1, "701")
    assert "New Album" not in titles(ops, uid=1) and "New Album" in titles(ops, uid=2)
    assert ops.releases_hidden(1)["releases"][0]["title"] == "New Album"
    ops.release_dismiss(1, "701", False)
    assert "New Album" in titles(ops, uid=1)


def test_follow_an_artist_you_do_not_own(env):
    svc, ops, _ = env
    ops.artist_pref_set(1, "11", {"follow": True})                      # name looked up from Deezer
    assert ops.artist_prefs(1)["prefs"][0]["name"] == "Followed Artist"
    svc.releases.refresh()
    assert "Followed Release" in titles(ops, uid=1) and "Followed Release" not in titles(ops, uid=2)
    assert ops.releases_feed(1)["following"] == ["11"]


def test_a_playlist_artist_counts_too(env):
    svc, ops, _ = env
    pid = svc.db.add_playlist("PL", "Mix")
    with svc.db._connect() as conn:
        for i in range(2):
            conn.execute("INSERT INTO playlist_tracks (playlist_id, source_track_id, title, artist, norm_artist, position) "
                         "VALUES (?,?,?,?,?,?)", (pid, f"s{i}", f"T{i}", "Playlist Only", normalize_artist("Playlist Only"), i))
        conn.commit()
    assert "playlistonly" in [c["norm"].replace(" ", "") for c in svc.db.candidate_artists(3, 2)]


def test_deezer_down_leaves_the_cache_and_does_not_raise(env):
    svc, ops, _ = env
    svc.releases.refresh()
    before = titles(ops)
    STATE["down"] = True
    out = svc.releases.refresh(force=True)
    assert out["ok"] is False and svc.releases.last_error
    assert titles(ops) == before                                        # stale beats empty
    STATE["down"] = False
    assert svc.releases.refresh(force=True)["ok"] is True and svc.releases.last_error == ""


def test_off_means_no_requests_and_clear_answers(env):
    svc, ops, _ = env
    svc.config["releases_enabled"] = False
    d = ops.releases_feed(0)
    assert d["enabled"] is False and d["releases"] == [] and CALLS == []
    with pytest.raises(OpsError) as e:
        ops.releases_refresh()
    assert e.value.code == "releases_disabled" and e.value.http == 409


def test_api_routes_and_per_user_scope(env):
    svc, ops, client = env
    svc.releases.refresh()
    ops.create_first_admin("admin", "correct horse battery")
    ops._make_user("bob", "another long password", "user")
    bob = {"Authorization": "Bearer " + ops.login("bob", "another long password")["token"]}
    assert [r["title"] for r in client.get("/api/releases", headers=bob).json["releases"]][0] == "Purple Thing"
    assert client.put("/api/artist-prefs/9", json={"hide_releases": True, "name": "Prince"}, headers=bob).json["ok"]
    assert "Purple Thing" not in [r["title"] for r in client.get("/api/releases", headers=bob).json["releases"]]
    assert "Purple Thing" in [r["title"] for r in client.get("/api/releases", headers=H).json["releases"]]
    assert client.post("/api/releases/701/dismiss", headers=bob).json["ok"]
    assert client.get("/api/releases/hidden", headers=bob).json["releases"][0]["title"] == "New Album"
    assert client.delete("/api/releases/701/dismiss", headers=bob).json["ok"]
    assert client.put("/api/artist-prefs/abc", json={"follow": True}, headers=bob).json["error"] == "invalid_artist"
    assert client.put("/api/artist-prefs/9", json={"follow": "yes"}, headers=bob).json["error"] == "invalid_pref"
    assert client.post("/api/releases/refresh", headers=bob).status_code == 403          # admins only
    assert client.post("/api/releases/refresh", headers=H).json["started"] is True


def test_an_api_key_cannot_reach_releases(env):
    _, _, client = env
    r = client.post("/api/keys", json={"name": "k", "scopes": ["browse"]}, headers=H)
    key = {"Authorization": f"Bearer {r.json['token']}"}
    assert client.get("/api/releases", headers=key).status_code == 403


def test_release_dates_survive_the_catalog_view():
    assert dc.album_view({"id": 1, "title": "T", "release_date": "2026-01-02"})["release_date"] == "2026-01-02"
