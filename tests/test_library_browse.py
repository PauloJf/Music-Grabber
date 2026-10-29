"""Browsing the library by artist and album, from the cached rows. No network."""

import pytest
from test_api import H, TOKEN
from test_ops import FakeClient

from music_grabber import library_browse as lb
from music_grabber.api import create_app
from music_grabber.db import Database
from music_grabber.ops import Ops
from music_grabber.sync_engine import GrabberService
from music_grabber.text import normalize_artist, normalize_title


def t(path, title, artist, album, album_artist=None, no=None, disc=None, year=None):
    return {"path": path, "change_token": "t", "source": "filesystem", "title": title, "artist": artist, "album": album,
            "album_artist": album_artist, "track_no": no, "disc_no": disc, "year": year, "duration_ms": 1000, "isrc": None,
            "norm_title": normalize_title(title), "norm_artist": normalize_artist(artist)}


ROWS = [
    t("dua/1.mp3", "Physical", "Dua Lipa", "Future Nostalgia", "Dua Lipa", 2, 1, 2020),
    t("dua/2.mp3", "Levitating", "Dua Lipa", "Future Nostalgia", "Dua Lipa", 1, 1, 2020),
    t("dua/3.mp3", "Levitating (feat. DaBaby)", "Dua Lipa feat. DaBaby", "Future Nostalgia (Deluxe)", "Dua Lipa", 3, 1, 2020),
    t("dua/4.mp3", "Loose", "dua lipa", None, None),
    t("vа/1.mp3", "Hit", "Dua Lipa", "Now 100", "Various Artists", 4, 1, 2021),
    t("vа/2.mp3", "Other", "Someone Else", "Now 100", "Various Artists", 5, 1, 2021),
    t("es/1.mp3", "Song", "Éxito", "Álbum", None, 1, 1, 2019),
    t("es/2.mp3", "Collab", "Éxito, Dua Lipa", "Álbum", None, 2, 1, 2019),
]


def test_artists_group_by_name_ignoring_case_accents_and_credits():
    out = {a["name"]: a for a in lb.artists(ROWS)}
    assert out["Dua Lipa"]["tracks"] == 6 and out["Dua Lipa"]["albums"] == 3             # 3 own, the loose one, a compilation, a collab; the loose one is not an album
    assert "Éxito" in out and out["Éxito"]["tracks"] == 2
    assert "DaBaby" in out and out["DaBaby"]["tracks"] == 1                              # a credit counts
    assert [a["name"] for a in lb.artists(ROWS)][-1] == "Various Artists"                # compilations sort last
    assert [a["name"] for a in lb.artists(ROWS, "exito")] == ["Éxito"]                   # accent-insensitive filter


def test_an_artist_page_has_their_albums_and_where_they_appear():
    p = lb.artist_page(ROWS, "DUA lipa")
    assert p["name"] == "Dua Lipa"
    owned = {a["title"]: a for a in p["albums"]}
    assert owned["Future Nostalgia"]["tracks"] == 3                                      # the Deluxe edition folds into the album
    assert owned["Future Nostalgia"]["year"] == 2020
    assert p["albums"][-1]["title"] == lb.NO_ALBUM                                        # loose tracks last
    assert sorted((a["artist"], a["title"]) for a in p["appears_on"]) == [("Various Artists", "Now 100"), ("Éxito", "Álbum")]
    assert lb.artist_page(ROWS, "Nobody") is None


def test_an_album_page_lists_tracks_in_order_and_is_found_by_any_edition_title():
    p = lb.album_page(ROWS, "Dua Lipa", "Future Nostalgia (Deluxe Edition)")
    assert [x["title"] for x in p["tracks_list"]] == ["Levitating", "Physical", "Levitating (feat. DaBaby)"]
    assert p["artist"] == "Dua Lipa" and p["tracks"] == 3 and p["discs"] == 1
    assert lb.album_page(ROWS, "Dua Lipa", "Nope") is None
    assert lb.album_page(ROWS, "Various Artists", "Now 100")["tracks"] == 2


