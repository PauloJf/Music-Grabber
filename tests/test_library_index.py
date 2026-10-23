"""LibraryIndex + FilesystemSource: cache loading, incremental refresh, pruning safety."""

import os

import pytest
from audio_fixtures import write_flac
from mutagen.flac import FLAC

from music_grabber.db import Database
from music_grabber.library import FilesystemSource, LibraryIndex, LibrarySourceError, file_token
from music_grabber.matching import library_match


def _track(root, rel, **tags):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    write_flac(path)
    audio = FLAC(str(path))
    for k, v in tags.items():
        audio[k] = str(v)
    audio.save()
    return path


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    db = Database(str(tmp_path / "mg.db"))
    return root, db, LibraryIndex(db, FilesystemSource(str(root)), root=str(root))


def test_refresh_loads_tags_and_normalized_keys(lib):
    root, db, idx = lib
    _track(root, "Artist/Album/01 - Song.flac", title="Blinding Lights (Remastered)",
           artist="The Weeknd", album="After Hours", isrc="USUG11904206", tracknumber="1/12")
    stats = idx.refresh()
    assert stats["seen"] == 1 and stats["updated"] == 1 and stats["removed"] == 0
    (row,) = db.library_find_by_isrc("usug11904206")        # case-insensitive
    assert row["path"] == "Artist/Album/01 - Song.flac" and row["track_no"] == 1
    assert row["norm_title"] == "blinding lights" and row["norm_artist"] == "the weeknd"
    assert row["duration_ms"] == 1000
    assert db.library_has_path("Artist/Album/01 - Song.flac")


def test_second_refresh_skips_unchanged_and_rereads_changed(lib, monkeypatch):
    root, db, idx = lib
    p = _track(root, "a/x.flac", title="X", artist="A")
    idx.refresh()
    reads = []
    import music_grabber.library as L
    real = L.read_tags
    monkeypatch.setattr(L, "read_tags", lambda f: reads.append(f) or real(f))
    assert idx.refresh()["updated"] == 0 and reads == []     # unchanged → no tag read
    audio = FLAC(str(p)); audio["title"] = "Y"; audio.save()
    os.utime(p, (1, 1))                                      # token changes
    assert idx.refresh()["updated"] == 1 and len(reads) == 1
    assert db.library_find_candidates("a", "y")


def test_is_empty_only_for_a_configured_source_with_no_cached_files(lib, tmp_path):
    root, db, index = lib
    assert index.is_empty()                              # nothing scanned yet
    _track(root, "A/one.flac", title="One", artist="A")
    index.refresh()
    assert not index.is_empty()
    assert not LibraryIndex(db, source=None).is_empty()  # ledger-only: nothing to be wrong about
    (root / "A" / "one.flac").unlink()
    index.refresh()                                      # a scan that finds nothing keeps a populated cache
    assert not index.is_empty()


def test_deleted_files_are_pruned(lib):
    root, db, idx = lib
    for i in range(30):
        _track(root, f"d/{i}.flac", title=f"T{i}", artist="A")
    idx.refresh()
    assert db.library_count() == 30
    for i in range(3):
        (root / "d" / f"{i}.flac").unlink()
    stats = idx.refresh()
    assert stats["removed"] == 3 and db.library_count() == 27


def test_partial_listing_is_treated_as_failed_refresh_and_prunes_nothing(lib):
    """An unmounted share / hiccup must not wipe the cache (and re-queue the library)."""
    root, db, idx = lib
    for i in range(30):
        _track(root, f"d/{i}.flac", title=f"T{i}", artist="A")
    idx.refresh()
    for i in range(20):
        (root / "d" / f"{i}.flac").unlink()
    stats = idx.refresh()
    assert stats["pruned"] is False and stats["removed"] == 0 and db.library_count() == 30


def test_missing_library_dir_raises_and_leaves_cache(tmp_path):
    db = Database(str(tmp_path / "mg.db"))
    db.library_upsert([{"path": "a.mp3", "change_token": "1:1", "source": "filesystem",
                        "norm_title": "a", "norm_artist": "b"}])
    idx = LibraryIndex(db, FilesystemSource(str(tmp_path / "gone")), root=str(tmp_path / "gone"))
    with pytest.raises(LibrarySourceError):
        idx.refresh()
    assert db.library_count() == 1


def test_hidden_files_and_non_audio_are_ignored(lib):
    root, db, idx = lib
    _track(root, "ok.flac", title="Ok", artist="A")
    (root / ".mg_part_1.part").write_bytes(b"x")
    (root / ".hidden.flac").write_bytes(b"x")
    (root / "cover.jpg").write_bytes(b"x")
    (root / ".trash").mkdir()
    (root / ".trash" / "t.flac").write_bytes(b"x")
    assert idx.refresh()["seen"] == 1


def test_matching_end_to_end_isrc_then_fuzzy(lib):
    root, db, idx = lib
    _track(root, "A/B/01 - Song.flac", title="Song", artist="Band", album="LP", isrc="GBAAA0000001")
    idx.refresh()
    by_isrc = {"title": "Totally Different", "artist": "Nobody", "isrc": "GBAAA0000001"}
    assert library_match(by_isrc, idx) == "A/B/01 - Song.flac"
    fuzzy = {"title": "Song", "artist": "Band", "album": "LP", "duration_ms": 1000, "isrc": ""}
    assert library_match(fuzzy, idx) == "A/B/01 - Song.flac"
    assert library_match({"title": "Other", "artist": "Else", "isrc": ""}, idx) is None


def test_file_token_is_size_colon_mtime(tmp_path):
    f = tmp_path / "a"
    f.write_bytes(b"abc")
    os.utime(f, (5, 5))
    assert file_token(str(f)) == "3:5.0"


def test_source_without_a_source_is_a_noop(tmp_path):
    idx = LibraryIndex(Database(str(tmp_path / "mg.db")), None)
    assert idx.refresh()["seen"] == 0
