"""The mirror through its front doors: config wiring, Ops, the API (admin only) and the CLI."""

import json

import pytest
from fake_servers import FakeServer
from test_accounts import PW, bearer, setup_admin  # noqa: F401  (fixtures/helpers shared with the accounts tests)
from test_mirror import PATHS, FakeNavidrome, playlist, writes

from music_grabber import cli
from music_grabber import main as mg
from music_grabber.api import create_app
from music_grabber.db import Database
from music_grabber.main import main
from music_grabber.ops import Ops, OpsError
from music_grabber.sync_engine import GrabberService
from music_grabber.wiring import ConfigError, build_mirror

CFG = {"playlist_mirror": "navidrome", "navidrome_user": "u", "navidrome_pass": "p", "mirror_prefix": "Spotify: "}


def _cfg(tmp_path, **kw):
    return {"db_path": str(tmp_path / "mg.db"), "music_dir": str(tmp_path / "music"), "post_import_hooks": [], **kw}


# ── wiring ──────────────────────────────────────────────────────────────────────────────
def test_the_mirror_is_off_unless_asked_for_and_misconfiguration_is_explained(tmp_path):
    db = Database(str(tmp_path / "mg.db"))
    assert build_mirror({}, db) is None and build_mirror({"playlist_mirror": ""}, db) is None
    with pytest.raises(ConfigError, match="NAVIDROME_URL"):
        build_mirror({**CFG}, db)
    with pytest.raises(ConfigError, match="unknown PLAYLIST_MIRROR 'plex'"):
        build_mirror({"playlist_mirror": "plex"}, db)
    m = build_mirror({**CFG, "navidrome_url": "http://nd:4533/"}, db)
    assert m.url == "http://nd:4533" and m.prefix == "Spotify: "
    m.close()


def test_build_service_connects_sync_and_placements_to_the_mirror(tmp_path):
    svc = mg.build_service(_cfg(tmp_path, **CFG, navidrome_url="http://127.0.0.1:9"))
    try:
        assert svc.mirror is not None and svc.sync.on_synced == svc.mirror.request
        assert svc.mirror.on_placed in svc.pool.pipeline.on_placed
    finally:
        svc.hooks.close()
        svc.mirror.close()
    off = mg.build_service(_cfg(tmp_path))
    assert off.mirror is None and off.sync.on_synced is None
    off.hooks.close()


# ── ops ─────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def parts(tmp_path):
    nav = FakeNavidrome(PATHS)
    srv = FakeServer(nav.handler)
    srv.__enter__()
    cfg = _cfg(tmp_path, provider_order="ytdlp", output_format="mp3-128", auto_accept_threshold=0.85,
               ask_threshold=0.55, path_template="{Title}.{ext}", **CFG, navidrome_url=srv.url)
    svc = mg.build_service(cfg)
    ops = Ops(svc, cfg)
    yield ops, nav, srv, svc
    svc.hooks.close()
    svc.mirror.close()
    srv.__exit__()


def test_ops_report_the_mirror_and_run_it(parts):
    ops, nav, srv, svc = parts
    pid = playlist(svc.db)
    assert ops.status()["mirror"] == {"enabled": True, "disabled": False}
    assert ops.playlists_list()["playlists"][0]["mirror"]["state"] == "pending"
    st = ops.playlist_mirror(pid, "now")
    assert st["enabled"] and st["playlists"][pid]["state"] == "ok" and st["playlists"][pid]["matched"] == 3
    assert ops.playlists_list()["playlists"][0]["mirror"]["name"] == "Spotify: Mix"
    assert len(nav.playlists) == 1


def test_ops_actions_on_off_reset_remove_and_validation(parts):
    ops, nav, srv, svc = parts
    pid = playlist(svc.db)
    assert ops.playlist_mirror(pid, "off") == {"ok": True, "enabled": False}
    assert ops.playlists_list()["playlists"][0]["mirror"]["state"] == "off"
    ops.playlist_mirror(pid, "on")
    ops.mirror_run()
    nav.playlists.clear()
    ops.mirror_run()
    assert ops.mirror_status()["playlists"][pid]["state"] == "deleted_remotely"
    assert ops.playlist_mirror(pid, "reset")["playlists"][pid]["state"] == "ok"
    assert ops.playlist_mirror(pid, "remove") == {"ok": True, "removed": True} and nav.playlists == {}
    with pytest.raises(OpsError) as e:
        ops.playlist_mirror(pid, "explode")
    assert e.value.code == "invalid_action"
    with pytest.raises(OpsError) as e:
        ops.playlist_mirror(999, "now")
    assert e.value.code == "not_found"


