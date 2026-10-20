"""Post-import hooks: tell downstream servers a file was filed.

Fired from the worker after a file is placed and recorded in the ledger. Each hook is
independent, off unless listed in ``POST_IMPORT_HOOKS``, never raises into the pipeline,
and coalesces (see coalesce.py): one request per burst, not one per track.

* ``mediarium``: ``POST {MEDIARIUM_URL}/api/music/import/scan``. A running scan is
  returned if one is already running, so any 2xx is success. 404 means Mediarium's music
  module is off (pause an hour, no retry loop). 401/403 mean the key is wrong or *not
  allowed to call this route* (an API key is not assumed to reach admin routes): one clear
  WARNING, then the hook disables itself until restart.
* ``navidrome``: Subsonic ``startScan``. Leave it off when ``ANALYSIS`` is ``intake`` or
  ``api``: BPM Tagger triggers that rescan itself *after* it tags the file, and a rescan
  before the BPM tag exists is the one Navidrome's quick scan never revisits.
"""

import logging
import time
from typing import Optional

import requests

from .analysis import PlacedFile
from .coalesce import Coalescer
from .subsonic import SubsonicError, call, has_credentials

log = logging.getLogger(__name__)

MEDIARIUM_ROUTE = "/api/music/import/scan"
BACKOFF_FIRST = 60.0
BACKOFF_CAP = 3600.0
MODULE_OFF_PAUSE = 3600.0


def backoff(failures: int) -> float:
    """60 s, 120 s, 240 s … capped at one hour."""
    return min(BACKOFF_CAP, BACKOFF_FIRST * (2 ** max(0, failures - 1)))


class _Hook:
    name = "hook"

    def __init__(self, *, debounce: float, max_wait: float, min_interval: float,
                 clock=None, timer_factory=None):
        kw = {}
        if clock is not None:
            kw["clock"] = clock
        if timer_factory is not None:
            kw["timer_factory"] = timer_factory
        self._clock = clock or time.monotonic
        self.coalescer = Coalescer(self.fire, debounce=debounce, max_wait=max_wait,
                                   min_interval=min_interval, name=self.name, **kw)

    def on_placed(self, placed: PlacedFile) -> None:
        self.coalescer.notify()

    def close(self) -> None:
        self.coalescer.close()

    def fire(self) -> Optional[float]:   # pragma: no cover - overridden
        raise NotImplementedError


