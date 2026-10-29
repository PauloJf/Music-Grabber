"""Album covers from the files themselves: thumbnails, caching, signed links, the Deezer fallback. No network."""

import io
import os
import time

import pytest
from audio_fixtures import write_flac
from test_api import H, TOKEN
from test_ops import FakeClient

from music_grabber import library_covers as lc
from music_grabber import tagging
from music_grabber.api import create_app
from music_grabber.db import Database
from music_grabber.ops import Ops
from music_grabber.sync_engine import GrabberService
from music_grabber.text import normalize_artist, normalize_title

DZ = "https://cdn-images.dzcdn.net/images/cover/abc/250x250.jpg"


def jpeg(px=900, color=(200, 30, 30)) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (px, px), color).save(out, format="JPEG")
    return out.getvalue()


def track_row(rel, title, no, album="Future Nostalgia", artist="Dua Lipa"):
    return {"path": rel, "change_token": "t", "source": "filesystem", "title": title, "artist": artist, "album": album,
            "album_artist": artist, "duration_ms": 1000, "isrc": None, "track_no": no, "disc_no": 1, "year": 2020,
            "norm_title": normalize_title(title), "norm_artist": normalize_artist(artist)}


@pytest.fixture
def env(tmp_path):
    lib = tmp_path / "lib"
    (lib / "Dua Lipa" / "Future Nostalgia").mkdir(parents=True)
    (lib / "Nobody" / "Bare").mkdir(parents=True)
    a1, a2 = lib / "Dua Lipa" / "Future Nostalgia" / "01.flac", lib / "Dua Lipa" / "Future Nostalgia" / "02.flac"
    for f in (a1, a2, lib / "Nobody" / "Bare" / "01.flac"):
        write_flac(str(f))
    tagging.embed_cover(str(a2), jpeg())                          # only the SECOND track has a picture
    cfg = {"db_path": str(tmp_path / "data" / "mg.db"), "music_dir": str(lib), "library_dir": str(lib), "provider_order": "ytdlp",
           "output_format": "mp3-128", "auto_accept_threshold": 0.85, "ask_threshold": 0.55,
           "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}"}
    svc = GrabberService(cfg, Database(cfg["db_path"]))
    svc.client = FakeClient()
    svc.sync.client = svc.client
    svc.pool.pipeline.providers = []
    svc.db.library_upsert([track_row("Dua Lipa/Future Nostalgia/01.flac", "Cool", 1), track_row("Dua Lipa/Future Nostalgia/02.flac", "Levitating", 2),
                           track_row("Nobody/Bare/01.flac", "Quiet", 1, album="Bare", artist="Nobody")])
    ops = Ops(svc, cfg)
    return svc, ops, create_app(ops, TOKEN).test_client(), lib


def cover_url(client, artist, album):
    return client.get(f"/api/library/album?artist={artist}&album={album}", headers=H).json["cover_url"]


def test_the_picture_is_shrunk_cached_and_not_read_twice(env, monkeypatch):
    _, ops, _, _ = env
    first = ops.covers.thumbnail("Dua Lipa/Future Nostalgia/02.flac")
    assert first and first[1] == "image/jpeg"
    from PIL import Image
    assert max(Image.open(first[0]).size) <= lc.THUMB_PX
    monkeypatch.setattr(tagging, "read_cover", lambda p: pytest.fail("read the file again"))
    assert ops.covers.thumbnail("Dua Lipa/Future Nostalgia/02.flac") == first


def test_a_file_without_a_picture_is_remembered_and_a_changed_file_is_read_again(env, monkeypatch):
    _, ops, _, lib = env
    assert ops.covers.thumbnail("Nobody/Bare/01.flac") is None
    monkeypatch.setattr(tagging, "read_cover", lambda p: pytest.fail("looked again at a file known to have none"))
    assert ops.covers.thumbnail("Nobody/Bare/01.flac") is None
    monkeypatch.undo()
    f = lib / "Nobody" / "Bare" / "01.flac"
    tagging.embed_cover(str(f), jpeg())                                       # the file changed (new size and mtime)
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert ops.covers.thumbnail("Nobody/Bare/01.flac") is not None


