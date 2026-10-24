"""Library-index cache: what the library already has, as loaded by a LibrarySource."""

import unicodedata
from typing import Iterable, Optional


def fold(text: str) -> str:
    """Lower-case and strip accents, for substring matching ("Não" -> "nao")."""
    return "".join(c for c in unicodedata.normalize("NFKD", str(text).casefold()) if not unicodedata.combining(c))


class LibraryMixin:
    _LT_COLS = ("path", "change_token", "source", "title", "artist", "album", "album_artist",
                "duration_ms", "isrc", "track_no", "disc_no", "year", "norm_title", "norm_artist",
                "spotify_track_id")

    def library_tokens(self, source: str) -> dict:
        """{path: change_token} for rows this source loaded."""
        with self._connect() as conn:
            return {r["path"]: r["change_token"] for r in conn.execute(
                "SELECT path, change_token FROM library_tracks WHERE source = ?", (source,))}

    def library_upsert(self, rows: Iterable[dict]) -> int:
        vals = [tuple(r.get(c) for c in self._LT_COLS) for r in rows]
        if not vals:
            return 0
        cols = ", ".join(self._LT_COLS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in self._LT_COLS if c != "path")
        with self._connect() as conn:
            conn.executemany(
                f"INSERT INTO library_tracks ({cols}) VALUES ({','.join('?' * len(self._LT_COLS))}) "
                f"ON CONFLICT(path) DO UPDATE SET {updates}", vals)
            conn.commit()
        return len(vals)

    def library_delete(self, paths: Iterable[str]) -> int:
        paths = list(paths)
        n = 0
        with self._connect() as conn:
            for i in range(0, len(paths), 500):
                chunk = paths[i:i + 500]
                cur = conn.execute(
                    f"DELETE FROM library_tracks WHERE path IN ({','.join('?' * len(chunk))})", chunk)
                n += cur.rowcount
            conn.commit()
        return n

    def library_count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM library_tracks").fetchone()[0]

    def library_list(self, query: str = "", limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        """A page of the cached library ordered artist, album, disc, track, title, and the total
        that matched. `query`: every word must appear in the title, artist, album, album artist or
        path, ignoring case and accents ("nao" finds "Não"); no wildcards."""
        words = [fold(w) for w in (query or "").split() if w.strip()]
        where, args = "", []
        if words:
            cond = "instr(fold(COALESCE(title,'') || ' ' || COALESCE(artist,'') || ' ' || COALESCE(album,'') || ' ' || " \
                   "COALESCE(album_artist,'') || ' ' || path), ?) > 0"
            where = " WHERE " + " AND ".join([cond] * len(words))
            args = words
        with self._connect() as conn:
            conn.create_function("fold", 1, lambda v: fold(v or ""), deterministic=True)
            total = conn.execute(f"SELECT COUNT(*) FROM library_tracks{where}", args).fetchone()[0]
            rows = conn.execute(
                "SELECT path, title, artist, album, album_artist, duration_ms, year, track_no, disc_no "
                f"FROM library_tracks{where} ORDER BY COALESCE(album_artist, artist, '') COLLATE NOCASE, "
                "COALESCE(album, '') COLLATE NOCASE, COALESCE(disc_no, 0), COALESCE(track_no, 0), "
                "COALESCE(title, '') COLLATE NOCASE, path LIMIT ? OFFSET ?",
                [*args, int(limit), int(offset)]).fetchall()
        return [dict(r) for r in rows], total

    def library_has_path(self, path: str) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM library_tracks WHERE path = ? LIMIT 1",
                                (path,)).fetchone() is not None

    def library_find_by_spotify_id(self, spotify_track_id: str) -> list[dict]:
        if not spotify_track_id:
            return []
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM library_tracks WHERE spotify_track_id = ? ORDER BY path", (spotify_track_id,))]

    def library_tags(self, paths: list) -> dict:
        """{path: {isrc, norm_artist, norm_title, duration_ms}} for the paths the cache knows."""
        out: dict = {}
        paths = list(dict.fromkeys(paths))
        with self._connect() as conn:
            for i in range(0, len(paths), 500):
                chunk = paths[i:i + 500]
                for r in conn.execute(
                        "SELECT path, isrc, norm_artist, norm_title, duration_ms FROM library_tracks "
                        f"WHERE path IN ({','.join('?' * len(chunk))})", chunk):
                    out[r["path"]] = dict(r)
        return out

    def library_tagged(self) -> list[dict]:
        """Library files that carry a Spotify id tag."""
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT path, spotify_track_id, isrc, change_token FROM library_tracks "
                "WHERE spotify_track_id IS NOT NULL AND spotify_track_id != '' ORDER BY path")]

    def library_find_by_isrc(self, isrc: str) -> list[dict]:
        if not isrc:
            return []
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM library_tracks WHERE isrc = ? COLLATE NOCASE",
                                (isrc,)).fetchall()
        return [dict(r) for r in rows]

    def library_find_candidates(self, norm_artist: Optional[str], norm_title: Optional[str]) -> list[dict]:
        """Cheap SQL prefilter: rows sharing the normalized artist OR title.
        The fuzzy scorer in matching.py makes the final decision."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM library_tracks WHERE norm_artist = ? OR norm_title = ?",
                (norm_artist or "", norm_title or "")).fetchall()
        return [dict(r) for r in rows]
