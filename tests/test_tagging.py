"""Cover art resize + embed/read roundtrip; tag-write mtime handling; last_change token."""

import io
import os

from mutagen.flac import FLAC
from PIL import Image

from audio_fixtures import write_flac
from music_grabber.db import Database
from music_grabber.tagging import embed_cover, read_cover, resize_cover, write_track_tags


def _png(size):
    img = Image.new("RGB", (size, size), (120, 40, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_resize_cover_downscales_large():
    big = _png(2000)
    out = resize_cover(big, max_px=1200)
    img = Image.open(io.BytesIO(out))
    assert max(img.size) <= 1200
    assert img.format == "JPEG"


def test_resize_cover_leaves_small_untouched():
    small = _png(500)
    assert resize_cover(small, max_px=1200) is small


def test_embed_and_read_cover_flac(tmp_path):
    f = tmp_path / "song.flac"
    write_flac(f)
    jpeg = resize_cover(_png(2000))                    # >1200 → re-encoded to JPEG
    assert jpeg[:3] == b"\xff\xd8\xff"
    embed_cover(str(f), jpeg, mime="image/jpeg")
    assert FLAC(str(f)).pictures                       # embedded
    cover = read_cover(str(f))
    assert cover is not None and cover[0][:3] == b"\xff\xd8\xff"  # JPEG magic


def _flac(path):
    write_flac(path)
    # Backdate so any bump is unmistakable.
    old = os.stat(str(path)).st_mtime - 100_000
    os.utime(str(path), (old, old))
    return old


# Tag and cover writes must preserve mtime (watchers and media servers key off it).

def test_write_track_tags_preserves_mtime_by_default(tmp_path):
    f = tmp_path / "song.flac"
    old = _flac(f)
    assert write_track_tags(str(f), {"title": "T", "artist": "A"}) is None
    assert FLAC(str(f))["title"] == ["T"]
    assert os.stat(str(f)).st_mtime == old


def test_write_track_tags_bumps_mtime_when_disabled(tmp_path):
    f = tmp_path / "song.flac"
    old = _flac(f)
    assert write_track_tags(str(f), {"title": "T"}, preserve_mtime=False) is None
    assert os.stat(str(f)).st_mtime != old


def test_embed_cover_preserves_mtime_by_default(tmp_path):
    f = tmp_path / "song.flac"
    old = _flac(f)
    assert embed_cover(str(f), resize_cover(_png(2000))) is None
    assert read_cover(str(f)) is not None
    assert os.stat(str(f)).st_mtime == old


def test_embed_cover_bumps_mtime_when_disabled(tmp_path):
    f = tmp_path / "song.flac"
    old = _flac(f)
    assert embed_cover(str(f), resize_cover(_png(2000)), preserve_mtime=False) is None
    assert os.stat(str(f)).st_mtime != old


def test_last_change_updates_on_enqueue(tmp_path):
    db = Database(str(tmp_path / "mg.db"))
    before = db.get_last_change()
    db.enqueue_grab({"spotify_track_id": "s1", "title": "X"})
    assert db.get_last_change() != before


def test_fetch_cover_refuses_non_public_and_non_http_urls(monkeypatch):
    import requests
    from music_grabber.tagging import fetch_cover
    def never(*a, **k): raise AssertionError("must not connect")
    monkeypatch.setattr(requests, "get", never)
    for url in ("http://127.0.0.1:8080/x.jpg", "http://192.168.1.5/x", "http://[::1]/x",
                "file:///etc/passwd", "ftp://example.com/x", "http://169.254.169.254/latest"):
        assert fetch_cover(url) is None


def test_update_grab_rejects_unknown_columns(tmp_path):
    import pytest
    from music_grabber.db import Database
    db = Database(str(tmp_path / "g.db"))
    with pytest.raises(ValueError, match="unknown"):
        db.update_grab(1, **{"note=1, status": "x"})


def test_database_file_is_owner_only(tmp_path):
    import os
    import stat
    from music_grabber.db import Database
    Database(str(tmp_path / "g.db"))
    assert stat.S_IMODE(os.stat(tmp_path / "g.db").st_mode) == 0o600
