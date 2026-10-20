"""Coalesce a burst of "something changed" notifications into few calls.

Used by the post-import hooks (Mediarium, Navidrome) and by the BPM Tagger API analysis:
placing an album fires one notification per track, but the receiving server wants one
scan request per album (BPM Tagger's old Navidrome rescan was fired per track; see
docs/plans/extraction.md finding 5).

Rules, evaluated against an injectable clock so tests are deterministic:

* trailing-edge debounce: fire ``debounce`` seconds after the *last* notification;
* max-wait: never delay the first pending notification by more than ``max_wait``, so a
  long download run still fires;
* minimum interval: at least ``min_interval`` seconds between fires;
* dirty follow-up: notifications arriving while (or just after) a fire keep the pending
  flag, so exactly one further fire follows, never zero and never a storm;
* ``fire()`` may return a number of seconds to ask for a retry (backoff) with the pending
  flag kept; any exception is logged and dropped.
"""

import logging
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)


class Coalescer:
    def __init__(self, fire: Callable[[], Optional[float]], *, debounce: float = 45.0,
                 max_wait: float = 600.0, min_interval: float = 60.0, name: str = "coalescer",
                 clock: Callable[[], float] = time.monotonic, timer_factory=threading.Timer):
        self._fire, self.name = fire, name
        self._debounce, self._max_wait, self._min_interval = debounce, max_wait, min_interval
        self._clock, self._timer_factory = clock, timer_factory
        self._lock = threading.Lock()
        self._timer = None
        self._pending = False
        self._first: Optional[float] = None
        self._last_notify = 0.0
        self._last_fire: Optional[float] = None
        self._not_before = 0.0
        self._closed = False

    @property
    def pending(self) -> bool:
        return self._pending

    def notify(self) -> None:
        with self._lock:
            if self._closed:
                return
            now = self._clock()
            self._last_notify = now
            if not self._pending:
                self._pending, self._first = True, now
            self._arm(max(0.0, self._due() - now))

    def flush(self) -> None:
        """Fire now if anything is pending (shutdown, tests)."""
        with self._lock:
            if not self._pending:
                return
            self._cancel()
        self._run(force=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel()

    # ── internals ─────────────────────────────────────────────────────────────
    def _due(self) -> float:
        due = min(self._last_notify + self._debounce, (self._first or 0.0) + self._max_wait)
        if self._last_fire is not None:
            due = max(due, self._last_fire + self._min_interval)
        return max(due, self._not_before)

    def _cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _arm(self, delay: float) -> None:
        self._cancel()
        t = self._timer_factory(delay, self._run)
        t.daemon = True
        self._timer = t
        t.start()

    def _run(self, force: bool = False) -> None:
        with self._lock:
            self._timer = None
            if not self._pending or self._closed:
                return
            now = self._clock()
            due = self._due()
            if not force and now < due - 1e-9:      # woke early: a newer notification moved it
                self._arm(due - now)
                return
            self._pending, self._first = False, None
            self._last_fire = now
        try:
            retry = self._fire()
        except Exception as exc:
            log.warning("%s: fire failed: %s", self.name, exc)
            retry = None
        if retry:
            with self._lock:
                if self._closed:
                    return
                self._pending = True
                self._first = self._first if self._first is not None else now
                self._not_before = now + float(retry)
                self._arm(float(retry))