@pytest.mark.parametrize("rel", ["../outside.flac", "/etc/passwd", "Dua Lipa/../../x", "", "Dua Lipa", "missing.flac"])
def test_nothing_outside_the_library_or_that_is_not_a_file_is_read(env, rel):
    _, ops, _, _ = env
    assert ops.covers.thumbnail(rel) is None


def test_only_deezers_image_servers_are_ever_redirected_to():
    assert lc.deezer_cover(DZ) and lc.deezer_cover("https://e-cdns-images.dzcdn.net/x.jpg")
    for bad in ("http://cdn-images.dzcdn.net/x.jpg", "https://evil.example/dzcdn.net/x.jpg", "https://dzcdn.net.evil.example/x.jpg", "", "javascript:alert(1)"):
        assert not lc.deezer_cover(bad)


def test_the_albums_use_their_own_cover_and_try_the_next_track_when_the_first_has_none(env):
    _, _, client, _ = env
    url = cover_url(client, "Dua Lipa", "Future Nostalgia")
    assert url.startswith("/api/library/cover/") and "sig=" in url
    r = client.get(url)                                                      # no login: an <img> cannot send one
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data[:2] == b"\xff\xd8"
    assert "private" in r.headers["Cache-Control"] and "no-store" not in r.headers["Cache-Control"]


def test_the_listing_and_the_artist_page_carry_the_same_kind_of_link(env):
    _, _, client, _ = env
    albums = client.get("/api/library/albums", headers=H).json["albums"]
    assert all(a["cover_url"].startswith("/api/library/cover/") and "cover_paths" not in a for a in albums)
    page = client.get("/api/library/artist?name=Dua+Lipa", headers=H).json
    assert page["albums"][0]["cover_url"].startswith("/api/library/cover/")


def test_without_an_embedded_cover_it_redirects_to_deezers_when_known_else_404(env):
    svc, _, client, _ = env
    bare = cover_url(client, "Nobody", "Bare")
    assert client.get(bare).status_code == 404
    svc.db.save_artist_map(normalize_artist("Nobody"), "Nobody", "5", 1.0, "isrc")
    svc.db.save_albums("5", "Nobody", [{"dz_id": "50", "title": "Bare", "kind": "album", "release_date": "2020-01-01",
                                        "cover_url": DZ, "track_count": 3}])
    r = client.get(cover_url(client, "Nobody", "Bare"))
    assert r.status_code == 302 and r.headers["Location"] == DZ


def test_links_must_be_ours_and_fresh(env):
    _, _, client, _ = env
    url = cover_url(client, "Dua Lipa", "Future Nostalgia")
    name = url.split("/api/library/cover/")[1].split("?")[0]
    assert client.get(f"/api/library/cover/{name}").status_code == 403                    # no signature
    assert client.get(f"/api/library/cover/{name}?exp=9999999999&sig=deadbeef").status_code == 403
    tampered = lc.encode(["Nobody/Bare/01.flac"], "")
    assert client.get(f"/api/library/cover/{tampered}?" + url.split("?")[1]).status_code == 403   # another path, same signature
    other = client.get(url.replace("sig=", "sig=0"))
    assert other.status_code == 403
    assert client.get(url.replace("exp=", "exp=1")).status_code == 403                    # edited expiry
    assert client.get(url).status_code == 200


def test_an_expired_link_and_a_path_the_index_does_not_know_are_refused(env):
    _, ops, client, _ = env
    secret_name = lc.encode(["Dua Lipa/Future Nostalgia/02.flac"], "")
    # rebuild the app's secret the same way the API derives it, to make a validly signed but expired and unknown-path link
    import hmac
    secret = hmac.new(TOKEN.encode(), b"music-grabber-library-covers", "sha256").digest()
    old = lc.query(secret, secret_name, ttl=-10)
    assert client.get(f"/api/library/cover/{secret_name}?{old}").status_code == 403
    ghost = lc.encode(["Dua Lipa/Future Nostalgia/03.flac"], "")                           # a file nobody indexed
    write_flac(str(ops.covers.root + "/Dua Lipa/Future Nostalgia/03.flac"))
    tagging.embed_cover(ops.covers.root + "/Dua Lipa/Future Nostalgia/03.flac", jpeg())
    assert client.get(f"/api/library/cover/{ghost}?{lc.query(secret, ghost)}").status_code == 404
