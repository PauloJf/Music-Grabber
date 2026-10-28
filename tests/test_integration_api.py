"""The integration API: scoped keys, /api/v1 requests, attribution. Deezer is faked."""

import pytest
from test_api import H, TOKEN
from test_ops import FakeClient

from music_grabber import deezer_catalog as dc
from music_grabber.api import create_app
from music_grabber.db import Database
from music_grabber.ops import Ops, OpsError
from music_grabber.sync_engine import GrabberService

DZ = {"id": 101, "title": "Levitating", "duration": 203, "isrc": "gbaht2000001", "track_position": 5,
      "artist": {"id": 7, "name": "Dua Lipa"},
      "album": {"id": 55, "title": "Future Nostalgia", "cover_medium": "https://cdn-images.dzcdn.net/c.jpg",
                "release_date": "2020-03-27"}}
CALLS = []


def fake_get(path, params=None):
    CALLS.append(path)
    if path in ("track/101", "track/isrc:GBAHT2000001"):
        return DZ
    if path.startswith("track/"):
        return {"error": {"type": "DataException", "message": "no data", "code": 800}}
    if path == "search":
        return {"data": [DZ]}
    raise AssertionError(path)


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    CALLS.clear()
    monkeypatch.setattr(dc, "_get", fake_get)


@pytest.fixture
def env(tmp_path):
    cfg = {"db_path": str(tmp_path / "mg.db"), "music_dir": str(tmp_path / "music"),
           "provider_order": "ytdlp", "output_format": "mp3-128", "auto_accept_threshold": 0.85,
           "ask_threshold": 0.55, "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}"}
    svc = GrabberService(cfg, Database(cfg["db_path"]))
    svc.client = FakeClient()
    svc.sync.client = svc.client
    svc.pool.pipeline.providers = []
    ops = Ops(svc, cfg)
    return ops, create_app(ops, TOKEN).test_client()


def make_key(client, name="BPM Tagger", scopes=("browse", "request", "library")):
    r = client.post("/api/keys", json={"name": name, "scopes": list(scopes)}, headers=H)
    assert r.status_code == 201
    return r.json["token"], {"Authorization": f"Bearer {r.json['token']}"}


def test_key_is_shown_once_and_stored_hashed(env):
    ops, client = env
    token, _ = make_key(client)
    assert token.startswith("mgk_") and len(token) > 40
    listed = client.get("/api/keys", headers=H).json["keys"]
    assert listed[0]["name"] == "BPM Tagger" and listed[0]["scopes"] == ["browse", "request", "library"]
    assert token not in str(listed) and "key_hash" not in listed[0]
    with ops.db._connect() as conn:
        assert token not in str(conn.execute("SELECT * FROM api_keys").fetchall()[0][:])


def test_key_validation_and_duplicates(env):
    _, client = env
    for body in ({"name": "", "scopes": ["request"]}, {"name": "x", "scopes": []},
                 {"name": "x", "scopes": ["admin"]}, {"name": "x"}):
        assert client.post("/api/keys", json=body, headers=H).json["error"] == "invalid_key"
    make_key(client, "App")
    assert client.post("/api/keys", json={"name": "app", "scopes": ["request"]}, headers=H).json["error"] == "key_name_taken"


def test_only_admins_manage_keys(env):
    ops, client = env
    ops.create_first_admin("admin", "correct horse battery")
    ops._make_user("bob", "another long password", "user")
    bob = {"Authorization": "Bearer " + ops.login("bob", "another long password")["token"]}
    assert client.get("/api/keys", headers=bob).status_code == 403
    _, key_h = make_key(client)
    assert client.get("/api/keys", headers=key_h).status_code == 403


def test_a_key_cannot_reach_anything_outside_v1(env):
    _, client = env
    _, key_h = make_key(client)
    for path in ("/api/status", "/api/queue", "/api/settings", "/api/browse/search?q=x", "/api/me"):
        r = client.get(path, headers=key_h)
        assert r.status_code == 403 and r.json["error"] == "forbidden"
    assert client.post("/api/queue", json={"title": "x"}, headers=key_h).status_code == 403


def test_scopes_are_enforced_per_route(env):
    _, client = env
    _, only_req = make_key(client, "req", ["request"])
    _, only_browse = make_key(client, "br", ["browse"])
    assert client.get("/api/v1/search?q=levitating", headers=only_req).status_code == 403
    assert client.get("/api/v1/library/has?isrc=X", headers=only_req).status_code == 403
    assert client.post("/api/v1/requests", json={"dz_id": "101"}, headers=only_browse).status_code == 403
    assert client.get("/api/v1/search?q=levitating", headers=only_browse).json["results"][0]["title"] == "Levitating"


def test_revoked_and_unknown_keys_are_refused(env):
    ops, client = env
    _, key_h = make_key(client)
    assert client.get("/api/v1/search?q=a", headers=key_h).status_code == 200
    kid = client.get("/api/keys", headers=H).json["keys"][0]["id"]
    assert client.delete(f"/api/keys/{kid}", headers=H).json["ok"] is True
    assert client.get("/api/v1/search?q=a", headers=key_h).status_code == 401
    assert client.get("/api/v1/search?q=a", headers={"Authorization": "Bearer mgk_nope"}).status_code == 401
    assert client.delete(f"/api/keys/{kid}", headers=H).status_code == 404         # already revoked


def test_wrong_keys_count_toward_the_lockout(env):
    _, client = env
    for _ in range(10):
        client.get("/api/v1/search?q=a", headers={"Authorization": "Bearer mgk_wrong"})
    assert client.get("/api/v1/search?q=a", headers={"Authorization": "Bearer mgk_wrong"}).status_code == 429


