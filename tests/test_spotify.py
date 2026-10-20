"""SpotifyClient against a faked `requests`: OAuth, refresh, paging. No network."""

import pytest

from music_grabber import spotify as sp
from music_grabber.db import Database


class Resp:
    def __init__(self, status=200, data=None, text="", headers=None):
        self.status_code, self._data, self.text, self.headers = status, data or {}, text, headers or {}

    def json(self):
        return self._data


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(sp.time, "sleep", lambda *_: None)
    db = Database(str(tmp_path / "mg.db"))
    cfg = {"spotify_client_id": "id", "spotify_client_secret": "secret",
           "spotify_redirect_uri": "http://127.0.0.1:8099/cb"}
    return sp.SpotifyClient(cfg, db)


def test_authorize_url_requires_configuration(tmp_path):
    c = sp.SpotifyClient({}, Database(str(tmp_path / "mg.db")))
    assert c.is_configured() is False
    with pytest.raises(sp.SpotifyError):
        c.authorize_url("st")


def test_authorize_url_carries_state_scope_and_redirect(client):
    url = client.authorize_url("STATE123")
    assert url.startswith(sp.AUTH_URL) and "state=STATE123" in url
    assert "playlist-read-private" in url and "redirect_uri=http%3A%2F%2F127.0.0.1%3A8099%2Fcb" in url


def test_exchange_code_stores_refresh_token(client, monkeypatch):
    seen = {}

    def post(url, data=None, headers=None, timeout=None):
        seen.update(url=url, data=data, auth=headers["Authorization"])
        return Resp(data={"access_token": "A", "refresh_token": "R", "expires_in": 3600, "scope": "x"})

    monkeypatch.setattr(sp.requests, "post", post)
    client.exchange_code("the-code")
    assert seen["url"] == sp.TOKEN_URL and seen["data"]["code"] == "the-code"
    assert seen["auth"].startswith("Basic ")
    assert client.is_connected() and client.db.get_oauth_token("spotify")["refresh_token"] == "R"


def test_failed_exchange_is_an_auth_error(client, monkeypatch):
    monkeypatch.setattr(sp.requests, "post", lambda *a, **k: Resp(400, text="bad code"))
    with pytest.raises(sp.SpotifyAuthError):
        client.exchange_code("x")
    assert client.is_connected() is False


def test_invalid_grant_on_refresh_raises_auth_error(client, monkeypatch):
    client.db.save_oauth_token("spotify", "a", "r", "2000-01-01T00:00:00+00:00", "")
    monkeypatch.setattr(sp.requests, "post", lambda *a, **k: Resp(400, text='{"error":"invalid_grant"}'))
    with pytest.raises(sp.SpotifyAuthError):
        client.get_playlist_meta("PL")


def test_refresh_keeps_the_stored_refresh_token_when_none_is_returned(client, monkeypatch):
    client.db.save_oauth_token("spotify", "a", "KEEP", "2000-01-01T00:00:00+00:00", "")
    monkeypatch.setattr(sp.requests, "post",
                        lambda *a, **k: Resp(data={"access_token": "new", "expires_in": 3600}))
    monkeypatch.setattr(sp.requests, "get", lambda *a, **k: Resp(data={"id": "PL", "name": "N"}))
    assert client.get_playlist_meta("PL")["name"] == "N"
    assert client.db.get_oauth_token("spotify")["refresh_token"] == "KEEP"


def _connect(client, monkeypatch):
    client.db.save_oauth_token("spotify", "a", "r", "2000-01-01T00:00:00+00:00", "")
    monkeypatch.setattr(sp.requests, "post",
                        lambda *a, **k: Resp(data={"access_token": "t", "expires_in": 3600}))


TRACK = {"id": "t1", "name": "Song", "duration_ms": 1000, "track_number": 2, "disc_number": 1,
         "external_ids": {"isrc": "USX"}, "artists": [{"name": "A"}, {"name": "B"}],
         "album": {"name": "LP", "release_date": "2020-05-01", "images": [{"url": "http://img"}],
                   "artists": [{"name": "A"}]}}


def test_playlist_tracks_paginate_skip_local_files_and_normalize(client, monkeypatch):
    _connect(client, monkeypatch)
    pages = [
        {"items": [{"added_at": "d1", "track": TRACK}, {"track": None}], "next": "http://next"},
        {"items": [{"added_at": "d2", "track": {**TRACK, "id": "t2", "name": "Two"}}], "next": None},
    ]
    calls = []

    def get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        return Resp(data=pages[len(calls) - 1])

    monkeypatch.setattr(sp.requests, "get", get)
    rows = client.get_playlist_tracks("PL")
    assert [r["spotify_track_id"] for r in rows] == ["t1", "t2"]
    assert [r["position"] for r in rows] == [0, 2]          # the removed item still took a slot
    r = rows[0]
    assert r["artist"] == "A, B" and r["album_artist"] == "A" and r["year"] == 2020
    assert r["isrc"] == "USX" and r["cover_url"] == "http://img" and r["track_no"] == 2
    assert r["norm_title"] == "song" and calls[1] == "http://next"


def test_429_retries_and_404_is_a_clear_error(client, monkeypatch):
    _connect(client, monkeypatch)
    seq = iter([Resp(429, headers={"Retry-After": "1"}), Resp(data={"id": "PL", "name": "N"})])
    monkeypatch.setattr(sp.requests, "get", lambda *a, **k: next(seq))
    assert client.get_playlist_meta("PL")["spotify_id"] == "PL"
    monkeypatch.setattr(sp.requests, "get", lambda *a, **k: Resp(404))
    with pytest.raises(sp.SpotifyError, match="not found"):
        client.get_playlist_meta("PL")


def test_401_refreshes_once_then_retries(client, monkeypatch):
    _connect(client, monkeypatch)
    seq = iter([Resp(401), Resp(data={"id": "PL", "name": "N"})])
    monkeypatch.setattr(sp.requests, "get", lambda *a, **k: next(seq))
    assert client.get_playlist_meta("PL")["name"] == "N"


def test_parse_playlist_id_variants():
    assert sp.parse_playlist_id("37i9dQZF1DX") == "37i9dQZF1DX"
    assert sp.parse_playlist_id("spotify:playlist:abc") == "abc"
    assert sp.parse_playlist_id("https://open.spotify.com/playlist/abc?si=zz") == "abc"
    assert sp.parse_playlist_id("  ") == ""
