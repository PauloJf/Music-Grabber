# ruff: noqa: F811
"""Suggestions: gaps in artists you own, artists you may like, stars, and every switch. Deezer is faked
(the same fake as test_releases)."""

import pytest
from test_api import H
from test_releases import CALLS, _fake, env, lib_row, titles  # noqa: F401,F811  (fixtures: the fake Deezer, the app)

from music_grabber import releases as releases_mod
from music_grabber.suggest import same_album
from music_grabber.text import normalize_artist


def gap_titles(ops, uid=0):
    return [(g["title"], g["state"]) for g in ops.suggestions(uid)["gaps"]]


def names(ops, uid=0):
    return [a["name"] for a in ops.suggestions(uid)["artists"]]


def test_albums_you_have_none_of_are_gaps(env):
    svc, ops, _ = env
    svc.releases.refresh()
    got = dict(gap_titles(ops))
    assert got == {"New Album": "missing", "Old Album": "missing"}       # singles are not gaps; Prince has no albums listed
    assert "Summer Single" not in got


def test_an_album_you_own_a_deluxe_of_is_not_a_gap_and_a_thin_one_is_partial(env):
    svc, ops, _ = env
    svc.db.library_upsert([{**lib_row(100 + i, "Dua Lipa"), "album": "New Album (Deluxe Edition)"} for i in range(5)]
                          + [{**lib_row(200 + i, "Dua Lipa"), "album": "Old Album"} for i in range(2)])
    svc.releases.refresh()
    assert gap_titles(ops) == [("Old Album", "partial")]                 # 2 of 10; the deluxe counts as New Album
    assert ops.suggestions(0)["gaps"][0]["owned"] == 2


def test_same_album_is_cautious_with_short_titles():
    assert same_album("future nostalgia", "future nostalgia deluxe")
    assert not same_album("go", "good")                                  # too short to trust containment
    assert not same_album("", "x")


def test_artists_you_may_like_are_ranked_by_how_many_of_your_artists_point_at_them(env):
    svc, ops, _ = env
    svc.releases.refresh()
    got = ops.suggestions(0)["artists"]
    assert [a["name"] for a in got][:1] == ["Dojja"]                     # both Dua Lipa and Prince point at it
    assert set(got[0]["because"]) == {"Dua Lipa", "Prince"}
    assert "Prince" not in names(ops)                                    # you own him already


def test_stars_shift_the_weights(env, monkeypatch):
    svc, ops, _ = env
    svc.config.update(library_index="opensubsonic", library_url="http://lib", library_api_key="k")
    seen = []
    monkeypatch.setattr(releases_mod, "call", lambda url, ep, params, **kw: seen.append(ep) or {
        "starred2": {"song": [{"artist": "Prince"}] * 6}})
    svc.releases.refresh()
    assert seen == ["getStarred2"] and svc.db.artist_taste() == {normalize_artist("Prince"): 6}
    assert ops.suggestions(0)["artists"][0]["because"][0] == "Prince"    # six stars outweigh four tracks


def test_a_failing_stars_call_keeps_the_last_counts(env, monkeypatch):
    from music_grabber.subsonic import SubsonicError
    svc, ops, _ = env
    svc.config.update(library_index="opensubsonic", library_url="http://lib", library_api_key="k")
    svc.db.replace_artist_taste({"prince": 2})
    def boom(*a, **k): raise SubsonicError("down")
    monkeypatch.setattr(releases_mod, "call", boom)
    assert svc.releases.refresh()["ok"] is True
    assert svc.db.artist_taste() == {"prince": 2}


def test_hide_and_dismiss_are_per_user(env):
    svc, ops, _ = env
    svc.releases.refresh()
    ops.suggest_dismiss(1, "artist", "20")
    assert "Dojja" not in names(ops, 1) and "Dojja" in names(ops, 2)
    ops.suggest_dismiss(1, "album", "701")
    assert [t for t, _ in gap_titles(ops, 1)] == ["Old Album"] and len(gap_titles(ops, 2)) == 2
    ops.artist_pref_set(1, "7", {"hide_suggestions": True, "name": "Dua Lipa"})     # an owned artist: no gaps for you
    assert gap_titles(ops, 1) == [] and len(gap_titles(ops, 2)) == 2
    hidden = ops.suggest_hidden(1)
    assert {a["name"] for a in hidden["artists"]} == {"Dua Lipa", "Dojja"} and hidden["albums"][0]["title"] == "New Album"
    ops.suggest_dismiss(1, "artist", "20", False)
    assert "Dojja" in names(ops, 1)


