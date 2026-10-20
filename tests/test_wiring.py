"""Config → library source / analysis, and the wiring in main.build_service."""

import pytest

from music_grabber import main as mg
from music_grabber.analysis import ApiAnalysis, IntakeFolderAnalysis, NoAnalysis
from music_grabber.library import FilesystemSource
from music_grabber.library_opensubsonic import OpenSubsonicSource
from music_grabber.wiring import ConfigError, build_analysis, build_library_source


def test_defaults_are_standalone():
    cfg = {"music_dir": "/m"}
    src = build_library_source(cfg)
    assert isinstance(src, FilesystemSource) and src.root == "/m"
    assert isinstance(build_analysis(cfg), NoAnalysis)


def test_library_dir_wins_over_music_dir():
    assert build_library_source({"music_dir": "/m/raw", "library_dir": "/m"}).root == "/m"


@pytest.mark.parametrize("cfg, msg", [
    ({"library_index": "opensubsonic"}, "LIBRARY_URL"),
    ({"library_index": "opensubsonic", "library_url": "http://x"}, "LIBRARY_API_KEY"),
    ({"library_index": "plex"}, "unknown LIBRARY_INDEX 'plex'"),
])
def test_library_misconfiguration_is_explained(cfg, msg):
    with pytest.raises(ConfigError, match=msg):
        build_library_source({"music_dir": "/m", **cfg})


def test_opensubsonic_source_from_key_or_user_pass():
    s = build_library_source({"music_dir": "/m", "library_index": "opensubsonic",
                              "library_url": "http://bt:5000", "library_api_key": "k"})
    assert isinstance(s, OpenSubsonicSource) and s.api_key == "k"
    s = build_library_source({"music_dir": "/m", "library_index": "opensubsonic",
                              "library_url": "http://nd:4533", "library_user": "u", "library_pass": "p"})
    assert (s.user, s.password) == ("u", "p")


@pytest.mark.parametrize("cfg, msg", [
    ({"analysis": "intake"}, "INTAKE_DIR"),
    ({"analysis": "api", "intake_dir": "/i"}, "BPM_TAGGER_URL and BPM_TAGGER_PASSWORD"),
    ({"analysis": "magic"}, "unknown ANALYSIS 'magic'"),
])
def test_analysis_misconfiguration_is_explained(cfg, msg):
    with pytest.raises(ConfigError, match=msg):
        build_analysis(cfg)


def test_intake_and_api_analysis_are_built():
    a = build_analysis({"analysis": "intake", "intake_dir": "/music/Music-raw",
                        "analysis_wait_seconds": 12})
    assert isinstance(a, IntakeFolderAnalysis) and a.wait_seconds == 12
    a = build_analysis({"analysis": "api", "intake_dir": "/i", "bpm_tagger_url": "http://bt",
                        "bpm_tagger_password": "pw"})
    try:
        assert isinstance(a, ApiAnalysis) and a.client.url == "http://bt"
    finally:
        a.close()


def _cfg(tmp_path, **kw):
    return {"db_path": str(tmp_path / "mg.db"), "music_dir": str(tmp_path / "music"),
            "post_import_hooks": [], **kw}


def test_build_service_wires_intake_mode_end_to_end(tmp_path):
    cfg = _cfg(tmp_path, analysis="intake", intake_dir=str(tmp_path / "music" / "Music-raw"),
               post_import_hooks=["mediarium"], mediarium_url="http://m", mediarium_api_key="k")
    svc = mg.build_service(cfg)
    try:
        assert svc.analysis.name == "intake" and svc.hooks.names == ["mediarium"]
        # Files are filed in the intake dir; the library index still covers the whole music dir.
        assert svc.pool.pipeline.dest_root == str(tmp_path / "music" / "Music-raw")
        assert svc.index.root == str(tmp_path / "music") and svc.index.source.name == "filesystem"
    finally:
        svc.hooks.close()


def test_bad_config_stops_run_with_exit_code_2_and_a_message(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "mg.db"))
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "music"))
    monkeypatch.setenv("ANALYSIS", "intake")
    monkeypatch.delenv("INTAKE_DIR", raising=False)
    assert mg.main(["run"]) == 2
    assert "INTAKE_DIR" in caplog.text


def test_new_env_vars_are_read(monkeypatch):
    from music_grabber import config as c
    for k, v in {"LIBRARY_INDEX": "OpenSubsonic", "LIBRARY_URL": "http://bt", "ANALYSIS": "Intake",
                 "INTAKE_DIR": "/i", "POST_IMPORT_HOOKS": "Mediarium, navidrome",
                 "HOOK_DEBOUNCE_SECONDS": "5"}.items():
        monkeypatch.setenv(k, v)
    cfg = c.build_config()
    assert cfg["library_index"] == "opensubsonic" and cfg["analysis"] == "intake"
    assert cfg["post_import_hooks"] == ["mediarium", "navidrome"] and cfg["hook_debounce_seconds"] == 5.0


def test_new_secrets_are_masked_and_never_persisted(tmp_path):
    from music_grabber import config as c
    secrets = ("library_api_key", "library_pass", "bpm_tagger_password", "mediarium_api_key", "navidrome_pass")
    assert all(k in c.SECRET_KEYS and k in c.ENV_ONLY_KEYS for k in secrets)
    path = str(tmp_path / "settings.json")
    c.save_settings(path, {k: "x" for k in secrets} | {"output_format": "flac"})
    import json
    assert json.load(open(path)) == {"output_format": "flac"}
