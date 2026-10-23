"""Import an existing BPM Tagger database's grabber data (docs/plans/extraction.md §5.1).

One-way and read-only on the BPM Tagger side: the source is opened with ``mode=ro`` and is
never written to. Idempotent: re-running with ``merge=True`` skips what is already there.

What is copied: Spotify playlists + their tracks, the grab queue with candidates and events,
the lifetime counter, `managed` tracks (as ledger rows, so "already have it" answers stay
stable), and, only on request, the Spotify refresh token (a credential).
"""

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import quote

from .db import Database

log = logging.getLogger(__name__)

INFLIGHT = ("searching", "downloading", "transcoding", "tagging", "analyzing_bpm", "placing")
# BPM Tagger setting key → Music Grabber env var (values that are safe to print).
ENV_MAP = {
    "output_format": "OUTPUT_FORMAT", "path_template": "PATH_TEMPLATE",
    "provider_order": "PROVIDER_ORDER", "deezer_quality": "DEEZER_QUALITY",
    "grab_dry_run": "GRAB_DRY_RUN", "grab_workers": "GRAB_WORKERS",
    "auto_accept_threshold": "AUTO_ACCEPT_THRESHOLD", "ask_threshold": "ASK_THRESHOLD",
    "spotify_sync_minutes": "SPOTIFY_SYNC_MINUTES", "lyrics_enabled": "LYRICS_ENABLED",
    "ntfy_url": "NTFY_URL",
}
# Settings that exist on both sides but mean something else here: BPM Tagger's public address
# is not Music Grabber's, so copying it would point ntfy click-through links at the wrong app.
OWN_ADDRESS = {"ui_public_url": "UI_PUBLIC_URL"}
# Present in BPM Tagger's settings but never echoed: the user copies them over themselves.
SECRET_ENV = {"deezer_arl": "DEEZER_ARL", "ntfy_topic": "NTFY_TOPIC",
              "spotify_client_secret": "SPOTIFY_CLIENT_SECRET"}


class ImportFailed(Exception):
    """The source is not a usable BPM Tagger database, or the target refuses the import."""


@dataclass
class Report:
    dry_run: bool = False
    counts: dict = field(default_factory=dict)
    skipped: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def add(self, key, n=1):
        self.counts[key] = self.counts.get(key, 0) + n

    def skip(self, key, n=1):
        self.skipped[key] = self.skipped.get(key, 0) + n

    def to_dict(self) -> dict:
        return {"dry_run": self.dry_run, "imported": self.counts, "skipped": self.skipped,
                "warnings": self.warnings}


def _open_ro(path: str) -> sqlite3.Connection:
    if not os.path.isfile(path):
        raise ImportFailed(f"no such file: {path}")
    uri = f"file:{quote(os.path.abspath(path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn) -> set:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(conn, table) -> list:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _rows(conn, table, where="", params=()):
    return conn.execute(f"SELECT * FROM {table} {where}", params).fetchall()


def _insert(tconn, table, row: dict, target_cols: list, drop=("id",)):
    cols = [c for c in target_cols if c in row and c not in drop]
    cur = tconn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row[c] for c in cols])
    return cur.lastrowid


def _rel(path: str, source_root: str) -> str:
    """POSIX path relative to ``source_root`` (the BPM Tagger MUSIC_DIR), else the path itself."""
    if not path:
        return path
    try:
        parts = os.path.relpath(path, source_root).split(os.sep)
    except ValueError:
        return path
    return path if parts[0] == ".." else PurePosixPath(*parts).as_posix()


def _reroot(path, source_root: str, library_root: str):
    """``path`` as seen from this app's library, or None when it is not under BPM Tagger's
    MUSIC_DIR (a path from the other side's filesystem would only mislead)."""
    if not path:
        return None
    rel = _rel(path, source_root)
    if PurePosixPath(rel).is_absolute() or PureWindowsPath(rel).drive:
        return None
    return os.path.join(library_root, *PurePosixPath(rel).parts)


