"""Post-download analysis: BPM-tag the file (docs/plans/extraction.md §2.2).

BPM Tagger used to run detectors inside the grabber's worker, on the staged file,
before it entered the library. Music Grabber ships no detectors, so analysis happens
*after* placement, and by whoever owns it:

* ``NoAnalysis``: standalone. Files are filed under ``MUSIC_DIR`` as they are.
* ``IntakeFolderAnalysis`` / ``ApiAnalysis``: BPM Tagger's watcher picks the file up
  (Phase 2).

Implementations must never raise into the pipeline.
"""

from dataclasses import dataclass, field
from typing import Optional, Protocol


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