class MediariumHook(_Hook):
    name = "mediarium"

    def __init__(self, url: str, api_key: str, *, key_header: str = "X-Api-Key",
                 session=None, timeout: float = 15, **kw):
        super().__init__(**kw)
        self.url = url.rstrip("/")
        self._api_key, self._key_header = api_key, key_header
        self._http = session or requests
        self._timeout = timeout
        self.disabled = False
        self._failures = 0
        self._paused_until = 0.0

    def _headers(self) -> dict:
        if self._key_header.lower() == "authorization":
            return {"Authorization": f"Bearer {self._api_key}"}
        return {self._key_header: self._api_key}

    def fire(self) -> Optional[float]:
        if self.disabled:
            return None
        if self._clock() < self._paused_until:
            return None                      # module off: nothing to scan right now
        try:
            resp = self._http.post(f"{self.url}{MEDIARIUM_ROUTE}", headers=self._headers(),
                                   timeout=self._timeout)
        except requests.RequestException as exc:
            return self._transient(f"request failed ({type(exc).__name__})")
        code = resp.status_code
        if 200 <= code < 300:
            self._failures = 0
            log.info("Mediarium import scan requested")
            return None
        if code in (401, 403):
            self.disabled = True
            log.warning(
                "Mediarium rejected the API key (HTTP %d) calling POST %s: the key is wrong "
                "or is not allowed to call this route. Mediarium hook disabled until restart.",
                code, MEDIARIUM_ROUTE)
            return None
        if code == 404:
            self._paused_until = self._clock() + MODULE_OFF_PAUSE
            log.info("Mediarium returned 404 for %s: its music module looks off; pausing the "
                     "hook for %d min", MEDIARIUM_ROUTE, int(MODULE_OFF_PAUSE // 60))
            return None
        if code == 429:
            try:
                wait = float(resp.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                wait = BACKOFF_FIRST
            log.warning("Mediarium rate-limited the scan request; retrying in %ds", int(wait))
            return max(1.0, wait)
        return self._transient(f"HTTP {code}")

    def _transient(self, what: str) -> float:
        self._failures += 1
        delay = backoff(self._failures)
        log.warning("Mediarium scan request failed (%s); retrying in %ds", what, int(delay))
        return delay


class NavidromeHook(_Hook):
    name = "navidrome"

    def __init__(self, url: str, user: str, password: str, *, session=None, **kw):
        super().__init__(**kw)
        self.url, self.user, self.password, self._session = url, user, password, session
        self._failures = 0

    def fire(self) -> Optional[float]:
        try:
            call(self.url, "startScan", user=self.user, password=self.password,
                 timeout=10, session=self._session)
        except SubsonicError as exc:
            self._failures += 1
            delay = backoff(self._failures)
            log.warning("Navidrome rescan request failed (%s); retrying in %ds", exc, int(delay))
            return delay
        self._failures = 0
        log.info("Navidrome rescan triggered")
        return None


class PostImportHooks:
    """The runner the worker calls. Holds the enabled hooks; ``on_placed`` fans out."""

    def __init__(self, hooks: Optional[list] = None):
        self.hooks = list(hooks or [])

    @property
    def names(self) -> list[str]:
        return [h.name for h in self.hooks]

    def on_placed(self, placed: PlacedFile) -> None:
        for h in self.hooks:
            try:
                h.on_placed(placed)
            except Exception as exc:
                log.warning("Hook %s failed: %s", h.name, exc)

    def close(self) -> None:
        for h in self.hooks:
            h.close()


def build_hooks(config: dict) -> PostImportHooks:
    """Hooks named in ``post_import_hooks``; a ConfigError for a hook that is listed but
    not configured (a silent no-op would hide a typo)."""
    from .wiring import ConfigError
    opts = dict(debounce=float(config.get("hook_debounce_seconds", 45)),
                max_wait=float(config.get("hook_max_wait_seconds", 600)),
                min_interval=float(config.get("hook_min_interval_seconds", 60)))
    hooks = []
    for name in config.get("post_import_hooks") or []:
        if name == "mediarium":
            if not (config.get("mediarium_url") and config.get("mediarium_api_key")):
                raise ConfigError("POST_IMPORT_HOOKS lists mediarium but MEDIARIUM_URL and "
                                  "MEDIARIUM_API_KEY are not both set")
            hooks.append(MediariumHook(config["mediarium_url"], config["mediarium_api_key"],
                                       key_header=config.get("mediarium_api_key_header") or "X-Api-Key",
                                       **opts))
        elif name == "navidrome":
            if not (config.get("navidrome_url")
                    and has_credentials(config.get("navidrome_user", ""), config.get("navidrome_pass", ""))):
                raise ConfigError("POST_IMPORT_HOOKS lists navidrome but NAVIDROME_URL, "
                                  "NAVIDROME_USER and NAVIDROME_PASS are not all set")
            if config.get("analysis", "none") != "none":
                log.warning("navidrome hook with ANALYSIS=%s: BPM Tagger already triggers a "
                            "rescan after tagging; a rescan before the BPM tag exists is never "
                            "revisited by Navidrome's quick scan", config.get("analysis"))
            hooks.append(NavidromeHook(config["navidrome_url"], config["navidrome_user"],
                                       config["navidrome_pass"], **opts))
        else:
            raise ConfigError(f"unknown post-import hook '{name}' (known: mediarium, navidrome)")
    return PostImportHooks(hooks)
