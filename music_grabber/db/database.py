"""The composed Database: grab queue, Spotify playlists, ledger and library cache."""

from .base import _DBBase
from .ledger import LedgerMixin
from .library import LibraryMixin
from .playlists import PlaylistsMixin
from .queue import QueueMixin
from .users import UsersMixin


class Database(QueueMixin, PlaylistsMixin, LedgerMixin, LibraryMixin, UsersMixin, _DBBase):
    pass