def import_bpm_tagger(source_db: str, target: Database, *, library_root: str,
                      source_music_dir: str = "/music", include_tokens: bool = False,
                      dry_run: bool = False, merge: bool = False) -> Report:
    """Copy grabber data from a BPM Tagger DB into ``target``.

    ``source_music_dir`` is BPM Tagger's MUSIC_DIR as it saw it (inside its container);
    ``library_root`` is where the same tree lives for Music Grabber. Managed tracks become
    ledger rows with their paths re-rooted from the one to the other.
    """
    rep = Report(dry_run=dry_run)
    src = _open_ro(source_db)
    try:
        tables = _tables(src)
        if "grab_queue" not in tables:
            raise ImportFailed("this does not look like a BPM Tagger database (no grab_queue table)")
        tconn = target._connect()
        try:
            tconn.execute("PRAGMA foreign_keys=OFF")     # ids are remapped; FKs are re-checked at the end
            if not merge and any(tconn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
                                 for t in ("grab_queue", "playlists", "grabbed_files")):
                raise ImportFailed("the target database already has data; pass merge to add to it")
            tconn.execute("BEGIN")
            _copy(src, tables, tconn, rep, library_root, source_music_dir, include_tokens)
            bad = tconn.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise ImportFailed(f"import would leave {len(bad)} dangling reference(s); rolled back")
            if dry_run:
                tconn.rollback()
            else:
                tconn.commit()
        except BaseException:
            tconn.rollback()
            raise
        finally:
            tconn.close()
    finally:
        src.close()
    return rep


