"""User accounts: password hashing, login/session, roles, the first-run setup and preferences."""

import pytest
from test_api import TOKEN, FakeClient

from music_grabber import accounts
from music_grabber.api import AuthThrottle, create_app
from music_grabber.db import Database
from music_grabber.ops import Ops
from music_grabber.sync_engine import GrabberService

H = {"Authorization": f"Bearer {TOKEN}"}
PW = "correct horse battery"


@pytest.fixture
def parts(tmp_path):
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
def client(parts):
    return parts[0].test_client()


def bearer(t): return {"Authorization": f"Bearer {t}"}


def setup_admin(client, name="paulo"):
    r = client.post("/api/auth/setup", json={"username": name, "password": PW, "api_token": TOKEN})
    assert r.status_code == 201, r.json
    return r.json["token"]


def test_hash_roundtrip_is_salted_and_rejects_garbage():
    a, b = accounts.hash_password(PW), accounts.hash_password(PW)
    assert a != b and a.startswith("scrypt$") and PW not in a
    assert accounts.verify_password(PW, a) and not accounts.verify_password("nope", a)
    assert not accounts.verify_password(PW, "garbage") and not accounts.verify_password("x" * 5000, a)


@pytest.mark.parametrize("name,ok", [("paulo", True), ("a.b-c_9", True), ("ab", False), ("-bad", False),
                                     ("has space", False), ("x" * 40, False), ("", False)])
def test_username_rules(name, ok):
    assert (accounts.check_username(name) is None) is ok


def test_password_rules():
    assert accounts.check_password("short")
    assert accounts.check_password("samesamesame", "SameSameSame")
    assert accounts.check_password("x" * 300)
    assert accounts.check_password(PW, "paulo") is None


def test_first_run_state_and_setup_needs_the_api_token(client):
    assert client.get("/api/auth/state").json == {"needs_setup": True}
    bad = client.post("/api/auth/setup", json={"username": "paulo", "password": PW, "api_token": "wrong"})
    assert bad.status_code == 401
    t = setup_admin(client)
    assert client.get("/api/auth/state").json == {"needs_setup": False}
    me = client.get("/api/me", headers=bearer(t)).json
    assert me["user"]["username"] == "paulo" and me["user"]["role"] == "admin" and me["via"] == "session"
    again = client.post("/api/auth/setup", json={"username": "evil", "password": PW, "api_token": TOKEN})
    assert again.status_code == 409 and again.json["error"] == "already_set_up"


def test_login_logout_and_wrong_credentials(client):
    setup_admin(client)
    assert client.post("/api/auth/login", json={"username": "paulo", "password": "wrong-password"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "nobody", "password": PW}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "PAULO", "password": PW})     # case-insensitive name
    assert r.status_code == 200 and "password" not in str(r.json)
    t = r.json["token"]
    assert client.get("/api/status", headers=bearer(t)).status_code == 200
    assert client.post("/api/auth/logout", headers=bearer(t)).status_code == 200
    assert client.get("/api/status", headers=bearer(t)).status_code == 401          # revoked


def test_session_tokens_are_stored_hashed(parts, client):
    t = setup_admin(client)
    import sqlite3
    rows = sqlite3.connect(parts[1].db.db_path).execute("SELECT token_hash FROM sessions").fetchall()
    assert rows and all(t not in r[0] and len(r[0]) == 64 for r in rows)


def test_expired_session_is_refused(parts, client):
    t = setup_admin(client)
    import sqlite3
    with sqlite3.connect(parts[1].db.db_path) as c:
        c.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00'")
    assert client.get("/api/status", headers=bearer(t)).status_code == 401


def test_wrong_passwords_feed_the_lockout(parts):
    now = [0.0]
    c = create_app(parts[1], TOKEN, throttle=AuthThrottle(3, 60, clock=lambda: now[0])).test_client()
    for _ in range(3):
        assert c.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code == 429


def test_regular_users_cannot_reach_admin_routes(client):
    admin = setup_admin(client)
    r = client.post("/api/users", headers=bearer(admin), json={"username": "guest", "password": PW})
    assert r.status_code == 201 and r.json["user"]["role"] == "user"
    g = client.post("/api/auth/login", json={"username": "guest", "password": PW}).json["token"]
    assert client.get("/api/status", headers=bearer(g)).status_code == 200            # can use the app
    assert client.get("/api/queue", headers=bearer(g)).status_code == 200
    for method, path in (("get", "/api/settings"), ("post", "/api/settings"), ("get", "/api/users"),
                         ("post", "/api/users"), ("get", "/api/spotify/status")):
        assert getattr(client, method)(path, headers=bearer(g)).status_code == 403, path


