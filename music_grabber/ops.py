"""The operations layer: everything the API and the CLI can do, as plain methods.

``Ops`` works on a ``GrabberService`` and returns JSON-able dicts/lists. The Flask API
(api/) and the CLI (cli.py) are thin shells over it, so both behave identically. Failures
are ``OpsError`` with a stable ``code`` (the API maps it to an HTTP status).
"""

import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

from . import accounts, deezer_catalog, library_browse, suggest
from .config import SECRET_KEYS, __version__, masked, save_settings, settings_file_path
from .db import GRAB_TERMINAL
from .notify import Notifier
from .enqueue import enqueue_track
from .matching import library_match
from .preview_clips import ClipCache, ClipError
from .providers.ytdlp import YtDlpProvider
from .path_template import render
from .text import normalize_artist, normalize_title
from .spotify import SpotifyAuthError, SpotifyError, parse_playlist_id
from .subsonic import SubsonicError
from .transcode import PROFILES

log = logging.getLogger(__name__)

PROVIDER_NAMES = ("deezer", "ytdlp")
DEEZER_QUALITIES = ("MP3_128", "MP3_320", "FLAC")
MASK = "********"
OAUTH_STATE_TTL = 600
SESSION_DAYS = 14
# Integration API keys: what a key may do (docs/plans/integration-and-suggestions.md).
API_SCOPES = ("browse", "request", "library", "suggestions")
KEY_PREFIX = "mgk_"
# Per-user preferences: key -> (kind, allowed values or range).
PREFS = {"theme": ("enum", ("system", "light", "dark")), "preview_volume": ("float", (0.0, 1.0))}


class OpsError(Exception):
    """A request that cannot be done. ``code`` is stable; ``http`` is the status the API uses."""

    def __init__(self, code: str, message: str = "", http: int = 400, **extra):
        super().__init__(message or code)
        self.code, self.http, self.extra = code, http, extra

    def to_dict(self) -> dict:
        return {"error": self.code, "message": str(self), **self.extra}


def _not_found(what="item") -> OpsError:
    return OpsError("not_found", f"{what} not found", 404)


# Settings that can be changed at runtime. kind: str | bool | int | float | secret | enum.
SETTINGS = {
    "output_format":         ("enum", tuple(PROFILES)),
    "path_template":         ("str", None),
    "provider_order":        ("str", None),
    "deezer_quality":        ("enum", DEEZER_QUALITIES),
    "deezer_arl":            ("secret", None),
    "auto_accept_threshold": ("float", (0.0, 1.0)),
    "ask_threshold":         ("float", (0.0, 1.0)),
    "grab_dry_run":          ("bool", None),
    "lyrics_enabled":        ("bool", None),
    "write_spotify_id_tag":  ("bool", None),
    "spotify_sync_minutes":  ("int", (1, 24 * 60)),
    "grab_workers":          ("int", (1, 3)),
    "ntfy_url":              ("str", None),
    "ntfy_topic":            ("secret", None),
    "ntfy_notify_done":      ("bool", None),
    "ui_public_url":         ("str", None),
    "releases_enabled":      ("bool", None),
    "releases_notify":       ("bool", None),
    "suggest_gaps":          ("bool", None),
    "suggest_artists":       ("bool", None),
    "suggest_stars":         ("bool", None),
}
# Needs a restart to take effect (threads and wiring are created at boot).
RESTART_KEYS = {"grab_workers", "spotify_sync_minutes"}


def _coerce(key: str, value, kind: str, arg):
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if str(value).strip().lower() in ("1", "true", "yes", "on"):
            return True
        if str(value).strip().lower() in ("0", "false", "no", "off"):
            return False
        raise OpsError("invalid_setting", f"{key} must be true or false")
    if kind in ("int", "float"):
        try:
            v = int(value) if kind == "int" else float(value)
        except (TypeError, ValueError):
            raise OpsError("invalid_setting", f"{key} must be a number") from None
        lo, hi = arg
        if not lo <= v <= hi:
            raise OpsError("invalid_setting", f"{key} must be between {lo} and {hi}")
        return v
    value = "" if value is None else str(value).strip()
    if kind == "enum" and value not in arg:
        raise OpsError("invalid_setting", f"{key} must be one of: {', '.join(arg)}")
    if key == "provider_order":
        names = [p.strip().lower() for p in value.split(",") if p.strip()]
        bad = [n for n in names if n not in PROVIDER_NAMES]
        if bad or not names:
            raise OpsError("invalid_setting", f"provider_order must list {', '.join(PROVIDER_NAMES)} "
                           f"(got {value or 'nothing'})")
        return ",".join(names)
    if key in ("ntfy_url", "ui_public_url") and value:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise OpsError("invalid_setting", f"{key} must be an http:// or https:// URL")
        if parts.username or parts.password:
            raise OpsError("invalid_setting", f"{key} must not contain credentials")
    if key == "path_template":
        if not value:
            raise OpsError("invalid_setting", "path_template cannot be empty")
        try:
            sample = render(value, {"title": "T", "artist": "A", "album": "L", "track_no": 1}, "mp3")
        except Exception as exc:
            raise OpsError("invalid_setting", f"path_template does not render: {exc}") from None
        if not sample.endswith(".mp3"):
            raise OpsError("invalid_setting", "path_template must end with {ext}")
    return value