def _copy(src, tables, tconn, rep, library_root, source_music_dir, include_tokens):
    tcols = lambda t: _cols(tconn, t)       # noqa: E731

    # ── playlists (Spotify only) + tracks ─────────────────────────────────────
    pl_map, pt_map = {}, {}
    if "playlists" in tables:
        scols = _cols(src, "playlists")
        where = "WHERE source = 'spotify' AND spotify_id IS NOT NULL" if "source" in scols \
            else "WHERE spotify_id IS NOT NULL"
        for row in map(dict, _rows(src, "playlists", where)):
            have = tconn.execute("SELECT id FROM playlists WHERE spotify_id=?", (row["spotify_id"],)).fetchone()
            if have:
                pl_map[row["id"]] = have["id"]
                rep.skip("playlists")
                continue
            pl_map[row["id"]] = _insert(tconn, "playlists", row, tcols("playlists"))
            rep.add("playlists")
        if "playlist_tracks" in tables and pl_map:
            for row in map(dict, _rows(src, "playlist_tracks")):
                if row["playlist_id"] not in pl_map:
                    continue                     # a navidrome/local playlist's track
                new_pl = pl_map[row["playlist_id"]]
                sid = row.get("source_track_id") or row.get("spotify_track_id")
                have = tconn.execute("SELECT id FROM playlist_tracks WHERE playlist_id=? AND "
                                     "source_track_id=?", (new_pl, sid)).fetchone()
                if have:
                    pt_map[row["id"]] = have["id"]
                    rep.skip("playlist_tracks")
                    continue
                old_id = row["id"]
                row = {**row, "playlist_id": new_pl, "source_track_id": sid}
                pt_map[old_id] = _insert(tconn, "playlist_tracks", row, tcols("playlist_tracks"))
                rep.add("playlist_tracks")
    else:
        rep.warnings.append("source has no playlists table; playlists were not imported")

    # ── queue, candidates, events ─────────────────────────────────────────────
    q_map, c_map, fresh = {}, {}, set()      # fresh: queue items this import created
    for row in map(dict, _rows(src, "grab_queue", "ORDER BY id")):
        old = row["id"]
        dup = tconn.execute("SELECT id FROM grab_queue WHERE IFNULL(spotify_track_id,'')=? AND "
                            "IFNULL(created_at,'')=? AND IFNULL(title,'')=?",
                            (row.get("spotify_track_id") or "", row.get("created_at") or "",
                             row.get("title") or "")).fetchone()
        if dup:
            q_map[old] = dup["id"]
            rep.skip("grab_queue")
            continue
        if row.get("status") in INFLIGHT:
            row.update(status="pending", progress=0)
            rep.add("requeued_inflight")
        row["playlist_track_id"] = pt_map.get(row.get("playlist_track_id"))
        row["chosen_candidate_id"] = None
        row["tmp_path"] = None
        row["final_path"] = _reroot(row.get("final_path"), source_music_dir, library_root)
        q_map[old] = _insert(tconn, "grab_queue", row, tcols("grab_queue"))
        fresh.add(q_map[old])
        rep.add("grab_queue")
    # Children are copied only for items this import created, so a merge never duplicates them.
    if "grab_candidates" in tables:
        for row in map(dict, _rows(src, "grab_candidates", "ORDER BY id")):
            new_q = q_map.get(row["queue_item_id"])
            if new_q not in fresh:
                if new_q is not None:
                    rep.skip("grab_candidates")
                continue
            old = row["id"]
            row["queue_item_id"] = new_q
            c_map[old] = _insert(tconn, "grab_candidates", row, tcols("grab_candidates"))
            rep.add("grab_candidates")
        for old_q, new_q in q_map.items():
            if new_q not in fresh:
                continue
            r = src.execute("SELECT chosen_candidate_id FROM grab_queue WHERE id=?", (old_q,)).fetchone()
            if r and r[0] in c_map:
                tconn.execute("UPDATE grab_queue SET chosen_candidate_id=? WHERE id=?", (c_map[r[0]], new_q))
    if "grab_events" in tables:
        for row in map(dict, _rows(src, "grab_events", "ORDER BY id")):
            new_q = q_map.get(row["queue_item_id"])
            if new_q not in fresh:
                continue
            row["queue_item_id"] = new_q
            _insert(tconn, "grab_events", row, tcols("grab_events"))
            rep.add("grab_events")

    # ── token (opt-in), counter ───────────────────────────────────────────────
    if "oauth_tokens" in tables:
        tok = src.execute("SELECT * FROM oauth_tokens WHERE service='spotify'").fetchone()
        if tok and include_tokens:
            tconn.execute("INSERT INTO oauth_tokens (service, access_token, refresh_token, expires_at, scope) "
                          "VALUES ('spotify',?,?,?,?) ON CONFLICT(service) DO UPDATE SET "
                          "access_token=excluded.access_token, refresh_token=excluded.refresh_token, "
                          "expires_at=excluded.expires_at, scope=excluded.scope",
                          (tok["access_token"], tok["refresh_token"], tok["expires_at"], tok["scope"]))
            rep.add("oauth_token")
        elif tok:
            rep.warnings.append("Spotify token not imported (pass include_tokens, or run spotify-login)")
    if "app_counters" in tables:
        r = src.execute("SELECT value FROM app_counters WHERE key='grabbed_total'").fetchone()
        if r:
            tconn.execute("INSERT INTO app_counters (key, value) VALUES ('grabbed_total', ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = MAX(value, excluded.value)", (int(r[0]),))
            rep.add("grabbed_total", int(r[0]))

    # ── managed tracks → ledger ───────────────────────────────────────────────
    if "tracks" in tables and "managed" in _cols(src, "tracks"):
        providers = {r["final_path"]: r["provider"] for r in
                     src.execute("SELECT final_path, provider FROM grab_queue WHERE final_path IS NOT NULL")}
        scols = _cols(src, "tracks")
        outside = managed = 0
        for t in map(dict, src.execute("SELECT * FROM tracks WHERE managed = 1")):
            if t.get("status") == "deleted":
                rep.skip("ledger_deleted")
                continue
            rel = _rel(t["file_path"], source_music_dir)
            managed += 1
            if PurePosixPath(rel).is_absolute() or PureWindowsPath(rel).drive:
                outside += 1                     # not under source_music_dir: kept as it was
            abs_path = os.path.join(library_root, *PurePosixPath(rel).parts) if not os.path.isabs(rel) \
                else rel
            if tconn.execute("SELECT 1 FROM grabbed_files WHERE rel_path=?", (rel,)).fetchone():
                rep.skip("grabbed_files")
                continue
            tconn.execute("INSERT INTO grabbed_files (spotify_track_id, isrc, rel_path, abs_path, provider, "
                          "placed_hash, placed_at) VALUES (?,?,?,?,?,?,?)",
                          (t.get("spotify_track_id") or None, t.get("isrc") or None, rel, abs_path,
                           providers.get(t["file_path"]), t.get("file_hash") if "file_hash" in scols else None,
                           t.get("analyzed_at")))
            rep.add("grabbed_files")
        if outside:
            rep.warnings.append(
                f"{outside} of {managed} managed files are not under the source music dir "
                f"'{source_music_dir}' and keep their original path in the ledger; if that is "
                "not intended, check --source-music-dir (BPM Tagger's MUSIC_DIR as it saw it)")


def env_from_settings(settings_path: str) -> dict:
    """Env lines for a Music Grabber config, from a BPM Tagger settings.json. Secrets are
    never read into the output: they appear as names only, for the user to copy by hand."""
    try:
        with open(settings_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise ImportFailed(f"cannot read {settings_path}: {exc}") from None
    env = {}
    for key, var in ENV_MAP.items():
        if key in data and data[key] not in (None, ""):
            v = data[key]
            if key == "provider_order":
                v = ",".join(p for p in str(v).split(",") if p.strip().lower() in ("deezer", "ytdlp"))
            env[var] = str(v).lower() if isinstance(v, bool) else str(v)
    present = [var for key, var in SECRET_ENV.items() if data.get(key)]
    own = [var for key, var in OWN_ADDRESS.items() if data.get(key)]
    return {"env": env, "copy_by_hand": present, "set_to_your_own": own}
