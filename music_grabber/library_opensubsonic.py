"""OpenSubsonic library source: learn what the library has over HTTP.

Point it at BPM Tagger's Subsonic API (needs ``SUBSONIC_ENABLED=true`` there; songs carry
``path``, ``duration``, ``isrc`` and ``bpm``) or at Navidrome. It walks the whole library
and loads the same cache table the filesystem source does, so matching is identical.

The walk is ``search3`` with an empty query, which pages through every song on both
servers (BPM Tagger caps ``songCount`` at 1000). Servers that refuse an empty query fall
back to ``getAlbumList2`` + ``getAlbum``.
"""

import hashlib
import json
import logging
from typing import Iterator, Mapping, Optional

from .library import LibraryEntry, LibrarySourceError, LibraryTrack
from .subsonic import SubsonicError, call

log = logging.getLogger(__name__)

PAGE = 500


def _int(v) -> Optional[int]:
    try:
        return int(v) if v is not None and str(v) != "" else None
    except (TypeError, ValueError):
        return None


def song_to_track(song: dict) -> Optional[LibraryTrack]:
    """A LibraryTrack from an OpenSubsonic song, or None when it has no usable path."""
    path = (song.get("path") or "").replace("\\", "/").lstrip("/")
    if not path:
        return None
    isrc = song.get("isrc")
    if isinstance(isrc, (list, tuple)):
        isrc = isrc[0] if isrc else None
    duration = _int(song.get("duration"))
    return LibraryTrack(
        path=path, title=song.get("title"), artist=song.get("artist"), album=song.get("album"),
        album_artist=song.get("albumArtist") or song.get("displayAlbumArtist"),
        duration_ms=duration * 1000 if duration is not None else None,
        isrc=str(isrc) if isrc else None, track_no=_int(song.get("track")),
        disc_no=_int(song.get("discNumber")), year=_int(song.get("year")))


def track_token(t: LibraryTrack) -> str:
    """The API gives no mtime, so the change token is a hash of the fields we store."""
    blob = json.dumps([t.title, t.artist, t.album, t.album_artist, t.duration_ms, t.isrc,
                       t.track_no, t.disc_no, t.year], ensure_ascii=False)
    return "h:" + hashlib.sha1(blob.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


class OpenSubsonicSource:
    name = "opensubsonic"

    def __init__(self, url: str, *, api_key: str = "", user: str = "", password: str = "",
                 timeout: float = 30, page: int = PAGE, session=None):
        self.url, self.api_key, self.user, self.password = url, api_key, user, password
        self.timeout, self.page, self.session = timeout, page, session

    def _call(self, endpoint: str, **params) -> dict:
        return call(self.url, endpoint, params, user=self.user, password=self.password,
                    api_key=self.api_key, timeout=self.timeout, session=self.session)

    def scan(self, known: Mapping[str, str]) -> Iterator[LibraryEntry]:
        try:
            yield from self._entries(known, self._songs())
        except SubsonicError as exc:
            # The index leaves its cache untouched on this error.
            raise LibrarySourceError(f"OpenSubsonic library walk failed: {exc}") from exc

    def _entries(self, known, songs) -> Iterator[LibraryEntry]:
        for song in songs:
            track = song_to_track(song)
            if track is None:
                continue
            token = track_token(track)
            yield LibraryEntry(track.path, token, None if known.get(track.path) == token else track)

    def _songs(self) -> Iterator[dict]:
        offset, first = 0, True
        while True:
            data = self._call("search3", query="", artistCount=0, albumCount=0,
                              songCount=self.page, songOffset=offset)
            songs = (data.get("searchResult3") or {}).get("song") or []
            if first and not songs:
                log.info("search3 with an empty query returned nothing; walking albums instead")
                yield from self._songs_by_album()
                return
            first = False
            yield from songs
            if len(songs) < self.page:
                return
            offset += self.page

    def _songs_by_album(self) -> Iterator[dict]:
        offset = 0
        while True:
            data = self._call("getAlbumList2", type="alphabeticalByName", size=self.page,
                              offset=offset)
            albums = (data.get("albumList2") or {}).get("album") or []
            for a in albums:
                if not a.get("id"):
                    continue
                album = self._call("getAlbum", id=a["id"]).get("album") or {}
                yield from album.get("song") or []
            if len(albums) < self.page:
                return
            offset += self.page
