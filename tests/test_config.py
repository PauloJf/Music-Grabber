import json
import os

from music_grabber import config as cfgmod


def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("SPOTIFY_", "DEEZER_", "NTFY_")) or k in (
                "MUSIC_DIR", "LIBRARY_DIR", "DB_PATH", "DATA_DIR", "WATCH_PLAYLISTS",
                "PROVIDER_ORDER", "OUTPUT_FORMAT", "GRAB_DRY_RUN", "LYRICS_ENABLED"):
            monkeypatch.delenv(k, raising=False)


def test_defaults(monkeypatch):
    _clean_env(monkeypatch)
    c = cfgmod.build_config()
    assert c["music_dir"] == "/music" and c["library_dir"] == "/music"
    assert c["db_path"] == "/data/music_grabber.db"
    assert c["provider_order"] == "deezer,ytdlp" and c["output_format"] == "mp3-128"
    assert c["grab_dry_run"] is False and c["watch_playlists"] == []
    assert c["auto_accept_threshold"] == 0.85 and c["ask_threshold"] == 0.55


def test_env_names_match_bpm_tagger_and_library_dir_follows_music_dir(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("MUSIC_DIR", "/m/Music-raw")
    monkeypatch.setenv("DEEZER_ARL", "arl")
    monkeypatch.setenv("GRAB_DRY_RUN", "true")
    monkeypatch.setenv("WATCH_PLAYLISTS", "https://open.spotify.com/playlist/abc?si=1, spotify:playlist:def\nghi")
    c = cfgmod.build_config()
    assert c["music_dir"] == "/m/Music-raw" and c["library_dir"] == "/m/Music-raw"
    assert c["deezer_arl"] == "arl" and c["grab_dry_run"] is True
    assert c["watch_playlists"] == ["https://open.spotify.com/playlist/abc?si=1", "spotify:playlist:def", "ghi"]
    monkeypatch.setenv("LIBRARY_DIR", "/m")
    assert cfgmod.build_config()["library_dir"] == "/m"


def test_settings_override_roundtrip_and_env_only_keys(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "from-env")
    c = cfgmod.build_config()
    c["db_path"] = str(tmp_path / "mg.db")
    path = cfgmod.settings_file_path(c["db_path"])
    cfgmod.save_settings(path, {"output_format": "flac", "spotify_client_secret": "nope", "x": None})
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    saved = json.load(open(path))
    assert saved == {"output_format": "flac"}          # secrets never persisted
    # A hand-edited file cannot override env-only keys either.
    saved["spotify_client_secret"] = "tampered"
    json.dump(saved, open(path, "w"))
    c = cfgmod.load_settings_override(c)
    assert c["output_format"] == "flac" and c["spotify_client_secret"] == "from-env"


def test_masked_hides_secrets():
    m = cfgmod.masked({"deezer_arl": "abc", "spotify_client_secret": "s", "ntfy_topic": "t",
                       "api_token": "tok", "deezer_quality": "x"})
    assert m["deezer_arl"] == "********" and m["spotify_client_secret"] == "********"
    assert m["ntfy_topic"] == "********" and m["api_token"] == "********"   # a topic acts as a password
    assert m["deezer_quality"] == "x"
    assert cfgmod.masked({"deezer_arl": ""})["deezer_arl"] == ""


def test_version_is_read_from_version_file():
    assert cfgmod.__version__ and cfgmod.__version__[0].isdigit()