def test_last_used_is_recorded(env):
    _, client = env
    _, key_h = make_key(client)
    assert client.get("/api/keys", headers=H).json["keys"][0]["last_used_at"] is None
    client.get("/api/v1/search?q=a", headers=key_h)
    assert client.get("/api/keys", headers=H).json["keys"][0]["last_used_at"]


def test_request_by_deezer_id_queues_with_attribution(env):
    ops, client = env
    _, key_h = make_key(client)
    r = client.post("/api/v1/requests", json={"dz_id": "101", "source_ref": "bpm:42"}, headers=key_h)
    assert r.status_code == 201 and r.json["result"] == "queued" and r.json["status"] == "pending"
    item = ops.db.get_grab_item(r.json["id"])
    assert (item["title"], item["artist"], item["isrc"]) == ("Levitating", "Dua Lipa", "GBAHT2000001")
    assert (item["requested_by"], item["source_ref"]) == ("BPM Tagger", "bpm:42")
    assert ops.queue_list()["items"][0]["requested_by"] == "BPM Tagger"


def test_request_by_isrc_and_by_metadata(env):
    ops, client = env
    _, key_h = make_key(client)
    r = client.post("/api/v1/requests", json={"isrc": "gbaht2000001"}, headers=key_h)
    assert r.json["result"] == "queued" and "track/isrc:GBAHT2000001" in CALLS
    r = client.post("/api/v1/requests", json={"title": "Physical", "artist": "Dua Lipa", "isrc": "GBAHT2000002"},
                    headers=key_h)
    assert r.json["result"] == "queued"
    assert ops.db.get_grab_item(r.json["id"])["isrc"] == "GBAHT2000002"
    assert CALLS.count("track/isrc:GBAHT2000002") == 0                 # metadata given: no lookup


def test_request_validation_and_unknown_track(env):
    _, client = env
    _, key_h = make_key(client)
    assert client.post("/api/v1/requests", json={"title": "only a title"}, headers=key_h).json["error"] == "invalid_track"
    assert client.post("/api/v1/requests", json={"dz_id": "999"}, headers=key_h).status_code == 404


def test_same_source_ref_is_idempotent_but_a_failed_one_can_be_retried(env):
    ops, client = env
    _, key_h = make_key(client)
    first = client.post("/api/v1/requests", json={"dz_id": "101", "source_ref": "a"}, headers=key_h).json
    again = client.post("/api/v1/requests", json={"dz_id": "101", "source_ref": "a"}, headers=key_h)
    assert again.status_code == 200 and again.json["result"] == "already_requested" and again.json["id"] == first["id"]
    ops.db.transition(first["id"], "failed", "boom")
    third = client.post("/api/v1/requests", json={"dz_id": "101", "source_ref": "a"}, headers=key_h).json
    assert third["result"] == "queued" and third["id"] != first["id"]


def test_in_flight_track_is_not_queued_twice(env):
    _, client = env
    _, key_h = make_key(client)
    client.post("/api/v1/requests", json={"dz_id": "101"}, headers=key_h)
    r = client.post("/api/v1/requests", json={"dz_id": "101", "source_ref": "other"}, headers=key_h)
    assert r.status_code == 200 and r.json["result"] == "already_queued"


def test_owned_track_is_reported_not_queued(env, monkeypatch):
    ops, client = env
    _, key_h = make_key(client)
    monkeypatch.setattr("music_grabber.ops.library_match", lambda *a, **k: "Dua Lipa/x.mp3")
    r = client.post("/api/v1/requests", json={"dz_id": "101"}, headers=key_h)
    assert r.json["result"] == "in_library" and ops.queue_list()["items"] == []
    forced = client.post("/api/v1/requests", json={"dz_id": "101", "force": True}, headers=key_h)
    assert forced.json["result"] == "queued"


def test_a_key_sees_only_its_own_requests(env):
    _, client = env
    _, a = make_key(client, "A")
    _, b = make_key(client, "B")
    rid = client.post("/api/v1/requests", json={"dz_id": "101"}, headers=a).json["id"]
    got = client.get(f"/api/v1/requests/{rid}", headers=a).json
    assert got["status"] == "pending" and got["requested_by"] == "A" and "tmp_path" not in got
    assert client.get(f"/api/v1/requests/{rid}", headers=b).status_code == 404
    assert client.get(f"/api/v1/requests/{rid}", headers=H).json["id"] == rid        # admins see all
    assert client.get("/api/v1/requests/9999", headers=a).status_code == 404


def test_library_has(env, monkeypatch):
    _, client = env
    _, key_h = make_key(client)
    assert client.get("/api/v1/library/has", headers=key_h).json["error"] == "invalid_track"
    r = client.get("/api/v1/library/has?title=Levitating&artist=Dua+Lipa", headers=key_h)
    assert r.json == {"have": False}
    monkeypatch.setattr("music_grabber.ops.library_match", lambda *a, **k: "some/path.mp3")
    r = client.get("/api/v1/library/has?isrc=GBAHT2000001", headers=key_h)
    assert r.json == {"have": True}                                     # never a path


def test_accounts_and_the_api_token_can_use_v1_and_are_attributed(env):
    ops, client = env
    r = client.post("/api/v1/requests", json={"dz_id": "101"}, headers=H)
    assert ops.db.get_grab_item(r.json["id"])["requested_by"] == "api-token"


def test_keys_create_via_ops_rejects_bad_scopes():
    with pytest.raises(OpsError):
        Ops.keys_create(object.__new__(Ops), "n", ["nope"])
