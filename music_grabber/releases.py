"""The background pass behind New releases and the suggestions (docs/plans/integration-and-suggestions.md, part 2).

Never on page view: pick your artists (library artists with a few tracks, artists on watched playlists),
map each to a Deezer artist, then read each mapped artist's albums, and (when suggestions are on) the
related artists of your top artists and the tracks you starred. Mapping is conservative: a track's ISRC
names its Deezer artist exactly, a name search is accepted only when the normalised name is equal, and
"no confident match" is remembered (and shown nowhere) rather than guessed. Every Deezer failure leaves
the cache as it was. Everything is switchable at runtime: ``releases_enabled`` (the whole thing),
``suggest_gaps``, ``suggest_artists`` and ``suggest_stars``.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import deezer_catalog as dz
from .notify import Notifier
from .subsonic import SubsonicError, call
from .suggest import seeds
from .text import normalize_artist

log = logging.getLogger(__name__)

MIN_OWNED_TRACKS = 3          # library tracks that make an artist "yours"
MIN_PLAYLIST_TRACKS = 2       # ... or tracks on watched playlists
NEW_DAYS = 90                 # how far back the feed looks by default
KEEP_DAYS = 180               # releases older than this are not kept in the "new" cache
MAP_BATCH = 40                # artists mapped per pass (a big library takes a few passes)
POLL_BATCH = 60               # artists whose albums are read per pass
RELATED_BATCH = 15            # artists whose related list is read per pass
RELATED_DAYS = 7
ART_MAP_BATCH = 40            # artists outside the "yours" rule matched per pass, for photos and covers only
ART_POLL_BATCH = 60           # ... and their albums read per pass (once each)
PHOTO_BATCH = 40              # artists whose photo is looked up per pass (only ones matched before photos were kept)
NOTIFY_DAYS = 30              # a release older than this is never announced, even if never seen before
RETRY_UNMAPPED_DAYS = 30
FAILURES_BEFORE_GIVING_UP = 3
IDLE_SECONDS = 300            # how often a switched-off tracker looks at its switch


def _stamp(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class ReleaseTracker:
    def __init__(self, config: dict, db):
        self.config, self.db = config, db
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.last_run: Optional[str] = None
        self.last_error = ""
        self.more_to_do = False

    @property
    def refresh_hours(self) -> float:
        return max(0.25, float(self.config.get("releases_refresh_hours") or 24))

    # ── one pass ──────────────────────────────────────────────────────────────
    def map_artist(self, cand: dict) -> tuple[Optional[str], float, str, str]:
        """(Deezer artist id | None, confidence, how, photo url). May raise CatalogError (Deezer unreachable)."""
        norm = cand["norm"]
        for isrc in cand["isrcs"]:
            d = dz.track_detail_by_isrc(isrc)
            artist = (d or {}).get("artist") or {}
            if artist.get("id") and normalize_artist(artist.get("name")) == norm:
                return str(artist["id"]), 1.0, "isrc", artist.get("picture_medium") or artist.get("picture") or ""
        hits = [h for h in dz.search("artist", cand["name"], limit=5) if normalize_artist(h["name"]) == norm]
        if hits:
            best = max(hits, key=lambda h: h.get("fans") or 0)
            return best["dz_id"], 0.8, "name", best.get("image_url") or ""
        return None, 0.0, "none", ""

    def refresh(self, force: bool = False) -> dict:
        """One pass. Returns what it did; never raises."""
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "skipped": "a refresh is already running"}
        try:
            return self._pass(force)
        except Exception as exc:                                    # a bad pass must not kill the thread
            log.exception("release refresh failed")
            self.last_error = type(exc).__name__
            return {"ok": False, "error": self.last_error}
        finally:
            self._lock.release()

    def _pass(self, force: bool) -> dict:
        cands = self.db.candidate_artists(MIN_OWNED_TRACKS, MIN_PLAYLIST_TRACKS)
        stats = {"ok": True, "artists": len(cands), "mapped_now": 0, "polled": 0, "related": 0, "failed": 0}
        retry_before = _stamp(RETRY_UNMAPPED_DAYS)
        for c in cands:                                            # keep what we know; add rows for new artists
            if self.db.get_artist_map(c["norm"]) is None:
                self.db.save_artist_map(c["norm"], c["name"], None, 0.0, "pending", active=True)
        self.db.set_active_artists(c["norm"] for c in cands)

        failures = 0
        need = [c for c in cands if self._needs_mapping(c, retry_before)]
        self.more_to_do = len(need) > MAP_BATCH
        for c in need[:MAP_BATCH]:
            try:
                dz_id, conf, how, image = self.map_artist(c)
            except dz.CatalogError as exc:
                failures += 1
                self.last_error = str(exc)
                stats["failed"] += 1
                if failures >= FAILURES_BEFORE_GIVING_UP:
                    break
                continue
            self.db.save_artist_map(c["norm"], c["name"], dz_id, conf, how, active=True, image_url=image)
            stats["mapped_now"] += 1

        failures = 0
        stale = _stamp(0) if force else _stamp(self.refresh_hours * 0.9 / 24)
        due = self.db.artists_to_poll(stale, POLL_BATCH + 1)
        self.more_to_do = self.more_to_do or len(due) > POLL_BATCH
        cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).date().isoformat()
        for a in due[:POLL_BATCH]:
            try:
                albums = dz.artist_albums(a["id"])
            except dz.CatalogError as exc:
                failures += 1
                self.last_error = str(exc)
                stats["failed"] += 1
                if failures >= FAILURES_BEFORE_GIVING_UP:
                    break
                continue
            self.db.save_albums(a["id"], a["name"], albums)
            self.db.save_releases(a["id"], a["name"], [x for x in albums if (x.get("release_date") or "") >= cutoff])
            stats["polled"] += 1

        self._read_library_art(cands, retry_before, stats)
        self._read_photos(stats)
        if self.db.artists_missing_image(1):
            self.more_to_do = True
        if self.config.get("suggest_stars", True):
            self._read_stars()
        if self.config.get("suggest_artists", True):
            self._read_related(stats, force)
        if self.config.get("releases_notify"):
            stats["notified"] = self._notify()
        stats["ok"] = stats["failed"] == 0                         # a partial pass is reported, and retried next time
        if stats["ok"]:
            self.last_error = ""
        self.last_run = datetime.now(timezone.utc).isoformat()
        log.info("New releases: %s", stats)
        return stats

    def _notify(self) -> int:
        """One ntfy ping for releases not announced yet. The first time this runs everything already known
        is marked as announced without a ping (the back catalogue is not news). Best effort: a failed
        ping is logged by the Notifier and not retried. Returns how many releases were announced."""
        if not self.db.notify_baseline_done():
            self.db.mark_notified([r["dz_album_id"] for r in self.db.unnotified_releases("0000")])
            self.db.set_notify_baseline()
            return 0
        rows = self.db.unnotified_releases(_stamp(NOTIFY_DAYS)[:10])
        if not rows:
            return 0
        notifier = Notifier.from_config(self.config)
        if notifier:
            lines = [f"{r['artist_name']}: {r['title']} ({r['release_date']})" for r in rows[:8]]
            if len(rows) > 8:
                lines.append(f"and {len(rows) - 8} more")
            base = (self.config.get("ui_public_url") or "").rstrip("/")
            notifier.send_grabber(f"{len(rows)} new release{'s' if len(rows) != 1 else ''} from your artists",
                                  "\n".join(lines), click_url=f"{base}/releases" if base else "",
                                  tags="musical_note")
        self.db.mark_notified([r["dz_album_id"] for r in rows])
        return len(rows)

    def _read_library_art(self, active: list, retry_before: str, stats: dict) -> None:
        """Photos and covers for the rest of the library: artists with too few tracks to count for New releases and
        suggestions. They are matched on Deezer and their albums read once (for covers), kept inactive so they never
        reach the feed or the suggestions."""
        active_norms = {c["norm"] for c in active}
        rest = [c for c in self.db.candidate_artists(1, 10 ** 9) if c["norm"] not in active_norms]
        need = [c for c in rest if self._needs_mapping(c, retry_before)]
        failures = 0
        for c in need[:ART_MAP_BATCH]:
            try:
                dz_id, conf, how, image = self.map_artist(c)
            except dz.CatalogError as exc:
                failures += 1
                self.last_error = str(exc)
                stats["failed"] += 1
                if failures >= FAILURES_BEFORE_GIVING_UP:
                    break
                continue
            self.db.save_artist_map(c["norm"], c["name"], dz_id, conf, how, active=False, image_url=image)
            stats["art_mapped"] = stats.get("art_mapped", 0) + 1
        due = self.db.art_artists_to_poll(ART_POLL_BATCH + 1)
        failures = 0
        for a in due[:ART_POLL_BATCH]:
            try:
                albums = dz.artist_albums(a["id"])
            except dz.CatalogError as exc:
                failures += 1
                self.last_error = str(exc)
                stats["failed"] += 1
                if failures >= FAILURES_BEFORE_GIVING_UP:
                    break
                continue
            self.db.save_albums(a["id"], a["name"], albums)
            self.db.mark_checked(a["id"], a["name"])
            stats["art_polled"] = stats.get("art_polled", 0) + 1
        if len(need) > ART_MAP_BATCH or len(due) > ART_POLL_BATCH:
            self.more_to_do = True

    def run_to_completion(self, max_passes: int = 80) -> None:
        """Check now: a forced first pass, then more passes until everything has been looked at (or Deezer says no)."""
        for i in range(max_passes):
            out = self.refresh(force=(i == 0))
            if not out.get("ok") or not self.more_to_do or self._stop.is_set():
                return

    def _read_photos(self, stats: dict) -> None:
        """Photos for artists matched before photos were kept (the library browser shows them). One small call each."""
        failures = 0
        for a in self.db.artists_missing_image(PHOTO_BATCH):
            try:
                image = dz.artist_info(a["dz_artist_id"]).get("image_url") or ""
            except dz.CatalogError as exc:
                failures += 1
                self.last_error = str(exc)
                stats["failed"] += 1
                if failures >= FAILURES_BEFORE_GIVING_UP:
                    return
                continue
            self.db.set_artist_image(a["dz_artist_id"], image or "-")        # "-": looked, none, so not asked again
            stats["photos"] = stats.get("photos", 0) + 1

    def _read_related(self, stats: dict, force: bool) -> None:
        ids = [s["dz_id"] for s in seeds(self.db)]
        due = self.db.related_due(ids, _stamp(0) if force else _stamp(RELATED_DAYS))
        self.more_to_do = self.more_to_do or len(due) > RELATED_BATCH
        failures = 0
        for dz_id in due[:RELATED_BATCH]:
            try:
                related = dz.artist_related(dz_id)
            except dz.CatalogError as exc:
                failures += 1
                self.last_error = str(exc)
                stats["failed"] += 1
                if failures >= FAILURES_BEFORE_GIVING_UP:
                    return
                continue
            self.db.save_related(dz_id, related)
            stats["related"] += 1

    def _read_stars(self) -> None:
        """Stars from the library server (BPM Tagger or Navidrome), counted per artist. One call; the
        key belongs to one library user, so this is that user's taste. A failure keeps the last counts."""
        cfg = self.config
        if cfg.get("library_index") != "opensubsonic" or not cfg.get("library_url"):
            return
        try:
            inner = call(cfg["library_url"], "getStarred2", {}, user=cfg.get("library_user", ""),
                         password=cfg.get("library_pass", ""), api_key=cfg.get("library_api_key", ""))
        except SubsonicError as exc:
            log.warning("Could not read starred songs: %s", exc)
            return
        counts: dict[str, int] = {}
        for song in (inner.get("starred2") or {}).get("song") or []:
            n = normalize_artist(song.get("artist"))
            if n:
                counts[n] = counts.get(n, 0) + 1
        self.db.replace_artist_taste(counts)

    def _needs_mapping(self, cand: dict, retry_before: str) -> bool:
        row = self.db.get_artist_map(cand["norm"])
        if row is None or row["source"] == "pending":
            return True
        return row["dz_artist_id"] is None and (row["mapped_at"] or "") < retry_before

    def status(self) -> dict:
        return {**self.db.artist_map_counts(), "last_run": self.last_run, "last_checked": self.db.last_release_check(),
                "last_error": self.last_error, "more_to_do": self.more_to_do}

    # ── the thread ────────────────────────────────────────────────────────────
    def request(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="releases")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(5)

    def _run(self) -> None:
        if self._stop.wait(90):                                    # let the library index load first
            return
        while not self._stop.is_set():
            if self.config.get("releases_enabled"):
                self.refresh()
                wait = 600 if self.more_to_do else self.refresh_hours * 3600
            else:
                wait = IDLE_SECONDS                                # off: look at the switch now and then
            self._wake.wait(wait)
            self._wake.clear()
