"""The headless JSON API: a thin Flask shell over ``Ops``.

Bearer-token auth (``API_TOKEN``, required, at least 16 characters); no cookies, so no CSRF
surface. The one browser-facing route is the Spotify OAuth ``/callback``, which is protected
by a single-use ``state`` instead of the token. Only this package imports Flask, so the
engine and the CLI stay light.
"""

import hmac
import logging
from html import escape

from flask import Flask, Response, jsonify, request

from ..config import __version__
from ..ops import Ops, OpsError

log = logging.getLogger(__name__)

MIN_TOKEN_LENGTH = 16

_PAGE = ("<!doctype html><meta charset=utf-8><title>Music Grabber</title>"
         "<body style='font:16px system-ui;margin:3rem auto;max-width:32rem'>"
         "<h1>{title}</h1><p>{body}</p>")


def _page(title: str, body: str, status: int = 200) -> Response:
    return Response(_PAGE.format(title=escape(title), body=escape(body)), status=status,
                    mimetype="text/html")


def create_app(ops: Ops, token: str) -> Flask:
    if len(token or "") < MIN_TOKEN_LENGTH:
        raise ValueError(f"API token must be at least {MIN_TOKEN_LENGTH} characters")
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    open_paths = {"/healthz", "/callback"}

    @app.before_request
    def _auth():
        if request.path in open_paths:
            return None
        header = request.headers.get("Authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else ""
        if not supplied or not hmac.compare_digest(supplied.encode(), token.encode()):
            return (jsonify(error="unauthorized", message="send 'Authorization: Bearer <API_TOKEN>'"),
                    401, {"WWW-Authenticate": "Bearer"})
        return None

    @app.after_request
    def _headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.errorhandler(OpsError)
    def _ops_error(e: OpsError):
        return jsonify(e.to_dict()), e.http

    @app.errorhandler(404)
    def _404(_e):
        return jsonify(error="not_found", message="no such route"), 404

    @app.errorhandler(405)
    def _405(_e):
        return jsonify(error="method_not_allowed"), 405

    @app.errorhandler(413)
    def _413(_e):
        return jsonify(error="too_large"), 413

    def body() -> dict:
        data = request.get_json(force=True, silent=True)
        return data if isinstance(data, dict) else {}

    # ── open routes ───────────────────────────────────────────────────────────
    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, version=__version__)

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

    # ── status / library / settings ───────────────────────────────────────────
    @app.get("/api/status")
    def status():
        return jsonify(ops.status())

    @app.post("/api/library/refresh")
    def library_refresh():
        return jsonify(ops.library_refresh())

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

    return app


def serve(config: dict, ops: Ops) -> None:  # pragma: no cover - thin server wrapper
    from waitress import serve as waitress_serve
    app = create_app(ops, config["api_token"])
    log.info("API listening on %s:%s", config["api_host"], config["api_port"])
    waitress_serve(app, host=config["api_host"], port=int(config["api_port"]), threads=4,
                   ident="music-grabber")
