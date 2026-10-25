"""30-second preview clips for YouTube (yt-dlp) Inbox candidates.

Deezer candidates preview straight from Deezer's CDN; YouTube has nothing like that, so the server
fetches the audio with yt-dlp, cuts 30 seconds out of it with ffmpeg and serves the small MP3 from a
short-lived cache. The browser's audio element cannot send a bearer token, so the clip is handed out
as a URL that carries its own HMAC signature and expiry.
"""

import hashlib
import hmac
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from typing import Callable, Optional
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

CLIP_SECONDS = 30
CLIP_NAME = re.compile(r"^[0-9a-f]{16}\.mp3$")
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


class ClipError(Exception):
    """A clip could not be made; the message is safe to show."""


def watch_url(cand: dict) -> str:
    """The YouTube page to fetch for a candidate: its own https YouTube link, else built from its video
    id. Raises ClipError for anything else (this is called with attacker-influenced search results)."""
    url = (cand.get("url") or "").strip()
    if url:
        parts = urlsplit(url)
        if parts.scheme == "https" and (parts.hostname or "").lower() in YOUTUBE_HOSTS:
            return url
    vid = (cand.get("provider_track_id") or "").strip()
    if VIDEO_ID.match(vid):
        return f"https://music.youtube.com/watch?v={vid}"
    raise ClipError("this result has no usable YouTube link")


def sign(secret: bytes, name: str, exp: int) -> str:
    return hmac.new(secret, f"{name}.{exp}".encode(), hashlib.sha256).hexdigest()


def signed_query(secret: bytes, name: str, ttl: int = 600, now: Callable[[], float] = time.time) -> str:
    exp = int(now()) + ttl
    return f"exp={exp}&sig={sign(secret, name, exp)}"


def valid_signature(secret: bytes, name: str, exp: str, sig: str, now: Callable[[], float] = time.time) -> bool:
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    return bool(CLIP_NAME.match(name or "")) and exp_i >= now() and hmac.compare_digest(sign(secret, name, exp_i), sig or "")


def _download_audio(ydl_cls, url: str, dest_dir: str) -> str:
    """The best audio stream of `url` in `dest_dir` (no postprocessing); returns its path."""
    opts = {"quiet": True, "no_warnings": True, "format": "bestaudio/best", "noplaylist": True,
            "outtmpl": os.path.join(dest_dir, "src.%(ext)s"), "socket_timeout": 15, "retries": 1,
            "max_filesize": 60_000_000, "postprocessors": []}
    with ydl_cls(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


def _cut(src: str, dest: str, start: float) -> None:
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.1f}", "-t", str(CLIP_SECONDS),
            "-i", src, "-vn", "-ac", "2", "-b:a", "96k", dest]
    try:
        res = subprocess.run(args, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClipError(f"ffmpeg failed: {type(exc).__name__}") from None
    if res.returncode != 0 or not os.path.isfile(dest):
        raise ClipError("ffmpeg could not cut the clip")


class ClipCache:
    def __init__(self, directory: str, ydl_cls_factory: Callable[[], type], *, ttl: float = 3600, max_files: int = 60,
                 concurrency: int = 2, downloader: Callable = _download_audio, cutter: Callable = _cut,
                 clock: Callable[[], float] = time.time):
        self.dir, self.ttl, self.max_files, self.clock = directory, ttl, max_files, clock
        self._ydl_cls, self._download, self._cut = ydl_cls_factory, downloader, cutter
        self._slots = threading.BoundedSemaphore(concurrency)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @staticmethod
    def name_for(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:16] + ".mp3"

    def path(self, name: str) -> Optional[str]:
        p = os.path.join(self.dir, name)
        return p if CLIP_NAME.match(name) and os.path.isfile(p) else None

    def _start(self, duration_ms) -> float:
        """A quarter of the way in (past the intro), but never so late that the clip runs off the end."""
        if not duration_ms or duration_ms <= CLIP_SECONDS * 1000:
            return 0.0
        return max(0.0, min(duration_ms / 4000.0, duration_ms / 1000.0 - CLIP_SECONDS))

    def clip_for(self, cand: dict) -> str:
        """The cached clip's file name for a candidate, making it first if needed. Raises ClipError."""
        url = watch_url(cand)
        name = self.name_for(url)
        if self.path(name):
            return name
        with self._guard:
            lock = self._locks.setdefault(name, threading.Lock())
        with lock:
            if self.path(name):                      # another request made it while we waited
                return name
            if not self._slots.acquire(timeout=20):
                raise ClipError("busy making other previews; try again")
            try:
                os.makedirs(self.dir, exist_ok=True)
                self.cleanup()
                with tempfile.TemporaryDirectory(dir=self.dir, prefix=".work-") as work:
                    try:
                        src = self._download(self._ydl_cls(), url, work)
                    except ClipError:
                        raise
                    except Exception as exc:
                        log.debug("preview download failed for %s: %s", url, exc)
                        raise ClipError("could not fetch the audio from YouTube") from None
                    tmp_out = os.path.join(work, "clip.mp3")
                    self._cut(src, tmp_out, self._start(cand.get("duration_ms")))
                    os.replace(tmp_out, os.path.join(self.dir, name))
            finally:
                self._slots.release()
        return name

    def cleanup(self) -> int:
        """Delete clips past their time to live, then the oldest beyond `max_files`."""
        try:
            entries = [(e.stat().st_mtime, e.path) for e in os.scandir(self.dir) if CLIP_NAME.match(e.name)]
        except OSError:
            return 0
        entries.sort()
        doomed = [p for m, p in entries if self.clock() - m > self.ttl]
        keep = [p for m, p in entries if p not in doomed]
        doomed += keep[:max(0, len(keep) - self.max_files + 1)]      # room for the one being made
        for p in doomed:
            try: os.remove(p)
            except OSError: pass
        return len(doomed)
