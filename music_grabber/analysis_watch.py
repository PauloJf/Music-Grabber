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
    def __init__(self, db, library_root: str, *, interval: float = 60.0, window_hours: float = 24.0,
                 settle_seconds: float = 300.0, reader: Callable[[str], dict] = read_analysis,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 mtime: Callable[[str], float] = os.path.getmtime, clock: Callable[[], float] = time.time):
        super().__init__(daemon=True, name="AnalysisWatcher")
        self.db, self.library_root = db, library_root
        self.interval, self.window = interval, timedelta(hours=window_hours)
        self.settle_seconds = settle_seconds
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

    def run_once(self) -> int:
        """Check every item still waiting; returns how many got a result."""
        done = 0
        for item in self.db.get_items_awaiting_analysis((self._now() - self.window).isoformat()):
            path = self._locate(item)
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
