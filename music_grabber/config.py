"""Configuration: version discovery, env → config table, settings.json overrides.

Env var names match BPM Tagger's grabber settings where the meaning is unchanged, so
an existing compose block can be copied over (docs/plans/extraction.md §1.4).
"""

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def _read_version() -> str:
    """VERSION lives at the repo root (and next to the package in the image)."""
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "VERSION", here / "VERSION"):
        try:
            return candidate.read_text(encoding="utf-8").strip() or "0.0.0"
        except OSError:
            continue
    return "0.0.0"


__version__ = _read_version()

# Secrets that are only ever read from the environment (never written to settings.json).
ENV_ONLY_KEYS = ("spotify_client_id", "spotify_client_secret", "library_api_key", "library_pass",
                 "bpm_tagger_password", "mediarium_api_key", "navidrome_pass")

# Keys that carry credentials: masked in any API/CLI output.
SECRET_KEYS = ("spotify_client_secret", "deezer_arl", "library_api_key", "library_pass",
               "bpm_tagger_password", "mediarium_api_key", "navidrome_pass")


def _bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _split_list(raw: str) -> list[str]:
    return [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]


def build_config() -> dict:
    data_dir = os.environ.get("DATA_DIR", "/data")
    return {
        # Where grabbed files are filed (BPM Tagger's MUSIC_DIR meaning).
        "music_dir":             os.environ.get("MUSIC_DIR", "/music"),
        # What the filesystem library index scans for "do we already have it?".
        # Defaults to music_dir; point it at a parent when you file into a subfolder.
        "library_dir":           os.environ.get("LIBRARY_DIR") or os.environ.get("MUSIC_DIR", "/music"),
        "db_path":               os.environ.get("DB_PATH", os.path.join(data_dir, "music_grabber.db")),
        "log_level":             os.environ.get("LOG_LEVEL", "INFO").upper(),

        # Library index: what the library already has (docs/plans/extraction.md §2.1).
        #   filesystem   scan LIBRARY_DIR (standalone)
        #   opensubsonic ask BPM Tagger's Subsonic API (SUBSONIC_ENABLED there) or Navidrome
        "library_index":         os.environ.get("LIBRARY_INDEX", "filesystem").strip().lower(),
        "library_url":           os.environ.get("LIBRARY_URL", ""),
        "library_api_key":       os.environ.get("LIBRARY_API_KEY", ""),
        "library_user":          os.environ.get("LIBRARY_USER", ""),
        "library_pass":          os.environ.get("LIBRARY_PASS", ""),

        # Post-download analysis (§2.2):
        #   none    standalone: file under MUSIC_DIR, no BPM
        #   intake  file into INTAKE_DIR; BPM Tagger's watcher tags it
        #   api     intake + a coalesced scan request to BPM Tagger's HTTP API
        "analysis":              os.environ.get("ANALYSIS", "none").strip().lower(),
        "intake_dir":            os.environ.get("INTAKE_DIR", ""),
        "analysis_wait_seconds": float(os.environ.get("ANALYSIS_WAIT_SECONDS", "0")),
        "bpm_tagger_url":        os.environ.get("BPM_TAGGER_URL", ""),
        "bpm_tagger_username":   os.environ.get("BPM_TAGGER_USERNAME", ""),
        "bpm_tagger_password":   os.environ.get("BPM_TAGGER_PASSWORD", ""),

        # Post-import hooks (§2.4): comma list of mediarium, navidrome. Off by default.
        "post_import_hooks":     [h.lower() for h in _split_list(os.environ.get("POST_IMPORT_HOOKS", ""))],
        "mediarium_url":         os.environ.get("MEDIARIUM_URL", ""),
        "mediarium_api_key":     os.environ.get("MEDIARIUM_API_KEY", ""),
        "mediarium_api_key_header": os.environ.get("MEDIARIUM_API_KEY_HEADER", "X-Api-Key"),
        "navidrome_url":         os.environ.get("NAVIDROME_URL", ""),
        "navidrome_user":        os.environ.get("NAVIDROME_USER", ""),
        "navidrome_pass":        os.environ.get("NAVIDROME_PASS", ""),
        "hook_debounce_seconds": float(os.environ.get("HOOK_DEBOUNCE_SECONDS", "45")),
        "hook_max_wait_seconds": float(os.environ.get("HOOK_MAX_WAIT_SECONDS", "600")),
        "hook_min_interval_seconds": float(os.environ.get("HOOK_MIN_INTERVAL_SECONDS", "60")),

        # Spotify (Authorization Code flow; refresh token lives in the DB).
        "spotify_client_id":     os.environ.get("SPOTIFY_CLIENT_ID", ""),
        "spotify_client_secret": os.environ.get("SPOTIFY_CLIENT_SECRET", ""),
        "spotify_redirect_uri":  os.environ.get("SPOTIFY_REDIRECT_URI", ""),
        "spotify_sync_minutes":  int(os.environ.get("SPOTIFY_SYNC_MINUTES", "30")),
        # Playlists to watch: URLs, URIs or ids, comma/newline separated. Upserted at start.
        "watch_playlists":       _split_list(os.environ.get("WATCH_PLAYLISTS", "")),

        # Providers / pipeline.
        "deezer_arl":            os.environ.get("DEEZER_ARL", ""),
        "deezer_quality":        os.environ.get("DEEZER_QUALITY", "MP3_128"),
        "provider_order":        os.environ.get("PROVIDER_ORDER", "deezer,ytdlp"),
        "output_format":         os.environ.get("OUTPUT_FORMAT", "mp3-128"),
        "path_template":         os.environ.get(
            "PATH_TEMPLATE", "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}"),
        "grab_workers":          int(os.environ.get("GRAB_WORKERS", "1")),
        "grab_dry_run":          _bool("GRAB_DRY_RUN"),
        "auto_accept_threshold": float(os.environ.get("AUTO_ACCEPT_THRESHOLD", "0.85")),
        "ask_threshold":         float(os.environ.get("ASK_THRESHOLD", "0.55")),
        "lyrics_enabled":        _bool("LYRICS_ENABLED"),

        # Notifications.
        "ntfy_url":              os.environ.get("NTFY_URL", ""),
        "ntfy_topic":            os.environ.get("NTFY_TOPIC", ""),
        "ui_public_url":         os.environ.get("UI_PUBLIC_URL", ""),
    }


def settings_file_path(db_path: str) -> str:
    return str(Path(db_path).parent / "settings.json")


def load_settings_override(config: dict) -> dict:
    """Merge persisted settings.json overrides into config (overwrites env values)."""
    path = settings_file_path(config["db_path"])
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for key in ENV_ONLY_KEYS:
                data.pop(key, None)
            config.update(data)
        except Exception as exc:
            log.warning("Could not load settings override: %s", exc)
    return config


def save_settings(settings_path: str, updates: dict) -> None:
    """Merge updates into settings.json (creating it if absent). A value of ``None``
    removes the key. Kept at 0600: it can hold the Deezer ARL."""
    existing = {}
    if os.path.isfile(settings_path):
        try:
            with open(settings_path, encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
    for key, val in updates.items():
        if key in ENV_ONLY_KEYS:
            continue
        if val is None:
            existing.pop(key, None)
        else:
            existing[key] = val
    os.makedirs(os.path.dirname(os.path.abspath(settings_path)), exist_ok=True)
    fd = os.open(settings_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
    try:
        os.chmod(settings_path, 0o600)
    except OSError:
        pass


def masked(config: dict) -> dict:
    """A copy of config that is safe to print or return over an API."""
    return {k: ("********" if (k in SECRET_KEYS and v) else v) for k, v in config.items()}