class Ops:
    def __init__(self, service, config: dict, settings_path: Optional[str] = None):
        self.svc = service
        self.db = service.db
        self.config = config
        self.settings_path = settings_path or settings_file_path(config["db_path"])
        self._started = time.time()
        self._oauth_states: dict[str, float] = {}
        self._clips: Optional[ClipCache] = None
        self._totals: dict[str, int] = {}                # Deezer album id -> track count, for "3 of 12 in library"
        self._lock = threading.Lock()

    # ── status ────────────────────────────────────────────────────────────────
    def status(self) -> dict:
        db = self.db
        playlists = db.list_playlists()
        pipe = self.svc.pool.pipeline
        sync = self.svc.sync
        return {
            "version": __version__,
            "uptime_seconds": int(time.time() - self._started),
            "spotify": self.svc.status(),
            "library": {"index": self.svc.index.source.name if self.svc.index.source else None,
                        "tracks": db.library_count(),
                        "auto_queue_paused": sync.queue_paused()},
            "analysis": getattr(self.svc, "analysis", pipe.analysis).name,
            "mirror": {"enabled": self._mirror is not None, "disabled": bool(self._mirror and self._mirror.disabled)},
            "hooks": getattr(getattr(self.svc, "hooks", None), "names", []),
            "providers": [p.name for p in pipe.providers],
            "queue": db.get_queue_counts(),
            "grabbed_total": db.get_grabbed_total(),
            "managed_files": db.get_grabbed_count(),
            "last_synced_epoch": max(sync.last_synced_epoch, db.get_last_sync()) or None,
            "playlists": {
                "total": len(playlists),
                "watched": sum(1 for p in playlists if p.get("enabled")),
                "have": sum(p.get("have_count", 0) for p in playlists),
                "missing": sum(p.get("missing_count", 0) for p in playlists),
                "queued": sum(p.get("queued_count", 0) for p in playlists),
            },
            "dry_run": bool(self.config.get("grab_dry_run")),
            "releases_enabled": bool(self.config.get("releases_enabled")),
        }

    # ── playlists ─────────────────────────────────────────────────────────────
    def playlists_list(self) -> dict:
        playlists = self.db.list_playlists()
        mirror = self._mirror_info(playlists)
        for p in playlists:
            p["mirror"] = mirror.get(p["id"])
        return {"playlists": playlists}

    # ── Navidrome playlist mirror (Q5) ────────────────────────────────────────
    @property
    def _mirror(self):
        return getattr(self.svc, "mirror", None)

    def _mirror_info(self, playlists=None) -> dict:
        """Per playlist: {enabled, state, name, matched, unresolved, synced_at, note, error}, or
        nothing at all when the mirror is not configured."""
        if self._mirror is None:
            return {}
        states = self.db.list_mirrors()
        out = {}
        for p in playlists if playlists is not None else self.db.list_playlists():
            st = states.get(p["id"]) or {}
            on = bool(p.get("mirror", 1)) and bool(p.get("enabled"))
            out[p["id"]] = {"enabled": on, "state": st.get("state") or ("pending" if on else "off"),
                            "name": st.get("remote_name"), "matched": st.get("matched") or 0,
                            "unresolved": st.get("unresolved") or 0, "synced_at": st.get("synced_at"),
                            "note": st.get("note") or "", "error": st.get("last_error") or ""}
        return out

    def mirror_status(self) -> dict:
        m = self._mirror
        return {"enabled": m is not None, "target": "navidrome" if m else None,
                "disabled": bool(m and m.disabled), "prefix": m.prefix if m else None,
                "playlists": self._mirror_info()}

    def mirror_run(self, pid=None) -> dict:
        """Mirror now: one playlist, or every enabled one. Runs in the calling thread."""
        m = self._mirror
        if m is None:
            raise OpsError("mirror_disabled", "the playlist mirror is off: set PLAYLIST_MIRROR=navidrome", 409)
        if pid is not None:
            self._playlist(pid)
        try:
            m.run_pass(only=int(pid) if pid is not None else None, retry_unresolved=True)
        except SubsonicError as exc:
            raise OpsError("navidrome_error", str(exc), 502) from None
        return {"ok": True, **self.mirror_status()}

    def playlist_mirror(self, pid: int, action: str = "now") -> dict:
        """now | on | off | reset | remove for one playlist (see music_grabber/mirror.py)."""
        self._playlist(pid)
        pid = int(pid)
        if action in ("on", "off"):
            self.db.set_playlist_mirror(pid, action == "on")
            return {"ok": True, "enabled": action == "on"}
        m = self._mirror
        if action not in ("now", "reset", "remove"):
            raise OpsError("invalid_action", "action must be now, on, off, reset or remove")
        if m is None:
            raise OpsError("mirror_disabled", "the playlist mirror is off: set PLAYLIST_MIRROR=navidrome", 409)
        try:
            if action == "remove":
                return {"ok": True, "removed": m.remove(pid)}
            if action == "reset":
                m.reset(pid)
            return self.mirror_run(pid)
        except SubsonicError as exc:
            raise OpsError("navidrome_error", str(exc), 502) from None

    def _playlist(self, pid: int) -> dict:
        p = self.db.get_playlist(int(pid), with_counts=True)
        if not p:
            raise _not_found("playlist")
        return p

    def playlist_get(self, pid: int, status: str = "") -> dict:
        p = self._playlist(pid)
        return {"playlist": p, "tracks": self.db.get_playlist_tracks(p["id"], status)}

    def playlist_add(self, value: str) -> dict:
        sid = parse_playlist_id(value)
        if not sid:
            raise OpsError("invalid_playlist", "give a Spotify playlist URL, URI or id")
        name, image, count = sid, "", 0
        client = self.svc.client
        if client.is_connected():
            try:
                meta = client.get_playlist_meta(sid)
                name, image, count = meta["name"] or sid, meta["image_url"], meta["track_count"]
            except SpotifyAuthError as exc:
                raise OpsError("spotify_auth", str(exc), 401) from None
            except SpotifyError as exc:
                raise OpsError("spotify_error", str(exc), 502) from None
        pid = self.db.add_playlist(sid, name, image, count)
        self.svc.request_sync()
        return {"ok": True, "playlist": self._playlist(pid), "connected": client.is_connected()}

    def playlist_remove(self, pid: int) -> dict:
        self._playlist(pid)
        self.db.delete_playlist(int(pid))
        return {"ok": True}

    def playlist_enable(self, pid: int, enabled: bool = True) -> dict:
        self._playlist(pid)
        self.db.set_playlist_enabled(int(pid), bool(enabled))
        return {"ok": True, "enabled": bool(enabled)}

    def sync(self, pid: Optional[int] = None) -> dict:
        """Sync one playlist now, or every enabled one. Runs in the calling thread."""
        client = self.svc.client
        if not client.is_connected():
            raise OpsError("spotify_disconnected", "Spotify is not connected: run `spotify-login`", 409)
        try:
            if pid is not None:
                self._playlist(pid)
                self.svc.sync.sync_playlist(int(pid))
            else:
                self.svc.sync.sync_all()
        except SpotifyAuthError as exc:
            raise OpsError("spotify_auth", str(exc), 401) from None
        except SpotifyError as exc:
            raise OpsError("spotify_error", str(exc), 502) from None
        return {"ok": True, **self.playlists_list(), "queue": self.db.get_queue_counts()}

    def enqueue_missing(self, pid: int) -> dict:
        self._playlist(pid)
        return {"ok": True, "enqueued": self.svc.enqueue_missing(int(pid))}

    # ── queue ─────────────────────────────────────────────────────────────────
    def queue_list(self, status: str = "") -> dict:
        return {"items": self.db.get_queue(status), "counts": self.db.get_queue_counts()}

    def queue_history(self) -> dict:
        return {"items": self.db.get_grab_history()}

    def _item(self, item_id: int) -> dict:
        item = self.db.get_grab_item(int(item_id))
        if not item:
            raise _not_found("queue item")
        return item

    def queue_item(self, item_id: int) -> dict:
        item = self._item(item_id)
        return {"item": item, "candidates": self.db.get_grab_candidates(item["id"]),
                "events": self.db.get_grab_events(item["id"])}

    def queue_add(self, meta: dict) -> dict:
        """Manually enqueue a track (from a Spotify search, or by hand)."""
        if not (meta.get("title") or meta.get("spotify_track_id")):
            raise OpsError("invalid_track", "title or spotify_track_id is required")
        keys = ("spotify_track_id", "title", "artist", "album", "album_artist", "duration_ms",
                "isrc", "track_no", "disc_no", "year", "cover_url")
        item_id = self.db.enqueue_grab({k: meta.get(k) for k in keys})
        if item_id is None:
            raise OpsError("already_queued", "already queued", 409)
        self.svc.request_sync()
        return {"ok": True, "id": item_id}

    def queue_retry(self, item_id: int) -> dict:
        item = self._item(item_id)
        if item["status"] not in ("failed", "skipped"):
            raise OpsError("not_retryable", f"item is {item['status']}", status=item["status"])
        self.db.update_grab(item["id"], error=None, progress=0)
        self.db.transition(item["id"], "pending", "retry")
        return {"ok": True}

    def queue_retry_failed(self) -> dict:
        n = 0
        for it in self.db.get_queue("failed"):
            self.db.update_grab(it["id"], error=None, progress=0)
            self.db.transition(it["id"], "pending", "retry (all failed)")
            n += 1
        return {"ok": True, "requeued": n}

    def queue_cancel(self, item_id: int) -> dict:
        item = self._item(item_id)
        if item["status"] in GRAB_TERMINAL:
            raise OpsError("already_terminal", f"item is {item['status']}", status=item["status"])
        self.db.transition(item["id"], "skipped", "cancelled")
        return {"ok": True}

    def queue_delete(self, item_id: int) -> dict:
        item = self._item(item_id)
        if item["status"] not in GRAB_TERMINAL:
            raise OpsError("not_deletable", f"cancel it first (item is {item['status']})",
                           status=item["status"])
        self.db.delete_grab(item["id"])
        return {"ok": True}

    def queue_clear_completed(self) -> dict:
        return {"ok": True, "removed": self.db.delete_completed_grabs()}

    def queue_priority(self, item_id: int, priority: int) -> dict:
        self._item(item_id)
        try:
            pri = int(priority)
        except (TypeError, ValueError):
            raise OpsError("invalid_priority", "priority must be an integer") from None
        self.db.update_grab(int(item_id), priority=pri)
        return {"ok": True, "priority": pri}

    # ── inbox (ambiguous matches waiting for a human) ─────────────────────────
    def inbox_list(self) -> dict:
        items = self.db.get_queue("awaiting_user")
        for it in items:
            it["candidates"] = self.db.get_grab_candidates(it["id"])
        return {"items": items}

    def _awaiting(self, item_id: int) -> dict:
        item = self._item(item_id)
        if item["status"] != "awaiting_user":
            raise OpsError("not_awaiting", f"item is {item['status']}", status=item["status"])
        return item

    def inbox_preview(self, item_id: int, candidate_id=None) -> dict:
        """Something to listen to before choosing: a candidate's 30-second Deezer preview, or (no
        candidate) the track being looked for, found on Deezer by its ISRC. ``url`` is empty when
        there is none; a YouTube candidate has no preview but ``open_url`` to listen to it there."""
        item = self._item(item_id)
        if candidate_id in (None, ""):
            res = deezer_catalog.track_by_isrc(item.get("isrc") or "") if item.get("isrc") else {}
            return {"url": res.get("preview_url", ""), "open_url": "",
                    "reason": "" if res.get("preview_url") else "no preview found for the original track"}
        cand = self.db.get_grab_candidate(int(candidate_id))
        if not cand or cand["queue_item_id"] != item["id"]:
            raise OpsError("invalid_candidate", "that candidate does not belong to this item")
        if cand["provider"] == "deezer":
            url = deezer_catalog.track_preview_url(cand["provider_track_id"] or "")
            return {"url": url, "open_url": "", "reason": "" if url else "Deezer has no preview for this track"}
        link = cand.get("url") or (f"https://music.youtube.com/watch?v={cand['provider_track_id']}"
                                   if cand["provider"] == "ytdlp" and cand.get("provider_track_id") else "")
        link = link if link.startswith("https://") else ""
        if cand["provider"] == "ytdlp":
            # A 30-second clip cut on the server; when that fails the caller still gets the link to open.
            try:
                name = self.preview_clips().clip_for(cand)
            except ClipError as exc:
                return {"url": "", "clip": "", "open_url": link,
                        "reason": f"couldn't make a preview ({exc}); open it to listen"}
            return {"url": "", "clip": name, "open_url": link, "reason": ""}
        return {"url": "", "open_url": link, "reason": "this source has no preview; open it to listen"}

    def preview_clips(self) -> ClipCache:
        """The cache of YouTube preview clips (under the data folder), made on first use."""
        if self._clips is None:
            def ydl_cls():
                prov = next((p for p in self.svc.pool.pipeline.providers if getattr(p, "name", "") == "ytdlp"), None)
                return (prov or YtDlpProvider(self.config))._YoutubeDL()
            self._clips = ClipCache(os.path.join(os.path.dirname(os.path.abspath(self.config["db_path"])), "previews"), ydl_cls)
        return self._clips

    def inbox_choose(self, item_id: int, candidate_id: int) -> dict:
        item = self._awaiting(item_id)
        cand = self.db.get_grab_candidate(int(candidate_id)) if candidate_id else None
        if not cand or cand["queue_item_id"] != item["id"]:
            raise OpsError("invalid_candidate", "that candidate does not belong to this item")
        self.db.update_grab(item["id"], chosen_candidate_id=cand["id"], search_override=None)
        self.db.transition(item["id"], "pending", f"chose candidate #{cand['id']}")
        return {"ok": True}

    def inbox_search(self, item_id: int, query: str) -> dict:
        item = self._awaiting(item_id)
        query = str(query or "").strip()
        if not query:
            raise OpsError("query_required", "give a search query")
        self.db.update_grab(item["id"], search_override=query, chosen_candidate_id=None)
        self.db.transition(item["id"], "pending", f"re-search: {query}")
        return {"ok": True}

    def inbox_research(self, item_id: int) -> dict:
        item = self._awaiting(item_id)
        self.db.update_grab(item["id"], search_override=None, chosen_candidate_id=None)
        self.db.transition(item["id"], "pending", "search again (original metadata)")
        return {"ok": True}

    def inbox_research_all(self) -> dict:
        n = 0
        for it in self.db.get_queue("awaiting_user"):
            self.db.update_grab(it["id"], search_override=None, chosen_candidate_id=None)
            self.db.transition(it["id"], "pending", "search again (all)")
            n += 1
        return {"ok": True, "requeued": n}

    def inbox_skip(self, item_id: int) -> dict:
        item = self._awaiting(item_id)
        self.db.transition(item["id"], "skipped", "skipped from inbox")
        return {"ok": True}

    # ── library ───────────────────────────────────────────────────────────────
    def library_refresh(self) -> dict:
        try:
            return {"ok": True, **self.svc.index.refresh()}
        except Exception as exc:
            raise OpsError("library_error", str(exc), 502) from None

    def library_list(self, query: str = "", limit: int = 100, offset: int = 0) -> dict:
        """The tracks the library index knows (what search marks as owned), a page at a time."""
        try:
            limit, offset = int(limit), int(offset)
        except (TypeError, ValueError):
            raise OpsError("invalid_paging", "limit and offset must be numbers", 400) from None
        limit, offset = max(1, min(limit, 500)), max(0, offset)
        tracks, total = self.db.library_list(str(query or "").strip(), limit, offset)
        return {"tracks": tracks, "total": total, "limit": limit, "offset": offset, "q": str(query or "").strip(),
                "index": self.svc.index.source.name if self.svc.index.source else None}

    # ── browsing the library by artist / album ────────────────────────────────
    def _with_art(self, albums: list[dict]) -> list[dict]:
        """Cover and Deezer ids for album summaries, from what New releases already cached (no Deezer calls)."""
        art, by_dz = self.db.artist_art(), self.db.album_art_all()
        for a in albums:
            info = art.get(normalize_artist(a["artist"]))
            a.update(cover_url="", dz_album_id=None, dz_artist_id=info["dz_id"] if info else None)
            if info:
                nt = normalize_title(a["title"])
                for norm, cover, aid in by_dz.get(info["dz_id"], []):
                    if suggest.same_album(norm, nt):
                        a["cover_url"], a["dz_album_id"] = cover, aid
                        break
        return albums

    def library_artists(self, query: str = "") -> dict:
        art = self.db.artist_art()
        out = library_browse.artists(self.db.browse_rows(), str(query or ""))
        for a in out:
            info = art.get(normalize_artist(a["name"]))
            a["image_url"] = (info["image_url"] if info and info["image_url"] != "-" else "")
        return {"artists": out, "total": len(out)}

    def library_artist(self, name: str) -> dict:
        page = library_browse.artist_page(self.db.browse_rows(), str(name or ""))
        if not page:
            raise _not_found("artist")
        info = self.db.artist_art().get(normalize_artist(page["name"]))
        page["dz_artist_id"] = info["dz_id"] if info else None
        page["image_url"] = info["image_url"] if info and info["image_url"] != "-" else ""
        self._with_art(page["albums"])
        self._with_art(page["appears_on"])
        return page

    def library_albums(self, query: str = "") -> dict:
        out = self._with_art(library_browse.albums(self.db.browse_rows(), str(query or "")))
        return {"albums": out, "total": len(out)}

    def library_album(self, artist: str, album: str) -> dict:
        page = library_browse.album_page(self.db.browse_rows(), str(artist or ""), str(album or ""))
        if not page:
            raise _not_found("album")
        self._with_art([page])
        return page

    def library_related(self, path: str) -> dict:
        """Where an owned track lives on Deezer, and who is related to its artist. The ISRC names the track,
        artist and album exactly; without one only the artist can be found (the artist map the new releases
        keep, then an equal-name search), never a guess. ``found`` is False when nothing is certain."""
        row = self.db.library_track(str(path or ""))
        if not row:
            raise _not_found("track")
        artist = album = track = None
        how = ""
        if row.get("isrc"):
            d = deezer_catalog.track_detail_by_isrc(row["isrc"])
            if d is None:
                raise OpsError("deezer_unreachable", "Deezer is unreachable", 502)
            if d.get("id") and (d.get("artist") or {}).get("id"):
                artist = {"dz_id": str(d["artist"]["id"]), "name": d["artist"].get("name") or ""}
                if (d.get("album") or {}).get("id"):
                    album = {"dz_id": str(d["album"]["id"]), "title": d["album"].get("title") or ""}
                track = {"dz_id": str(d["id"]), "title": d.get("title") or row.get("title") or ""}
                how = "isrc"
        if artist is None and row.get("norm_artist"):
            mapped = self.db.get_artist_map(row["norm_artist"])
            if mapped and mapped.get("dz_artist_id"):
                artist, how = {"dz_id": mapped["dz_artist_id"], "name": mapped.get("name") or row.get("artist") or ""}, "artist_map"
        if artist is None and row.get("artist"):
            hits = [h for h in self._catalog(deezer_catalog.search, "artist", row["artist"], 5)
                    if normalize_artist(h["name"]) == normalize_artist(row["artist"])]
            if hits:
                best = max(hits, key=lambda h: h.get("fans") or 0)
                artist, how = {"dz_id": best["dz_id"], "name": best["name"]}, "name"
        related = []
        if artist:
            related = [{k: a[k] for k in ("dz_id", "name", "image_url", "fans")}
                       for a in self._catalog(deezer_catalog.artist_related, artist["dz_id"])]
        return {"found": artist is not None, "matched_by": how or None, "artist": artist, "album": album,
                "track": track, "related": related}

    def ledger_rebuild(self) -> dict:
        """Add a ledger row for every library file that carries our Spotify id tag and has none.
        Only adds: existing rows and the files themselves are never touched. Needs the
        filesystem library index (the OpenSubsonic API does not expose custom tags)."""
        index = self.svc.index
        if not index.source or index.source.name != "filesystem":
            raise OpsError("unsupported", "ledger rebuild reads tags from files: it needs "
                           "LIBRARY_INDEX=filesystem", 400)
        try:
            index.refresh()
        except Exception as exc:
            raise OpsError("library_error", str(exc), 502) from None
        root = index.root
        tagged = self.db.library_tagged()
        rows = [{"spotify_track_id": t["spotify_track_id"], "isrc": t["isrc"], "rel_path": t["path"],
                 "abs_path": os.path.join(root, *t["path"].split("/")),
                 "output_format": os.path.splitext(t["path"])[1].lstrip(".").lower(),
                 "placed_hash": t["change_token"]} for t in tagged]
        added = self.db.add_grabbed_files_missing(rows)
        return {"ok": True, "tagged_files": len(rows), "added": added, "already_in_ledger": len(rows) - added}

    # ── accounts ──────────────────────────────────────────────────────────────
    def auth_state(self) -> dict:
        return {"needs_setup": self.db.count_users() == 0}

    def _public_user(self, user: dict) -> dict:
        return {k: user.get(k) for k in ("id", "username", "role", "prefs", "created_at", "last_login")}

    def _validated(self, username: str, password: str) -> str:
        username = (username or "").strip()
        problem = accounts.check_username(username) or accounts.check_password(password, username)
        if problem:
            raise OpsError("invalid_account", problem)
        return username

    def _make_user(self, username: str, password: str, role: str) -> dict:
        username = self._validated(username, password)
        if role not in accounts.ROLES:
            raise OpsError("invalid_account", f"role must be one of: {', '.join(accounts.ROLES)}")
        try:
            return self._public_user(self.db.create_user(username, accounts.hash_password(password), role))
        except ValueError:
            raise OpsError("username_taken", "that username is already taken", 409) from None

    def create_first_admin(self, username: str, password: str) -> dict:
        with self._lock:                       # two simultaneous setups must not both win
            if self.db.count_users():
                raise OpsError("already_set_up", "an account already exists; sign in instead", 409)
            return self._make_user(username, password, "admin")

    def login(self, username: str, password: str) -> dict:
        row = self.db.get_user_credentials((username or "").strip())
        ok = accounts.verify_password(password if isinstance(password, str) else "",
                                      row["password_hash"] if row else accounts.DUMMY_HASH)
        if not row or not ok:
            raise OpsError("bad_credentials", "wrong username or password", 401)
        token = secrets.token_urlsafe(32)
        self.db.create_session(row["id"], token, SESSION_DAYS)
        self.db.touch_login(row["id"])
        return {"token": token, "user": self._public_user(self.db.get_user(row["id"]))}

    def logout(self, token: str) -> dict:
        self.db.delete_session(token)
        return {"ok": True}

    def session_user(self, token: str) -> Optional[dict]:
        u = self.db.session_user(token, SESSION_DAYS)
        return self._public_user(u) if u else None

    def change_password(self, user_id: int, current: str, new: str, keep_token: Optional[str] = None) -> dict:
        row = self.db.get_user(user_id)
        if not row:
            raise _not_found("user")
        creds = self.db.get_user_credentials(row["username"])
        if not accounts.verify_password(current if isinstance(current, str) else "", creds["password_hash"]):
            raise OpsError("bad_credentials", "the current password is wrong", 403)
        problem = accounts.check_password(new, row["username"])
        if problem:
            raise OpsError("invalid_account", problem)
        self.db.set_user_password(user_id, accounts.hash_password(new))
        self.db.delete_user_sessions(user_id, keep_token)          # other devices sign in again
        return {"ok": True}

    def prefs_update(self, user_id: int, changes: dict) -> dict:
        if not isinstance(changes, dict) or not changes:
            raise OpsError("invalid_pref", "no preferences given")
        user = self.db.get_user(user_id)
        if not user:
            raise _not_found("user")
        prefs = dict(user["prefs"])
        for key, value in changes.items():
            if key not in PREFS:
                raise OpsError("invalid_pref", f"unknown preference '{key}'", known=sorted(PREFS))
            kind, arg = PREFS[key]
            if kind == "enum":
                if value not in arg:
                    raise OpsError("invalid_pref", f"{key} must be one of: {', '.join(arg)}")
                prefs[key] = value
            else:
                try:
                    v = float(value)
                except (TypeError, ValueError):
                    raise OpsError("invalid_pref", f"{key} must be a number") from None
                if not arg[0] <= v <= arg[1]:
                    raise OpsError("invalid_pref", f"{key} must be between {arg[0]} and {arg[1]}")
                prefs[key] = v
        self.db.set_user_prefs(user_id, prefs)
        return {"ok": True, "prefs": prefs}

    def users_list(self) -> dict:
        return {"users": [self._public_user(u) for u in self.db.list_users()]}

    def users_create(self, username: str, password: str, role: str = "user") -> dict:
        return {"user": self._make_user(username, password, role)}

    def _other_admin_needed(self, user: dict) -> None:
        if user["role"] == "admin" and self.db.count_admins() <= 1:
            raise OpsError("last_admin", "there must be at least one admin", 409)

    def users_update(self, user_id: int, changes: dict) -> dict:
        user = self.db.get_user(user_id)
        if not user:
            raise _not_found("user")
        if "role" in changes:
            if changes["role"] not in accounts.ROLES:
                raise OpsError("invalid_account", f"role must be one of: {', '.join(accounts.ROLES)}")
            if changes["role"] != "admin":
                self._other_admin_needed(user)
            self.db.set_user_role(user_id, changes["role"])
        if "password" in changes:
            problem = accounts.check_password(changes["password"], user["username"])
            if problem:
                raise OpsError("invalid_account", problem)
            self.db.set_user_password(user_id, accounts.hash_password(changes["password"]))
            self.db.delete_user_sessions(user_id)
        return {"user": self._public_user(self.db.get_user(user_id))}

    def users_delete(self, user_id: int) -> dict:
        user = self.db.get_user(user_id)
        if not user:
            raise _not_found("user")
        self._other_admin_needed(user)
        self.db.delete_user(user_id)
        return {"ok": True}

    # ── settings ──────────────────────────────────────────────────────────────
    def settings_get(self) -> dict:
        keys = list(SETTINGS)
        return {"settings": {k: masked(self.config).get(k) for k in keys},
                "editable": {k: {"kind": v[0], "options": v[1] if v[0] == "enum" else None}
                             for k, v in SETTINGS.items()},
                "secrets": [k for k in keys if k in SECRET_KEYS]}

    def settings_update(self, changes: dict) -> dict:
        if not isinstance(changes, dict) or not changes:
            raise OpsError("invalid_setting", "no settings given")
        updates = {}
        for key, value in changes.items():
            if key not in SETTINGS:
                raise OpsError("unknown_setting", f"unknown or read-only setting '{key}'",
                               known=sorted(SETTINGS))
            kind, arg = SETTINGS[key]
            if kind == "secret":
                if value == MASK:
                    continue                         # the masked placeholder: leave unchanged
                updates[key] = None if value in (None, "") else str(value).strip()
                continue
            updates[key] = _coerce(key, value, kind, arg)
        accept = updates.get("auto_accept_threshold", self.config.get("auto_accept_threshold", 0.85))
        ask = updates.get("ask_threshold", self.config.get("ask_threshold", 0.55))
        if ask > accept:
            raise OpsError("invalid_setting", "ask_threshold cannot be above auto_accept_threshold")
        self.config.update({k: ("" if v is None else v) for k, v in updates.items()})
        save_settings(self.settings_path, updates)
        if {"releases_enabled", "suggest_artists", "suggest_stars"} & set(updates) and self.svc.releases:
            self.svc.releases.request()                  # switched on: look now, not in five minutes
        pipe = self.svc.pool.pipeline
        pipe.reload_settings()
        if {"ntfy_url", "ntfy_topic"} & set(updates):
            notifier = Notifier.from_config(self.config)
            pipe.notifier = notifier
            self.svc.sync.notifier = notifier
        return {"ok": True, "applied": sorted(updates),
                "restart_required": sorted(RESTART_KEYS & set(updates))}

    def test_deezer(self, arl: Optional[str] = None) -> dict:
        arl = arl if arl and arl != MASK else self.config.get("deezer_arl", "")
        if not arl:
            raise OpsError("arl_required", "no Deezer ARL configured")
        from .providers.deezer import DeezerProvider
        error = DeezerProvider({**self.config, "deezer_arl": arl.strip()}).verify()
        return {"ok": error is None, "message": "ARL accepted" if error is None else None, "error": error}

    def test_ntfy(self) -> dict:
        n = Notifier.from_config(self.config)
        if n is None:
            raise OpsError("ntfy_not_configured", "set NTFY_URL and NTFY_TOPIC first", 409)
        n.send_grabber("Music Grabber test", "ntfy is working.", priority="low", tags="white_check_mark")
        return {"ok": True}

    # ── Spotify ───────────────────────────────────────────────────────────────
    def spotify_status(self) -> dict:
        return self.svc.status()

    def spotify_authorize_url(self) -> dict:
        state = secrets.token_urlsafe(24)
        try:
            url = self.svc.authorize_url(state)
        except SpotifyError as exc:
            raise OpsError("spotify_not_configured", str(exc), 409) from None
        now = time.time()
        with self._lock:
            self._oauth_states = {s: t for s, t in self._oauth_states.items() if now - t < OAUTH_STATE_TTL}
            self._oauth_states[state] = now
        return {"url": url, "state": state}

    def spotify_callback(self, code: str, state: str) -> dict:
        """Finish the OAuth flow. ``state`` must be one we issued (single use)."""
        with self._lock:
            issued = self._oauth_states.pop(state or "", None)
        if issued is None or time.time() - issued >= OAUTH_STATE_TTL:
            raise OpsError("invalid_state", "unknown or expired login: start again", 400)
        if not code:
            raise OpsError("missing_code", "no authorization code", 400)
        try:
            self.svc.handle_callback(code)
        except SpotifyError as exc:
            raise OpsError("spotify_auth", str(exc), 502) from None
        return {"ok": True}

    def spotify_disconnect(self) -> dict:
        self.svc.client.disconnect()
        return {"ok": True}

    def spotify_playlists(self) -> dict:
        client = self.svc.client
        if not client.is_connected():
            raise OpsError("spotify_disconnected", "Spotify is not connected", 409)
        try:
            mine = client.get_my_playlists()
        except SpotifyAuthError as exc:
            raise OpsError("spotify_auth", str(exc), 401) from None
        except SpotifyError as exc:
            raise OpsError("spotify_error", str(exc), 502) from None
        watched = {p["spotify_id"] for p in self.db.list_playlists()}
        for p in mine:
            p["watched"] = p["spotify_id"] in watched
        return {"playlists": mine}

    def spotify_search(self, query: str) -> dict:
        client = self.svc.client
        if not client.is_connected():
            raise OpsError("spotify_disconnected", "Spotify is not connected", 409)
        try:
            return {"tracks": client.search_tracks(query, limit=20)}
        except SpotifyAuthError as exc:
            raise OpsError("spotify_auth", str(exc), 401) from None
        except SpotifyError as exc:
            raise OpsError("spotify_error", str(exc), 502) from None

    # ── browse Deezer's catalogue and queue from it ───────────────────────────
    def _catalog(self, fn, *a):
        try:
            return fn(*a)
        except deezer_catalog.CatalogError as exc:
            raise OpsError("deezer_unreachable", str(exc), 502) from None

    def _states(self, tracks: list) -> list:
        """Annotate catalogue tracks with what this app already knows: in the library, queued
        (with live progress), waiting in the inbox, or failed. Matching is by normalised title +
        artist (catalogue results carry no ISRC), against the library index and the queue."""
        queue: dict = {}
        for it in self.db.get_queue():           # newest first; keep the first (most recent) per key
            queue.setdefault((normalize_title(it.get("title")), normalize_artist(it.get("artist"))), it)
        for t in tracks:
            key = (normalize_title(t.get("title")), normalize_artist(t.get("artist")))
            it, state = queue.get(key), {"kind": "none"}
            if it and it["status"] not in GRAB_TERMINAL:
                state = {"kind": "review" if it["status"] == "awaiting_user" else
                         ("queued" if it["status"] == "pending" else "active"),
                         "item_id": it["id"], "status": it["status"], "progress": it.get("progress") or 0}
            elif library_match({"title": t.get("title"), "artist": t.get("artist"), "album": t.get("album"),
                                "duration_ms": t.get("duration_ms"), "isrc": ""}, self.svc.index, 0.88) \
                    or (it and it["status"] == "done"):
                state = {"kind": "library"}
            elif it and it["status"] in ("failed", "skipped"):
                state = {"kind": "failed" if it["status"] == "failed" else "none", "item_id": it["id"],
                         "error": it.get("error") or ""}
            t["state"] = state
        return tracks

    def browse_search(self, query: str, kind: str = "track") -> dict:
        query = (query or "").strip()
        if not query:
            raise OpsError("query_required", "type something to search for")
        results = self._catalog(deezer_catalog.search, kind, query)
        if kind == "track":
            self._states(results)
        return {"kind": kind, "query": query, "results": results}

    MAX_TOTAL_LOOKUPS = 12

    def browse_artist(self, dz_id: str) -> dict:
        page = self._catalog(deezer_catalog.artist_page, str(dz_id))
        self._states(page["top"])
        self._ownership(page["artist"]["name"], page["albums"])
        return page

    def _ownership(self, artist: str, albums: list) -> None:
        """Mark each release with how much of it the library has: ``own_state`` is none, some (a count but no known
        length), partial (3 of 12) or full. An album is matched by title like the suggestions do (a "Deluxe" counts as the
        album), a single by its title among the artist's tracks. A release's length is looked up from Deezer only when
        you own part of it, at most a dozen per page view, and remembered."""
        per_album, titles = library_browse.owned_index(self.db.browse_rows(), artist)
        lookups = 0
        for a in albums:
            nt = normalize_title(a["title"])
            owned = sum(n for key, n in per_album.items() if key and suggest.same_album(nt, key))
            total = a.get("track_count") or self._totals.get(a["dz_id"])
            if owned == 0 and a.get("kind") == "single" and nt in titles:
                owned, total = 1, 1                      # the track is in the library, on some other album
            if owned and not total and lookups < self.MAX_TOTAL_LOOKUPS:
                lookups += 1
                try:
                    detail = deezer_catalog.album_page(a["dz_id"])
                    total = detail["album"].get("track_count") or len(detail["tracks"]) or None
                except deezer_catalog.CatalogError:
                    total = None
                if total:
                    self._totals[a["dz_id"]] = total
            a["owned"], a["owned_total"] = owned, total
            a["own_state"] = ("none" if not owned else "some" if not total else "full" if owned >= total else "partial")

    def browse_album(self, dz_id: str) -> dict:
        page = self._catalog(deezer_catalog.album_page, str(dz_id))
        self._states(page["tracks"])
        return page

    MAX_QUEUE_BATCH = 100

    def browse_queue(self, tracks: list, force: bool = False) -> dict:
        """Queue catalogue tracks. Already-owned tracks are skipped unless ``force``; a track
        already in flight is reported, not duplicated. Returns per-track outcomes and the new item
        ids (so the UI can offer undo)."""
        if not isinstance(tracks, list) or not tracks:
            raise OpsError("no_tracks", "nothing to queue")
        if len(tracks) > self.MAX_QUEUE_BATCH:
            raise OpsError("too_many", f"queue at most {self.MAX_QUEUE_BATCH} tracks at once")
        if not force:
            self._states(tracks)
        out, ids = [], []
        for t in tracks:
            if not (t.get("title") and t.get("dz_id")):
                out.append({"dz_id": t.get("dz_id"), "result": "invalid"})
                continue
            kind = (t.get("state") or {}).get("kind")
            if kind == "library" and not force:
                out.append({"dz_id": t["dz_id"], "result": "in_library"})
                continue
            meta = {"title": t["title"], "artist": t.get("artist") or "", "album": t.get("album") or "",
                    "album_artist": t.get("album_artist") or t.get("artist") or "",
                    "duration_ms": t.get("duration_ms"), "cover_url": t.get("cover_url") or "",
                    "track_no": t.get("track_no"), "disc_no": t.get("disc_no"), "year": t.get("year")}
            item_id = enqueue_track(self.db, self.svc, meta, dz_track_id=str(t["dz_id"]))
            if item_id is None:
                out.append({"dz_id": t["dz_id"], "result": "already_queued"})
            else:
                ids.append(item_id)
                out.append({"dz_id": t["dz_id"], "result": "queued", "item_id": item_id})
        if ids:
            self.svc.request_sync()
        return {"ok": True, "queued": len(ids), "item_ids": ids, "results": out}

    def browse_queue_album(self, dz_id: str, force: bool = False) -> dict:
        page = self._catalog(deezer_catalog.album_page, str(dz_id))
        return {**self.browse_queue(page["tracks"], force), "album": page["album"]["title"]}

    def undo_queue(self, item_ids: list) -> dict:
        """Cancel items that were just queued and are still pending (the toast's Undo)."""
        n = 0
        for i in item_ids or []:
            it = self.db.get_grab_item(int(i))
            if it and it["status"] == "pending":
                self.db.delete_grab(it["id"])
                n += 1
        return {"ok": True, "removed": n}

    # ── integration API: keys for other apps, and requests from them ──────────
    def keys_list(self) -> dict:
        return {"keys": self.db.list_api_keys()}

    def keys_create(self, name: str, scopes, created_by: str = "") -> dict:
        name = (name or "").strip()
        if not 1 <= len(name) <= 40 or not name.isprintable():
            raise OpsError("invalid_key", "a key needs a name of 1 to 40 characters")
        if isinstance(scopes, str):
            scopes = [s.strip() for s in scopes.split(",") if s.strip()]
        if not isinstance(scopes, (list, tuple)) or not scopes or any(s not in API_SCOPES for s in scopes):
            raise OpsError("invalid_key", f"scopes must be some of: {', '.join(API_SCOPES)}")
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        try:
            row = self.db.create_api_key(name, key, sorted(set(scopes), key=API_SCOPES.index), created_by)
        except ValueError:
            raise OpsError("key_name_taken", "a key with that name already exists", 409) from None
        return {"key": row, "token": key}          # the only time the plain key is ever shown

    def keys_revoke(self, key_id: int) -> dict:
        if not self.db.revoke_api_key(int(key_id)):
            raise _not_found("key")
        return {"ok": True}

    def api_key_principal(self, token: str) -> Optional[dict]:
        """The principal for a presented API key (None for an unknown or revoked one)."""
        if not token.startswith(KEY_PREFIX):
            return None
        row = self.db.api_key_for(token)
        if not row:
            return None
        return {"id": None, "username": row["name"], "role": "key", "scopes": row["scopes"], "prefs": {}}

    def request_track(self, data: dict, requested_by: str) -> dict:
        """Queue one track for another app. Identify it by ``dz_id`` or ``isrc`` (exact), or by
        ``title`` + ``artist``. Already owned -> ``in_library``; already in flight -> ``already_queued``;
        the same ``source_ref`` filed twice -> ``already_requested`` (idempotent)."""
        ref = str(data.get("source_ref") or "").strip()[:200]
        if ref:
            prev = self.db.find_request(requested_by, ref)
            if prev and prev["status"] not in ("failed", "skipped"):
                return {"ok": True, "result": "already_requested", "id": prev["id"], "status": prev["status"]}
        dz_id, isrc = str(data.get("dz_id") or "").strip(), str(data.get("isrc") or "").strip().upper()
        title, artist = str(data.get("title") or "").strip(), str(data.get("artist") or "").strip()
        if dz_id or (isrc and not (title and artist)):
            found = self._catalog(deezer_catalog.track_page, dz_id or f"isrc:{isrc}")
            if not found:
                raise OpsError("not_found", "the catalogue has no such track", 404)
            dz_id = found["dz_id"]
            meta = {k: found.get(k) for k in ("title", "artist", "album", "duration_ms", "cover_url", "track_no",
                                              "disc_no", "year", "isrc")}
        elif title and artist:
            meta = {"title": title, "artist": artist, "album": str(data.get("album") or "").strip(),
                    "duration_ms": data.get("duration_ms") if isinstance(data.get("duration_ms"), int) else None,
                    "isrc": isrc}
        else:
            raise OpsError("invalid_track", "send dz_id, isrc, or title and artist")
        meta["album_artist"] = meta["artist"]
        if not data.get("force") and library_match(
                {"title": meta["title"], "artist": meta["artist"], "album": meta.get("album"),
                 "duration_ms": meta.get("duration_ms"), "isrc": meta.get("isrc") or ""}, self.svc.index, 0.88):
            return {"ok": True, "result": "in_library"}
        item_id = enqueue_track(self.db, self.svc, {**meta, "requested_by": requested_by, "source_ref": ref or None},
                                dz_track_id=dz_id)
        if item_id is None:
            return {"ok": True, "result": "already_queued"}
        self.svc.request_sync()
        return {"ok": True, "result": "queued", "id": item_id, "status": "pending"}

    REQUEST_FIELDS = ("id", "status", "progress", "title", "artist", "album", "error", "source_ref",
                      "requested_by", "created_at", "updated_at")

    def request_status(self, item_id: int, requested_by: Optional[str] = None) -> dict:
        """One queue item as an integration sees it. ``requested_by`` limits it to that caller's own."""
        item = self.db.get_grab_item(int(item_id))
        if not item or (requested_by is not None and item.get("requested_by") != requested_by):
            raise _not_found("request")
        return {k: item.get(k) for k in self.REQUEST_FIELDS}

    def library_has(self, data: dict) -> dict:
        title, artist, isrc = (str(data.get(k) or "").strip() for k in ("title", "artist", "isrc"))
        if not (isrc or (title and artist)):
            raise OpsError("invalid_track", "send isrc, or title and artist")
        have = library_match({"title": title, "artist": artist, "album": str(data.get("album") or ""),
                              "duration_ms": data.get("duration_ms") if isinstance(data.get("duration_ms"), int) else None,
                              "isrc": isrc}, self.svc.index, 0.88)
        return {"have": bool(have)}

    # ── new releases from your artists (docs/plans/integration-and-suggestions.md, part 2) ──
    def releases_feed(self, user_id: int, days=0) -> dict:
        from .releases import NEW_DAYS
        days = int(days) if str(days).isdigit() and 0 < int(days) <= 365 else NEW_DAYS
        since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        rel = self.svc.releases if self.config.get("releases_enabled") else None
        items = [{k: r[k] for k in ("dz_artist_id", "dz_album_id", "title", "kind", "release_date", "cover_url",
                                    "artist_name")} for r in self.db.release_feed(user_id, since)] if rel else []
        return {"enabled": rel is not None, "days": days, "releases": items,
                "status": rel.status() if rel else None,
                "following": [p["dz_artist_id"] for p in self.db.get_artist_prefs(user_id) if p["follow"]]}

    def releases_refresh(self) -> dict:
        if self.svc.releases is None or not self.config.get("releases_enabled"):
            raise OpsError("releases_disabled", "new releases are off: turn them on in Settings (RELEASES_ENABLED)", 409)
        threading.Thread(target=self.svc.releases.run_to_completion, daemon=True, name="releases-refresh").start()
        return {"ok": True, "started": True}

    def artist_pref_set(self, user_id: int, dz_id: str, data: dict) -> dict:
        dz_id = str(dz_id or "").strip()
        if not dz_id.isdigit():
            raise OpsError("invalid_artist", "artist id must be a Deezer artist id")
        changes = {k: data[k] for k in ("follow", "hide_releases", "hide_suggestions") if k in data}
        if not changes or any(not isinstance(v, bool) for v in changes.values()):
            raise OpsError("invalid_pref", "send follow, hide_releases and/or hide_suggestions as true or false")
        name = str(data.get("name") or "").strip()[:200]
        if not name:
            known = next((p["name"] for p in self.db.get_artist_prefs(user_id) if p["dz_artist_id"] == dz_id), "")
            name = known or self._artist_name(dz_id)
        pref = self.db.set_artist_pref(user_id, dz_id, name, changes)
        if changes.get("follow") and self.svc.releases:
            self.svc.releases.request()
        return {"ok": True, "pref": pref}

    def _artist_name(self, dz_id: str) -> str:
        with self.db._connect() as conn:
            cached = conn.execute("SELECT artist_name FROM release_cache WHERE dz_artist_id=? LIMIT 1", (dz_id,)).fetchone()
        if cached:
            return cached[0]
        return self._catalog(deezer_catalog.artist_page, dz_id)["artist"]["name"]

    def artist_prefs(self, user_id: int) -> dict:
        return {"prefs": self.db.get_artist_prefs(user_id)}

    def release_dismiss(self, user_id: int, album_id: str, on: bool = True) -> dict:
        if not str(album_id).isdigit():
            raise OpsError("invalid_release", "release id must be a Deezer album id")
        self.db.dismiss_item(user_id, "release", str(album_id), on)
        return {"ok": True}

    def releases_hidden(self, user_id: int) -> dict:
        prefs = [p for p in self.db.get_artist_prefs(user_id) if p["hide_releases"]]
        return {"artists": prefs, "releases": self.db.dismissed_releases(user_id)}

    def suggestions(self, user_id: int) -> dict:
        """Gaps in artists you own and artists you may like, each behind its own switch. Reads the cache
        only: nothing here asks Deezer."""
        on = bool(self.config.get("releases_enabled"))
        gaps_on, artists_on = on and bool(self.config.get("suggest_gaps", True)), on and bool(self.config.get("suggest_artists", True))
        return {"enabled": on, "gaps_enabled": gaps_on, "artists_enabled": artists_on,
                "gaps": suggest.gaps(self.db, user_id) if gaps_on else [],
                "artists": suggest.artists(self.db, user_id) if artists_on else [],
                "status": self.svc.releases.status() if on and self.svc.releases else None}

    def suggest_dismiss(self, user_id: int, kind: str, ref: str, on: bool = True) -> dict:
        if kind not in ("album", "artist") or not str(ref).isdigit():
            raise OpsError("invalid_suggestion", "kind must be album or artist, with a Deezer id")
        self.db.dismiss_item(user_id, kind, str(ref), on)
        return {"ok": True}

    def suggest_hidden(self, user_id: int) -> dict:
        d = self.db.dismissed_suggestions(user_id)
        d["artists"] = [p for p in self.db.get_artist_prefs(user_id) if p["hide_suggestions"]] + d["artists"]
        return d

    # ── activity (the always-visible tray) ────────────────────────────────────
    def activity(self) -> dict:
        active = self.db.get_active_grabs()
        recent = [i for i in self.db.get_grab_history(limit=12)]
        return {"active": [i for i in active if i["status"] != "awaiting_user"],
                "inbox": sum(1 for i in active if i["status"] == "awaiting_user"),
                "recent": [{k: i.get(k) for k in ("id", "status", "title", "artist", "updated_at", "error")}
                           for i in recent],
                "counts": self.db.get_queue_counts()}
