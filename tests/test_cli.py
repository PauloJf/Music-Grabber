"""The CLI: parsing, local management commands, --json, exit codes, and --remote."""

import json
import threading

import pytest
from test_api import TOKEN
from test_ops import FakeClient
from werkzeug.serving import make_server

from music_grabber import cli
from music_grabber.api import create_app
from music_grabber.config import build_config
from music_grabber.db import Database
from music_grabber.main import build_service, main
from music_grabber.ops import Ops
from music_grabber.remote import RemoteOps


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI", "DEEZER_ARL", "API_TOKEN",
              "WATCH_PLAYLISTS", "ANALYSIS", "INTAKE_DIR", "POST_IMPORT_HOOKS", "LIBRARY_INDEX", "MG_REMOTE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "mg.db"))
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "music"))
    monkeypatch.setenv("PROVIDER_ORDER", "ytdlp")
    return tmp_path


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def seed(env_dir):
    db = Database(str(env_dir / "mg.db"))
    pid = db.add_playlist("PL1", "Road Trip")
    a = db.enqueue_grab({"spotify_track_id": "s1", "title": "Blinding Lights", "artist": "The Weeknd", "isrc": "X"})
    db.transition(a, "awaiting_user", "best 0.70 below auto-accept")
    db.add_grab_candidates(a, [{"provider": "deezer", "provider_track_id": "1", "title": "Blinding Lights",
                                "artist": "The Weeknd", "quality": "MP3_128", "score": 0.7, "rank": 0}])
    return db, pid, a


def test_no_command_means_run_and_help_lists_everything():
    p = cli.build_parser()
    assert p.parse_args([]).command is None
    text = p.format_help()
    for word in ("sync", "queue", "inbox", "status", "import-bpm-tagger", "spotify-login"):
        assert word in text


def test_status_text_and_json(env, capsys):
    seed(env)
    code, out, _ = run(["status"], capsys)
    assert code == 0 and "Spotify     NOT connected" in out and "1 awaiting_user" in out
    assert "Providers   ytdlp" in out and "Playlists   1/1 watched" in out
    code, out, _ = run(["--json", "status"], capsys)
    data = json.loads(out)
    assert data["queue"] == {"awaiting_user": 1} and data["playlists"]["total"] == 1


def test_inbox_show_lists_candidate_ids_and_the_next_commands(env, capsys):
    _db, _pid, a = seed(env)
    code, out, _ = run(["inbox"], capsys)
    assert code == 0 and "Blinding Lights" in out and "1 candidate(s)" in out
    code, out, _ = run(["inbox", "show", str(a)], capsys)
    assert "MP3_128" in out and "0.70" in out and f"inbox choose {a} <candidate id>" in out


def test_inbox_choose_then_the_item_is_pending(env, capsys):
    db, _pid, a = seed(env)
    cand = db.get_grab_candidates(a)[0]["id"]
    code, out, _ = run(["inbox", "choose", str(a), str(cand)], capsys)
    assert code == 0 and "queued candidate" in out
    assert db.get_grab_item(a)["status"] == "pending"
    code, _, err = run(["inbox", "skip", str(a)], capsys)           # no longer awaiting_user
    assert code == 2 and "not_awaiting" in err                      # 2 = the request was invalid


def test_inbox_search_joins_the_words_and_skip_works(env, capsys):
    db, _pid, a = seed(env)
    assert run(["inbox", "search", str(a), "weeknd", "blinding", "lights"], capsys)[0] == 0
    assert db.get_grab_item(a)["search_override"] == "weeknd blinding lights"
    b = db.enqueue_grab({"spotify_track_id": "s2", "title": "T"})
    db.transition(b, "awaiting_user", "x")
    assert run(["inbox", "skip", str(b)], capsys)[0] == 0
    assert db.get_grab_item(b)["status"] == "skipped"


def test_queue_commands(env, capsys):
    db, _pid, a = seed(env)
    assert run(["queue", "add", "Song", "--artist", "Band", "--spotify-id", "sp9"], capsys)[1].startswith("Queued #")
    code, out, _ = run(["queue"], capsys)
    assert code == 0 and "Blinding Lights" in out and "Song" in out
    code, out, _ = run(["queue", "show", str(a)], capsys)
    assert "awaiting_user" in out and "best 0.70" in out
    assert run(["queue", "priority", str(a), "7"], capsys)[0] == 0 and db.get_grab_item(a)["priority"] == 7
    assert run(["queue", "cancel", str(a)], capsys)[0] == 0
    assert run(["queue", "retry", str(a)], capsys)[0] == 0
    code, _, err = run(["queue", "delete", str(a)], capsys)
    assert code == 2 and "not_deletable" in err
    assert "Re-queued 0" in run(["queue", "retry-failed"], capsys)[1]
    assert "Removed 0" in run(["queue", "clear-completed"], capsys)[1]
    assert run(["queue", "history"], capsys)[0] == 0