def test_user_management_rules(client):
    admin = setup_admin(client)
    h = bearer(admin)
    assert client.post("/api/users", headers=h, json={"username": "Paulo", "password": PW}).status_code == 409
    assert client.post("/api/users", headers=h, json={"username": "bob", "password": "short"}).status_code == 400
    assert client.post("/api/users", headers=h, json={"username": "bob", "password": PW, "role": "root"}).status_code == 400
    bob = client.post("/api/users", headers=h, json={"username": "bob", "password": PW}).json["user"]
    me = client.get("/api/me", headers=h).json["user"]["id"]
    assert client.delete(f"/api/users/{me}", headers=h).status_code == 400             # not yourself
    assert client.patch(f"/api/users/{me}", headers=h, json={"role": "user"}).status_code == 409   # last admin
    assert client.patch(f"/api/users/{bob['id']}", headers=h, json={"role": "admin"}).json["user"]["role"] == "admin"
    assert client.patch(f"/api/users/{me}", headers=h, json={"role": "user"}).status_code == 200
    assert client.delete(f"/api/users/{bob['id']}", headers=H).status_code == 409     # API token: still the last admin
    assert [u["username"] for u in client.get("/api/users", headers=H).json["users"]] == ["bob", "paulo"]


def test_admin_password_reset_signs_the_user_out(client):
    admin = setup_admin(client)
    bob = client.post("/api/users", headers=bearer(admin), json={"username": "bob", "password": PW}).json["user"]
    bt = client.post("/api/auth/login", json={"username": "bob", "password": PW}).json["token"]
    new = "an entirely new secret"
    assert client.patch(f"/api/users/{bob['id']}", headers=bearer(admin), json={"password": new}).status_code == 200
    assert client.get("/api/status", headers=bearer(bt)).status_code == 401
    assert client.post("/api/auth/login", json={"username": "bob", "password": new}).status_code == 200


def test_change_own_password_keeps_this_session_drops_others(client):
    setup_admin(client)
    a = client.post("/api/auth/login", json={"username": "paulo", "password": PW}).json["token"]
    b = client.post("/api/auth/login", json={"username": "paulo", "password": PW}).json["token"]
    bad = client.post("/api/me/password", headers=bearer(a), json={"current": "nope-nope-nope", "new": "brand new password"})
    assert bad.status_code == 403
    assert client.post("/api/me/password", headers=bearer(a), json={"current": PW, "new": "short"}).status_code == 400
    ok = client.post("/api/me/password", headers=bearer(a), json={"current": PW, "new": "brand new password"})
    assert ok.status_code == 200
    assert client.get("/api/status", headers=bearer(a)).status_code == 200
    assert client.get("/api/status", headers=bearer(b)).status_code == 401
    assert client.post("/api/auth/login", json={"username": "paulo", "password": PW}).status_code == 401


def test_preferences_validate_and_persist(client):
    t = setup_admin(client)
    h = bearer(t)
    assert client.patch("/api/me/prefs", headers=h, json={"theme": "dark", "preview_volume": 0.4}).json["prefs"] == \
        {"theme": "dark", "preview_volume": 0.4}
    assert client.get("/api/me", headers=h).json["user"]["prefs"]["theme"] == "dark"
    for bad in ({"theme": "neon"}, {"preview_volume": 3}, {"nope": 1}, {}):
        assert client.patch("/api/me/prefs", headers=h, json=bad).status_code == 400


def test_api_token_is_an_admin_without_a_profile(client):
    assert client.get("/api/me", headers=H).json["via"] == "token"
    assert client.patch("/api/me/prefs", headers=H, json={"theme": "dark"}).status_code == 409
    assert client.get("/api/settings", headers=H).status_code == 200


def test_user_rows_never_expose_hashes(client):
    admin = setup_admin(client)
    text = client.get("/api/users", headers=bearer(admin)).get_data(as_text=True)
    assert "scrypt" not in text and "password" not in text