def test_the_albums_list_filters_by_title_or_artist_and_skips_albumless_tracks():
    titles = [a["title"] for a in lb.albums(ROWS)]
    assert lb.NO_ALBUM not in titles and "Now 100" in titles
    assert [a["title"] for a in lb.albums(ROWS, "various")] == ["Now 100"]


@pytest.fixture
def env(tmp_path):
    cfg = {"db_path": str(tmp_path / "mg.db"), "music_dir": str(tmp_path / "music"), "provider_order": "ytdlp",
           "output_format": "mp3-128", "auto_accept_threshold": 0.85, "ask_threshold": 0.55,
           "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}"}
    svc = GrabberService(cfg, Database(cfg["db_path"]))
    svc.client = FakeClient()
    svc.sync.client = svc.client
    svc.pool.pipeline.providers = []
    svc.db.library_upsert(ROWS)
    return svc, Ops(svc, cfg), create_app(Ops(svc, cfg), TOKEN).test_client()


def test_covers_and_photos_come_from_what_new_releases_cached_and_never_from_deezer(env, monkeypatch):
    svc, ops, _ = env
    monkeypatch.setattr("music_grabber.deezer_catalog._get", lambda *a, **k: pytest.fail("Deezer was called"))
    d = ops.library_artists()
    assert all(a["image_url"] == "" for a in d["artists"])                               # nothing matched yet: placeholders
    svc.db.save_artist_map(normalize_artist("Dua Lipa"), "Dua Lipa", "7", 1.0, "isrc", image_url="https://cdn/dua.jpg")
    svc.db.save_albums("7", "Dua Lipa", [{"dz_id": "55", "title": "Future Nostalgia", "kind": "album", "release_date": "2020-03-27",
                                          "cover_url": "https://cdn/fn.jpg", "track_count": 11}])
    d = ops.library_artists()
    assert next(a for a in d["artists"] if a["name"] == "Dua Lipa")["image_url"] == "https://cdn/dua.jpg"
    page = ops.library_artist("Dua Lipa")
    assert page["dz_artist_id"] == "7" and page["image_url"] == "https://cdn/dua.jpg"
    fn = next(a for a in page["albums"] if a["title"] == "Future Nostalgia")
    assert fn["cover_url"] == "https://cdn/fn.jpg" and fn["dz_album_id"] == "55"        # the Deluxe tracks share it
    alb = ops.library_album("Dua Lipa", "Future Nostalgia")
    assert alb["cover_url"] == "https://cdn/fn.jpg" and alb["dz_artist_id"] == "7"
    assert [a["cover_url"] for a in ops.library_albums("now")["albums"]] == [""]


def test_a_photo_marked_looked_up_with_none_shows_as_a_placeholder(env):
    svc, ops, _ = env
    svc.db.save_artist_map(normalize_artist("Éxito"), "Éxito", "9", 0.8, "name")
    svc.db.set_artist_image("9", "-")
    assert next(a for a in ops.library_artists()["artists"] if a["name"] == "Éxito")["image_url"] == ""
    assert svc.db.artists_missing_image(10) == []


def test_routes(env):
    _, _, client = env
    assert client.get("/api/library/artists?q=dua", headers=H).json["artists"][0]["name"] == "Dua Lipa"
    assert client.get("/api/library/artist?name=Dua+Lipa", headers=H).json["albums"][0]["title"] in ("Future Nostalgia", "Now 100")
    assert client.get("/api/library/artist?name=Nobody", headers=H).status_code == 404
    assert client.get("/api/library/albums", headers=H).json["total"] == 3
    r = client.get("/api/library/album?artist=Dua+Lipa&album=Future+Nostalgia", headers=H).json
    assert [x["title"] for x in r["tracks_list"]][0] == "Levitating"
    assert client.get("/api/library/album?artist=x&album=y", headers=H).status_code == 404
    key = client.post("/api/keys", json={"name": "k", "scopes": ["browse"]}, headers=H).json["token"]
    assert client.get("/api/library/artists", headers={"Authorization": f"Bearer {key}"}).status_code == 403
