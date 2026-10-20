import pytest

from music_grabber import main as mg
from music_grabber.config import __version__


def test_version_command(capsys):
    assert mg.main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_parse_callback_accepts_full_url_or_bare_code():
    url = "http://127.0.0.1:8099/callback?code=AQD123&state=ST"
    assert mg.parse_callback(url, "ST") == "AQD123"
    assert mg.parse_callback("  AQD123  ", "ST") == "AQD123"


def test_parse_callback_rejects_state_mismatch_and_errors():
    with pytest.raises(ValueError, match="state mismatch"):
        mg.parse_callback("http://x/cb?code=C&state=OTHER", "ST")
    with pytest.raises(ValueError, match="access_denied"):
        mg.parse_callback("http://x/cb?error=access_denied&state=ST", "ST")


def test_spotify_login_without_client_config_explains_itself(tmp_path, monkeypatch, capsys):
    for k in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "mg.db"))
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "music"))
    assert mg.main(["spotify-login"]) == 2
    assert "not configured" in capsys.readouterr().err


def test_spotify_login_end_to_end_with_fake_exchange(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8099/cb")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "mg.db"))
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "music"))
    exchanged = []

    def fake_exchange(self, code):
        exchanged.append(code)
        self.db.save_oauth_token("spotify", "a", "r", "2099-01-01T00:00:00+00:00", "")

    monkeypatch.setattr("music_grabber.spotify.SpotifyClient.exchange_code", fake_exchange)

    def fake_input(prompt=""):
        import re
        out = capsys.readouterr().out
        state = re.search(r"state=([\w-]+)", out).group(1)
        return f"http://127.0.0.1:8099/cb?code=THECODE&state={state}"

    monkeypatch.setattr("builtins.input", fake_input)
    assert mg.main(["spotify-login"]) == 0
    assert exchanged == ["THECODE"]
