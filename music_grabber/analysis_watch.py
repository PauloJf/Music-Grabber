"""Fill in "BPM Tagger will analyse it": once BPM Tagger has tagged a file Music Grabber filed
for it, record the BPM and loudness it wrote on the queue item's history.

Strictly read-only: a file is only ever opened for reading, so its mtime never changes and
BPM Tagger never sees it as new again. BPM Tagger may also move the file after tagging, so the
path stored on the item is tried first and the library index (by Spotify id) second.
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .analysis import describe_analysis, read_analysis

log = logging.getLogger(__name__)


class AnalysisWatcher(threading.Thread):
    def __init__(self, db, library_root: str, *, index=None, interval: float = 60.0, window_hours: float = 24.0,
                 settle_seconds: float = 300.0, refresh_fast: float = 120.0, refresh_slow: float = 900.0,
                 reader: Callable[[str], dict] = read_analysis,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 mtime: Callable[[str], float] = os.path.getmtime, clock: Callable[[], float] = time.time):
        super().__init__(daemon=True, name="AnalysisWatcher")
        self.db, self.library_root = db, library_root
        self.interval, self.window = interval, timedelta(hours=window_hours)
        self.settle_seconds = settle_seconds
        self.index, self.refresh_fast, self.refresh_slow = index, refresh_fast, refresh_slow
        self._last_refresh = 0.0
        self._reader, self._now, self._mtime, self._clock = reader, now, mtime, clock
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()

    def run(self):
        while not self._halt.wait(self.interval):
            try:
                self.run_once()
            except Exception as exc:                  # never let the loop die
                log.warning("analysis watcher: %s", exc)

    def _locate(self, item: dict) -> Optional[str]:
        candidates = [item.get("final_path") or ""]
        for row in self.db.library_find_by_spotify_id(item.get("spotify_track_id") or ""):
            candidates.append(os.path.join(self.library_root, *str(row["path"]).split("/")))
        return next((p for p in candidates if p and os.path.isfile(p)), None)

    def _refresh_index(self, missing: list[dict]) -> bool:
        """BPM Tagger moves a file once it has tagged it, and the library index only learns the new
        place when it is refreshed. When files cannot be found, refresh it ourselves: every couple of
        minutes while the newest missing item is under an hour old, every 15 minutes after that, so
        a file that was deleted does not make the whole library be re-scanned all day."""
        if self.index is None:
            return False
        newest = min((self._age(i) for i in missing), default=0.0)
        if self._clock() - self._last_refresh < (self.refresh_fast if newest < 3600 else self.refresh_slow):
            return False
        self._last_refresh = self._clock()
        try:
            self.index.refresh()
        except Exception as exc:
            log.warning("analysis watcher: library refresh failed: %s", exc)
            return False
        return True

    def _age(self, item: dict) -> float:
        try:
            return max(0.0, (self._now() - datetime.fromisoformat(item["updated_at"])).total_seconds())
        except (KeyError, TypeError, ValueError):
            return 0.0

    def run_once(self) -> int:
        """Check every item still waiting; returns how many got a result."""
        waiting = self.db.get_items_awaiting_analysis((self._now() - self.window).isoformat())
        located = {i["id"]: self._locate(i) for i in waiting}
        missing = [i for i in waiting if not located[i["id"]]]
        if missing and self._refresh_index(missing):
            located.update({i["id"]: self._locate(i) for i in missing})
        done = 0
        for item in waiting:
            path = located[item["id"]]
            if not path:
                continue
            found = self._reader(path)
            if "bpm" not in found:
                continue
            # BPM first, loudness a moment later is possible: give it time unless both are there.
            if "gain_db" not in found:
                try:
                    if self._clock() - self._mtime(path) < self.settle_seconds:
                        continue
                except OSError:
                    continue
            self.db.add_grab_event(item["id"], "analysis_result", describe_analysis(found))
            done += 1
        return done
