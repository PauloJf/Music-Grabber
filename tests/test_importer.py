"""Importing grab_* data from a BPM Tagger database: read-only, remapped, idempotent."""

import hashlib
import os
import sqlite3

import pytest

from music_grabber.db import Database
from music_grabber.importer import ImportFailed, import_bpm_tagger

BT_SCHEMA = """
CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT UNIQUE NOT NULL, file_hash TEXT,
    status TEXT, managed INTEGER DEFAULT 0, spotify_track_id TEXT, isrc TEXT, analyzed_at TEXT);
CREATE TABLE playlists (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL DEFAULT 'spotify',
    spotify_id TEXT UNIQUE, navidrome_id TEXT UNIQUE, name TEXT, description TEXT DEFAULT '', pinned INTEGER DEFAULT 0,
    snapshot_id TEXT, enabled INTEGER DEFAULT 1, image_url TEXT, track_count INTEGER DEFAULT 0,
    last_synced_at TEXT, created_at TEXT);
CREATE TABLE playlist_tracks (id INTEGER PRIMARY KEY AUTOINCREMENT, playlist_id INTEGER NOT NULL,
    source_track_id TEXT, spotify_track_id TEXT, position INTEGER, title TEXT, artist TEXT, album TEXT,
    album_artist TEXT, duration_ms INTEGER, isrc TEXT, track_no INTEGER, disc_no INTEGER, year INTEGER,
    cover_url TEXT, added_at TEXT, norm_title TEXT, norm_artist TEXT, match_status TEXT DEFAULT 'unknown',
    matched_file_path TEXT, first_seen_at TEXT, is_new INTEGER DEFAULT 0, removed_at TEXT);
CREATE TABLE grab_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, playlist_track_id INTEGER, spotify_track_id TEXT,
    title TEXT, artist TEXT, album TEXT, album_artist TEXT, duration_ms INTEGER, isrc TEXT, track_no INTEGER,
    disc_no INTEGER, year INTEGER, cover_url TEXT, status TEXT DEFAULT 'pending', provider TEXT,
    chosen_candidate_id INTEGER, search_override TEXT, error TEXT, attempts INTEGER DEFAULT 0,
    progress REAL DEFAULT 0, tmp_path TEXT, final_path TEXT, priority INTEGER DEFAULT 0, created_at TEXT,
    updated_at TEXT, note TEXT);
CREATE TABLE grab_candidates (id INTEGER PRIMARY KEY AUTOINCREMENT, queue_item_id INTEGER NOT NULL, provider TEXT,
    provider_track_id TEXT, title TEXT, artist TEXT, album TEXT, duration_ms INTEGER, isrc TEXT, quality TEXT,
    score REAL, score_breakdown TEXT, url TEXT, cover_url TEXT, rank INTEGER);
CREATE TABLE grab_events (id INTEGER PRIMARY KEY AUTOINCREMENT, queue_item_id INTEGER NOT NULL, event TEXT,
    detail TEXT, created_at TEXT);
CREATE TABLE oauth_tokens (service TEXT UNIQUE NOT NULL, access_token TEXT, refresh_token TEXT, expires_at TEXT, scope TEXT);
CREATE TABLE app_counters (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
"""


def make_source(path):
    c = sqlite3.connect(path)
    c.executescript(BT_SCHEMA)
    c.execute("INSERT INTO playlists (id, source, spotify_id, name, snapshot_id) VALUES (7,'spotify','PL1','Road Trip','snap')")
    c.execute("INSERT INTO playlists (id, source, spotify_id, navidrome_id, name) VALUES (8,'navidrome',NULL,'nd1','Nav')")
    c.execute("INSERT INTO playlists (id, source, name) VALUES (9,'local','Mine')")
    for pid, sid, title in ((7, "s1", "Song One"), (7, "s2", "Song Two"), (8, "nd-song", "Nav Track")):
        c.execute("INSERT INTO playlist_tracks (playlist_id, source_track_id, spotify_track_id, position, title, artist, "
                  "match_status) VALUES (?,?,?,0,?,'A','missing')", (pid, sid, sid if pid == 7 else None, title))
    # 1: done with candidates + events and a chosen candidate; 2: in flight; 3: awaiting_user
    q = [(1, 1, "s1", "Song One", "done", "deezer", "/music/Band/LP/01 - Song One.mp3"),
         (2, 2, "s2", "Song Two", "downloading", None, None),
         (3, None, "s3", "Song Three", "awaiting_user", None, None)]
    for qid, ptid, sid, title, status, prov, final in q:
        c.execute("INSERT INTO grab_queue (id, playlist_track_id, spotify_track_id, title, artist, status, provider, "
                  "final_path, progress, tmp_path, created_at) VALUES (?,?,?,?,'A',?,?,?,0.5,'/data/grab_tmp/x',?)",
                  (qid, ptid, sid, title, status, prov, final, f"2026-01-0{qid}"))
    c.execute("INSERT INTO grab_candidates (id, queue_item_id, provider, provider_track_id, title, score, rank) "
              "VALUES (40, 3, 'deezer', 'dz1', 'Song Three', 0.7, 0), (41, 3, 'ytdlp', 'yt1', 'Song Three (live)', 0.5, 1)")
    c.execute("UPDATE grab_queue SET chosen_candidate_id=41 WHERE id=3")
    c.execute("INSERT INTO grab_events (queue_item_id, event, detail) VALUES (1,'enqueued','Song One'), (1,'done','x'), (3,'awaiting_user','y')")
    c.execute("INSERT INTO oauth_tokens VALUES ('spotify','acc','REFRESH-TOKEN','2099-01-01','scope')")
    c.execute("INSERT INTO app_counters VALUES ('grabbed_total', 41)")
    c.execute("INSERT INTO tracks (file_path, file_hash, status, managed, spotify_track_id, isrc, analyzed_at) VALUES "
              "('/music/Band/LP/01 - Song One.mp3','100:5.0','done',1,'s1','ISRC1','2026-01-01'),"
              "('/music/Band/LP/gone.mp3','1:1','deleted',1,'s9','',NULL),"
              "('/music/Other/own.mp3','2:2','done',0,NULL,NULL,NULL)")
    c.commit()
    c.close()


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "bpm_tagger.db"
    make_source(str(p))
    return str(p)


