"""SpotifySync: background playlist→library reconciliation.

A 2s-tick thread that runs a full sync every SPOTIFY_SYNC_MINUTES or when nudged via
``request_sync()``. Per enabled playlist: refresh token → compare snapshot_id →
(rebuild playlist_tracks) → library-match every track. Auth failure marks disconnected
and never crashes the loop. GrabberService wraps the client, the library index, the
sync thread and the worker pool for the API/CLI layer to use.
"""

import logging
import threading
import time

from .library import FilesystemSource, LibraryIndex
from .matching import library_match
from .spotify import SpotifyAuthError, SpotifyClient, SpotifyError, parse_playlist_id

log = logging.getLogger(__name__)


class SpotifySync(threading.Thread):
    def __init__(self, config, db, index, client, notifier=None):
        super().__init__(daemon=True, name="SpotifySync")
        self.config = config
        self.db = db
        self.index = index
        self.client = client
        self.notifier = notifier
        self._stop = threading.Event()
        self._sync_now = threading.Event()
        self._lock = threading.Lock()          # serialize sync_all vs manual sync
        self.last_error = ""
        self.last_synced_epoch = 0.0
        self.auto_queue_paused = False         # the library index is empty: see _match_playlist
        self.on_synced = None                  # called after playlists were matched (the mirror)

    # ── control ───────────────────────────────────────────────────────────────
    def request_sync(self):
        self._sync_now.set()

    def stop(self):
        self._stop.set()
        self._sync_now.set()

    # ── loop ──────────────────────────────────────────────────────────────────
    def run(self):
        interval = max(60, int(self.config.get("spotify_sync_minutes", 30)) * 60)
        log.info("SpotifySync started (every %d min)", interval // 60)
        # Initial sync shortly after boot.
        self._sync_now.set()
        while not self._stop.is_set():
            due = (time.time() - self.last_synced_epoch) >= interval
            if self._sync_now.is_set() or due:
                self._sync_now.clear()
                try:
                    self.sync_all()
                except Exception as exc:  # never let the loop die
                    log.exception("SpotifySync cycle failed: %s", exc)
            self._stop.wait(2)

    # ── work ──────────────────────────────────────────────────────────────────
    def sync_all(self):
        if not self.client.is_connected():
            return
        with self._lock:
            self._refresh_index()
            playlists = self.db.get_enabled_playlists()
            for pl in playlists:
                if self._stop.is_set():
                    break
                self._sync_one(pl)
            self.last_synced_epoch = time.time()
            self.last_error = ""
        self._synced()

    def _refresh_index(self):
        """Reload the library cache. A failing source leaves the cache as it was, so a
        refresh problem degrades to slightly stale matching rather than a dead sync."""
        try:
            self.index.refresh()
        except Exception as exc:
            log.warning("Library index refresh failed (using cached index): %s", exc)

    def sync_playlist(self, playlist_id: int) -> dict:
        """Manual single-playlist sync (used by the API/CLI, works without the thread)."""
        pl = self.db.get_playlist(playlist_id)
        if not pl:
            raise SpotifyError("Playlist not found")
        with self._lock:
            self._refresh_index()
            self._sync_one(pl)
        self._synced()
        return self.db.get_playlist(playlist_id)

    def _synced(self):
        try:
            self.db.set_last_sync(time.time())
        except Exception as exc:
            log.warning("could not persist the last-sync time: %s", exc)
        if self.on_synced:
            try:
                self.on_synced()
            except Exception as exc:
                log.warning("after-sync callback failed: %s", exc)

    def _sync_one(self, pl: dict):
        if not pl.get("spotify_id"):
            return
        try:
            meta = self.client.get_playlist_meta(pl["spotify_id"])
            if meta["snapshot_id"] != (pl.get("snapshot_id") or ""):
                tracks = self.client.get_playlist_tracks(pl["spotify_id"])
                added, removed = self.db.sync_playlist_tracks(pl["id"], tracks)
                self.db.update_playlist_sync(pl["id"], meta["snapshot_id"], meta["name"],
                                             meta["image_url"], meta["track_count"])
                log.info("Playlist '%s': %d tracks (snapshot changed; +%d new, -%d removed)",
                         meta["name"], len(tracks), added, removed)
            # Always re-match: the library changes even when the playlist doesn't.
            self._match_playlist(pl["id"])
        except SpotifyAuthError as exc:
            self.last_error = str(exc)
            log.error("Spotify disconnected during sync: %s", exc)
            self.client.disconnect()
            if self.notifier:
                try:
                    self.notifier.send_grabber(
                        "Spotify disconnected",
                        "The Spotify connection expired — reconnect in Settings.",
                        priority="high", tags="warning",
                    )
                except Exception:
                    pass
            raise
        except SpotifyError as exc:
            self.last_error = str(exc)
            log.warning("Playlist sync error (%s): %s", pl.get("name"), exc)

    def queue_paused(self) -> bool:
        """Is automatic queueing held back because the library index looks unmounted?"""
        return bool(getattr(self.index, "is_empty", lambda: False)()
                    and (self.db.get_grabbed_count() > 0 or self.db.get_grabbed_total() > 0))

    def _match_playlist(self, playlist_id: int):
        rows = self.db.get_playlist_track_rows(playlist_id)
        # An index that sees no files at all, in an install that has grabbed or imported before,
        # is far more likely a wrong or unmounted library path than an emptied library, and every
        # playlist track would read as missing and be queued for download. Keep matching, but queue
        # nothing until the index has files ("queue missing" still works). A brand-new install with
        # an empty library has no history, so it queues as usual.
        paused = self.queue_paused()
        if paused and not self.auto_queue_paused:
            log.warning("Library index is empty: not auto-queueing missing tracks. Check that the "
                        "library is mounted where MUSIC_DIR/LIBRARY_DIR says (or LIBRARY_INDEX); "
                        "'queue missing' on a playlist still works.")
        self.auto_queue_paused = paused
        for pt in rows:
            path = library_match(pt, self.index)
            new_status = "have" if path else "missing"
            if new_status != pt.get("match_status") or path != pt.get("matched_file_path"):
                self.db.set_playlist_track_match(pt["id"], new_status, path)
            # Auto-enqueue freshly-missing tracks (skip ones already tried — failed/
            # skipped items are only retried via an explicit user action).
            if new_status == "missing" and not paused and pt.get("spotify_track_id") \
                    and not self.db.has_any_grab(pt["spotify_track_id"]):
                self.db.enqueue_grab(_grab_meta(pt))


def _grab_meta(pt: dict) -> dict:
    return {"playlist_track_id": pt.get("id"), "spotify_track_id": pt.get("spotify_track_id"),
            "title": pt.get("title"), "artist": pt.get("artist"), "album": pt.get("album"),
            "album_artist": pt.get("album_artist"), "duration_ms": pt.get("duration_ms"),
            "isrc": pt.get("isrc"), "track_no": pt.get("track_no"), "disc_no": pt.get("disc_no"),
            "year": pt.get("year"), "cover_url": pt.get("cover_url")}


class GrabberService:
    """Owns the Spotify client, the library index, the sync thread and the worker pool.
    The API/CLI layer reaches grabber features through this."""

    def __init__(self, config, db, notifier=None, index=None, analysis=None, on_placed=None):
        self.config = config
        self.db = db
        self.client = SpotifyClient(config, db)
        library_dir = config.get("library_dir") or config["music_dir"]
        self.index = index if index is not None else LibraryIndex(
            db, FilesystemSource(library_dir), root=library_dir)
        self.sync = SpotifySync(config, db, self.index, self.client, notifier)
        from .worker import GrabPool
        self.pool = GrabPool(config, db, notifier, analysis=analysis, on_placed=on_placed)
        self._thread_started = False
        self.mirror = None                       # set by main.build_service (PLAYLIST_MIRROR)

    def seed_playlists(self) -> int:
        """Register WATCH_PLAYLISTS (URLs/URIs/ids). No network: names are filled in by
        the first sync. Returns how many were newly added."""
        added = 0
        for raw in self.config.get("watch_playlists") or []:
            sid = parse_playlist_id(raw)
            if not sid:
                continue
            if self.db.get_playlist_by_spotify_id(sid) is None:
                added += 1
            self.db.add_playlist(sid, name=sid)
        return added

    def start_background(self):
        if not self._thread_started:
            self.sync.start()
            self.pool.start()
            self._thread_started = True

    def stop_background(self):
        if self._thread_started:
            self.sync.stop()
            self.pool.stop()

    def enqueue_missing(self, playlist_id: int) -> int:
        """Manually enqueue every missing (non-tombstone) track of a playlist
        (re-attempts failed ones, unlike the sync auto-enqueue). Routes through the
        shared enrich+enqueue helper. Returns how many were enqueued."""
        from .enqueue import enqueue_track
        n = 0
        for pt in self.db.get_playlist_tracks(playlist_id, "missing"):
            if enqueue_track(self.db, self, _grab_meta(pt),
                             playlist_track_id=pt.get("id")) is not None:
                n += 1
        self.sync.request_sync()
        return n

    # Convenience pass-throughs for the API layer.
    def status(self) -> dict:
        s = self.client.status()
        s["last_error"] = self.sync.last_error
        return s

    def authorize_url(self, state: str) -> str:
        return self.client.authorize_url(state)

    def handle_callback(self, code: str) -> None:
        self.client.exchange_code(code)
        self.sync.request_sync()

    def request_sync(self):
        self.sync.request_sync()
