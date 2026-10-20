"""The operations layer: everything the API and the CLI can do, as plain methods.

``Ops`` works on a ``GrabberService`` and returns JSON-able dicts/lists. The Flask API
(api/) and the CLI (cli.py) are thin shells over it, so both behave identically. Failures
are ``OpsError`` with a stable ``code`` (the API maps it to an HTTP status).
"""

import logging
import secrets
import threading
import time
from typing import Optional

from .config import SECRET_KEYS, __version__, masked, save_settings, settings_file_path
from .db import GRAB_TERMINAL
from .notify import Notifier
from .path_template import render
from .spotify import SpotifyAuthError, SpotifyError, parse_playlist_id
from .transcode import PROFILES

log = logging.getLogger(__name__)

PROVIDER_NAMES = ("deezer", "ytdlp")
DEEZER_QUALITIES = ("MP3_128", "MP3_320", "FLAC")
MASK = "********"
OAUTH_STATE_TTL = 600


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
    "spotify_sync_minutes":  ("int", (1, 24 * 60)),
    "grab_workers":          ("int", (1, 3)),
    "ntfy_url":              ("str", None),
    "ntfy_topic":            ("secret", None),
    "ntfy_notify_done":      ("bool", None),
    "ui_public_url":         ("str", None),
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
                        "tracks": db.library_count()},
            "analysis": getattr(self.svc, "analysis", pipe.analysis).name,
            "hooks": getattr(getattr(self.svc, "hooks", None), "names", []),
            "providers": [p.name for p in pipe.providers],
            "queue": db.get_queue_counts(),
            "grabbed_total": db.get_grabbed_total(),
            "managed_files": db.get_grabbed_count(),
            "last_synced_epoch": sync.last_synced_epoch or None,
            "playlists": {
                "total": len(playlists),
                "watched": sum(1 for p in playlists if p.get("enabled")),
                "have": sum(p.get("have_count", 0) for p in playlists),
                "missing": sum(p.get("missing_count", 0) for p in playlists),
                "queued": sum(p.get("queued_count", 0) for p in playlists),
            },
            "dry_run": bool(self.config.get("grab_dry_run")),
        }

    # ── playlists ─────────────────────────────────────────────────────────────
    def playlists_list(self) -> dict:
        return {"playlists": self.db.list_playlists()}

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
