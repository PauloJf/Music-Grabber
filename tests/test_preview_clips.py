"""YouTube preview clips: link validation, the cache, signed URLs, and the Ops/API wiring (no network, no yt-dlp)."""

import os
import time

import pytest

from music_grabber import preview_clips as pc
from music_grabber.preview_clips import ClipCache, ClipError

CAND = {"provider": "ytdlp", "provider_track_id": "abcDEF12345", "url": "https://music.youtube.com/watch?v=abcDEF12345",
        "duration_ms": 200_000}


def _cache(tmp_path, **kw):
    calls = {"download": [], "cut": []}

    def download(ydl_cls, url, dest):
        calls["download"].append(url)
        p = os.path.join(dest, "src.webm")
        open(p, "wb").write(b"audio")
        return p

    def cut(src, dest, start):
        calls["cut"].append(start)
        open(dest, "wb").write(b"mp3clip")

    kw.setdefault("downloader", download)
    kw.setdefault("cutter", cut)
    return ClipCache(str(tmp_path / "previews"), lambda: object, **kw), calls


def test_only_youtube_https_links_or_a_video_id_are_fetched():
    assert pc.watch_url(CAND) == CAND["url"]
    assert pc.watch_url({"url": "", "provider_track_id": "abcDEF12345"}) == "https://music.youtube.com/watch?v=abcDEF12345"
    for bad in ({"url": "http://youtube.com/watch?v=x", "provider_track_id": ""},          # not https
                {"url": "https://evil.example/watch?v=x", "provider_track_id": ""},        # not YouTube
                {"url": "https://youtube.com.evil.example/x", "provider_track_id": "a b"},
                {"url": "file:///etc/passwd", "provider_track_id": "../../x"}, {}):
        with pytest.raises(ClipError):
            pc.watch_url(bad)
    # a bad link with a good id falls back to the id, never to the bad link
    assert pc.watch_url({"url": "https://evil.example/x", "provider_track_id": "abcDEF12345"}).startswith("https://music.youtube.com/")


def test_a_clip_is_made_once_then_served_from_the_cache(tmp_path):
    cache, calls = _cache(tmp_path)
    name = cache.clip_for(CAND)
    assert pc.CLIP_NAME.match(name) and open(cache.path(name), "rb").read() == b"mp3clip"
    assert cache.clip_for(CAND) == name and len(calls["download"]) == 1
    assert [f for f in os.listdir(cache.dir) if f.startswith(".work-")] == []          # scratch space is gone


def test_the_clip_starts_a_quarter_in_and_never_runs_off_the_end(tmp_path):
    cache, calls = _cache(tmp_path)
    cache.clip_for({**CAND, "duration_ms": 200_000})
    assert calls["cut"] == [50.0]
    assert cache._start(35_000) == 5.0 and cache._start(20_000) == 0.0 and cache._start(None) == 0.0


def test_failures_become_a_safe_clip_error_and_leave_nothing_behind(tmp_path):
    def boom(*a): raise RuntimeError("HTTP 403 secret-ish detail")
    cache, _ = _cache(tmp_path, downloader=boom)
    with pytest.raises(ClipError) as e:
        cache.clip_for(CAND)
    assert "secret-ish" not in str(e.value)
    assert [f for f in os.listdir(cache.dir)] == []
    cache2, _ = _cache(tmp_path, cutter=lambda *a: (_ for _ in ()).throw(ClipError("ffmpeg could not cut the clip")))
    with pytest.raises(ClipError):
        cache2.clip_for(CAND)


def test_old_clips_and_overflow_are_cleaned_up(tmp_path):
    now = {"t": time.time()}
    cache, _ = _cache(tmp_path, ttl=3600, max_files=3, clock=lambda: now["t"])
    os.makedirs(cache.dir)
    names = [f"{i:016x}.mp3" for i in range(5)]
    for i, n in enumerate(names):
        p = os.path.join(cache.dir, n)
        open(p, "wb").write(b"x")
        os.utime(p, (now["t"] - 10 * (5 - i),) * 2)          # names[0] is the oldest
    cache.cleanup()
    assert sorted(os.listdir(cache.dir)) == sorted(names[3:])      # room kept for the one being made
    now["t"] += 7200
    cache.cleanup()
    assert os.listdir(cache.dir) == []


def test_signed_urls_expire_and_cannot_be_forged():
    secret, name, t = b"s3cret", "0123456789abcdef.mp3", 1_000_000
    q = dict(kv.split("=") for kv in pc.signed_query(secret, name, ttl=600, now=lambda: t).split("&"))
    assert pc.valid_signature(secret, name, q["exp"], q["sig"], now=lambda: t + 599)
    assert not pc.valid_signature(secret, name, q["exp"], q["sig"], now=lambda: t + 601)         # expired
    assert not pc.valid_signature(secret, "fedcba9876543210.mp3", q["exp"], q["sig"], now=lambda: t)   # another clip
    assert not pc.valid_signature(b"other", name, q["exp"], q["sig"], now=lambda: t)               # another secret
    assert not pc.valid_signature(secret, name, str(int(q["exp"]) + 999), q["sig"], now=lambda: t)  # longer expiry
    assert not pc.valid_signature(secret, "../../etc/passwd", q["exp"], q["sig"], now=lambda: t)
    assert not pc.valid_signature(secret, name, "x", "") and not pc.valid_signature(secret, name, "", None)
