"""The grabbed-files ledger: what Music Grabber itself filed.

Replaces BPM Tagger's ``tracks.managed`` flag and ``tracks.spotify_track_id`` stamp
(docs/plans/extraction.md §3): "managed" means "has a row here", and a stamped Spotify
id still wins outright in library matching.
"""

from datetime import datetime, timezone
from typing import Optional


class LedgerMixin:
    def record_grabbed_file(self, *, spotify_track_id: str, isrc: str, rel_path: str,
                            abs_path: str, queue_item_id: Optional[int], provider: str,
                            output_format: str, placed_hash: str) -> None:
        """Insert/replace the ledger row for a file we just placed."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO grabbed_files
                    (spotify_track_id, isrc, rel_path, abs_path, queue_item_id, provider,
                     output_format, placed_hash, placed_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(rel_path) DO UPDATE SET
                    spotify_track_id=excluded.spotify_track_id, isrc=excluded.isrc,
                    abs_path=excluded.abs_path, queue_item_id=excluded.queue_item_id,
                    provider=excluded.provider, output_format=excluded.output_format,
                    placed_hash=excluded.placed_hash, placed_at=excluded.placed_at
            """, (spotify_track_id or None, isrc or None, rel_path, abs_path, queue_item_id,
                  provider, output_format, placed_hash, now))
            conn.commit()

    def add_grabbed_files_missing(self, rows: list[dict]) -> int:
        """Insert ledger rows for files not in the ledger yet (matched by ``rel_path``); existing
        rows are never changed. Used by ``ledger rebuild``. Returns how many were added."""
        now = datetime.now(timezone.utc).isoformat()
        added = 0
        with self._connect() as conn:
            for r in rows:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO grabbed_files (spotify_track_id, isrc, rel_path, abs_path, "
                    "provider, output_format, placed_hash, placed_at) VALUES (?,?,?,?,?,?,?,?)",
                    (r["spotify_track_id"], r.get("isrc") or None, r["rel_path"], r["abs_path"], "",
                     r.get("output_format") or "", r.get("placed_hash") or "", now))
                added += cur.rowcount
            conn.commit()
        return added

    def find_grabbed_by_spotify_id(self, spotify_track_id: str) -> list[dict]:
        if not spotify_track_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM grabbed_files WHERE spotify_track_id = ? ORDER BY id DESC",
                (spotify_track_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_grabbed_count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM grabbed_files").fetchone()[0]

    def get_source_stats(self) -> dict:
        """Managed (ledger) count plus completed downloads per provider."""
        with self._connect() as conn:
            providers = {r["p"]: r["n"] for r in conn.execute(
                "SELECT COALESCE(provider, 'unknown') AS p, COUNT(*) AS n "
                "FROM grab_queue WHERE status='done' GROUP BY p ORDER BY n DESC")}
        return {"managed": self.get_grabbed_count(), "providers": providers}
