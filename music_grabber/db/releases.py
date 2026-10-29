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
                        source: str, active: bool = True, image_url: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO artist_map (norm_name, name, dz_artist_id, confidence, source, active, mapped_at, image_url) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(norm_name) DO UPDATE SET name=excluded.name, "
                "dz_artist_id=excluded.dz_artist_id, confidence=excluded.confidence, source=excluded.source, "
                "active=excluded.active, mapped_at=excluded.mapped_at, "
                "image_url=COALESCE(NULLIF(excluded.image_url, ''), artist_map.image_url)",
                (norm, name, dz_artist_id, confidence, source, int(active), _now(), image_url))
            conn.commit()

    def artist_art(self) -> dict:
        """{normalised artist name: {dz_id, image_url}} for every artist matched on Deezer."""
        with self._connect() as conn:
            return {r["norm_name"]: {"dz_id": r["dz_artist_id"], "image_url": r["image_url"] or ""} for r in conn.execute(
                "SELECT norm_name, dz_artist_id, image_url FROM artist_map WHERE dz_artist_id IS NOT NULL")}

    def artists_missing_image(self, limit: int) -> list[dict]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT norm_name, name, dz_artist_id FROM artist_map WHERE dz_artist_id IS NOT NULL "
                "AND (image_url IS NULL OR image_url='') LIMIT ?", (limit,))]

    def art_artists_to_poll(self, limit: int) -> list[dict]:
        """Matched artists outside New releases (inactive) whose albums have never been read."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT m.dz_artist_id AS id, m.name AS name FROM artist_map m "
                "LEFT JOIN artist_checks c ON c.dz_artist_id = m.dz_artist_id "
                "WHERE m.active=0 AND m.dz_artist_id IS NOT NULL AND c.checked_at IS NULL LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def mark_checked(self, dz_artist_id: str, name: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO artist_checks (dz_artist_id, name, checked_at) VALUES (?,?,?) "
                         "ON CONFLICT(dz_artist_id) DO UPDATE SET name=excluded.name, checked_at=excluded.checked_at",
                         (dz_artist_id, name, _now()))
            conn.commit()

    def set_artist_image(self, dz_artist_id: str, image_url: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE artist_map SET image_url=? WHERE dz_artist_id=?", (image_url, dz_artist_id))
            conn.commit()

    def album_art_all(self) -> dict:
        """{dz artist id: [(normalised title, cover url, dz album id)]} from the cached discographies."""
        out: dict = {}
        with self._connect() as conn:
            for r in conn.execute("SELECT dz_artist_id, norm_title, cover_url, dz_album_id FROM album_cache WHERE kind IN ('album','ep','single','compile')"):
                out.setdefault(r["dz_artist_id"], []).append((r["norm_title"], r["cover_url"] or "", r["dz_album_id"]))
        return out

    def artist_map_counts(self) -> dict:
        """New releases' artists, and the whole library's: how many exist, how many are matched on Deezer and how many
        of those have a photo."""
        with self._connect() as conn:
            r = conn.execute("SELECT COUNT(*) AS active, SUM(dz_artist_id IS NOT NULL) AS mapped "
                             "FROM artist_map WHERE active=1").fetchone()
            total = conn.execute("SELECT COUNT(DISTINCT norm_artist) FROM library_tracks WHERE norm_artist <> ''").fetchone()[0]
            m = conn.execute("SELECT SUM(dz_artist_id IS NOT NULL) AS mapped, "
                             "SUM(dz_artist_id IS NOT NULL AND image_url IS NOT NULL AND image_url NOT IN ('', '-')) AS photos "
                             "FROM artist_map WHERE norm_name IN (SELECT DISTINCT norm_artist FROM library_tracks)").fetchone()
        return {"artists": r["active"] or 0, "mapped": r["mapped"] or 0,
                "library_artists": total or 0, "library_matched": m["mapped"] or 0, "library_photos": m["photos"] or 0}

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

    # ── suggestions: discography, related artists, taste ─────────────────────
    def save_albums(self, dz_artist_id: str, name: str, albums: list[dict]) -> None:
        from ..text import normalize_title
        with self._connect() as conn:
            for a in albums:
                conn.execute(
                    "INSERT INTO album_cache (dz_artist_id, dz_album_id, title, norm_title, kind, release_date, cover_url, "
                    "track_count, artist_name) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(dz_artist_id, dz_album_id) DO UPDATE SET "
                    "title=excluded.title, norm_title=excluded.norm_title, kind=excluded.kind, "
                    "release_date=excluded.release_date, cover_url=excluded.cover_url, track_count=excluded.track_count, "
                    "artist_name=excluded.artist_name",
                    (dz_artist_id, a["dz_id"], a["title"], normalize_title(a["title"]), a["kind"], a["release_date"],
                     a["cover_url"], a.get("track_count"), name))
            conn.commit()

    def albums_of(self, dz_artist_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM album_cache WHERE dz_artist_id=?", (dz_artist_id,)).fetchall()
        return [dict(r) for r in rows]

    def active_mapped(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT norm_name AS norm, name, dz_artist_id AS dz_id FROM artist_map "
                                "WHERE active=1 AND dz_artist_id IS NOT NULL").fetchall()
        return [dict(r) for r in rows]

    def library_artist_counts(self) -> dict:
        with self._connect() as conn:
            return {r[0]: r[1] for r in conn.execute(
                "SELECT norm_artist, COUNT(*) FROM library_tracks WHERE norm_artist <> '' GROUP BY norm_artist")}

    def library_album_counts(self, norms: list[str]) -> dict:
        """{norm_artist: {album title: tracks owned}} for the given artists."""
        out: dict = {}
        with self._connect() as conn:
            for i in range(0, len(norms), 500):
                chunk = norms[i:i + 500]
                for r in conn.execute(
                        f"SELECT norm_artist, album, COUNT(*) AS n FROM library_tracks WHERE norm_artist IN "
                        f"({','.join('?' * len(chunk))}) AND album IS NOT NULL AND album <> '' GROUP BY norm_artist, album", chunk):
                    out.setdefault(r["norm_artist"], {})[r["album"]] = r["n"]
        return out

    def replace_artist_taste(self, starred: dict) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM artist_taste")
            conn.executemany("INSERT INTO artist_taste (norm_name, starred) VALUES (?,?)", list(starred.items()))
            conn.commit()

    def artist_taste(self) -> dict:
        with self._connect() as conn:
            return {r[0]: r[1] for r in conn.execute("SELECT norm_name, starred FROM artist_taste")}

    def related_due(self, ids: list[str], stale_before: str) -> list[str]:
        """Those of ``ids`` whose related artists were never read, or not since ``stale_before``."""
        if not ids:
            return []
        with self._connect() as conn:
            fresh = {r[0] for r in conn.execute(
                f"SELECT dz_artist_id FROM related_checks WHERE checked_at >= ? AND dz_artist_id IN "
                f"({','.join('?' * len(ids))})", (stale_before, *ids))}
        return [i for i in ids if i not in fresh]

    def save_related(self, dz_artist_id: str, related: list[dict]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM related_cache WHERE dz_artist_id=?", (dz_artist_id,))
            conn.executemany(
                "INSERT INTO related_cache (dz_artist_id, rel_id, rel_name, image_url, fans, rank) VALUES (?,?,?,?,?,?)",
                [(dz_artist_id, r["dz_id"], r["name"], r["image_url"], r.get("fans"), i) for i, r in enumerate(related)])
            conn.execute("INSERT INTO related_checks (dz_artist_id, checked_at) VALUES (?,?) "
                         "ON CONFLICT(dz_artist_id) DO UPDATE SET checked_at=excluded.checked_at", (dz_artist_id, _now()))
            conn.commit()

    def related_of(self, ids: list[str]) -> list[dict]:
        out: list[dict] = []
        with self._connect() as conn:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                out += [dict(r) for r in conn.execute(
                    f"SELECT * FROM related_cache WHERE dz_artist_id IN ({','.join('?' * len(chunk))})", chunk)]
        return out

    def dismissed_refs(self, user_id: int, kind: str) -> set:
        with self._connect() as conn:
            return {r[0] for r in conn.execute("SELECT ref FROM item_dismissals WHERE user_id=? AND kind=?", (user_id, kind))}

    def dismissed_suggestions(self, user_id: int) -> dict:
        """What this user dismissed from the suggestions, with names: {artists: [...], albums: [...]}."""
        with self._connect() as conn:
            artists = [dict(r) for r in conn.execute(
                "SELECT DISTINCT d.ref AS dz_artist_id, COALESCE(r.rel_name, d.ref) AS name FROM item_dismissals d "
                "LEFT JOIN related_cache r ON r.rel_id = d.ref WHERE d.user_id=? AND d.kind='artist' "
                "GROUP BY d.ref", (user_id,))]
            albums = [dict(r) for r in conn.execute(
                "SELECT a.dz_album_id, a.dz_artist_id, a.title, a.artist_name FROM item_dismissals d "
                "JOIN album_cache a ON a.dz_album_id = d.ref WHERE d.user_id=? AND d.kind='album'", (user_id,))]
        return {"artists": artists, "albums": albums}

    # ── the ntfy ping ─────────────────────────────────────────────────────────
    def notify_baseline_done(self) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM app_counters WHERE key='releases_notify_baseline'").fetchone() is not None

    def set_notify_baseline(self) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO app_counters (key, value) VALUES ('releases_notify_baseline', 1)")
            conn.commit()

    def unnotified_releases(self, since: str) -> list[dict]:
        """Releases dated ``since`` or later by anyone's artists (library/playlist artists and followed ones)
        that no ping has mentioned yet, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT r.* FROM release_cache r WHERE r.release_date >= ? "
                "AND r.dz_album_id NOT IN (SELECT dz_album_id FROM release_notified) "
                "AND (r.dz_artist_id IN (SELECT dz_artist_id FROM artist_map WHERE active=1 AND dz_artist_id IS NOT NULL) "
                "     OR r.dz_artist_id IN (SELECT dz_artist_id FROM artist_prefs WHERE follow=1)) "
                "ORDER BY r.release_date, r.title", (since,)).fetchall()
        return [dict(r) for r in rows]

    def mark_notified(self, album_ids: list[str]) -> None:
        with self._connect() as conn:
            conn.executemany("INSERT OR IGNORE INTO release_notified (dz_album_id, notified_at) VALUES (?,?)",
                             [(i, _now()) for i in album_ids])
            conn.commit()
