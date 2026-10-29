"""The JSON API (a thin Flask shell over ``Ops``), and the small web UI's static files.

Bearer auth on every ``/api/*`` route, with two kinds of credential: the ``API_TOKEN``
(required, at least 16 characters; the machine/bootstrap key, always an admin) and a session
token issued by ``POST /api/auth/login`` for a user account (admin or user). No cookies, so no
CSRF surface. The browser-facing routes are the Spotify OAuth
``/callback`` (protected by a single-use ``state``) and, when a built UI is present, its
static files (public: they contain no data, and the UI asks for the token itself). Only this
package imports Flask, so the engine and the CLI stay light.
"""

import hmac
import logging
import os
import threading
import time
from html import escape

from flask import Flask, Response, g, jsonify, redirect, request, send_file, send_from_directory

from .. import library_covers
from ..ops import Ops, OpsError
from ..preview_clips import signed_query, valid_signature

log = logging.getLogger(__name__)

MIN_TOKEN_LENGTH = 16
ADMIN_PREFIXES = ("/api/settings", "/api/users", "/api/spotify", "/api/keys")      # administrators only
# Writes that reach outside this app (Navidrome) or rebuild its ledger: administrators only too.
ADMIN_WRITE_PREFIXES = ("/api/mirror", "/api/ledger", "/api/releases/refresh")
# After this many wrong tokens inside the window, that address is refused (429) until the
# window passes, whatever it sends. Per address, in memory.
# Keys have no account, so what they read is the unfiltered view: nobody's hides or dismissals apply.
INTEGRATION_USER = -1
AUTH_MAX_FAILURES = 10
AUTH_WINDOW_SECONDS = 60


class AuthThrottle:
    def __init__(self, max_failures: int = AUTH_MAX_FAILURES, window: float = AUTH_WINDOW_SECONDS,
                 clock=time.monotonic):
        self.max, self.window, self.clock = max_failures, window, clock
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, who: str) -> list[float]:
        cutoff = self.clock() - self.window
        recent = [t for t in self._fails.get(who, []) if t > cutoff]
        if recent: self._fails[who] = recent
        else: self._fails.pop(who, None)
        return recent

    def retry_after(self, who: str) -> int:
        """Seconds until ``who`` may try again; 0 when not locked out."""
        with self._lock:
            recent = self._recent(who)
            if len(recent) < self.max:
                return 0
            return max(1, int(recent[0] + self.window - self.clock()) + 1)

    def fail(self, who: str) -> None:
        with self._lock:
            self._recent(who)
            if len(self._fails) > 4096:          # bound memory under a spray from many addresses
                self._fails.clear()
            self._fails.setdefault(who, []).append(self.clock())

_PAGE = ("<!doctype html><meta charset=utf-8><title>Music Grabber</title>"
         "<body style='font:16px system-ui;margin:3rem auto;max-width:32rem'>"
         "<h1>{title}</h1><p>{body}</p>")


def _page(title: str, body: str, status: int = 200) -> Response:
    return Response(_PAGE.format(title=escape(title), body=escape(body)), status=status,
                    mimetype="text/html")


