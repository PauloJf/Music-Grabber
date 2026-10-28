"""New releases: which library artists map to which Deezer artist, what Deezer last said about
their albums, and each user's follows, hides and dismissals."""

from datetime import datetime, timezone
from typing import Iterable, Optional

PREF_FLAGS = ("follow", "hide_releases", "hide_suggestions")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReleasesMixin:
    # ── who counts as "your artists" ──────────────────────────────────────────
    def candidate_artists(self, min_owned: int, min_playlist: int) -> list[dict]:
        """Library artists with at least ``min_owned`` tracks, plus artists with at least
        ``min_playlist`` tracks on enabled playlists: [{norm, name, isrcs}]."""
        found: dict[str, str] = {}
        with self._connect() as conn:
            for r in conn.execute("SELECT norm_artist AS n, MAX(artist) AS name FROM library_tracks "
                                  "WHERE norm_artist <> '' GROUP BY norm_artist HAVING COUNT(*) >= ?", (min_owned,)):
                found[r["n"]] = r["name"]
            for r in conn.execute(
                    "SELECT pt.norm_artist AS n, MAX(pt.artist) AS name FROM playlist_tracks pt "
                    "JOIN playlists p ON p.id = pt.playlist_id WHERE p.enabled = 1 AND pt.removed_at IS NULL "
                    "AND pt.norm_artist <> '' GROUP BY pt.norm_artist HAVING COUNT(*) >= ?", (min_playlist,)):
                found.setdefault(r["n"], r["name"])
            out = []
            for n, name in found.items():
                isrcs = [r[0] for r in conn.execute(
                    "SELECT isrc FROM library_tracks WHERE norm_artist=? AND isrc IS NOT NULL AND isrc <> '' LIMIT 3", (n,))]
                out.append({"norm": n, "name": name, "isrcs": isrcs})
        return out

    def set_active_artists(self, norms: Iterable[str]) -> None:
        norms = list(norms)
        with self._connect() as conn:
            conn.execute("UPDATE artist_map SET active=0")
            for i in range(0, len(norms), 500):
                chunk = norms[i:i + 500]
                conn.execute(f"UPDATE artist_map SET active=1 WHERE norm_name IN ({','.join('?' * len(chunk))})", chunk)
            conn.commit()

    def get_artist_map(self, norm: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM artist_map WHERE norm_name=?", (norm,)).fetchone()
        return dict(row) if row else None

    def save_artist_map(self, norm: str, name: str, dz_artist_id: Optional[str], confidence: float,
                        source: str, active: bool = True) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO artist_map (norm_name, name, dz_artist_id, confidence, source, active, mapped_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(norm_name) DO UPDATE SET name=excluded.name, "
                "dz_artist_id=excluded.dz_artist_id, confidence=excluded.confidence, source=excluded.source, "
                "active=excluded.active, mapped_at=excluded.mapped_at",
                (norm, name, dz_artist_id, confidence, source, int(active), _now()))
            conn.commit()

    def artist_map_counts(self) -> dict:
        with self._connect() as conn:
            r = conn.execute("SELECT COUNT(*) AS active, SUM(dz_artist_id IS NOT NULL) AS mapped "
                             "FROM artist_map WHERE active=1").fetchone()
        return {"artists": r["active"] or 0, "mapped": r["mapped"] or 0}

    # ── polling Deezer ────────────────────────────────────────────────────────
    def artists_to_poll(self, stale_before: str, limit: int) -> list[dict]:
        """Mapped active artists and everyone's followed artists whose releases were last checked
        before ``stale_before`` (never-checked first), oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT d.dz_artist_id AS id, d.name AS name, c.checked_at AS checked_at FROM ("
                "  SELECT dz_artist_id, name FROM artist_map WHERE active=1 AND dz_artist_id IS NOT NULL "
                "  UNION SELECT dz_artist_id, name FROM artist_prefs WHERE follow=1) d "
                "LEFT JOIN artist_checks c ON c.dz_artist_id = d.dz_artist_id "
                "WHERE c.checked_at IS NULL OR c.checked_at < ? "
                "ORDER BY c.checked_at IS NOT NULL, c.checked_at LIMIT ?", (stale_before, limit)).fetchall()
        return [dict(r) for r in rows]

    def save_releases(self, dz_artist_id: str, name: str, albums: list[dict]) -> None:
        now = _now()
        with self._connect() as conn:
            for a in albums:
                conn.execute(
                    "INSERT INTO release_cache (dz_artist_id, dz_album_id, title, kind, release_date, cover_url, "
                    "artist_name, first_seen_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(dz_artist_id, dz_album_id) "
                    "DO UPDATE SET title=excluded.title, kind=excluded.kind, release_date=excluded.release_date, "
                    "cover_url=excluded.cover_url, artist_name=excluded.artist_name",
                    (dz_artist_id, a["dz_id"], a["title"], a["kind"], a["release_date"], a["cover_url"], name, now))
            conn.execute("INSERT INTO artist_checks (dz_artist_id, name, checked_at) VALUES (?,?,?) "
                         "ON CONFLICT(dz_artist_id) DO UPDATE SET name=excluded.name, checked_at=excluded.checked_at",
                         (dz_artist_id, name, now))
            conn.commit()

    def last_release_check(self) -> Optional[str]:
        with self._connect() as conn:
            return conn.execute("SELECT MAX(checked_at) FROM artist_checks").fetchone()[0]

    # ── the feed, per user ────────────────────────────────────────────────────
    def release_feed(self, user_id: int, since: str, limit: int = 200) -> list[dict]:
        """Releases dated ``since`` or later by your artists (library/playlist artists and the ones this user
        follows), minus the artists this user hid and the releases they dismissed. Newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT r.* FROM release_cache r WHERE r.release_date >= ? "
                "AND (r.dz_artist_id IN (SELECT dz_artist_id FROM artist_map WHERE active=1 AND dz_artist_id IS NOT NULL) "
                "     OR r.dz_artist_id IN (SELECT dz_artist_id FROM artist_prefs WHERE user_id=? AND follow=1)) "
                "AND r.dz_artist_id NOT IN (SELECT dz_artist_id FROM artist_prefs WHERE user_id=? AND hide_releases=1) "
                "AND r.dz_album_id NOT IN (SELECT ref FROM item_dismissals WHERE user_id=? AND kind='release') "
                "ORDER BY r.release_date DESC, r.title LIMIT ?", (since, user_id, user_id, user_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # ── prefs and dismissals ──────────────────────────────────────────────────
    def get_artist_prefs(self, user_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM artist_prefs WHERE user_id=? ORDER BY name COLLATE NOCASE", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def set_artist_pref(self, user_id: int, dz_artist_id: str, name: str, changes: dict) -> Optional[dict]:
        """Merge flag changes into one artist's pref row; a row with every flag off is removed (None)."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM artist_prefs WHERE user_id=? AND dz_artist_id=?",
                               (user_id, dz_artist_id)).fetchone()
            cur = dict(row) if row else {"name": name, **{f: 0 for f in PREF_FLAGS}}
            cur.update({k: int(bool(v)) for k, v in changes.items() if k in PREF_FLAGS})
            if name:
                cur["name"] = name
            if not any(cur[f] for f in PREF_FLAGS):
                conn.execute("DELETE FROM artist_prefs WHERE user_id=? AND dz_artist_id=?", (user_id, dz_artist_id))
                conn.commit()
                return None
            conn.execute("INSERT INTO artist_prefs (user_id, dz_artist_id, name, follow, hide_releases, hide_suggestions) "
                         "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id, dz_artist_id) DO UPDATE SET name=excluded.name, "
                         "follow=excluded.follow, hide_releases=excluded.hide_releases, "
                         "hide_suggestions=excluded.hide_suggestions",
                         (user_id, dz_artist_id, cur["name"], cur["follow"], cur["hide_releases"], cur["hide_suggestions"]))
            conn.commit()
        return {"dz_artist_id": dz_artist_id, **cur}

    def dismiss_item(self, user_id: int, kind: str, ref: str, on: bool = True) -> None:
        with self._connect() as conn:
            if on:
                conn.execute("INSERT OR IGNORE INTO item_dismissals (user_id, kind, ref) VALUES (?,?,?)", (user_id, kind, ref))
            else:
                conn.execute("DELETE FROM item_dismissals WHERE user_id=? AND kind=? AND ref=?", (user_id, kind, ref))
            conn.commit()

    def dismissed_releases(self, user_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT r.* FROM item_dismissals d JOIN release_cache r ON r.dz_album_id = d.ref "
                "WHERE d.user_id=? AND d.kind='release' ORDER BY r.release_date DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]
