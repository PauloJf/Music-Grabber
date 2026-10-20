"""Entry point: ``python -m music_grabber [run|spotify-login|version]``.

``run`` (the default) starts the Spotify sync thread and the download worker pool and
blocks until SIGINT/SIGTERM. ``spotify-login`` is the headless one-time OAuth connect:
open the printed URL, approve, then paste back the URL Spotify redirected to (the page
itself may fail to load; only its ``code`` matters). The fuller CLI/API arrives in a
later release (docs/plans/extraction.md §4).
"""

import argparse
import logging
import secrets
import signal
import sys
import threading
from urllib.parse import parse_qs, urlparse

from .config import __version__, build_config, load_settings_override
from .db import Database
from .notify import Notifier
from .hooks import build_hooks
from .library import LibraryIndex
from .sync_engine import GrabberService
from .wiring import ConfigError, build_analysis, build_library_source

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
    service = GrabberService(config, db, notifier=Notifier.from_config(config), index=index,
                             analysis=analysis, on_placed=[hooks.on_placed])
    service.hooks = hooks
    service.analysis = analysis
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
    service.start_background()
    stop.wait()
    log.info("Shutting down")
    service.stop_background()
    service.pool.join(10)
    service.hooks.close()
    if hasattr(service.analysis, "close"):
        service.analysis.close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="music_grabber")
    parser.add_argument("command", nargs="?", default="run",
                        choices=("run", "spotify-login", "version"))
    args = parser.parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    config = load_settings_override(build_config())
    _setup_logging(config["log_level"])
    return {"run": cmd_run, "spotify-login": cmd_spotify_login}[args.command](config)


if __name__ == "__main__":
    sys.exit(main())