def test_ops_without_the_mirror_say_so(tmp_path):
    cfg = _cfg(tmp_path, provider_order="ytdlp", output_format="mp3-128", auto_accept_threshold=0.85,
               ask_threshold=0.55, path_template="{Title}.{ext}")
    svc = GrabberService(cfg, Database(cfg["db_path"]))
    ops = Ops(svc, cfg)
    pid = playlist(svc.db)
    assert ops.mirror_status()["enabled"] is False and ops.playlists_list()["playlists"][0]["mirror"] is None
    for call in (lambda: ops.mirror_run(), lambda: ops.playlist_mirror(pid, "now")):
        with pytest.raises(OpsError) as e:
            call()
        assert e.value.code == "mirror_disabled"
    assert ops.playlist_mirror(pid, "off")["ok"]                    # the switch itself is harmless


def test_a_navidrome_failure_is_a_502_not_a_crash(parts):
    ops, nav, srv, svc = parts
    playlist(svc.db)
    nav.user_ok = False
    with pytest.raises(OpsError) as e:
        ops.mirror_run()
    assert e.value.code == "navidrome_error" and e.value.http == 502


# ── API ─────────────────────────────────────────────────────────────────────────────────
def test_the_api_routes_and_that_only_admins_may_write(parts):
    ops, nav, srv, svc = parts
    pid = playlist(svc.db)
    from test_api import TOKEN
    app = create_app(ops, TOKEN)
    c = app.test_client()
    H = {"Authorization": f"Bearer {TOKEN}"}
    assert c.get("/api/mirror", headers=H).json["enabled"] is True
    r = c.post(f"/api/playlists/{pid}/mirror", json={"action": "now"}, headers=H)
    assert r.status_code == 200 and r.json["playlists"][str(pid)]["state"] == "ok"
    assert c.post("/api/mirror/run", headers=H).status_code == 200
    assert c.post(f"/api/playlists/{pid}/mirror", json={"action": "x"}, headers=H).status_code == 400
    admin = setup_admin(c)
    assert c.post("/api/users", headers=bearer(admin), json={"username": "guest", "password": PW}).status_code == 201
    g = c.post("/api/auth/login", json={"username": "guest", "password": PW}).json["token"]
    assert c.get("/api/mirror", headers=bearer(g)).status_code == 200                 # reading is fine
    assert c.get("/api/playlists", headers=bearer(g)).status_code == 200
    for path in ("/api/mirror/run", f"/api/playlists/{pid}/mirror", "/api/ledger/rebuild"):
        assert c.post(path, headers=bearer(g), json={"action": "remove"}).status_code == 403, path
    assert len(nav.playlists) == 1                                                       # nothing was removed


# ── CLI ─────────────────────────────────────────────────────────────────────────────────
def test_the_cli_runs_and_controls_the_mirror(tmp_path, monkeypatch, capsys):
    nav = FakeNavidrome(PATHS)
    with FakeServer(nav.handler) as srv:
        for k, v in {"DB_PATH": str(tmp_path / "mg.db"), "MUSIC_DIR": str(tmp_path / "music"), "PROVIDER_ORDER": "ytdlp",
                     "PLAYLIST_MIRROR": "navidrome", "NAVIDROME_URL": srv.url, "NAVIDROME_USER": "u",
                     "NAVIDROME_PASS": "p"}.items():
            monkeypatch.setenv(k, v)
        pid = playlist(Database(str(tmp_path / "mg.db")))

        def run(*argv):
            code = main(list(argv))
            return code, capsys.readouterr()
        code, out = run("playlist", "mirror", str(pid))
        assert code == 0 and "ok" in out.out and "Mix" not in out.err and len(nav.playlists) == 1
        code, out = run("playlist", "mirror")                                   # every watched playlist
        assert code == 0 and writes(srv) and "ok" in out.out
        assert run("playlist", "mirror", str(pid), "--off")[1].out.strip() == f"Playlist #{pid} mirror off"
        code, out = run("playlist", "mirror", "--off")
        assert code == 2 and "needs a playlist id" in out.err
        code, out = run("--json", "playlist", "mirror", str(pid), "--remove")
        assert code == 0 and json.loads(out.out) == {"ok": True, "removed": True} and nav.playlists == {}
        assert "Mirror      navidrome" in run("status")[1].out
        assert cli.build_parser().parse_args(["playlist", "mirror", "3", "--reset"]).reset is True
