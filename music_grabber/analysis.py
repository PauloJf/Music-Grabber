"""Post-download analysis: BPM-tag the file (docs/plans/extraction.md §2.2).

BPM Tagger used to run detectors inside the grabber's worker, on the staged file,
before it entered the library. Music Grabber ships no detectors, so analysis happens
*after* placement, and by whoever owns it:

* ``NoAnalysis``: standalone. Files are filed under ``MUSIC_DIR`` as they are.
* ``IntakeFolderAnalysis``: files are filed into ``INTAKE_DIR``, a directory BPM Tagger's
  watcher covers; BPM Tagger analyses and tags them. Optionally waits to confirm.
* ``ApiAnalysis``: intake placement plus a coalesced "scan now" request to BPM Tagger's
  HTTP API, for setups that run it in a one-shot scan mode instead of watching.

Implementations must never raise into the pipeline. Nothing here may modify the file after
placement: a later mtime change would make BPM Tagger analyse it twice.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

import requests

from .coalesce import Coalescer

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlacedFile:
    """A finished file at its final path."""
    path: str                 # absolute
    rel_path: str             # POSIX, relative to the filing root
    meta: dict
    queue_item_id: Optional[int] = None


@dataclass
class AnalysisResult:
    ok: bool = True
    detail: str = ""
    extra: dict = field(default_factory=dict)


class PostDownloadAnalysis(Protocol):
    name: str

    def destination_root(self, config: dict) -> str:
        """Where this mode wants files filed."""
        ...

    def after_place(self, placed: PlacedFile) -> AnalysisResult:
        ...


class NoAnalysis:
    name = "none"

    def destination_root(self, config: dict) -> str:
        return config["music_dir"]

    def after_place(self, placed: PlacedFile) -> AnalysisResult:
        return AnalysisResult(ok=True, detail="no analysis configured")


def read_bpm_tag(path: str) -> Optional[float]:
    """The BPM tag of a file, or None. Read-only (never changes the file or its mtime)."""
    import mutagen
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".mp3":
            fr = ID3(path).getall("TBPM")
            raw = fr[0].text[0] if fr and fr[0].text else None
        elif ext in (".m4a", ".aac", ".mp4"):
            mp4 = MP4(path)
            tmpo = mp4.get("tmpo")
            raw = tmpo[0] if tmpo else None
        else:
            audio = mutagen.File(path)
            vals = (audio.get("BPM") or audio.get("bpm")) if audio is not None else None
            raw = vals[0] if vals else None
        return float(raw) if raw not in (None, "") and float(raw) > 0 else None
    except Exception:
        return None


class IntakeFolderAnalysis:
    """File into BPM Tagger's watched folder and let its watcher do the rest."""
    name = "intake"

    def __init__(self, intake_dir: str, wait_seconds: float = 0.0, poll_seconds: float = 2.0,
                 bpm_reader: Callable[[str], Optional[float]] = read_bpm_tag,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.intake_dir = intake_dir
        self.wait_seconds, self.poll_seconds = wait_seconds, poll_seconds
        self._bpm_reader, self._sleep, self._clock = bpm_reader, sleep, clock

    def destination_root(self, config: dict) -> str:
        return self.intake_dir

    def after_place(self, placed: PlacedFile) -> AnalysisResult:
        if self.wait_seconds <= 0:
            return AnalysisResult(ok=True, detail="filed in intake; BPM Tagger will analyse it")
        deadline = self._clock() + self.wait_seconds
        while True:
            bpm = self._bpm_reader(placed.path)
            if bpm:
                return AnalysisResult(ok=True, detail=f"bpm: {bpm:g}", extra={"bpm": bpm})
            if self._clock() >= deadline:
                return AnalysisResult(ok=True, detail="bpm: not yet (BPM Tagger has not tagged it)")
            self._sleep(self.poll_seconds)


class BpmTaggerAuthError(Exception):
    """BPM Tagger refused our login for a reason retrying will not fix."""


class BpmTaggerClient:
    """The one BPM Tagger call ApiAnalysis makes: log in with the admin password and ask
    it to scan. ``POST /api/scan/start`` is session + CSRF protected and scans the library
    per BPM Tagger's MODE; it needs the admin password and does not work when admin
    two-factor is on (docs/plans/extraction.md §2.2, Q2)."""

    def __init__(self, url: str, password: str, username: str = "", session=None, timeout: float = 15):
        self.url = url.rstrip("/")
        self._password, self._username = password, username
        self._http = session or requests.Session()
        self._timeout = timeout
        self._csrf = ""

    def _login(self) -> None:
        body = {"password": self._password}
        if self._username:
            body["username"] = self._username
        resp = self._http.post(f"{self.url}/api/login", json=body, timeout=self._timeout)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code == 200 and data.get("ok"):
            self._csrf = data.get("csrf_token", "")
            return
        err = data.get("error", "")
        if err == "totp_required":
            raise BpmTaggerAuthError("BPM Tagger has admin two-factor enabled, which the API "
                                     "analysis cannot satisfy; run BPM Tagger in watch mode "
                                     "and use ANALYSIS=intake instead")
        if err == "invalid_password":
            raise BpmTaggerAuthError("BPM Tagger rejected the admin password (BPM_TAGGER_PASSWORD)")
        if resp.status_code == 429 or err == "locked_out":
            raise RuntimeError("BPM Tagger login is locked out; will retry later")
        raise RuntimeError(f"BPM Tagger login failed: HTTP {resp.status_code}")

    def trigger_scan(self) -> str:
        """Ask for a scan. Returns a short status. Raises BpmTaggerAuthError (permanent)
        or another exception (transient)."""
        for attempt in range(2):
            if not self._csrf:
                self._login()
            resp = self._http.post(f"{self.url}/api/scan/start", headers={"X-CSRF-Token": self._csrf},
                                   timeout=self._timeout)
            if resp.status_code in (401, 403) and attempt == 0:
                self._csrf = ""                      # session expired: log in once more
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"BPM Tagger scan request failed: HTTP {resp.status_code}")
            data = resp.json() if resp.content else {}
            if data.get("ok"):
                return "scan started"
            if "already running" in str(data.get("error", "")):
                return "scan already running"
            raise RuntimeError(f"BPM Tagger refused the scan: {data.get('error', 'unknown error')}")
        raise RuntimeError("BPM Tagger session could not be established")