@pytest.fixture
def target(tmp_path):
    return Database(str(tmp_path / "mg.db"))


def imp(src, target, tmp_path, **kw):
    return import_bpm_tagger(src, target, library_root=str(tmp_path / "lib"), **kw)


def test_imports_spotify_playlists_only_with_their_tracks(src, target, tmp_path):
    rep = imp(src, target, tmp_path)
    assert rep.counts["playlists"] == 1 and rep.counts["playlist_tracks"] == 2
    (pl,) = target.list_playlists()
    assert pl["spotify_id"] == "PL1" and pl["snapshot_id"] == "snap"
    assert [t["title"] for t in target.get_playlist_track_rows(pl["id"])] == ["Song One", "Song Two"]


def test_queue_ids_are_remapped_and_inflight_items_requeued(src, target, tmp_path):
    rep = imp(src, target, tmp_path)
    assert rep.counts["grab_queue"] == 3 and rep.counts["requeued_inflight"] == 1
    items = {i["title"]: i for i in target.get_queue()}
    assert items["Song One"]["status"] == "done" and items["Song One"]["provider"] == "deezer"
    two = items["Song Two"]
    assert two["status"] == "pending" and two["progress"] == 0 and two["tmp_path"] is None
    pl = target.list_playlists()[0]
    pt = {t["title"]: t["id"] for t in target.get_playlist_track_rows(pl["id"])}
    assert items["Song One"]["playlist_track_id"] == pt["Song One"]      # re-pointed at the new row
    assert items["Song Three"]["playlist_track_id"] is None


def test_candidates_events_and_the_chosen_candidate_follow_their_item(src, target, tmp_path):
    imp(src, target, tmp_path)
    three = next(i for i in target.get_queue() if i["title"] == "Song Three")
    cands = target.get_grab_candidates(three["id"])
    assert [c["provider"] for c in cands] == ["deezer", "ytdlp"]
    assert three["chosen_candidate_id"] == cands[1]["id"]                # remapped, not the old id 41
    assert [e["event"] for e in target.get_grab_events(three["id"])] == ["awaiting_user"]
    one = next(i for i in target.get_queue() if i["title"] == "Song One")
    assert [e["event"] for e in target.get_grab_events(one["id"])] == ["enqueued", "done"]


def test_managed_tracks_become_ledger_rows_rerooted(src, target, tmp_path):
    rep = imp(src, target, tmp_path, source_music_dir="/music")
    assert rep.counts["grabbed_files"] == 1 and rep.skipped["ledger_deleted"] == 1
    (row,) = target.find_grabbed_by_spotify_id("s1")
    assert row["rel_path"] == "Band/LP/01 - Song One.mp3"
    assert row["abs_path"] == str(tmp_path / "lib" / "Band" / "LP" / "01 - Song One.mp3")
    assert row["isrc"] == "ISRC1" and row["placed_hash"] == "100:5.0" and row["provider"] == "deezer"
    assert target.find_grabbed_by_spotify_id("s9") == []                 # deleted track not carried over


def test_final_paths_are_rerooted_into_this_library_or_cleared(src, target, tmp_path):
    imp(src, target, tmp_path, source_music_dir="/music")
    one = next(i for i in target.get_queue() if i["title"] == "Song One")
    assert one["final_path"] == str(tmp_path / "lib" / "Band" / "LP" / "01 - Song One.mp3")
    assert next(i for i in target.get_queue() if i["title"] == "Song Three")["final_path"] is None
    other = Database(str(tmp_path / "other.db"))
    imp(src, other, tmp_path, source_music_dir="/srv/elsewhere")          # a path from the other side
    assert next(i for i in other.get_queue() if i["title"] == "Song One")["final_path"] is None


