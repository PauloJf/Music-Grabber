# ruff: noqa: F811
"""The ntfy ping for new releases, and the read-only /api/v1 suggestions and releases. Deezer is faked
(the same fake as test_releases)."""

import pytest
from test_api import H
from test_releases import ALBUMS, _fake, days_ago, env, titles  # noqa: F401  (fixtures: the fake Deezer, the app)

from music_grabber import releases as releases_mod


class FakeNotifier:
    sent: list = []

    def send_grabber(self, title, body, click_url="", priority="default", tags="", actions=""):
        FakeNotifier.sent.append({"title": title, "body": body, "click": click_url, "tags": tags})


@pytest.fixture
def pings(monkeypatch):
    FakeNotifier.sent = []
    monkeypatch.setattr(releases_mod.Notifier, "from_config", classmethod(lambda cls, config: FakeNotifier()))
    return FakeNotifier.sent


def add_release(monkeypatch, album_id, title, days):
    monkeypatch.setitem(ALBUMS, "7", ALBUMS["7"] + [{"id": album_id, "title": title, "release_date": days_ago(days),
                                                      "record_type": "album", "cover_medium": "c"}])


def test_the_first_pass_is_a_silent_baseline_then_only_news_is_announced(env, pings, monkeypatch):
    svc, ops, _ = env
    svc.config.update(releases_notify=True, ui_public_url="http://mg.local/")
    out = svc.releases.refresh()
    assert pings == [] and out["notified"] == 0                          # everything already known is not news
    add_release(monkeypatch, 704, "Fresh One", 1)
    out = svc.releases.refresh(force=True)
    assert out["notified"] == 1 and len(pings) == 1
    assert pings[0]["title"] == "1 new release from your artists"
    assert "Dua Lipa: Fresh One" in pings[0]["body"] and pings[0]["click"] == "http://mg.local/releases"
    svc.releases.refresh(force=True)
    assert len(pings) == 1                                               # announced once


def test_several_releases_are_one_ping_with_a_cap(env, pings, monkeypatch):
    svc, _, _ = env
    svc.config["releases_notify"] = True
    svc.releases.refresh()
    for i in range(10):
        add_release(monkeypatch, 800 + i, f"Drop {i}", 2)
    svc.releases.refresh(force=True)
    assert len(pings) == 1 and pings[0]["title"] == "10 new releases from your artists"
    assert pings[0]["body"].count("\n") == 8 and pings[0]["body"].endswith("and 2 more")


def test_old_releases_found_late_are_not_announced(env, pings, monkeypatch):
    svc, _, _ = env
    svc.config["releases_notify"] = True
    svc.releases.refresh()
    add_release(monkeypatch, 705, "Dusty", 45)
    svc.releases.refresh(force=True)
    assert pings == []


def test_off_means_no_ping_and_no_baseline_taken(env, pings, monkeypatch):
    svc, _, _ = env
    svc.releases.refresh()
    add_release(monkeypatch, 706, "Fresh Two", 1)
    svc.releases.refresh(force=True)
    assert pings == [] and not svc.db.notify_baseline_done()
    svc.config["releases_notify"] = True                                 # turned on later: that moment is the baseline
    svc.releases.refresh(force=True)
    assert pings == [] and svc.db.notify_baseline_done()


def test_no_ntfy_configured_is_harmless(env, monkeypatch):
    svc, _, _ = env
    monkeypatch.setattr(releases_mod.Notifier, "from_config", classmethod(lambda cls, config: None))
    svc.config["releases_notify"] = True
    svc.releases.refresh()
    add_release(monkeypatch, 707, "Quiet", 1)
    assert svc.releases.refresh(force=True)["ok"] is True


def test_the_ping_is_a_runtime_setting(env):
    svc, ops, client = env
    ops.create_first_admin("admin", "correct horse battery")
    assert client.post("/api/settings", json={"releases_notify": True}, headers=H).json["applied"] == ["releases_notify"]
    assert svc.config["releases_notify"] is True


def key(client, scopes):
    r = client.post("/api/keys", json={"name": "-".join(scopes), "scopes": scopes}, headers=H)
    return {"Authorization": f"Bearer {r.json['token']}"}


def test_a_key_with_the_suggestions_scope_reads_suggestions_and_releases(env):
    svc, ops, client = env
    svc.releases.refresh()
    k = key(client, ["suggestions"])
    s = client.get("/api/v1/suggestions", headers=k).json
    assert s["enabled"] and s["gaps"] and s["artists"][0]["name"] == "Dojja"
    assert [r["title"] for r in client.get("/api/v1/releases", headers=k).json["releases"]][0] == "Purple Thing"
    assert [r["title"] for r in client.get("/api/v1/releases?days=20", headers=k).json["releases"]] == ["Purple Thing", "New Album"]


def test_other_scopes_cannot_read_them_and_keys_cannot_read_the_internal_routes(env):
    _, _, client = env
    other, sug = key(client, ["request", "browse", "library"]), key(client, ["suggestions"])
    for path in ("/api/v1/suggestions", "/api/v1/releases"):
        assert client.get(path, headers=other).status_code == 403
    assert client.get("/api/suggest", headers=sug).status_code == 403


def test_switches_and_personal_hides_do_not_leak_into_the_key_view(env):
    svc, ops, client = env
    svc.releases.refresh()
    ops.suggest_dismiss(0, "artist", "20")                               # the API token's own dismissal
    ops.artist_pref_set(0, "9", {"hide_releases": True, "name": "Prince"})
    k = key(client, ["suggestions"])
    assert "Dojja" in [a["name"] for a in client.get("/api/v1/suggestions", headers=k).json["artists"]]
    assert "Purple Thing" in [r["title"] for r in client.get("/api/v1/releases", headers=k).json["releases"]]
    svc.config["suggest_gaps"] = False
    assert client.get("/api/v1/suggestions", headers=k).json["gaps"] == []
    svc.config["releases_enabled"] = False
    assert client.get("/api/v1/suggestions", headers=k).json["enabled"] is False
    assert titles(ops) == []


def test_accounts_can_use_the_v1_routes_too_with_their_own_view(env):
    svc, ops, client = env
    svc.releases.refresh()
    ops.create_first_admin("admin", "correct horse battery")
    ops._make_user("bob", "another long password", "user")
    bob = {"Authorization": "Bearer " + ops.login("bob", "another long password")["token"]}
    client.put("/api/artist-prefs/9", json={"hide_releases": True, "name": "Prince"}, headers=bob)
    assert "Purple Thing" not in [r["title"] for r in client.get("/api/v1/releases", headers=bob).json["releases"]]
