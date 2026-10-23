"""State of the Navidrome playlist mirror (music_grabber/mirror.py): one row per mirrored playlist."""

from typing import Optional

_COLS = ("remote_id", "remote_name", "paths_hash", "content_hash", "matched", "unresolved",
         "state", "note", "last_error", "synced_at")


class MirrorMixin:
    def get_mirror(self, playlist_id: int) -> Optional[dict]:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM playlist_mirrors WHERE playlist_id = ?",
                             (playlist_id,)).fetchone()
        return dict(r) if r else None

    def list_mirrors(self) -> dict:
        with self._connect() as conn:
            return {r["playlist_id"]: dict(r) for r in conn.execute("SELECT * FROM playlist_mirrors")}

    def save_mirror(self, playlist_id: int, **fields) -> None:
        """Upsert: only the given fields change (the others keep their stored value)."""
        bad = set(fields) - set(_COLS)
        if bad:
            raise ValueError(f"unknown mirror field(s): {sorted(bad)}")
        cols = list(fields)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO playlist_mirrors (playlist_id{''.join(', ' + c for c in cols)}) "
                f"VALUES (?{', ?' * len(cols)}) ON CONFLICT(playlist_id) DO UPDATE SET "
                + (", ".join(f"{c}=excluded.{c}" for c in cols) or "playlist_id=playlist_id"),
                [playlist_id, *fields.values()])
            conn.commit()

    def delete_mirror(self, playlist_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM playlist_mirrors WHERE playlist_id = ?", (playlist_id,))
            conn.commit()

    def set_playlist_mirror(self, playlist_id: int, enabled: bool) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE playlists SET mirror = ? WHERE id = ?", (int(enabled), playlist_id))
            conn.commit()