class ApiAnalysis(IntakeFolderAnalysis):
    """Intake placement plus a coalesced scan request to BPM Tagger's HTTP API."""
    name = "api"

    def __init__(self, intake_dir: str, client: BpmTaggerClient, *, debounce: float = 45.0,
                 max_wait: float = 600.0, min_interval: float = 60.0, wait_seconds: float = 0.0,
                 clock=time.monotonic, timer_factory=None, **kw):
        super().__init__(intake_dir, wait_seconds=wait_seconds, clock=clock, **kw)
        self.client = client
        self.disabled = False
        self._failures = 0
        opts = {} if timer_factory is None else {"timer_factory": timer_factory}
        self.coalescer = Coalescer(self._fire, debounce=debounce, max_wait=max_wait,
                                   min_interval=min_interval, name="bpm-tagger-scan",
                                   clock=clock, **opts)

    def after_place(self, placed: PlacedFile) -> AnalysisResult:
        if not self.disabled:
            self.coalescer.notify()
        res = super().after_place(placed)
        res.detail = ("scan requested (coalesced); " + res.detail) if not self.disabled else res.detail
        return res

    def _fire(self) -> Optional[float]:
        if self.disabled:
            return None
        try:
            status = self.client.trigger_scan()
        except BpmTaggerAuthError as exc:
            self.disabled = True
            log.warning("%s. BPM Tagger API analysis disabled until restart.", exc)
            return None
        except Exception as exc:
            self._failures += 1
            delay = min(3600.0, 60.0 * 2 ** (self._failures - 1))
            log.warning("BPM Tagger scan request failed (%s); retrying in %ds", type(exc).__name__, int(delay))
            return delay
        self._failures = 0
        log.info("BPM Tagger: %s", status)
        return None

    def close(self) -> None:
        self.coalescer.close()
