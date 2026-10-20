"""Music Grabber's own SQLite database.

``from music_grabber.db import Database`` is the public surface; the domain mixins
(queue, playlists, ledger, library cache) are composed in ``database.py``.
"""

from .constants import GRAB_NONTERMINAL, GRAB_TERMINAL
from .database import Database

__all__ = ["Database", "GRAB_TERMINAL", "GRAB_NONTERMINAL"]