def test_a_wrong_source_music_dir_is_called_out(src, target, tmp_path):
    rep = imp(src, target, tmp_path, source_music_dir="/srv/elsewhere")
    assert any("1 of 1 managed files are not under" in w and "--source-music-dir" in w for w in rep.warnings)
    (row,) = target.find_grabbed_by_spotify_id("s1")
    assert row["rel_path"] == "/music/Band/LP/01 - Song One.mp3"           # kept, not mangled
    target2 = Database(str(tmp_path / "ok.db"))
    ok = imp(src, target2, tmp_path, source_music_dir="/music")
    assert not any("managed files" in w for w in ok.warnings)


def test_a_top_level_folder_that_starts_with_dots_is_still_inside_the_library():
    from music_grabber.importer import _rel
    assert _rel("/music/..Hidden/x.mp3", "/music") == "..Hidden/x.mp3"
    assert _rel("/elsewhere/x.mp3", "/music") == "/elsewhere/x.mp3"


def test_counter_and_token_handling(src, target, tmp_path):
    rep = imp(src, target, tmp_path)
    assert target.get_grabbed_total() == 41
    assert target.get_oauth_token("spotify") is None
    assert any("not imported" in w for w in rep.warnings)
    target2 = Database(str(tmp_path / "mg2.db"))
    imp(src, target2, tmp_path, include_tokens=True)
    assert target2.get_oauth_token("spotify")["refresh_token"] == "REFRESH-TOKEN"


def test_source_is_opened_read_only_and_never_modified(src, target, tmp_path):
    before = (hashlib.sha256(open(src, "rb").read()).hexdigest(), os.stat(src).st_mtime_ns)
    imp(src, target, tmp_path, include_tokens=True)
    imp(src, Database(str(tmp_path / "x.db")), tmp_path, dry_run=True)
    assert (hashlib.sha256(open(src, "rb").read()).hexdigest(), os.stat(src).st_mtime_ns) == before
    leftovers = [f for f in os.listdir(os.path.dirname(src))
                 if f.startswith(os.path.basename(src)) and f != os.path.basename(src)]
    assert leftovers == []                                  # no -wal/-journal/-shm beside the source


def test_dry_run_reports_but_writes_nothing(src, target, tmp_path):
    rep = imp(src, target, tmp_path, dry_run=True)
    assert rep.dry_run and rep.counts["grab_queue"] == 3
    assert target.get_queue() == [] and target.list_playlists() == [] and target.get_grabbed_count() == 0
    assert target.get_grabbed_total() == 0


def test_a_non_empty_target_is_refused_unless_merging(src, target, tmp_path):
    imp(src, target, tmp_path)
    with pytest.raises(ImportFailed, match="already has data"):
        imp(src, target, tmp_path)
    assert len(target.get_queue()) == 3                                  # untouched by the refusal


def test_merge_is_idempotent_and_never_duplicates_children(src, target, tmp_path):
    imp(src, target, tmp_path)
    rep = imp(src, target, tmp_path, merge=True)
    assert "grab_queue" not in rep.counts and rep.skipped["grab_queue"] == 3
    assert "grab_candidates" not in rep.counts and "grab_events" not in rep.counts
    assert len(target.get_queue()) == 3 and target.get_grabbed_count() == 1
    three = next(i for i in target.get_queue() if i["title"] == "Song Three")
    assert len(target.get_grab_candidates(three["id"])) == 2
    assert target.get_grabbed_total() == 41                              # MAX, not a sum


def test_merge_adds_only_new_items(src, target, tmp_path):
    imp(src, target, tmp_path)
    c = sqlite3.connect(src)
    c.execute("INSERT INTO grab_queue (spotify_track_id, title, status, created_at) VALUES ('s4','Song Four','pending','2026-02-01')")
    c.commit()
    c.close()
    rep = imp(src, target, tmp_path, merge=True)
    assert rep.counts["grab_queue"] == 1 and len(target.get_queue()) == 4


def test_not_a_bpm_tagger_database_and_missing_file(tmp_path, target):
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE x (a)").connection.close()
    with pytest.raises(ImportFailed, match="no grab_queue"):
        imp(str(other), target, tmp_path)
    with pytest.raises(ImportFailed, match="no such file"):
        imp(str(tmp_path / "nope.db"), target, tmp_path)


def test_an_old_schema_without_newer_columns_imports(tmp_path, target):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE grab_queue (id INTEGER PRIMARY KEY, spotify_track_id TEXT, title TEXT, artist TEXT,
            status TEXT, created_at TEXT, updated_at TEXT);
        INSERT INTO grab_queue VALUES (1,'s1','Old','A','failed','2025-01-01','2025-01-01');
    """)
    c.commit()
    c.close()
    rep = imp(str(p), target, tmp_path)
    assert rep.counts["grab_queue"] == 1 and any("no playlists" in w for w in rep.warnings)
    assert target.get_queue()[0]["title"] == "Old"


def test_the_cli_import_command_end_to_end(src, tmp_path, monkeypatch, capsys):
    from music_grabber.main import main
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "lib"))
    assert main(["import-bpm-tagger", src, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "3 grab_queue" in out
    assert main(["import-bpm-tagger", src]) == 0
    assert main(["import-bpm-tagger", src]) == 1                         # refuses without --merge
    assert "already has data" in capsys.readouterr().err
    assert main(["import-bpm-tagger", src, "--merge", "--json"]) == 0
