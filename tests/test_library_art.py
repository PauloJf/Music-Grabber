# ruff: noqa: F811
"""Photos and covers for the whole library, including artists too small for New releases. Deezer is faked
(the same fake as test_releases)."""

from test_api import H
from test_releases import CALLS, _fake, env, titles  # noqa: F401  (fixtures: the fake Deezer, the app)

from music_grabber.text import normalize_artist


def test_a_one_track_artist_gets_a_photo_and_covers_but_never_reaches_the_feed_or_suggestions(env):
    svc, ops, _ = env
    svc.releases.refresh()
    key = normalize_artist("Seldom Heard")
    row = svc.db.get_artist_map(key)
    assert (row["dz_artist_id"], row["image_url"], row["active"]) == ("31", "https://cdn/sh.jpg", 0)
    assert svc.db.albums_of("31")[0]["cover_url"] == "https://cdn/rare.jpg"          # the covers are there for the album pages
    assert "Rare LP" not in titles(ops)                                              # ... but the release is not news
    assert all(r["name"] != "Seldom Heard" for r in svc.db.active_mapped())
    assert "Seldom Heard" not in [a["name"] for a in ops.suggestions(0)["artists"]]
    assert ops.releases_feed(0)["status"]["artists"] == 3                            # the "yours" count is unchanged


def test_the_library_pages_show_them(env):
    svc, ops, _ = env
    svc.releases.refresh()
    a = next(x for x in ops.library_artists()["artists"] if x["name"] == "Seldom Heard")
    assert a["image_url"] == "https://cdn/sh.jpg"
    assert ops.library_artist("Seldom Heard")["dz_artist_id"] == "31"


def test_the_albums_are_read_once_not_every_pass(env):
    svc, _, _ = env
    svc.releases.refresh()
    CALLS.clear()
    svc.releases.refresh(force=True)
    assert CALLS.count("artist/31/albums") == 0


def test_status_counts_the_whole_library(env):
    svc, ops, _ = env
    svc.releases.refresh()
    st = ops.releases_feed(0)["status"]
    assert st["library_artists"] == 4 and st["library_matched"] == 3 and st["library_photos"] == 1
    # (Dua Lipa and Prince matched without a photo in the fake; Seldom Heard matched with one; Obscure Band is no match)


def test_check_now_keeps_going_until_everything_has_been_looked_at(env, monkeypatch):
    svc, ops, client = env
    runs = []
    real = svc.releases.refresh
    def counting(force=False):
        runs.append(force)
        out = real(force)
        svc.releases.more_to_do = len(runs) < 3          # pretend the library needed three passes
        return out
    monkeypatch.setattr(svc.releases, "refresh", counting)
    svc.releases.run_to_completion()
    assert runs == [True, False, False]
    ops.create_first_admin("admin", "correct horse battery")
    assert client.post("/api/releases/refresh", headers=H).json["started"] is True
