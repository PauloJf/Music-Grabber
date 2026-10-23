"""What does the library already have? (docs/plans/extraction.md §2.1)

The port is a ``LibrarySource`` (how to enumerate the library). Every source loads into
one cache table, so matching is identical whichever source is configured: queries are
local indexed SQL, and a source is only consulted at ``LibraryIndex.refresh()``.

Phase 1 ships the standalone ``FilesystemSource``. The OpenSubsonic/BPM Tagger HTTP
source follows in Phase 2.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterator, Mapping, Optional, Protocol

import mutagen
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

from .text import normalize_artist, normalize_title

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wav", ".opus", ".wv"}


@dataclass(frozen=True)
class LibraryTrack:
    """One library file. ``path`` is POSIX and relative to the library root."""
    path: str
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    album_artist: Optional[str] = None
    duration_ms: Optional[int] = None
    isrc: Optional[str] = None
    track_no: Optional[int] = None
    disc_no: Optional[int] = None
    year: Optional[int] = None


@dataclass(frozen=True)
class LibraryEntry:
    """One file a source saw. ``track`` is None when the change token matched the cached
    one, i.e. "still here, unchanged"; the cached row is kept."""
    path: str
    token: str
    track: Optional[LibraryTrack] = None


class LibrarySource(Protocol):
    name: str

    def scan(self, known: Mapping[str, str]) -> Iterator[LibraryEntry]:
        """Yield an entry for EVERY file currently in the library. ``known`` is the
        cached ``{path: change_token}``: skip reading unchanged files (``track=None``)."""
        ...


class LibrarySourceError(RuntimeError):
    """A source could not enumerate the library; the cache is left untouched."""


# ── tag reading (duplicated from BPM Tagger's bpm/tags.py, by design) ──────────
def _first(v):
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v


def _int_prefix(v):
    """Parse '5', '5/12', 5 → 5; None on failure."""
    s = _first(v)
    if s is None:
        return None
    try:
        return int(str(s).split("/")[0].strip())
    except (ValueError, TypeError):
        return None


def _read_isrc(file_path: str, easy) -> Optional[str]:
    try:
        v = _first(easy.get("isrc"))
        if v:
            return str(v)
    except Exception:
        pass
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".mp3":
            fr = ID3(file_path).getall("TSRC")
            if fr:
                return str(fr[0].text[0])
        elif ext in (".m4a", ".aac"):
            mp4 = MP4(file_path)
            for key in ("----:com.apple.iTunes:ISRC", "----:com.apple.iTunes:isrc"):
                if key in mp4:
                    raw = mp4[key][0]
                    return raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
    except Exception:
        pass
    return None


def read_tags(file_path: str) -> dict:
    """Best-effort metadata read. Missing fields → None."""
    out = {"title": None, "artist": None, "album": None, "album_artist": None,
           "track_no": None, "disc_no": None, "year": None, "isrc": None, "duration_ms": None}
    try:
        easy = mutagen.File(file_path, easy=True)
        if easy is None:
            return out
        out["title"] = _first(easy.get("title"))
        out["artist"] = _first(easy.get("artist"))
        out["album"] = _first(easy.get("album"))
        out["album_artist"] = _first(easy.get("albumartist")) or _first(easy.get("album artist"))
        out["track_no"] = _int_prefix(easy.get("tracknumber"))
        out["disc_no"] = _int_prefix(easy.get("discnumber"))
        date = _first(easy.get("date")) or _first(easy.get("year"))
        if date:
            digits = "".join(c for c in str(date)[:4] if c.isdigit())
            out["year"] = int(digits) if len(digits) == 4 else None
        out["isrc"] = _read_isrc(file_path, easy)
        if getattr(easy, "info", None) and getattr(easy.info, "length", None):
            out["duration_ms"] = int(easy.info.length * 1000)
    except Exception as exc:
        log.debug("read_tags failed for %s: %s", file_path, exc)
    return out


def file_token(file_path: str) -> str:
    """Cheap change token: ``size:mtime`` (same scheme BPM Tagger uses)."""
    st = os.stat(file_path)
    return f"{st.st_size}:{st.st_mtime}"


class FilesystemSource:
    """Standalone source: walk a directory tree and read tags with mutagen."""
    name = "filesystem"

    def __init__(self, root: str):
        self.root = root

    def scan(self, known: Mapping[str, str]) -> Iterator[LibraryEntry]:
        if not os.path.isdir(self.root):
            raise LibrarySourceError(f"library directory not found: {self.root}")
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith(".") or os.path.splitext(name)[1].lower() not in AUDIO_EXTENSIONS:
                    continue
                full = os.path.join(dirpath, name)
                rel = PurePosixPath(*os.path.relpath(full, self.root).split(os.sep)).as_posix()
                try:
                    token = file_token(full)
                except OSError:
                    continue
                if known.get(rel) == token:
                    yield LibraryEntry(rel, token, None)
                    continue
                tags = read_tags(full)
                yield LibraryEntry(rel, token, LibraryTrack(path=rel, **tags))


class LibraryIndex:
    """The cache + the lookups ``library_match`` uses. Owns no policy about *where* the
    library comes from; that is the source's job."""

    # A pass that sees less than this share of a non-trivial cache is treated as a
    # failed refresh (unmounted share, API hiccup) and prunes nothing.
    PRUNE_FLOOR = 0.5
    PRUNE_MIN_CACHE = 20

    def __init__(self, db, source: Optional[LibrarySource] = None, root: str = ""):
        self.db = db
        self.source = source
        self.root = root

    # ── refresh ───────────────────────────────────────────────────────────────
    def refresh(self) -> dict:
        """Drive the source into the cache. Returns {seen, updated, removed, pruned}."""
        if self.source is None:
            return {"seen": 0, "updated": 0, "removed": 0, "pruned": False}
        known = self.db.library_tokens(self.source.name)
        seen: set[str] = set()
        batch: list[dict] = []
        updated = 0
        for entry in self.source.scan(known):
            seen.add(entry.path)
            if entry.track is None:
                continue
            batch.append({**_track_row(entry.track), "change_token": entry.token,
                          "source": self.source.name})
            if len(batch) >= 500:
                updated += self.db.library_upsert(batch)
                batch = []
        updated += self.db.library_upsert(batch)
        gone = [p for p in known if p not in seen]
        pruned = True
        if gone and known and (not seen or (len(known) >= self.PRUNE_MIN_CACHE
                                            and len(seen) < len(known) * self.PRUNE_FLOOR)):
            log.warning("Library refresh saw %d of %d cached files; keeping the cache "
                        "(share unmounted?)", len(seen), len(known))
            pruned = False
            gone = []
        removed = self.db.library_delete(gone) if gone else 0
        log.info("Library index (%s): %d files, %d updated, %d removed",
                 self.source.name, len(seen), updated, removed)
        return {"seen": len(seen), "updated": updated, "removed": removed, "pruned": pruned}

    def is_empty(self) -> bool:
        """True when a source is configured but the cache holds no files at all. A scan that
        finds nothing never empties a populated cache (see PRUNE_FLOOR), so this means "never
        seen any files"."""
        return self.source is not None and self.db.library_count() == 0

    # ── lookups used by matching.library_match ────────────────────────────────
    def find_by_spotify_id(self, spotify_track_id: str) -> list[dict]:
        """Ledger hits whose file still exists. A deleted track is re-grabbed rather
        than reported as 'have' forever."""
        out = []
        for row in self.db.find_grabbed_by_spotify_id(spotify_track_id):
            if os.path.exists(row["abs_path"]):
                out.append({"file_path": self.display_path(row["abs_path"])})
        return out

    def find_by_isrc(self, isrc: str) -> list[dict]:
        return [{**r, "file_path": r["path"]} for r in self.db.library_find_by_isrc(isrc)]

    def find_candidates(self, norm_artist: str, norm_title: str) -> list[dict]:
        return [{**r, "file_path": r["path"]}
                for r in self.db.library_find_candidates(norm_artist, norm_title)]

    def display_path(self, abs_path: str) -> str:
        """Library-root-relative POSIX path when under the root, else the absolute path."""
        if self.root:
            try:
                rel = os.path.relpath(abs_path, self.root)
                if not rel.startswith(".."):
                    return PurePosixPath(*rel.split(os.sep)).as_posix()
            except ValueError:
                pass
        return abs_path


def _track_row(t: LibraryTrack) -> dict:
    return {"path": t.path, "title": t.title, "artist": t.artist, "album": t.album,
            "album_artist": t.album_artist, "duration_ms": t.duration_ms, "isrc": t.isrc,
            "track_no": t.track_no, "disc_no": t.disc_no, "year": t.year,
            "norm_title": normalize_title(t.title), "norm_artist": normalize_artist(t.artist)}
