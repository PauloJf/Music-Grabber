"""The composed Database: grab queue, Spotify playlists, ledger and library cache."""

from .apikeys import ApiKeysMixin
from .base import _DBBase
from .ledger import LedgerMixin
from .library import LibraryMixin
from .mirror import MirrorMixin
from .playlists import PlaylistsMixin
from .queue import QueueMixin
from .releases import ReleasesMixin
from .users import UsersMixin


class Database(QueueMixin, PlaylistsMixin, LedgerMixin, LibraryMixin, MirrorMixin, UsersMixin, ApiKeysMixin,
               ReleasesMixin, _DBBase):
    pass
