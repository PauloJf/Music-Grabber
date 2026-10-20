"""The Flask API over Ops: bearer auth, route mapping, error mapping, the OAuth callback."""

import pytest
from test_ops import FakeClient

from music_grabber.api import MIN_TOKEN_LENGTH, create_app
from music_grabber.db import Database
from music_grabber.ops import Ops
from music_grabber.sync_engine import GrabberService

TOKEN = "t" * 12 + "-a-long-api-token"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app_ops(tmp_path):
    cfg = {"db_path": str(tmp_path / "mg.db"), "music_dir": str(tmp_path / "music"),
           "provider_order": "ytdlp", "output_format": "mp3-128", "auto_accept_threshold": 0.85,
           "ask_threshold": 0.55, "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}"}
    svc = GrabberService(cfg, Database(cfg["db_path"]))
    svc.client = FakeClient()
    svc.sync.client = svc.client
    svc.pool.pipeline.providers = []
    ops = Ops(svc, cfg)
    return create_app(ops, TOKEN), ops


@pytest.fixture
def client(app_ops):
    return app_ops[0].test_client()


def test_token_must_be_long_enough(app_ops):
    with pytest.raises(ValueError, match=str(MIN_TOKEN_LENGTH)):
        create_app(app_ops[1], "short")


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN},
                                     {"Authorization": "Basic " + TOKEN}, {"X-Api-Token": TOKEN}])
def test_every_api_route_needs_the_bearer_token(client, headers):
    for path in ("/api/status", "/api/queue", "/api/inbox", "/api/settings", "/api/playlists"):
        r = client.get(path, headers=headers)
        assert r.status_code == 401 and r.json["error"] == "unauthorized"
        assert r.headers["WWW-Authenticate"] == "Bearer"
    assert client.post("/api/sync", headers=headers).status_code == 401
    assert client.delete("/api/queue/1", headers=headers).status_code == 401


def test_healthz_and_callback_are_open_but_nothing_else(client):
    assert client.get("/healthz").json["ok"] is True
    assert client.get("/callback").status_code == 400          # reachable, rejects a bad login
    assert client.get("/api/status").status_code == 401


def test_responses_are_not_cacheable(client):
    r = client.get("/api/status", headers=H)
    assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Content-Type-Options"] == "nosniff"


def test_status_and_unknown_routes(client):
    r = client.get("/api/status", headers=H)
    assert r.status_code == 200 and r.json["spotify"]["connected"] is True
    assert client.get("/api/nope", headers=H).json["error"] == "not_found"
    assert client.put("/api/status", headers=H).status_code == 405


def test_playlist_lifecycle(client):
    r = client.post("/api/playlists", json={"value": "https://open.spotify.com/playlist/PL1"}, headers=H)
    assert r.status_code == 200 and r.json["playlist"]["name"] == "Road Trip"
    pid = r.json["playlist"]["id"]
    assert client.get("/api/playlists", headers=H).json["playlists"][0]["id"] == pid
    assert client.get(f"/api/playlists/{pid}", headers=H).json["playlist"]["id"] == pid
    assert client.patch(f"/api/playlists/{pid}", json={"enabled": False}, headers=H).json["enabled"] is False
    assert client.patch(f"/api/playlists/{pid}", json={"name": "x"}, headers=H).status_code == 400
    assert client.post(f"/api/playlists/{pid}/enqueue-missing", headers=H).json["enqueued"] == 0
    assert client.delete(f"/api/playlists/{pid}", headers=H).json["ok"] is True
    assert client.get(f"/api/playlists/{pid}", headers=H).status_code == 404


def test_error_mapping_uses_ops_codes_and_statuses(client, app_ops):
    r = client.post("/api/playlists", json={"value": ""}, headers=H)
    assert r.status_code == 400 and r.json["error"] == "invalid_playlist" and r.json["message"]
    app_ops[1].svc.client.connected = False
    r = client.post("/api/sync", headers=H)
    assert r.status_code == 409 and r.json["error"] == "spotify_disconnected"