def test_playlist_commands_without_a_spotify_connection(env, capsys):
    code, out, _ = run(["playlist", "add", "https://open.spotify.com/playlist/AAA?si=1"], capsys)
    assert code == 0 and "first sync" in out
    code, out, _ = run(["playlist", "list"], capsys)
    assert "AAA" in out
    assert run(["playlist", "disable", "1"], capsys)[1].strip() == "Playlist #1 disabled"
    assert run(["playlist", "show", "1"], capsys)[0] == 0
    assert run(["playlist", "remove", "1"], capsys)[0] == 0
    code, _, err = run(["playlist", "show", "1"], capsys)
    assert code == 1 and "not_found" in err


def test_playlist_queue_missing_and_the_empty_library_note(env, capsys):
    db, pid, _ = seed(env)
    from test_sync_engine import _t
    db.sync_playlist_tracks(pid, [_t("s7", "Seven", "Band")])
    (row,) = db.get_playlist_track_rows(pid)
    db.set_playlist_track_match(row["id"], "missing", None)
    code, out, _ = run(["playlist", "queue-missing", str(pid)], capsys)
    assert code == 0 and "Queued 1 missing" in out
    assert any(i["spotify_track_id"] == "s7" for i in db.get_queue())
    assert "EMPTY: auto-queueing paused" not in run(["status"], capsys)[1]   # new install: nothing to protect
    db.bump_grabbed_total()                                                  # it has grabbed before
    assert "EMPTY: auto-queueing paused" in run(["status"], capsys)[1]
    assert json.loads(run(["--json", "status"], capsys)[1])["library"]["auto_queue_paused"] is True


def test_sync_without_spotify_is_a_clear_error_with_exit_code_2(env, capsys):
    code, _, err = run(["sync"], capsys)
    assert code == 2 and "spotify_disconnected" in err and "spotify-login" in err


def test_settings_commands(env, capsys):
    code, out, _ = run(["settings", "set", "output_format", "flac", "grab_dry_run", "true"], capsys)
    assert code == 0 and "output_format" in out
    code, out, _ = run(["settings", "show"], capsys)
    assert "output_format = flac" in out and "deezer_arl = " in out
    code, _, err = run(["settings", "set", "output_format", "wav"], capsys)
    assert code == 2 and "invalid_setting" in err
    code, _, err = run(["settings", "set", "output_format"], capsys)
    assert code == 2 and "KEY VALUE" in err


def test_library_refresh_and_misconfiguration(env, capsys):
    (env / "music").mkdir()
    assert "0 files" in run(["library", "refresh"], capsys)[1]


def test_import_env_never_prints_secrets(tmp_path, capsys):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"output_format": "flac", "provider_order": "deezer,monochrome,ytdlp",
                             "grab_dry_run": True, "deezer_arl": "SECRET-ARL", "ntfy_topic": "SECRET-TOPIC",
                             "ntfy_url": "https://ntfy.sh", "navidrome_pass": "x",
                             "ui_public_url": "https://bpm.example"}))
    code = cli.run_command(cli.build_parser().parse_args(["import-env", str(f)]), {})
    out = capsys.readouterr().out
    assert code == 0 and "OUTPUT_FORMAT=flac" in out and "PROVIDER_ORDER=deezer,ytdlp" in out
    assert "GRAB_DRY_RUN=true" in out and "NTFY_URL=https://ntfy.sh" in out
    assert "SECRET-ARL" not in out and "SECRET-TOPIC" not in out
    assert "# DEEZER_ARL=" in out and "# NTFY_TOPIC=" in out
    # BPM Tagger's own address must not become Music Grabber's (ntfy links would point at the wrong app)
    assert "bpm.example" not in out and "\nUI_PUBLIC_URL=" not in out and "# UI_PUBLIC_URL=" in out
    code = cli.run_command(cli.build_parser().parse_args(["import-env", str(tmp_path / "nope.json")]), {})
    assert code == 1


