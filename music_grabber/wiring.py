"""Build the configured library source, analysis mode and hooks from the config dict."""

from .analysis import ApiAnalysis, BpmTaggerClient, IntakeFolderAnalysis, NoAnalysis
from .library import FilesystemSource
from .library_opensubsonic import OpenSubsonicSource
from .subsonic import has_credentials

LIBRARY_INDEXES = ("filesystem", "opensubsonic")
MIRRORS = ("navidrome",)
ANALYSES = ("none", "intake", "api")


class ConfigError(ValueError):
    """The configuration names something that cannot work; the message says what to set."""


def build_library_source(config: dict):
    kind = (config.get("library_index") or "filesystem").lower()
    if kind == "filesystem":
        return FilesystemSource(config.get("library_dir") or config["music_dir"])
    if kind == "opensubsonic":
        url = config.get("library_url") or ""
        user, pw, key = (config.get("library_user", ""), config.get("library_pass", ""),
                         config.get("library_api_key", ""))
        if not url:
            raise ConfigError("LIBRARY_INDEX=opensubsonic needs LIBRARY_URL (BPM Tagger or Navidrome)")
        if not has_credentials(user, pw, key):
            raise ConfigError("LIBRARY_INDEX=opensubsonic needs LIBRARY_API_KEY, or LIBRARY_USER "
                              "and LIBRARY_PASS")
        return OpenSubsonicSource(url, api_key=key, user=user, password=pw)
    raise ConfigError(f"unknown LIBRARY_INDEX '{kind}' (known: {', '.join(LIBRARY_INDEXES)})")


def build_analysis(config: dict):
    kind = (config.get("analysis") or "none").lower()
    if kind == "none":
        return NoAnalysis()
    if kind not in ANALYSES:
        raise ConfigError(f"unknown ANALYSIS '{kind}' (known: {', '.join(ANALYSES)})")
    intake = config.get("intake_dir") or ""
    if not intake:
        raise ConfigError(f"ANALYSIS={kind} needs INTAKE_DIR: a directory BPM Tagger's watcher "
                          "covers (typically a subfolder of its MUSIC_DIR)")
    wait = float(config.get("analysis_wait_seconds", 0) or 0)
    if kind == "intake":
        return IntakeFolderAnalysis(intake, wait_seconds=wait)
    url, pw = config.get("bpm_tagger_url") or "", config.get("bpm_tagger_password") or ""
    if not (url and pw):
        raise ConfigError("ANALYSIS=api needs BPM_TAGGER_URL and BPM_TAGGER_PASSWORD (the admin "
                          "password; it does not work with admin two-factor, use ANALYSIS=intake)")
    client = BpmTaggerClient(url, pw, username=config.get("bpm_tagger_username", ""))
    return ApiAnalysis(intake, client, wait_seconds=wait,
                       debounce=float(config.get("hook_debounce_seconds", 45)),
                       max_wait=float(config.get("hook_max_wait_seconds", 600)),
                       min_interval=float(config.get("hook_min_interval_seconds", 60)))


def build_mirror(config: dict, db):
    """The Navidrome playlist mirror, or None when PLAYLIST_MIRROR is empty."""
    kind = (config.get("playlist_mirror") or "").lower()
    if not kind:
        return None
    if kind not in MIRRORS:
        raise ConfigError(f"unknown PLAYLIST_MIRROR '{kind}' (known: {', '.join(MIRRORS)}; empty = off)")
    if not (config.get("navidrome_url")
            and has_credentials(config.get("navidrome_user", ""), config.get("navidrome_pass", ""))):
        raise ConfigError("PLAYLIST_MIRROR=navidrome needs NAVIDROME_URL, NAVIDROME_USER and NAVIDROME_PASS")
    from .mirror import PlaylistMirror
    return PlaylistMirror(db, config["navidrome_url"], config["navidrome_user"], config["navidrome_pass"],
                          prefix=config.get("mirror_prefix", "Spotify: "))


def validate_api(config: dict) -> None:
    """The API token, when set, must be long enough to resist guessing."""
    token = config.get("api_token") or ""
    if token and len(token) < 16:
        raise ConfigError("API_TOKEN must be at least 16 characters "
                          "(python -c \"import secrets; print(secrets.token_urlsafe(32))\")")
