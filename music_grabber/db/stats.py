"""Read-only aggregates behind ``GET /api/v1/stats`` (see ``music_grabber/stats.py``)."""

from typing import Optional


class StatsMixin:
    def stats_grabbed_since(self, since: Optional[str]) -> int:
        """Files the ledger recorded as placed at ``since`` (UTC ISO) or later; all of them for None."""
        with self._connect() as conn:
            if since is None:
                return conn.execute("SELECT COUNT(*) FROM grabbed_files").fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM grabbed_files WHERE placed_at >= ?", (since,)).fetchone()[0]

    def stats_last_grab(self) -> Optional[str]:
        """When the last grab succeeded (UTC ISO): newest ledger row, else newest finished queue item."""
        with self._connect() as conn:
            last = conn.execute("SELECT MAX(placed_at) FROM grabbed_files").fetchone()[0]
            return last or conn.execute("SELECT MAX(updated_at) FROM grab_queue WHERE status='done'").fetchone()[0]

    def stats_provider_outcomes(self) -> dict:
        """{provider: {"done": n, "failed": n}} over finished queue items. Items that failed before a provider
        was chosen are filed under ''."""
        out: dict = {}
        with self._connect() as conn:
            for r in conn.execute("SELECT COALESCE(provider, '') AS p, status, COUNT(*) AS n FROM grab_queue "
                                  "WHERE status IN ('done', 'failed') GROUP BY p, status"):
                out.setdefault(r["p"], {"done": 0, "failed": 0})[r["status"]] = r["n"]
        return out

    def stats_library_artists(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(DISTINCT norm_artist) FROM library_tracks WHERE norm_artist <> ''").fetchone()[0]

    def stats_release_counts(self, since_date: str, since_iso: str) -> dict:
        """Counts over the release cache and the dismissal tables. ``since_date`` is a YYYY-MM-DD release date."""
        with self._connect() as conn:
            one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
            mine = ("(dz_artist_id IN (SELECT dz_artist_id FROM artist_map WHERE active=1 AND dz_artist_id IS NOT NULL) "
                    "OR dz_artist_id IN (SELECT dz_artist_id FROM artist_prefs WHERE follow=1))")
            return {
                "releases_new": one(f"SELECT COUNT(*) FROM release_cache WHERE release_date >= ? AND {mine} "
                                    "AND dz_album_id NOT IN (SELECT ref FROM item_dismissals WHERE kind='release')", since_date),
                "releases_unannounced": one(f"SELECT COUNT(*) FROM release_cache WHERE release_date >= ? AND {mine} "
                                            "AND dz_album_id NOT IN (SELECT dz_album_id FROM release_notified)", since_date),
                "releases_announced": one("SELECT COUNT(*) FROM release_notified"),
                "releases_announced_week": one("SELECT COUNT(*) FROM release_notified WHERE notified_at >= ?", since_iso),
                "dismissed": one("SELECT COUNT(*) FROM item_dismissals"),
                "artists_starred": one("SELECT COUNT(*) FROM artist_taste WHERE starred > 0"),
            }
