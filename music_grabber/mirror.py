"""Mirror watched Spotify playlists to Navidrome (docs/plans/q3-q5-design.md, Q5).

Each watched playlist becomes a normal Navidrome playlist named ``<prefix><spotify name>``
holding the tracks Music Grabber matched in the library, in Spotify order. BPM Tagger imports
Navidrome playlists, so a mirrored Spotify playlist is usable in its Run and Listen pages again.

It writes to the user's Navidrome, so it is deliberately cautious:

* off unless ``PLAYLIST_MIRROR=navidrome``; a per-playlist switch on top;
* **one-way, and only its own playlists**: a remote playlist is ours when its id is the one we
  recorded, or its comment carries ``music-grabber:<spotify playlist id>`` (which lets a restored
  database adopt it instead of duplicating it). Nothing else is ever updated or deleted;
* a playlist of the same name that is not ours is left alone and reported ("name taken");
* a mirror the user deleted in Navidrome is not silently recreated (``--reset`` brings it back);
* contents are replaced in one request, and only when the desired song list differs from what we
  wrote last time; an unchanged playlist causes no write at all;
* nothing is deleted except by the explicit ``remove`` action, and only for our own playlist.
"""

import hashlib
import json
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from .analysis import PlacedFile
from .coalesce import Coalescer
from .hooks import backoff
from .library_opensubsonic import OpenSubsonicSource
from .subsonic import SubsonicError, call
from .text import normalize_artist, normalize_title

log = logging.getLogger(__name__)

MARKER = "music-grabber:"
AUTH_CODES = {40, 41, 42, 43, 44}       # Subsonic: wrong credentials / token / API key


def _norm(path: str) -> str:
    return (path or "").replace("\\", "/").strip("/")


def _digest(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False).encode("utf-8"),
                        usedforsecurity=False).hexdigest()


DURATION_SLACK_MS = 3000


class SongIndex:
    """Navidrome song ids for our library files. In order, the first rule that names exactly one
    song wins; anything ambiguous or absent stays unresolved (a gap, never a wrong track):

    1. the same path (BPM Tagger's Subsonic API and older Navidrome report real paths);
    2. a ``/``-aligned path suffix either way (Navidrome's library root can differ from ours);
    3. the file's ISRC;
    4. the file's normalized artist + title (within a few seconds of its duration).

    Rules 3 and 4 use the matched file's own tags, so they hold even when Navidrome reports a
    display path instead of the real one (newer versions do: ``Artist/Album/01-01 - Title.mp3``)."""

    def __init__(self, songs):
        self.exact: dict = {}
        self.by_name: dict = defaultdict(list)
        self.by_isrc: dict = defaultdict(set)
        self.by_key: dict = defaultdict(list)
        for s in songs:
            sid = s.get("id")
            if not sid:
                continue
            path = _norm(s.get("path"))
            if path:
                self.exact[path] = sid
                self.by_name[path.rsplit("/", 1)[-1].casefold()].append((path.casefold(), sid))
            isrc = s.get("isrc")
            for code in (isrc if isinstance(isrc, (list, tuple)) else [isrc]):
                if code:
                    self.by_isrc[str(code).strip().casefold()].add(sid)
            if s.get("title") and s.get("artist"):
                dur = int(s["duration"]) * 1000 if str(s.get("duration") or "").isdigit() else None
                self.by_key[(normalize_artist(s["artist"]), normalize_title(s["title"]))].append((sid, dur))

    def find(self, rel_path: str, tags: Optional[dict] = None) -> Optional[str]:
        rel = _norm(rel_path)
        if rel in self.exact:
            return self.exact[rel]
        if rel:
            low = rel.casefold()
            ids = {sid for path, sid in self.by_name.get(low.rsplit("/", 1)[-1], [])
                   if path == low or path.endswith("/" + low) or low.endswith("/" + path)}
            if len(ids) == 1:
                return next(iter(ids))
        tags = tags or {}
        if tags.get("isrc"):
            ids = self.by_isrc.get(str(tags["isrc"]).strip().casefold(), set())
            if len(ids) == 1:
                return next(iter(ids))
        if tags.get("norm_artist") and tags.get("norm_title"):
            cands = self.by_key.get((tags["norm_artist"], tags["norm_title"]), [])
            want = tags.get("duration_ms")
            if want:
                cands = [c for c in cands if c[1] is None or abs(c[1] - want) <= DURATION_SLACK_MS]
            if len({sid for sid, _ in cands}) == 1:
                return cands[0][0]
        return None


