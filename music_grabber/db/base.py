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
        self._restrict_permissions()

    def _restrict_permissions(self) -> None:
        """The database holds the Spotify refresh token: owner read/write only (best effort)."""
        for suffix in ("", "-wal", "-shm"):
            try:
                if os.path.exists(self.db_path + suffix):
                    os.chmod(self.db_path + suffix, 0o600)
            except OSError:  # pragma: no cover - read-only mounts, foreign owners
                pass

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
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'user',
                prefs         TEXT NOT NULL DEFAULT '{}',
                created_at    TEXT,
                last_login    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                created_at TEXT,
                expires_at TEXT NOT NULL,
                last_seen  TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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
                norm_artist  TEXT,
                spotify_track_id TEXT                     -- our own file tag (Q3), filesystem source only
            )
        """)
        if "spotify_track_id" not in {r[1] for r in conn.execute("PRAGMA table_info(library_tracks)")}:
            conn.execute("ALTER TABLE library_tracks ADD COLUMN spotify_track_id TEXT")   # pre-Q3 database
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lt_spotify_id ON library_tracks(spotify_track_id)")
        # Navidrome playlist mirror (Q5): a per-playlist switch and what we last wrote.
        if "mirror" not in {r[1] for r in conn.execute("PRAGMA table_info(playlists)")}:
            conn.execute("ALTER TABLE playlists ADD COLUMN mirror INTEGER DEFAULT 1")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlist_mirrors (
                playlist_id  INTEGER PRIMARY KEY REFERENCES playlists(id) ON DELETE CASCADE,
                remote_id    TEXT,
                remote_name  TEXT,
                paths_hash   TEXT,
                content_hash TEXT,
                matched      INTEGER DEFAULT 0,
                unresolved   INTEGER DEFAULT 0,
                state        TEXT,
                note         TEXT,
                last_error   TEXT,
                synced_at    TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lt_isrc ON library_tracks(isrc)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lt_norm_artist ON library_tracks(norm_artist)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lt_norm_title ON library_tracks(norm_title)")
        # Integration API (docs/plans/integration-and-suggestions.md, part 1): scoped keys for
        # other apps (stored hashed), and where a queue item came from.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL UNIQUE COLLATE NOCASE,
                key_hash     TEXT NOT NULL UNIQUE,
                scopes       TEXT NOT NULL,
                created_by   TEXT,
                created_at   TEXT,
                last_used_at TEXT,
                revoked_at   TEXT
            )
        """)
        queue_cols = {r[1] for r in conn.execute("PRAGMA table_info(grab_queue)")}
        for col in ("requested_by", "source_ref"):
            if col not in queue_cols:
                conn.execute(f"ALTER TABLE grab_queue ADD COLUMN {col} TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gq_source ON grab_queue(requested_by, source_ref)")
        # New releases (docs/plans/integration-and-suggestions.md, part 2). artist_map answers
        # "which Deezer artist is this library artist?" (a row with no dz_artist_id caches "no
        # confident match"); artist_checks and release_cache hold what Deezer last said; the prefs
        # and dismissals are per user (user 0 is the API token).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS artist_map (
                norm_name    TEXT PRIMARY KEY,
                name         TEXT,
                dz_artist_id TEXT,
                confidence   REAL,
                source       TEXT,
                active       INTEGER DEFAULT 0,
                mapped_at    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS artist_checks (
                dz_artist_id TEXT PRIMARY KEY,
                name         TEXT,
                checked_at   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS release_cache (
                dz_artist_id  TEXT NOT NULL,
                dz_album_id   TEXT NOT NULL,
                title         TEXT,
                kind          TEXT,
                release_date  TEXT,
                cover_url     TEXT,
                artist_name   TEXT,
                first_seen_at TEXT,
                PRIMARY KEY (dz_artist_id, dz_album_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rc_date ON release_cache(release_date)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS artist_prefs (
                user_id          INTEGER NOT NULL,
                dz_artist_id     TEXT NOT NULL,
                name             TEXT,
                follow           INTEGER DEFAULT 0,
                hide_releases    INTEGER DEFAULT 0,
                hide_suggestions INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, dz_artist_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS item_dismissals (
                user_id INTEGER NOT NULL,
                kind    TEXT NOT NULL,
                ref     TEXT NOT NULL,
                PRIMARY KEY (user_id, kind, ref)
            )
        """)