def test_gaps_can_be_switched_off_and_the_rest_still_works(env):
    svc, ops, _ = env
    svc.releases.refresh()
    svc.config["suggest_gaps"] = False
    d = ops.suggestions(0)
    assert d["gaps_enabled"] is False and d["gaps"] == [] and d["artists"] and titles(ops)


def test_artist_suggestions_off_means_no_related_requests_and_no_list(env):
    svc, ops, _ = env
    svc.config["suggest_artists"] = False
    svc.releases.refresh()
    assert not [c for c in CALLS if c.endswith("/related")]
    d = ops.suggestions(0)
    assert d["artists_enabled"] is False and d["artists"] == [] and d["gaps"]


def test_stars_off_never_calls_the_library_server(env, monkeypatch):
    svc, _, _ = env
    svc.config.update(library_index="opensubsonic", library_url="http://lib", suggest_stars=False)
    monkeypatch.setattr(releases_mod, "call", lambda *a, **k: pytest.fail("starred songs were read"))
    svc.releases.refresh()


def test_the_master_switch_turns_everything_off(env):
    svc, ops, _ = env
    svc.releases.refresh()
    svc.config["releases_enabled"] = False
    d = ops.suggestions(0)
    assert d["enabled"] is False and d["gaps"] == [] and d["artists"] == [] and d["status"] is None


def test_every_switch_is_a_runtime_setting_and_applies_at_once(env):
    svc, ops, client = env
    ops.create_first_admin("admin", "correct horse battery")
    r = client.post("/api/settings", json={"suggest_gaps": False, "suggest_artists": "false", "suggest_stars": False,
                                           "releases_enabled": False}, headers=H)
    assert r.json["applied"] == ["releases_enabled", "suggest_artists", "suggest_gaps", "suggest_stars"]
    assert (svc.config["suggest_gaps"], svc.config["releases_enabled"]) == (False, False)
    shown = client.get("/api/settings", headers=H).json["settings"]
    assert all(shown[k] is False for k in ("suggest_gaps", "suggest_artists", "suggest_stars", "releases_enabled"))
    assert client.get("/api/suggest", headers=H).json["enabled"] is False
    assert client.post("/api/settings", json={"suggest_gaps": "maybe"}, headers=H).json["error"] == "invalid_setting"


def test_suggest_routes(env):
    svc, ops, client = env
    svc.releases.refresh()
    ops.create_first_admin("admin", "correct horse battery")
    ops._make_user("bob", "another long password", "user")
    bob = {"Authorization": "Bearer " + ops.login("bob", "another long password")["token"]}
    d = client.get("/api/suggest", headers=bob).json
    assert d["enabled"] and d["gaps"] and d["artists"]
    assert client.post("/api/suggest/artist/20/dismiss", headers=bob).json["ok"]
    assert "Dojja" not in [a["name"] for a in client.get("/api/suggest", headers=bob).json["artists"]]
    assert client.get("/api/suggest/hidden", headers=bob).json["artists"][0]["name"] == "Dojja"
    assert client.delete("/api/suggest/artist/20/dismiss", headers=bob).json["ok"]
    assert client.post("/api/suggest/nope/20/dismiss", headers=bob).json["error"] == "invalid_suggestion"
    assert client.post("/api/suggest/artist/x/dismiss", headers=bob).json["error"] == "invalid_suggestion"
    key = client.post("/api/keys", json={"name": "k", "scopes": ["browse"]}, headers=H).json["token"]
    assert client.get("/api/suggest", headers={"Authorization": f"Bearer {key}"}).status_code == 403