def test_queue_routes(client, app_ops):
    db = app_ops[1].db
    r = client.post("/api/queue", json={"title": "Song", "artist": "Band", "spotify_track_id": "s1"}, headers=H)
    iid = r.json["id"]
    assert client.post("/api/queue", json={"title": "Song", "spotify_track_id": "s1"}, headers=H).status_code == 409
    assert client.get("/api/queue?status=pending", headers=H).json["counts"] == {"pending": 1}
    assert client.get(f"/api/queue/{iid}", headers=H).json["item"]["title"] == "Song"
    assert client.post(f"/api/queue/{iid}/priority", json={"priority": 3}, headers=H).json["priority"] == 3
    assert client.post(f"/api/queue/{iid}/retry", headers=H).status_code == 400
    assert client.delete(f"/api/queue/{iid}", headers=H).json["error"] == "not_deletable"
    assert client.post(f"/api/queue/{iid}/cancel", headers=H).json["ok"]
    assert client.get("/api/queue/history", headers=H).json["items"][0]["id"] == iid
    assert client.post(f"/api/queue/{iid}/retry", headers=H).json["ok"]
    db.transition(iid, "failed", "x")
    assert client.post("/api/queue/retry-failed", headers=H).json["requeued"] == 1
    db.transition(iid, "done", "x")
    assert client.post("/api/queue/clear-completed", headers=H).json["removed"] == 1
    assert client.get("/api/queue/99999", headers=H).status_code == 404


def test_inbox_routes(client, app_ops):
    db = app_ops[1].db
    iid = db.enqueue_grab({"spotify_track_id": "s1", "title": "Song", "artist": "Band"})
    db.transition(iid, "awaiting_user", "ambiguous")
    db.add_grab_candidates(iid, [{"provider": "deezer", "title": "Song", "rank": 0}])
    cand = db.get_grab_candidates(iid)[0]["id"]
    assert client.get("/api/inbox", headers=H).json["items"][0]["candidates"][0]["id"] == cand
    assert client.post(f"/api/inbox/{iid}/search", json={"query": ""}, headers=H).json["error"] == "query_required"
    assert client.post(f"/api/inbox/{iid}/choose", json={"candidate_id": cand}, headers=H).json["ok"]
    assert client.post(f"/api/inbox/{iid}/skip", headers=H).json["error"] == "not_awaiting"
    assert client.post("/api/inbox/research-all", headers=H).json["ok"]


def test_settings_routes_mask_secrets(client):
    r = client.post("/api/settings", json={"deezer_arl": "very-secret-arl", "output_format": "flac"}, headers=H)
    assert r.json["applied"] == ["deezer_arl", "output_format"]
    body = client.get("/api/settings", headers=H).get_data(as_text=True)
    assert "very-secret-arl" not in body and "********" in body
    r = client.post("/api/settings", json={"bogus": 1}, headers=H)
    assert r.status_code == 400 and r.json["error"] == "unknown_setting"
    assert client.post("/api/settings", data="not json", headers=H).status_code == 400


def test_body_size_is_capped(client):
    r = client.post("/api/queue", data=b"x" * (2 * 1024 * 1024), headers={**H, "Content-Type": "application/json"})
    assert r.status_code == 413 and r.json["error"] == "too_large"


def test_oauth_round_trip_and_callback_never_reflects_input(client, app_ops):
    url = client.get("/api/spotify/authorize-url", headers=H).json
    assert url["state"] in url["url"]
    r = client.get(f"/callback?code=CODE&state={url['state']}")
    assert r.status_code == 200 and "Connected" in r.get_data(as_text=True)
    assert app_ops[1].svc.client.exchanged == ["CODE"]
    # Replaying it, forging a state, or an error redirect all fail without echoing input.
    for q in (f"code=CODE&state={url['state']}", "code=X&state=<script>alert(1)</script>",
              "error=<script>alert(1)</script>"):
        r = client.get(f"/callback?{q}")
        assert r.status_code == 400 and "<script>" not in r.get_data(as_text=True)


