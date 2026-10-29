"""How much of each release on an artist's Deezer page the library already has. Deezer is faked."""

import sys

import pytest
from test_api import H, TOKEN
from test_ops import FakeClient

from music_grabber import deezer_catalog as dc
from music_grabber.api import create_app
from music_grabber.db import Database
from music_grabber.ops import Ops
from music_grabber.sync_engine import GrabberService
from music_grabber.text import normalize_artist, normalize_title

CALLS = []
ALBUMS = [  # Deezer's list carries no track counts, as in real life
    {"id": 1, "title": "Resurrected", "release_date": "2015-01-01", "record_type": "album", "cover_medium": "c"},
    {"id": 2, "title": "The Outbreak", "release_date": "2014-01-01", "record_type": "album", "cover_medium": "c"},
    {"id": 3, "title": "Never Heard", "release_date": "2013-01-01", "record_type": "album", "cover_medium": "c"},
    {"id": 4, "title": "Lone Wolf", "release_date": "2020-01-01", "record_type": "single", "cover_medium": "c"},
    {"id": 5, "title": "Nuclear (Hands Up)", "release_date": "2019-01-01", "record_type": "single", "cover_medium": "c"},
    {"id": 6, "title": "Dead Symphonic", "release_date": "2018-01-01", "record_type": "ep", "cover_medium": "c"},
]
LENGTH = {"1": 4, "2": 3, "6": 5}
DOWN = {"on": False}


def fake_get(path, params=None):
    CALLS.append(path)
    if path == "artist/7":
        return {"id": 7, "name": "Zomboy", "nb_fan": 1, "nb_album": 6}
    if path in ("artist/7/top", "artist/7/related"):
        return {"data": []}
    if path == "artist/7/albums":
        return {"data": ALBUMS, "total": len(ALBUMS)}
    if path.startswith("album/"):
        n = path.split("/")[1]
        if DOWN["on"]:
            raise ConnectionError("down")
        return {"id": int(n), "title": "x", "nb_tracks": LENGTH.get(n, 1), "tracks": {"data": [{"id": i, "title": f"t{i}"} for i in range(LENGTH.get(n, 1))]}}
    raise AssertionError(path)


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    CALLS.clear()
    DOWN["on"] = False
    monkeypatch.setattr(dc, "_get", fake_get)
    monkeypatch.setattr("music_grabber.ratelimit.deezer_limiter.acquire", lambda: None)


def row(i, title, album, artist="Zomboy"):
    return {"path": f"z/{i}.mp3", "change_token": "t", "source": "filesystem", "title": title, "artist": artist, "album": album,
            "album_artist": artist, "duration_ms": 1000, "isrc": None, "norm_title": normalize_title(title),
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
    svc.db.library_upsert([row(0, "Song A", "Resurrected"), row(1, "Song B", "Resurrected"),               # 2 of 4
                           *[row(10 + i, f"O{i}", "The Outbreak (Deluxe Edition)") for i in range(3)],      # 3 of 3, a Deluxe title
                           row(20, "Nuclear (Hands Up)", "Some Compilation"),                                # a single, filed elsewhere
                           row(30, "Dead", "Dead Symphonic"), row(31, "Dead 2", "Dead Symphonic"),          # an EP, 2 of 5
                           row(40, "Other Artist Song", "Resurrected", artist="Someone Else")])            # not theirs
    return svc, Ops(svc, cfg), create_app(Ops(svc, cfg), TOKEN).test_client()


def states(page):
    return {a["title"]: (a["own_state"], a["owned"], a["owned_total"]) for a in page["albums"]}


def test_partial_full_none_and_singles_are_told_apart(env):
    _, ops, _ = env
    s = states(ops.browse_artist("7"))
    assert s["Resurrected"] == ("partial", 2, 4)
    assert s["The Outbreak"] == ("full", 3, 3)                                  # a Deluxe edition counts as the album
    assert s["Never Heard"] == ("none", 0, None)
    assert s["Nuclear (Hands Up)"] == ("full", 1, 1)                            # the single's track is on another album
    assert s["Lone Wolf"][0] == "none"
    assert s["Dead Symphonic"] == ("partial", 2, 5)
    assert "album/3" not in CALLS and "album/4" not in CALLS                    # lengths asked only for what you own part of


def test_lengths_are_remembered_between_page_views(env):
    _, ops, _ = env
    ops.browse_artist("7")
    first = [c for c in CALLS if c.startswith("album/")]
    assert sorted(first) == ["album/1", "album/2", "album/6"]
    CALLS.clear()
    ops.browse_artist("7")
    assert not [c for c in CALLS if c.startswith("album/")]


def test_when_the_length_cannot_be_read_the_count_is_still_shown(env):
    _, ops, _ = env
    DOWN["on"] = True
    s = states(ops.browse_artist("7"))
    assert s["Resurrected"] == ("some", 2, None) and s["The Outbreak"] == ("some", 3, None)
    DOWN["on"] = False
    assert states(ops.browse_artist("7"))["Resurrected"] == ("partial", 2, 4)    # asked again next time, not remembered as unknown


def test_at_most_a_dozen_lengths_are_looked_up_per_view(env, monkeypatch):
    svc, ops, _ = env
    many = [{"id": 100 + i, "title": f"Album {i}", "release_date": "2010-01-01", "record_type": "album", "cover_medium": "c"} for i in range(20)]
    monkeypatch.setattr(sys.modules[__name__], "ALBUMS", many)
    svc.db.library_upsert([row(200 + i, f"T{i}", f"Album {i}") for i in range(20)])
    states(ops.browse_artist("7"))
    assert len([c for c in CALLS if c.startswith("album/")]) == ops.MAX_TOTAL_LOOKUPS


def test_the_api_carries_the_fields(env):
    _, _, client = env
    page = client.get("/api/browse/artist/7", headers=H).json
    r = next(a for a in page["albums"] if a["title"] == "Resurrected")
    assert (r["own_state"], r["owned"], r["owned_total"]) == ("partial", 2, 4)
