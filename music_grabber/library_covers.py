"""Album covers from your own files: the picture embedded in a track, shrunk and cached.

The Albums and Artists pages ask for ``/api/library/cover/<name>``, where ``name`` is a signed, opaque bundle: up to
three library paths of the album's tracks (tried in order) and, as a fallback, the Deezer cover the New releases pass
cached. This module only reads files the index already knows (the path must be in the library cache and stay inside
the library folder), and it never talks to the network.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import tempfile
import time
from typing import Optional
from urllib.parse import urlsplit

from . import tagging
from .preview_clips import sign

log = logging.getLogger(__name__)

THUMB_PX = 400
MAX_PATHS = 3
COVER_TTL = 6 * 3600
_EXT = {b"\x89PNG": ("png", "image/png"), b"RIFF": ("webp", "image/webp")}


def encode(paths: list[str], fallback: str = "") -> str:
    raw = json.dumps({"p": list(paths)[:MAX_PATHS], "d": fallback or ""}, ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode(name: str) -> Optional[tuple[list[str], str]]:
    try:
        data = json.loads(base64.urlsafe_b64decode(name + "=" * (-len(name) % 4)))
        paths = [p for p in data.get("p", []) if isinstance(p, str)][:MAX_PATHS]
        return paths, str(data.get("d") or "")
    except Exception:
        return None


def query(secret: bytes, name: str, ttl: int = COVER_TTL, now=time.time) -> str:
    exp = int(now()) + ttl
    return f"exp={exp}&sig={sign(secret, name, exp)}"


def valid(secret: bytes, name: str, exp: str, sig: str, now=time.time) -> bool:
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    return exp_i >= now() and hmac.compare_digest(sign(secret, name, exp_i), sig or "")


def deezer_cover(url: str) -> bool:
    """Only Deezer's image servers are ever redirected to."""
    parts = urlsplit(url or "")
    host = parts.hostname or ""
    return parts.scheme == "https" and (host == "dzcdn.net" or host.endswith(".dzcdn.net"))


class CoverCache:
    def __init__(self, library_root: str, cache_dir: str):
        self.root = os.path.realpath(library_root) if library_root else ""
        self.dir = cache_dir

    def resolve(self, rel: str) -> Optional[str]:
        """The real file for a library path, or None when it is not a file inside the library folder."""
        if not self.root or not rel or os.path.isabs(rel):
            return None
        full = os.path.realpath(os.path.join(self.root, rel))
        inside = full == self.root or full.startswith(self.root + os.sep)
        return full if inside and os.path.isfile(full) else None

    def thumbnail(self, rel: str) -> Optional[tuple[str, str]]:
        """(cached image path, mime type) for the cover embedded in this file, or None when it has none."""
        full = self.resolve(rel)
        if not full:
            return None
        st = os.stat(full)
        key = hashlib.sha1(f"{rel}|{st.st_mtime_ns}|{st.st_size}".encode(), usedforsecurity=False).hexdigest()
        os.makedirs(self.dir, exist_ok=True)
        for ext, mime in (("jpg", "image/jpeg"), ("png", "image/png"), ("webp", "image/webp")):
            if os.path.isfile(os.path.join(self.dir, f"{key}.{ext}")):
                return os.path.join(self.dir, f"{key}.{ext}"), mime
        if os.path.isfile(os.path.join(self.dir, f"{key}.none")):
            return None                                           # looked before: this file has no picture
        found = tagging.read_cover(full)
        if not found or not found[0]:
            open(os.path.join(self.dir, f"{key}.none"), "w").close()
            return None
        data = tagging.resize_cover(found[0], THUMB_PX)
        ext, mime = next(((e, m) for magic, (e, m) in _EXT.items() if data.startswith(magic)), ("jpg", "image/jpeg"))
        target = os.path.join(self.dir, f"{key}.{ext}")
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".part")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)                                   # two requests for one album cannot leave half a file
        return target, mime