def test_spotify_routes(client, app_ops):
    assert client.get("/api/spotify/status", headers=H).json["connected"] is True
    assert {p["spotify_id"] for p in client.get("/api/spotify/playlists", headers=H).json["playlists"]} == {"PL1", "PL2"}
    assert client.get("/api/spotify/search?q=hello", headers=H).json["tracks"][0]["title"] == "hello"
    assert client.post("/api/spotify/disconnect", headers=H).json["ok"]
    assert client.get("/api/spotify/playlists", headers=H).status_code == 409


def test_library_refresh_route(client, app_ops, tmp_path):
    import os
    assert client.post("/api/library/refresh", headers=H).status_code == 502
    os.makedirs(tmp_path / "music")
    assert client.post("/api/library/refresh", headers=H).json["seen"] == 0


# ── the web UI's static files ─────────────────────────────────────────────────
@pytest.fixture
def ui(tmp_path, app_ops):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>Music Grabber</title><div id=root></div>")
    (d / "assets" / "app-abc123.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside the UI dir")
    return create_app(app_ops[1], TOKEN, str(d)).test_client()


def test_ui_is_public_but_the_api_still_needs_the_token(ui):
    r = ui.get("/")
    assert r.status_code == 200 and b"Music Grabber" in r.data
    assert ui.get("/api/status").status_code == 401
    assert ui.get("/api/status", headers=H).status_code == 200


def test_client_side_routes_fall_back_to_index_but_unknown_api_routes_do_not(ui):
    for path in ("/queue", "/inbox/12", "/playlists/3"):
        r = ui.get(path)
        assert r.status_code == 200 and b"id=root" in r.data, path
    assert ui.get("/api/nope", headers=H).status_code == 404
    assert ui.get("/api/nope", headers=H).json["error"] == "not_found"
    assert ui.post("/queue").status_code == 404            # the fallback is for GET only


def test_hashed_assets_are_cached_forever_and_html_never(ui):
    a = ui.get("/assets/app-abc123.js")
    assert a.status_code == 200 and "immutable" in a.headers["Cache-Control"]
    index = ui.get("/").headers["Cache-Control"]
    assert "immutable" not in index and ("no-cache" in index or "no-store" in index)
    assert ui.get("/assets/missing.js").status_code == 404              # not index.html in disguise


def test_html_carries_a_same_origin_only_csp_and_no_framing(ui):
    h = ui.get("/").headers
    csp = h["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "img-src 'self' data: https://*.dzcdn.net" in csp and "media-src https://*.dzcdn.net" in csp
    assert "unsafe-eval" not in csp and "script-src 'self';" in csp and h["X-Frame-Options"] == "DENY" and h["Referrer-Policy"] == "no-referrer"
    assert "Content-Security-Policy" not in ui.get("/healthz").headers      # JSON needs none


def test_path_traversal_cannot_read_outside_the_ui_dir(ui):
    for path in ("/assets/../../secret.txt", "/assets/..%2F..%2Fsecret.txt", "/..%2Fsecret.txt"):
        r = ui.get(path)
        assert b"outside the UI dir" not in r.data, path


def test_without_a_built_ui_the_root_is_a_plain_404(client):
    r = client.get("/")
    assert r.status_code == 404 and r.json["error"] == "not_found"


def test_find_ui_dir(tmp_path):
    from music_grabber.api import find_ui_dir
    assert find_ui_dir(str(tmp_path)) != str(tmp_path)      # no index.html there: not chosen
    (tmp_path / "index.html").write_text("x")
    assert find_ui_dir(str(tmp_path)) == str(tmp_path)