# ── --remote ──────────────────────────────────────────────────────────────────
@pytest.fixture
def server(tmp_path):
    cfg = {"db_path": str(tmp_path / "srv.db"), "music_dir": str(tmp_path / "music"),
           "provider_order": "ytdlp", "output_format": "mp3-128", "auto_accept_threshold": 0.85,
           "ask_threshold": 0.55, "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}"}
    from music_grabber.sync_engine import GrabberService
    svc = GrabberService(cfg, Database(cfg["db_path"]))
    svc.client = FakeClient()
    svc.sync.client = svc.client
    ops = Ops(svc, cfg)
    srv = make_server("127.0.0.1", 0, create_app(ops, TOKEN), threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}", ops
    srv.shutdown()


def test_remote_ops_round_trip_against_a_running_api(server):
    url, ops = server
    r = RemoteOps(url, TOKEN)
    assert r.status()["spotify"]["connected"] is True
    item = r.queue_add({"title": "Song", "artist": "Band", "spotify_track_id": "s1"})["id"]
    assert ops.db.get_grab_item(item)["title"] == "Song"
    assert r.queue_list("pending")["items"][0]["id"] == item
    assert r.queue_item(item)["item"]["id"] == item
    pid = r.playlist_add("PL1")["playlist"]["id"]
    assert r.playlist_get(pid)["playlist"]["name"] == "Road Trip"
    assert r.settings_update({"output_format": "flac"})["applied"] == ["output_format"]
    assert ops.config["output_format"] == "flac"           # applied in the running instance
    assert r.playlist_enable(pid, False)["enabled"] is False
    r.playlist_remove(pid)


def test_remote_ops_maps_server_errors_back_to_opserror(server):
    from music_grabber.ops import OpsError
    url, _ops = server
    r = RemoteOps(url, TOKEN)
    with pytest.raises(OpsError) as e:
        r.queue_item(99999)
    assert e.value.code == "not_found" and e.value.http == 404
    with pytest.raises(OpsError) as e:
        RemoteOps(url, "wrong-token-wrong-token").status()
    assert e.value.code == "unauthorized" and e.value.http == 401


def test_remote_ops_unreachable_is_a_clear_error():
    from music_grabber.ops import OpsError
    with pytest.raises(OpsError) as e:
        RemoteOps("http://127.0.0.1:9", TOKEN, timeout=2).status()
    assert e.value.code == "unreachable" and TOKEN not in str(e.value)


def test_cli_remote_flag_drives_the_running_instance(server, env, capsys, monkeypatch):
    url, ops = server
    monkeypatch.setenv("API_TOKEN", TOKEN)
    code, out, _ = run(["--remote", url, "queue", "add", "Remote Song", "--spotify-id", "r1"], capsys)
    assert code == 0 and out.startswith("Queued #")
    assert ops.db.get_queue()[0]["title"] == "Remote Song"
    code, out, _ = run(["--remote", url, "status"], capsys)
    assert code == 0 and "1 pending" in out
    monkeypatch.delenv("API_TOKEN")
    code, _, err = run(["--remote", url, "status"], capsys)
    assert code == 1 and "token_required" in err


def test_make_ops_local_builds_the_service_without_starting_threads(env):
    args = cli.build_parser().parse_args(["status"])
    ops = cli.make_ops(args, build_config(), build_service)
    assert not ops.svc._thread_started


def test_json_flag_works_before_or_after_the_command(env, capsys):
    seed(env)
    for argv in (["--json", "status"], ["status", "--json"], ["queue", "list", "--json"],
                 ["--json", "inbox", "list"], ["inbox", "list", "--json"], ["playlist", "list", "--json"]):
        code, out, _ = run(argv, capsys)
        assert code == 0 and isinstance(json.loads(out), dict), argv


def test_users_commands_create_list_reset_and_protect_the_last_admin(env, capsys, monkeypatch):
    import io
    def with_pw(argv, pw):
        monkeypatch.setattr("sys.stdin", io.StringIO(pw + "\n"))
        return run(argv, capsys)
    assert run(["users", "list"], capsys)[1].startswith("No accounts")
    code, out, _ = with_pw(["users", "add", "paulo", "--role", "admin"], "a long enough secret")
    assert code == 0 and "Created admin 'paulo'" in out
    code, out, err = with_pw(["users", "add", "bob"], "short")
    assert code != 0 and "at least" in (out + err)
    assert "paulo" in run(["users", "list"], capsys)[1] and "secret" not in run(["users", "list", "--json"], capsys)[1]
    assert with_pw(["users", "passwd", "PAULO"], "another long secret")[0] == 0
    assert run(["users", "delete", "paulo"], capsys)[0] != 0               # the last admin stays
    assert run(["users", "role", "paulo", "user"], capsys)[0] != 0
    assert run(["users", "delete", "ghost"], capsys)[0] != 0
