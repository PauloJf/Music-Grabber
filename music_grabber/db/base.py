"""Connection, schema creation and pre-migration backup."""

import logging
import os
import shutil
import sqlite3

from ..config import __version__

log = logging.getLogger(__name__)


class _DBBase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._backup_once()
        self._init_db()

    def _backup_once(self) -> None:
        """Copy the DB file to ``<db>.bak-<version>`` before migrating, once per
        version. No-op on a fresh/empty DB. Never fatal."""
        try:
            if not os.path.isfile(self.db_path) or os.path.getsize(self.db_path) == 0:
                return
            bak = f"{self.db_path}.bak-{__version__}"
            if os.path.exists(bak):
                return
            shutil.copy2(self.db_path, bak)
            log.info("DB backup written before migration: %s", os.path.basename(bak))
        except Exception as exc:  # pragma: no cover - best effort
            log.warning("DB pre-migration backup failed (continuing): %s", exc)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        # Foreign keys are off by default in SQLite and scoped per connection.
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            self._create_tables(conn)
            # Start the lifetime "tracks grabbed" tally at the ledger size when the
            # counter row is missing (a fresh or imported database).
            # (A scalar subquery, not `SELECT COUNT(*) ... WHERE`: an aggregate returns one
            # row even when the WHERE excludes everything, which broke every reopen.)
            conn.execute(
                "INSERT INTO app_counters (key, value) "
                "SELECT 'grabbed_total', (SELECT COUNT(*) FROM grabbed_files) "
                "WHERE NOT EXISTS (SELECT 1 FROM app_counters WHERE key='grabbed_total')")
            conn.commit()

    def _create_tables(self, conn):
        """All CREATE ... IF NOT EXISTS, safe to re-run. Later versions add columns
        with additive ALTER TABLE ADD COLUMN checks below the CREATEs."""
        # Spotify playlists only (BPM Tagger's table also held navidrome/local rows).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                spotify_id     TEXT UNIQUE NOT NULL,
                name           TEXT,
                snapshot_id    TEXT,
                enabled        INTEGER DEFAULT 1,
                image_url      TEXT,
                track_count    INTEGER DEFAULT 0,
                last_synced_at TEXT,
                created_at     TEXT
            )
        """)
        # Membership state (is_new / removed_at) is independent of local availability
        # (match_status). No UNIQUE(playlist_id, position): positions are rewritten on
        # every sync, the diff keys on source_track_id in Python instead. Columns match
        # BPM Tagger's playlist_tracks so the import tool is a straight copy.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id       INTEGER NOT NULL,
                source_track_id   TEXT,                     -- spotify track id (diff key)
                spotify_track_id  TEXT,
                position          INTEGER,
                title             TEXT,
                artist            TEXT,
                album             TEXT,
                album_artist      TEXT,
                duration_ms       INTEGER,
                isrc              TEXT,
                track_no          INTEGER,
                disc_no           INTEGER,
                year              INTEGER,
                cover_url         TEXT,
                added_at          TEXT,
                norm_title        TEXT,
                norm_artist       TEXT,
                match_status      TEXT DEFAULT 'unknown',   -- have | missing | unknown
                matched_file_path TEXT,
                first_seen_at     TEXT,
                is_new            INTEGER DEFAULT 0,
                removed_at        TEXT,                     -- tombstone (NULL = present)
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_playlist ON playlist_tracks(playlist_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_sid ON playlist_tracks(spotify_track_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_src ON playlist_tracks(playlist_id, source_track_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS grab_queue (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_track_id   INTEGER,
                spotify_track_id    TEXT,
                title               TEXT,
                artist              TEXT,
                album               TEXT,
                album_artist        TEXT,
                duration_ms         INTEGER,
                isrc                TEXT,
                track_no            INTEGER,
                disc_no             INTEGER,
                year                INTEGER,
                cover_url           TEXT,
                status              TEXT DEFAULT 'pending',
                provider            TEXT,
                chosen_candidate_id INTEGER,
                search_override     TEXT,
                error               TEXT,
                attempts            INTEGER DEFAULT 0,
                progress            REAL DEFAULT 0,
                tmp_path            TEXT,
                final_path          TEXT,
                priority            INTEGER DEFAULT 0,
                created_at          TEXT,
                updated_at          TEXT,
                note                TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gq_status ON grab_queue(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gq_sid ON grab_queue(spotify_track_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS grab_candidates (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_item_id     INTEGER NOT NULL,
                provider          TEXT,
                provider_track_id TEXT,
                title             TEXT,
                artist            TEXT,
                album             TEXT,
                duration_ms       INTEGER,
                isrc              TEXT,
                quality           TEXT,
                score             REAL,
                score_breakdown   TEXT,
                url               TEXT,
                cover_url         TEXT,
                rank              INTEGER,
                FOREIGN KEY (queue_item_id) REFERENCES grab_queue(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gc_item ON grab_candidates(queue_item_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS grab_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_item_id INTEGER NOT NULL,
                event         TEXT,
                detail        TEXT,
                created_at    TEXT,
                FOREIGN KEY (queue_item_id) REFERENCES grab_queue(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ge_item ON grab_events(queue_item_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                service       TEXT UNIQUE NOT NULL,
                access_token  TEXT,
                refresh_token TEXT,
                expires_at    TEXT,
                scope         TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_counters (
                key   TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0
            )
        """)
        # What Music Grabber itself filed. "Managed" == "has a row here"; also the
        # Spotify-id fast path of library matching (docs/plans/extraction.md §3).
        # `rel_path` is relative to the library root so a changed mount point doesn't
        # strand the ledger; `placed_hash` is size:mtime of the file as placed.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS grabbed_files (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                spotify_track_id TEXT,
                isrc             TEXT,
                rel_path         TEXT NOT NULL UNIQUE,
                abs_path         TEXT NOT NULL,
                queue_item_id    INTEGER,
                provider         TEXT,
                output_format    TEXT,
                placed_hash      TEXT,
                placed_at        TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gf_sid ON grabbed_files(spotify_track_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gf_isrc ON grabbed_files(isrc)")
        # The library index cache (docs/plans/extraction.md §2.1). Every LibrarySource
        # loads into this table; matching queries it locally.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS library_tracks (
                path         TEXT PRIMARY KEY,           -- POSIX, relative to the library root
                change_token TEXT,
                source       TEXT,
                title        TEXT,
                artist       TEXT,
                album        TEXT,
                album_artist TEXT,
                duration_ms  INTEGER,
                isrc         TEXT,
                track_no     INTEGER,
                disc_no      INTEGER,
                year         INTEGER,
                norm_title   TEXT,
                norm_artist  TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lt_isrc ON library_tracks(isrc)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lt_norm_artist ON library_tracks(norm_artist)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lt_norm_title ON library_tracks(norm_title)")
