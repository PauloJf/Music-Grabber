"""Embed lyrics into audio files (embedded tags only; no sidecar files).

Trimmed from BPM Tagger's bpm/lyrics.py (docs/plans/extraction.md §1.1). Synced lyrics
are stored as LRC-formatted text inside the standard *unsynced* fields (USLT / LYRICS= /
©lyr); Navidrome and most players parse the timestamps out of those. Writes preserve
mtime. Used only for the opt-in ``LYRICS_ENABLED`` fetch of a freshly grabbed file,
before it is placed.
"""

import logging
import os
import re
from pathlib import Path

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, USLT
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

log = logging.getLogger(__name__)

# An LRC timestamp at a line start, e.g. [01:23.45]: presence means "synced".
_LRC_TS_RE = re.compile(r"^\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]", re.MULTILINE)

_VORBIS_KEYS = ("LYRICS", "UNSYNCEDLYRICS")


def is_synced(text: str | None) -> bool:
    return bool(text and _LRC_TS_RE.search(text))


def _write_embedded(file_path: str, text: str) -> bool:
    ext = Path(file_path).suffix.lower()
    if ext == ".mp3":
        try:
            tags = ID3(file_path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("USLT")
        if text:
            tags["USLT"] = USLT(encoding=3, lang="XXX", desc="", text=text)
        tags.save(file_path)
    elif ext == ".flac":
        audio = FLAC(file_path)
        for key in _VORBIS_KEYS:
            audio.pop(key, None)
        if text:
            audio["LYRICS"] = text
        audio.save()
    elif ext in (".ogg", ".opus"):
        audio = OggVorbis(file_path) if ext == ".ogg" else mutagen.File(file_path)
        if audio is None:
            return False
        for key in _VORBIS_KEYS:
            audio.pop(key, None)
        if text:
            audio["LYRICS"] = text
        audio.save()
    elif ext in (".m4a", ".aac", ".mp4"):
        audio = MP4(file_path)
        if text:
            audio["\xa9lyr"] = [text]
        else:
            audio.pop("\xa9lyr", None)
        audio.save()
    else:
        return False
    return True


def write_lyrics(file_path: str, text: str, mode: str = "embed",
                 preserve_mtime: bool = True) -> bool:
    """Embed a track's lyrics (``mode`` is accepted for call compatibility; only
    'embed' exists here). Returns False when the format is unsupported or on failure."""
    text = (text or "").strip()
    if not text:
        return False
    try:
        st = os.stat(file_path) if preserve_mtime else None
        ok = _write_embedded(file_path, text)
        if st is not None:
            os.utime(file_path, (st.st_atime, st.st_mtime))
        return ok
    except Exception as exc:
        log.error("Failed to write lyrics for %s: %s", file_path, exc)
        return False
