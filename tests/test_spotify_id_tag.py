"""Q3: the Spotify track id written into grabbed files, read back by the filesystem index,
and the ledger rebuilt from it (docs/plans/q3-q5-design.md). No network, no audio libraries."""

import os
import sqlite3

import pytest
from audio_fixtures import minimal_mp3_bytes, write_flac

from music_grabber.db import Database
from music_grabber.library import FilesystemSource, LibraryIndex, read_tags
from music_grabber.matching import library_match
from music_grabber.ops import Ops, OpsError
from music_grabber.sync_engine import GrabberService
from music_grabber.tagging import write_track_tags

META = {"title": "Song", "artist": "Band", "album": "LP", "album_artist": "Band", "track_no": 1,
        "isrc": "GBAAA2400001", "spotify_track_id": "sp123"}


def _file(path, kind):
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "flac":
        write_flac(path)
    else:
        path.write_bytes(minimal_mp3_bytes())
    return str(path)


@pytest.mark.parametrize("kind", ["mp3", "flac"])
def test_the_id_round_trips_in_every_format_and_the_mtime_is_kept(tmp_path, kind):
    p = _file(tmp_path / f"a.{kind}", kind)
    os.utime(p, (1_700_000_000, 1_700_000_000))
    assert write_track_tags(p, META) is None
    tags = read_tags(p)
    assert tags["spotify_track_id"] == "sp123" and tags["isrc"] == "GBAAA2400001" and tags["title"] == "Song"
    assert int(os.stat(p).st_mtime) == 1_700_000_000


@pytest.mark.parametrize("kind", ["mp3", "flac"])
def test_no_id_is_written_when_the_flag_is_off_or_there_is_none(tmp_path, kind):
    off = _file(tmp_path / f"off.{kind}", kind)
    write_track_tags(off, META, spotify_id_tag=False)
    assert read_tags(off)["spotify_track_id"] is None and read_tags(off)["title"] == "Song"
    none = _file(tmp_path / f"none.{kind}", kind)
    write_track_tags(none, {**META, "spotify_track_id": ""})
    assert read_tags(none)["spotify_track_id"] is None


def test_the_worker_passes_the_setting_to_the_tag_writer(tmp_path, monkeypatch):
    from test_worker import FakeProvider, _config, _enqueue

    from music_grabber import worker
    from music_grabber.worker import GrabPipeline
    seen = []
    monkeypatch.setattr(worker, "write_track_tags", lambda p, m, **kw: seen.append(kw) or None)
    for flag in (None, False):
        cfg = _config(tmp_path / str(flag), **({} if flag is None else {"write_spotify_id_tag": flag}))
        db = Database(cfg["db_path"])
        item = _enqueue(db)
        GrabPipeline(cfg, db, providers=[FakeProvider()]).process_item(db.get_grab_item(item))
    assert seen == [{"spotify_id_tag": True}, {"spotify_id_tag": False}]       # default on


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    db = Database(str(tmp_path / "mg.db"))
    return root, db, LibraryIndex(db, FilesystemSource(str(root)), root=str(root))


def test_a_moved_grabbed_file_is_still_have(lib):
    root, db, index = lib
    p = _file(root / "Band" / "LP" / "01 - Song.flac", "flac")
    write_track_tags(p, META)
    db.record_grabbed_file(spotify_track_id="sp123", isrc="", rel_path="Band/LP/01 - Song.flac", abs_path=p,
                           queue_item_id=None, provider="x", output_format="flac", placed_hash="")
    index.refresh()
    sp = {"spotify_track_id": "sp123", "title": "Totally Different", "artist": "Nobody", "isrc": ""}
    assert library_match(sp, index) == "Band/LP/01 - Song.flac"             # ledger answers
    moved = root / "Elsewhere" / "renamed.flac"
    moved.parent.mkdir()
    os.replace(p, moved)                                                    # ledger path is now dead
    index.refresh()
    assert library_match(sp, index) == "Elsewhere/renamed.flac"             # the file's own tag answers
    assert library_match({**sp, "spotify_track_id": "other"}, index) is None


def test_an_old_database_gets_the_column(tmp_path):
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE library_tracks (path TEXT PRIMARY KEY, change_token TEXT, source TEXT, title TEXT, "
              "artist TEXT, album TEXT, album_artist TEXT, duration_ms INTEGER, isrc TEXT, track_no INTEGER, "
              "disc_no INTEGER, year INTEGER, norm_title TEXT, norm_artist TEXT)")
    c.execute("INSERT INTO library_tracks (path, title) VALUES ('a.flac', 'Kept')")
    c.commit()
    c.close()
    db = Database(path)
    assert db.library_count() == 1 and db.library_find_by_spotify_id("x") == []
    Database(path)                                                          # reopening is harmless


@pytest.fixture
def ops(lib, tmp_path):
    root, db, index = lib
    cfg = {"db_path": str(tmp_path / "mg.db"), "music_dir": str(root), "provider_order": "ytdlp",
           "output_format": "mp3-128", "auto_accept_threshold": 0.85, "ask_threshold": 0.55,
           "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}"}
    svc = GrabberService(cfg, db, index=index)
    svc.pool.pipeline.providers = []
    return Ops(svc, cfg), root, db


def test_ledger_rebuild_adds_rows_for_tagged_files_only_and_is_idempotent(ops):
    o, root, db = ops
    tagged = _file(root / "A" / "one.flac", "flac")
    write_track_tags(tagged, META)
    write_track_tags(_file(root / "A" / "plain.flac", "flac"), {**META, "spotify_track_id": ""})
    before = open(tagged, "rb").read()
    r = o.ledger_rebuild()
    assert r["tagged_files"] == 1 and r["added"] == 1
    (row,) = db.find_grabbed_by_spotify_id("sp123")
    assert row["rel_path"] == "A/one.flac" and row["isrc"] == "GBAAA2400001"
    assert row["abs_path"] == os.path.join(str(root), "A", "one.flac")
    assert o.ledger_rebuild()["added"] == 0 and db.get_grabbed_count() == 1
    assert open(tagged, "rb").read() == before                              # the file is never touched


def test_ledger_rebuild_never_changes_an_existing_row(ops):
    o, root, db = ops
    p = _file(root / "A" / "one.flac", "flac")
    write_track_tags(p, META)
    db.record_grabbed_file(spotify_track_id="sp123", isrc="", rel_path="A/one.flac", abs_path=p, queue_item_id=7,
                           provider="deezer", output_format="flac", placed_hash="h")
    assert o.ledger_rebuild()["already_in_ledger"] == 1
    (row,) = db.find_grabbed_by_spotify_id("sp123")
    assert row["provider"] == "deezer" and row["queue_item_id"] == 7


def test_ledger_rebuild_needs_the_filesystem_index(tmp_path):
    cfg = {"db_path": str(tmp_path / "mg.db"), "music_dir": str(tmp_path), "provider_order": "ytdlp",
           "output_format": "mp3-128", "auto_accept_threshold": 0.85, "ask_threshold": 0.55,
           "path_template": "{Title}.{ext}"}
    db = Database(cfg["db_path"])
    svc = GrabberService(cfg, db, index=LibraryIndex(db, None))
    with pytest.raises(OpsError) as e:
        Ops(svc, cfg).ledger_rebuild()
    assert e.value.code == "unsupported"