# The UI is same-origin only: no third-party scripts, fonts or connections. Inline styles are
# allowed because React sets style attributes.
# Cover art and 30-second previews come straight from Deezer's CDN (and Spotify's, for playlist
# art); everything else is same-origin.
CSP = ("default-src 'self'; img-src 'self' data: https://*.dzcdn.net https://i.scdn.co; "
       "media-src 'self' https://*.dzcdn.net; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


UI_ROOT_FILES = {"manifest.webmanifest": "application/manifest+json", "favicon.ico": "image/x-icon",
                 "favicon.svg": "image/svg+xml", "apple-touch-icon.png": "image/png",
                 "icon-192.png": "image/png", "icon-512.png": "image/png", "icon-maskable-512.png": "image/png"}


def find_ui_dir(configured: str = "") -> str:
    """The built UI (index.html present), or "" when there is none."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for cand in (configured, os.path.join(here, "frontend", "dist")):
        if cand and os.path.isfile(os.path.join(cand, "index.html")):
            return os.path.abspath(cand)
    return ""


def create_app(ops: Ops, token: str, ui_dir: str = "", throttle: AuthThrottle | None = None) -> Flask:
    if len(token or "") < MIN_TOKEN_LENGTH:
        raise ValueError(f"API token must be at least {MIN_TOKEN_LENGTH} characters")
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    clip_secret = hmac.new(token.encode(), b"music-grabber-preview-clips", "sha256").digest()
    cover_secret = hmac.new(token.encode(), b"music-grabber-library-covers", "sha256").digest()
    open_paths = {"/healthz", "/callback", "/api/auth/state", "/api/auth/login", "/api/auth/setup"}
    throttle = throttle or AuthThrottle()

    def too_many(who: str):
        wait = throttle.retry_after(who)
        if not wait:
            return None
        return (jsonify(error="too_many_attempts", message=f"too many wrong attempts; retry in {wait}s"),
                429, {"Retry-After": str(wait)})

    @app.before_request
    def _auth():
        g.principal, g.bearer = None, ""
        if request.path in open_paths or not request.path.startswith("/api/") or request.path.startswith(("/api/clips/", "/api/library/cover/")):
            return None                  # open routes, the UI's static files, and signed preview clips
        who = request.remote_addr or "?"
        locked = too_many(who)
        if locked:
            return locked
        header = request.headers.get("Authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else ""
        principal = None
        if supplied and hmac.compare_digest(supplied.encode(), token.encode()):
            principal = {"id": None, "username": "api-token", "role": "admin", "prefs": {}}   # the machine/bootstrap key
        elif supplied:
            principal = ops.session_user(supplied) or ops.api_key_principal(supplied)
        if not principal:
            throttle.fail(who)
            return (jsonify(error="unauthorized", message="sign in, or send 'Authorization: Bearer <token>'"),
                    401, {"WWW-Authenticate": "Bearer"})
        g.principal, g.bearer = principal, supplied
        if principal["role"] == "key" and not request.path.startswith("/api/v1/"):
            return jsonify(error="forbidden", message="an API key can only use /api/v1"), 403
        admin_only = request.path.startswith(ADMIN_PREFIXES) or (
            request.method != "GET" and (request.path.startswith(ADMIN_WRITE_PREFIXES)
                                         or request.path.endswith("/mirror")))
        if admin_only and principal["role"] != "admin":
            return jsonify(error="forbidden", message="administrators only"), 403
        return None

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if resp.mimetype == "text/html":
            resp.headers["Content-Security-Policy"] = CSP
            resp.headers["X-Frame-Options"] = "DENY"
        return resp

    @app.errorhandler(OpsError)
    def _ops_error(e: OpsError):
        return jsonify(e.to_dict()), e.http

    @app.errorhandler(404)
    def _404(_e):
        if (ui_dir and request.method == "GET"
                and not request.path.startswith(("/api/", "/assets/"))):     # a missing asset is a real 404
            return send_from_directory(ui_dir, "index.html")      # client-side route
        return jsonify(error="not_found", message="no such route"), 404

    @app.errorhandler(405)
    def _405(_e):
        return jsonify(error="method_not_allowed"), 405

    @app.errorhandler(413)
    def _413(_e):
        return jsonify(error="too_large"), 413

    @app.errorhandler(Exception)
    def _500(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return jsonify(error=e.name.lower().replace(" ", "_")), e.code
        log.exception("unhandled error on %s %s", request.method, request.path)
        return jsonify(error="internal_error", message="see the server log"), 500

    def need(scope: str) -> None:
        """An API key must hold ``scope``; accounts and the API token can do everything under /api/v1."""
        if g.principal["role"] == "key" and scope not in g.principal["scopes"]:
            raise OpsError("forbidden", f"this key does not have the '{scope}' scope", 403)

    def body() -> dict:
        data = request.get_json(force=True, silent=True)
        return data if isinstance(data, dict) else {}

    # ── open routes ───────────────────────────────────────────────────────────
    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True)          # open: no version or other fingerprint (/api/status has it)

    @app.get("/callback")
    def callback():
        """Spotify redirects the browser here. Never echoes request input."""
        if request.args.get("error"):
            return _page("Not connected", "Spotify did not grant access. Close this tab and try again.", 400)
        try:
            ops.spotify_callback(request.args.get("code", ""), request.args.get("state", ""))
        except OpsError as e:
            log.warning("Spotify callback rejected: %s", e.code)
            return _page("Not connected", "That login link is invalid or expired. Start again.", 400)
        return _page("Connected to Spotify", "You can close this tab.")

    # ── accounts ──────────────────────────────────────────────────────────────
    @app.get("/api/auth/state")
    def auth_state():
        return jsonify(ops.auth_state())

    @app.post("/api/auth/login")
    def auth_login():
        who = request.remote_addr or "?"
        locked = too_many(who)
        if locked:
            return locked
        b = body()
        try:
            return jsonify(ops.login(b.get("username", ""), b.get("password", "")))
        except OpsError as e:
            if e.code == "bad_credentials":
                throttle.fail(who)
            raise

    @app.post("/api/auth/setup")
    def auth_setup():
        """First-run only: creates the first admin. Needs the API token, so reaching the port is not enough."""
        who = request.remote_addr or "?"
        locked = too_many(who)
        if locked:
            return locked
        b = body()
        supplied = b.get("api_token", "")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), token.encode()):
            throttle.fail(who)
            return jsonify(error="unauthorized", message="the API token is wrong"), 401
        ops.create_first_admin(b.get("username", ""), b.get("password", ""))
        return jsonify(ops.login(b.get("username", ""), b.get("password", ""))), 201

    @app.post("/api/auth/logout")
    def auth_logout():
        return jsonify(ops.logout(g.bearer)) if g.principal["id"] else jsonify(ok=True)

    @app.get("/api/me")
    def me():
        p = g.principal
        return jsonify(user={k: p.get(k) for k in ("id", "username", "role", "prefs", "created_at", "last_login")},
                       via="token" if p["id"] is None else "session")

    def need_account():
        if g.principal["id"] is None:
            raise OpsError("no_account", "you are using the API token, which has no personal profile; sign in with an account", 409)
        return g.principal["id"]

    @app.post("/api/me/password")
    def me_password():
        b = body()
        return jsonify(ops.change_password(need_account(), b.get("current", ""), b.get("new", ""), g.bearer))

    @app.patch("/api/me/prefs")
    def me_prefs():
        return jsonify(ops.prefs_update(need_account(), body()))

    @app.get("/api/users")
    def users_list():
        return jsonify(ops.users_list())

    @app.post("/api/users")
    def users_create():
        b = body()
        return jsonify(ops.users_create(b.get("username", ""), b.get("password", ""), b.get("role", "user"))), 201

    @app.patch("/api/users/<int:user_id>")
    def users_update(user_id: int):
        return jsonify(ops.users_update(user_id, body()))

    @app.delete("/api/users/<int:user_id>")
    def users_delete(user_id: int):
        if user_id == g.principal["id"]:
            raise OpsError("invalid_account", "you cannot delete your own account while signed in as it")
        return jsonify(ops.users_delete(user_id))

    # ── API keys (administrators) ─────────────────────────────────────────────
    @app.get("/api/keys")
    def keys_list():
        return jsonify(ops.keys_list())

    @app.post("/api/keys")
    def keys_create():
        b = body()
        return jsonify(ops.keys_create(b.get("name", ""), b.get("scopes"), g.principal["username"])), 201

    @app.delete("/api/keys/<int:key_id>")
    def keys_revoke(key_id: int):
        return jsonify(ops.keys_revoke(key_id))

    # ── the integration API (/api/v1): for other apps, with an API key ────────
    @app.get("/api/v1/search")
    def v1_search():
        need("browse")
        return jsonify(ops.browse_search(request.args.get("q", ""), request.args.get("type", "track")))

    @app.get("/api/v1/artist/<dz_id>")
    def v1_artist(dz_id):
        need("browse")
        return jsonify(ops.browse_artist(dz_id))

    @app.get("/api/v1/album/<dz_id>")
    def v1_album(dz_id):
        need("browse")
        return jsonify(ops.browse_album(dz_id))

    @app.post("/api/v1/requests")
    def v1_request():
        need("request")
        out = ops.request_track(body(), g.principal["username"])
        return jsonify(out), 201 if out["result"] == "queued" else 200

    @app.get("/api/v1/requests/<int:item_id>")
    def v1_request_status(item_id: int):
        need("request")
        own = g.principal["username"] if g.principal["role"] == "key" else None
        return jsonify(ops.request_status(item_id, own))

    @app.get("/api/v1/suggestions")
    def v1_suggestions():
        need("suggestions")
        return jsonify(ops.suggestions(INTEGRATION_USER if g.principal["role"] == "key" else uid()))

    @app.get("/api/v1/releases")
    def v1_releases():
        need("suggestions")
        return jsonify(ops.releases_feed(INTEGRATION_USER if g.principal["role"] == "key" else uid(),
                                         request.args.get("days", "")))

    @app.get("/api/v1/library/has")
    def v1_library_has():
        need("library")
        return jsonify(ops.library_has(request.args))

    # ── new releases, follows and hides (per user) ────────────────────────────
    def uid() -> int:
        return g.principal["id"] or 0                # 0: the shared API token

    @app.get("/api/releases")
    def releases():
        return jsonify(ops.releases_feed(uid(), request.args.get("days", "")))

    @app.post("/api/releases/refresh")
    def releases_refresh():
        return jsonify(ops.releases_refresh())

    @app.get("/api/releases/hidden")
    def releases_hidden():
        return jsonify(ops.releases_hidden(uid()))

    @app.post("/api/releases/<album_id>/dismiss")
    def release_dismiss(album_id):
        return jsonify(ops.release_dismiss(uid(), album_id, True))

    @app.delete("/api/releases/<album_id>/dismiss")
    def release_undismiss(album_id):
        return jsonify(ops.release_dismiss(uid(), album_id, False))

    @app.get("/api/suggest")
    def suggestions():
        return jsonify(ops.suggestions(uid()))

    @app.get("/api/suggest/hidden")
    def suggest_hidden():
        return jsonify(ops.suggest_hidden(uid()))

    @app.post("/api/suggest/<kind>/<ref>/dismiss")
    def suggest_dismiss(kind, ref):
        return jsonify(ops.suggest_dismiss(uid(), kind, ref, True))

    @app.delete("/api/suggest/<kind>/<ref>/dismiss")
    def suggest_undismiss(kind, ref):
        return jsonify(ops.suggest_dismiss(uid(), kind, ref, False))

    @app.get("/api/artist-prefs")
    def artist_prefs():
        return jsonify(ops.artist_prefs(uid()))

    @app.put("/api/artist-prefs/<dz_id>")
    def artist_pref_set(dz_id):
        return jsonify(ops.artist_pref_set(uid(), dz_id, body()))

    # ── status / library / settings ───────────────────────────────────────────
    @app.get("/api/status")
    def status():
        return jsonify(ops.status())

    @app.get("/api/library")
    def library_list():
        return jsonify(ops.library_list(request.args.get("q", ""), request.args.get("limit", 100),
                                        request.args.get("offset", 0)))

    def local_cover(a: dict) -> dict:
        """Point an album at its own embedded cover (a signed, short-lived link; falls back to Deezer's)."""
        paths = a.pop("cover_paths", None)
        if paths:
            name = library_covers.encode(paths, a.get("cover_url") or "")
            a["cover_url"] = f"/api/library/cover/{name}?{library_covers.query(cover_secret, name)}"
        return a

    @app.get("/api/library/artists")
    def library_artists():
        return jsonify(ops.library_artists(request.args.get("q", "")))

    @app.get("/api/library/artist")
    def library_artist():
        page = ops.library_artist(request.args.get("name", ""))
        for a in page["albums"] + page["appears_on"]:
            local_cover(a)
        return jsonify(page)

    @app.get("/api/library/albums")
    def library_albums():
        out = ops.library_albums(request.args.get("q", ""))
        for a in out["albums"]:
            local_cover(a)
        return jsonify(out)

    @app.get("/api/library/album")
    def library_album():
        return jsonify(local_cover(ops.library_album(request.args.get("artist", ""), request.args.get("album", ""))))

    @app.get("/api/library/cover/<name>")
    def library_cover(name):
        """Open like the preview clips (an <img> cannot send our login), but only with a link we signed."""
        if not library_covers.valid(cover_secret, name, request.args.get("exp", ""), request.args.get("sig", "")):
            return jsonify(error="forbidden", message="bad or expired link"), 403
        decoded = library_covers.decode(name)
        if not decoded:
            return jsonify(error="not_found", message="no such cover"), 404
        got = ops.library_cover(*decoded)
        if "file" in got:
            resp = send_file(got["file"], mimetype=got["mime"])
            resp.headers["Cache-Control"] = "private, max-age=86400"
            return resp
        resp = redirect(got["redirect"], 302)
        resp.headers["Cache-Control"] = "private, max-age=3600"
        return resp

    @app.get("/api/library/related")
    def library_related():
        return jsonify(ops.library_related(request.args.get("path", "")))

    @app.post("/api/library/refresh")
    def library_refresh():
        return jsonify(ops.library_refresh())

    @app.get("/api/mirror")
    def mirror_status():
        return jsonify(ops.mirror_status())

    @app.post("/api/mirror/run")
    def mirror_run():
        return jsonify(ops.mirror_run())

    @app.post("/api/playlists/<int:pid>/mirror")
    def playlist_mirror(pid):
        body = request.get_json(silent=True) or {}
        return jsonify(ops.playlist_mirror(pid, str(body.get("action") or "now")))

    @app.post("/api/ledger/rebuild")
    def ledger_rebuild():
        return jsonify(ops.ledger_rebuild())

    @app.get("/api/settings")
    def settings_get():
        return jsonify(ops.settings_get())

    @app.post("/api/settings")
    def settings_update():
        return jsonify(ops.settings_update(body()))

    @app.post("/api/settings/test-deezer")
    def test_deezer():
        return jsonify(ops.test_deezer(body().get("deezer_arl")))

    @app.post("/api/settings/test-ntfy")
    def test_ntfy():
        return jsonify(ops.test_ntfy())

    # ── Spotify ───────────────────────────────────────────────────────────────
    @app.get("/api/spotify/status")
    def spotify_status():
        return jsonify(ops.spotify_status())

    @app.get("/api/spotify/authorize-url")
    def spotify_authorize_url():
        return jsonify(ops.spotify_authorize_url())

    @app.post("/api/spotify/disconnect")
    def spotify_disconnect():
        return jsonify(ops.spotify_disconnect())

    @app.get("/api/spotify/playlists")
    def spotify_playlists():
        return jsonify(ops.spotify_playlists())

    @app.get("/api/spotify/search")
    def spotify_search():
        return jsonify(ops.spotify_search(request.args.get("q", "")))

    # ── playlists ─────────────────────────────────────────────────────────────
    @app.get("/api/playlists")
    def playlists_list():
        return jsonify(ops.playlists_list())

    @app.post("/api/playlists")
    def playlist_add():
        return jsonify(ops.playlist_add(body().get("value", "")))

    @app.post("/api/sync")
    def sync_all():
        return jsonify(ops.sync())

    @app.get("/api/playlists/<int:pid>")
    def playlist_get(pid):
        return jsonify(ops.playlist_get(pid, request.args.get("status", "")))

    @app.delete("/api/playlists/<int:pid>")
    def playlist_remove(pid):
        return jsonify(ops.playlist_remove(pid))

    @app.patch("/api/playlists/<int:pid>")
    def playlist_patch(pid):
        data = body()
        if "enabled" not in data:
            raise OpsError("invalid_request", "only 'enabled' can be changed")
        return jsonify(ops.playlist_enable(pid, bool(data["enabled"])))

    @app.post("/api/playlists/<int:pid>/sync")
    def playlist_sync(pid):
        return jsonify(ops.sync(pid))

    @app.post("/api/playlists/<int:pid>/enqueue-missing")
    def playlist_enqueue_missing(pid):
        return jsonify(ops.enqueue_missing(pid))

    # ── browse Deezer + activity ──────────────────────────────────────────────
    @app.get("/api/browse/search")
    def browse_search():
        return jsonify(ops.browse_search(request.args.get("q", ""), request.args.get("type", "track")))

    @app.get("/api/browse/artist/<dz_id>")
    def browse_artist(dz_id):
        return jsonify(ops.browse_artist(dz_id))

    @app.get("/api/browse/album/<dz_id>")
    def browse_album(dz_id):
        return jsonify(ops.browse_album(dz_id))

    @app.post("/api/browse/queue")
    def browse_queue():
        data = body()
        if data.get("album_id"):
            return jsonify(ops.browse_queue_album(str(data["album_id"]), bool(data.get("force"))))
        return jsonify(ops.browse_queue(data.get("tracks"), bool(data.get("force"))))

    @app.post("/api/browse/undo")
    def browse_undo():
        return jsonify(ops.undo_queue(body().get("item_ids")))

    @app.get("/api/activity")
    def activity():
        return jsonify(ops.activity())

    # ── queue ─────────────────────────────────────────────────────────────────
    @app.get("/api/queue")
    def queue_list():
        return jsonify(ops.queue_list(request.args.get("status", "")))

    @app.post("/api/queue")
    def queue_add():
        return jsonify(ops.queue_add(body()))

    @app.get("/api/queue/history")
    def queue_history():
        return jsonify(ops.queue_history())

    @app.post("/api/queue/retry-failed")
    def queue_retry_failed():
        return jsonify(ops.queue_retry_failed())

    @app.post("/api/queue/clear-completed")
    def queue_clear_completed():
        return jsonify(ops.queue_clear_completed())

    @app.get("/api/queue/<int:item_id>")
    def queue_item(item_id):
        return jsonify(ops.queue_item(item_id))

    @app.delete("/api/queue/<int:item_id>")
    def queue_delete(item_id):
        return jsonify(ops.queue_delete(item_id))

    @app.post("/api/queue/<int:item_id>/retry")
    def queue_retry(item_id):
        return jsonify(ops.queue_retry(item_id))

    @app.post("/api/queue/<int:item_id>/cancel")
    def queue_cancel(item_id):
        return jsonify(ops.queue_cancel(item_id))

    @app.post("/api/queue/<int:item_id>/priority")
    def queue_priority(item_id):
        return jsonify(ops.queue_priority(item_id, body().get("priority", 0)))

    # ── inbox ─────────────────────────────────────────────────────────────────
    @app.get("/api/inbox")
    def inbox_list():
        return jsonify(ops.inbox_list())

    @app.get("/api/inbox/<int:item_id>/preview")
    def inbox_preview(item_id):
        d = ops.inbox_preview(item_id, request.args.get("candidate"))
        if d.get("clip"):                # a YouTube clip made on the server: a short-lived signed URL
            d["url"] = f"/api/clips/{d['clip']}?{signed_query(clip_secret, d['clip'])}"
        return jsonify(d)

    @app.get("/api/clips/<name>")
    def preview_clip(name):
        # Signed (the <audio> element cannot send a bearer token), so it is open to the auth hook.
        if not valid_signature(clip_secret, name, request.args.get("exp", ""), request.args.get("sig", "")):
            return jsonify(error="not_found", message="no such clip"), 404
        path = ops.preview_clips().path(name)
        if not path:
            return jsonify(error="not_found", message="no such clip"), 404
        resp = send_from_directory(os.path.dirname(path), name, mimetype="audio/mpeg", conditional=True)
        resp.headers["Cache-Control"] = "private, max-age=600"
        return resp

    @app.post("/api/inbox/research-all")
    def inbox_research_all():
        return jsonify(ops.inbox_research_all())

    @app.post("/api/inbox/<int:item_id>/choose")
    def inbox_choose(item_id):
        return jsonify(ops.inbox_choose(item_id, body().get("candidate_id")))

    @app.post("/api/inbox/<int:item_id>/search")
    def inbox_search(item_id):
        return jsonify(ops.inbox_search(item_id, body().get("query", "")))

    @app.post("/api/inbox/<int:item_id>/research")
    def inbox_research(item_id):
        return jsonify(ops.inbox_research(item_id))

    @app.post("/api/inbox/<int:item_id>/skip")
    def inbox_skip(item_id):
        return jsonify(ops.inbox_skip(item_id))

    # ── the UI (only when it has been built) ──────────────────────────────────
    if ui_dir:
        @app.get("/")
        def ui_index():
            return send_from_directory(ui_dir, "index.html")

        def _root_file(name: str):
            # Icons and the manifest live at the root (iOS and browsers ask for them there); a
            # fixed list, one route each, so nothing else is ever a file read.
            def view():
                resp = send_from_directory(ui_dir, name, mimetype=UI_ROOT_FILES[name])
                resp.headers["Cache-Control"] = "public, max-age=86400"
                return resp
            return view

        for _name in UI_ROOT_FILES:
            app.add_url_rule("/" + _name, endpoint="ui_root_" + _name, view_func=_root_file(_name), methods=["GET"])

        @app.get("/assets/<path:name>")
        def ui_asset(name):
            resp = send_from_directory(os.path.join(ui_dir, "assets"), name)
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"   # hashed file names
            return resp

    return app


def serve(config: dict, ops: Ops) -> None:  # pragma: no cover - thin server wrapper
    from waitress import serve as waitress_serve
    ui_dir = find_ui_dir(config.get("ui_dir", ""))
    app = create_app(ops, config["api_token"], ui_dir)
    log.info("API listening on %s:%s (web UI %s)", config["api_host"], config["api_port"],
             "served" if ui_dir else "not built")
    waitress_serve(app, host=config["api_host"], port=int(config["api_port"]), threads=4,
                   ident="music-grabber")
