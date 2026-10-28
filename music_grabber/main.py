"""Entry point: ``python -m music_grabber <command>`` (see cli.py for the full list).

``run`` (the default) starts the Spotify sync thread and the download worker pool, plus the
JSON API when ``API_TOKEN`` is set, and blocks until SIGINT/SIGTERM. ``spotify-login`` is
the headless one-time OAuth connect: open the printed URL, approve, then paste back the URL
Spotify redirected to (the page itself may fail to load; only its ``code`` matters).
Every other command is a management command handled by cli.py.
"""

import logging
import secrets
import signal
import sys
import threading
from urllib.parse import parse_qs, urlparse

from .cli import build_parser, run_command
from .config import __version__, build_config, load_settings_override
from .db import Database
from .notify import Notifier
from .hooks import build_hooks
from .library import LibraryIndex
from .sync_engine import GrabberService
from .wiring import ConfigError, build_analysis, build_library_source, build_mirror, validate_api

log = logging.getLogger("music_grabber")


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level, logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")


def build_service(config: dict) -> GrabberService:
    """Wire the configured library index, analysis mode and hooks. Raises ConfigError
    when the configuration names something that cannot work."""
    db = Database(config["db_path"])
    index = LibraryIndex(db, build_library_source(config),
                         root=config.get("library_dir") or config["music_dir"])
    analysis = build_analysis(config)
    hooks = build_hooks(config)
    mirror = build_mirror(config, db)
    validate_api(config)
    service = GrabberService(config, db, notifier=Notifier.from_config(config), index=index,
                             analysis=analysis,
                             on_placed=[hooks.on_placed] + ([mirror.on_placed] if mirror else []))
    service.hooks = hooks
    if mirror:
        service.mirror = mirror
        service.sync.on_synced = mirror.request
    service.analysis = analysis
    from .releases import ReleaseTracker              # always built: RELEASES_ENABLED is also a Settings switch
    service.releases = ReleaseTracker(config, db)
    if analysis.name in ("intake", "api"):
        from .analysis_watch import AnalysisWatcher
        service.analysis_watcher = AnalysisWatcher(db, config.get("library_dir") or config["music_dir"], index=index)
    return service


def parse_callback(pasted: str, expected_state: str) -> str:
    """The authorization code from a pasted redirect URL (or a bare code). Raises
    ValueError on a state mismatch or an error response."""
    pasted = (pasted or "").strip()
    if "code=" not in pasted and "error=" not in pasted:
        return pasted
    q = parse_qs(urlparse(pasted).query)
    if q.get("error"):
        raise ValueError(f"Spotify returned an error: {q['error'][0]}")
    if q.get("state", [""])[0] != expected_state:
        raise ValueError("state mismatch: paste the URL from the login you just started")
    return (q.get("code") or [""])[0]


def cmd_spotify_login(config: dict) -> int:
    try:
        service = build_service(config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    state = secrets.token_urlsafe(16)
    try:
        url = service.authorize_url(state)
    except Exception as exc:
        print(f"Cannot start login: {exc}", file=sys.stderr)
        return 2
    print("1. Open this URL and approve access:\n\n   " + url + "\n")
    print("2. Spotify redirects to your SPOTIFY_REDIRECT_URI. Copy the full URL from the\n"
          "   address bar (it may show an error page; that is fine) and paste it here.")
    try:
        code = parse_callback(input("\nRedirected URL: "), state)
        if not code:
            raise ValueError("no authorization code found")
        service.client.exchange_code(code)
    except Exception as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1
    print("Connected to Spotify.")
    return 0


def _start_api(config: dict, service: GrabberService):
    """Start the JSON API in a daemon thread when API_TOKEN is set."""
    if not config.get("api_token"):
        log.info("API disabled (set API_TOKEN, at least 16 characters, to enable it)")
        return None
    from .api import serve
    from .ops import Ops
    ops = Ops(service, config)
    t = threading.Thread(target=serve, args=(config, ops), daemon=True, name="api")
    t.start()
    return t


def cmd_run(config: dict) -> int:
    try:
        service = build_service(config)
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2
    added = service.seed_playlists()
    if added:
        log.info("Registered %d playlist(s) from WATCH_PLAYLISTS", added)
    st = service.client.status()
    log.info("Music Grabber %s: spotify configured=%s connected=%s, providers=%s, "
             "library index=%s, analysis=%s, hooks=%s",
             __version__, st["configured"], st["connected"],
             [p.name for p in service.pool.pipeline.providers] or "none",
             service.index.source.name, service.analysis.name, service.hooks.names or "none")
    if not st["connected"]:
        log.warning("Spotify is not connected: run `python -m music_grabber spotify-login`")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    _start_api(config, service)
    service.start_background()
    stop.wait()
    log.info("Shutting down")
    service.stop_background()
    service.pool.join(10)
    service.hooks.close()
    if service.mirror:
        service.mirror.close()
    if hasattr(service.analysis, "close"):
        service.analysis.close()
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "run"
    if command == "version":
        print(__version__)
        return 0
    config = load_settings_override(build_config())
    if command == "run":
        _setup_logging(config["log_level"])
        return cmd_run(config)
    if command == "spotify-login":
        return cmd_spotify_login(config)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return run_command(args, config, build_service=build_service)


if __name__ == "__main__":
    sys.exit(main())