class PlaylistMirror:
    name = "navidrome-mirror"

    def __init__(self, db, url: str, user: str = "", password: str = "", *, api_key: str = "",
                 prefix: str = "Spotify: ", session=None, timeout: float = 30,
                 debounce: float = 120.0, max_wait: float = 900.0, min_interval: float = 300.0,
                 clock=None, timer_factory=None):
        self.db, self.url, self.prefix = db, url.rstrip("/"), prefix
        self._auth = {"user": user, "password": password, "api_key": api_key}
        self._session, self._timeout = session, timeout
        self._run_lock = threading.Lock()
        self._retry_unresolved = False       # new files were placed: look for them in Navidrome
        self.disabled = False
        self._failures = 0
        kw = {}
        if clock is not None:
            kw["clock"] = clock
        if timer_factory is not None:
            kw["timer_factory"] = timer_factory
        self.coalescer = Coalescer(self._fire, debounce=debounce, max_wait=max_wait,
                                   min_interval=min_interval, name=self.name, **kw)

    # ── triggers ──────────────────────────────────────────────────────────────
    def request(self) -> None:
        """Something changed in a playlist or its matches (a sync finished)."""
        self.coalescer.notify()

    def on_placed(self, placed: PlacedFile) -> None:
        """A file was filed: once Navidrome has scanned it, unresolved tracks may resolve."""
        self._retry_unresolved = True
        self.coalescer.notify()

    def close(self) -> None:
        self.coalescer.close()

    def _fire(self) -> Optional[float]:
        retry, self._retry_unresolved = self._retry_unresolved, False
        try:
            self.run_pass(retry_unresolved=retry)
        except SubsonicError as exc:
            self._retry_unresolved = self._retry_unresolved or retry
            return self._transient(exc)
        self._failures = 0
        return None

    def _transient(self, exc) -> Optional[float]:
        if self._is_auth(exc):
            self.disabled = True
            log.warning("Navidrome rejected the credentials for the playlist mirror (%s): the "
                        "mirror is disabled until restart. Check NAVIDROME_USER/NAVIDROME_PASS.", exc)
            return None
        self._failures += 1
        delay = backoff(self._failures)
        log.warning("Playlist mirror: Navidrome request failed (%s); retrying in %ds", exc, int(delay))
        return delay

    @staticmethod
    def _is_auth(exc) -> bool:
        return getattr(exc, "status", None) in (401, 403) or getattr(exc, "code", None) in AUTH_CODES

    # ── Navidrome API ─────────────────────────────────────────────────────────
    def _api(self, endpoint: str, **params) -> dict:
        return call(self.url, endpoint, params, timeout=self._timeout, session=self._session,
                    **self._auth)

    def _remote_playlists(self) -> dict:
        data = self._api("getPlaylists")
        return {p["id"]: p for p in (data.get("playlists") or {}).get("playlist") or [] if p.get("id")}

    def _songs(self) -> SongIndex:
        source = OpenSubsonicSource(self.url, api_key=self._auth["api_key"], user=self._auth["user"],
                                    password=self._auth["password"], timeout=self._timeout,
                                    session=self._session)
        return SongIndex(source.songs())

    # ── the pass ──────────────────────────────────────────────────────────────
    def run_pass(self, *, only: Optional[int] = None, retry_unresolved: bool = False) -> bool:
        """Bring the mirrored playlists up to date. ``only`` limits it to one playlist id and
        counts as a request to retry unresolved tracks. Returns True if any playlist had an
        error. Raises SubsonicError when Navidrome itself cannot be reached."""
        if self.disabled:
            return False
        with self._run_lock:
            remote = self._remote_playlists()
            songs = None
            failed = False
            for pl in self.db.list_playlists():
                if only is not None and pl["id"] != only:
                    continue
                if only is None and not (pl.get("enabled") and pl.get("mirror", 1)):
                    continue
                try:
                    songs = self._mirror_one(pl, remote, songs, retry_unresolved or only is not None)
                except SubsonicError as exc:
                    if self._is_auth(exc):
                        raise
                    failed = True
                    self.db.save_mirror(pl["id"], state="error", last_error=str(exc)[:300])
                    log.warning("Playlist mirror '%s': %s", pl.get("name"), exc)
            return failed

    def _owner_of(self, pl: dict, st: Optional[dict], remote: dict) -> Optional[str]:
        """The id of our Navidrome playlist for ``pl``, or None."""
        if st and st.get("remote_id") in remote:
            return st["remote_id"]
        marker = f"{MARKER}{pl['spotify_id']}"
        for rid, rp in remote.items():
            if marker in (rp.get("comment") or ""):
                return rid
        return None

    def _desired_paths(self, pl: dict) -> list:
        rows = self.db.get_playlist_track_rows(pl["id"])
        return [r["matched_file_path"] for r in rows
                if r.get("match_status") == "have" and r.get("matched_file_path")]

    def _mirror_one(self, pl, remote, songs, retry_unresolved):
        pid = pl["id"]
        st = self.db.get_mirror(pid)
        name = f"{self.prefix}{pl.get('name') or pl['spotify_id']}"
        rid = self._owner_of(pl, st, remote)

        if st and st.get("state") == "deleted_remotely":
            return songs                                   # until `reset`
        if st and st.get("remote_id") and rid is None:
            self.db.save_mirror(pid, state="deleted_remotely", last_error="",
                                note="deleted in Navidrome; not recreated until reset")
            log.info("Playlist mirror '%s': the Navidrome playlist was deleted; not recreating it "
                     "(use `playlist mirror %d --reset`)", pl.get("name"), pid)
            return songs
        if rid is None:
            taken = [p for p in remote.values() if p.get("name") == name]
            if taken:
                self.db.save_mirror(pid, state="name_taken", last_error="",
                                    note=f"a Navidrome playlist named '{name}' exists and is not ours")
                log.warning("Playlist mirror '%s': a Navidrome playlist named '%s' exists and is "
                            "not ours; leaving it alone", pl.get("name"), name)
                return songs

        paths = self._desired_paths(pl)
        paths_hash = _digest(name, paths)
        if rid and st and st.get("paths_hash") == paths_hash and st.get("state") == "ok" \
                and not (retry_unresolved and st.get("unresolved")):
            return songs                                   # nothing changed since the last pass
        if not paths and rid is None:
            self.db.save_mirror(pid, state="empty", last_error="", note="no matched tracks yet",
                                matched=0, unresolved=0, paths_hash=paths_hash)
            return songs

        if songs is None:
            songs = self._songs()
        tags = self.db.library_tags(paths)
        ids = [sid for sid in (songs.find(p, tags.get(p)) for p in paths) if sid]
        unresolved = len(paths) - len(ids)
        content = _digest(name, ids)
        now = datetime.now(timezone.utc).isoformat()
        fields = dict(state="ok", last_error="", note="", matched=len(ids), unresolved=unresolved,
                      paths_hash=paths_hash, synced_at=now)

        if rid is None:
            if not ids:
                self.db.save_mirror(pid, state="empty", last_error="", matched=0, unresolved=unresolved,
                                    paths_hash=paths_hash, note="no matched track is in Navidrome yet")
                return songs
            rid = self._create(pid, pl, name, ids, remote)
            self.db.save_mirror(pid, remote_id=rid, remote_name=name, content_hash=content, **fields)
            log.info("Playlist mirror: created '%s' with %d track(s)", name, len(ids))
            return songs

        updates = {}
        if not st or st.get("content_hash") != content:
            if ids:
                self._api("createPlaylist", playlistId=rid, songId=ids)      # replaces the contents
            else:
                self._clear(rid)       # Navidrome ignores createPlaylist without songs: remove them one by one
            updates["content_hash"] = content
            log.info("Playlist mirror: updated '%s' (%d track(s))", name, len(ids))
        # Rename only when Spotify renamed it and nobody renamed it in Navidrome since.
        if st and st.get("remote_name") and st["remote_name"] != name \
                and remote[rid].get("name") == st["remote_name"]:
            self._api("updatePlaylist", playlistId=rid, name=name)
            log.info("Playlist mirror: renamed '%s' to '%s'", st["remote_name"], name)
        if not (remote[rid].get("comment") or "").count(f"{MARKER}{pl['spotify_id']}"):
            self._api("updatePlaylist", playlistId=rid, comment=f"{MARKER}{pl['spotify_id']}")
        self.db.save_mirror(pid, remote_id=rid, remote_name=name, **{**fields, **updates})
        return songs

    def _clear(self, rid: str) -> None:
        count = len((self._api("getPlaylist", id=rid).get("playlist") or {}).get("entry") or [])
        if count:
            self._api("updatePlaylist", playlistId=rid, songIndexToRemove=list(range(count)))

    def _create(self, pid: int, pl: dict, name: str, ids: list, remote: dict) -> str:
        data = self._api("createPlaylist", name=name, songId=ids)
        rid = (data.get("playlist") or {}).get("id")
        if not rid:                                         # an older server answers with just "ok"
            after = self._remote_playlists()
            fresh = [p["id"] for p in after.values() if p.get("name") == name and p["id"] not in remote]
            if len(fresh) != 1:
                raise SubsonicError("createPlaylist: could not find the playlist it created")
            rid = fresh[0]
        self.db.save_mirror(pid, remote_id=rid, remote_name=name, state="ok")   # before the marker call
        self._api("updatePlaylist", playlistId=rid, comment=f"{MARKER}{pl['spotify_id']}", public="false")
        return rid

    # ── explicit actions ──────────────────────────────────────────────────────
    def reset(self, pid: int) -> None:
        """Forget a 'deleted remotely' / 'name taken' state so the next pass tries again."""
        st = self.db.get_mirror(pid)
        if st and st.get("state") in ("deleted_remotely", "name_taken", "error", "empty"):
            self.db.delete_mirror(pid)

    def remove(self, pid: int) -> bool:
        """Delete our Navidrome playlist for ``pid`` (only if it is ours) and stop mirroring it."""
        pl = self.db.get_playlist(pid)
        st = self.db.get_mirror(pid)
        removed = False
        with self._run_lock:
            remote = self._remote_playlists()
            rid = self._owner_of(pl, st, remote) if pl else None
            if rid:
                self._api("deletePlaylist", id=rid)
                removed = True
        self.db.delete_mirror(pid)
        self.db.set_playlist_mirror(pid, False)
        return removed
