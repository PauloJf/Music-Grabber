"""The diff-based playlist sync (new / removed / re-added tombstone lifecycle).

Ported from BPM Tagger's test_playlist_sync.py (the Spotify half). These exercise the DB
layer directly: no Spotify needed."""

import pytest

from music_grabber.db import Database


def _track(sid, title, position=0, **kw):
    """A minimal source track as sync_playlist_tracks() consumes it."""
    return {"source_track_id": sid, "spotify_track_id": sid, "position": position,
            "title": title, "artist": kw.get("artist", "A"), "album": "",
            "album_artist": "", "duration_ms": 200000, "isrc": "", "track_no": position,
            "disc_no": 1, "year": 2020, "cover_url": "", "added_at": "",
            "norm_title": title.lower(), "norm_artist": "a"}


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "mg.db"))


def _by_title(db, pid):
    return {t["title"]: t for t in db.get_playlist_tracks(pid)}


# ── diff sync ──────────────────────────────────────────────────────────────

def test_initial_sync_marks_all_new(db):
    pid = db.add_playlist("PL1", "P")
    added, removed = db.sync_playlist_tracks(pid, [_track("a", "A", 0), _track("b", "B", 1)])
    assert (added, removed) == (2, 0)
    rows = _by_title(db, pid)
    assert set(rows) == {"A", "B"}
    assert all(r["is_new"] for r in rows.values())
    assert all(r["removed_at"] is None for r in rows.values())


def test_mark_seen_clears_new(db):
    pid = db.add_playlist("PL1", "P")
    db.sync_playlist_tracks(pid, [_track("a", "A")])
    db.mark_playlist_seen(pid)
    assert all(not r["is_new"] for r in _by_title(db, pid).values())


def test_removed_track_is_tombstoned_not_deleted(db):
    pid = db.add_playlist("PL1", "P")
    db.sync_playlist_tracks(pid, [_track("a", "A", 0), _track("b", "B", 1)])
    db.mark_playlist_seen(pid)

    added, removed = db.sync_playlist_tracks(pid, [_track("a", "A", 0), _track("c", "C", 1)])
    assert (added, removed) == (1, 1)

    rows = _by_title(db, pid)
    # B stays visible as a tombstone; C is the new one; A unchanged.
    assert rows["B"]["derived_status"] == "removed" and rows["B"]["removed_at"]
    assert rows["C"]["derived_status"] != "removed" and rows["C"]["is_new"]
    assert not rows["A"]["is_new"]

    # Live rows (matching / m3u) exclude the tombstone.
    live = {r["title"] for r in db.get_playlist_track_rows(pid)}
    assert live == {"A", "C"}

    # Coverage counts exclude the tombstone; new/removed surfaced separately.
    pl = next(p for p in db.list_playlists() if p["id"] == pid)
    assert pl["indexed_count"] == 2
    assert pl["new_count"] == 1 and pl["removed_count"] == 1


def test_tombstone_cleared_on_next_sync(db):
    pid = db.add_playlist("PL1", "P")
    db.sync_playlist_tracks(pid, [_track("a", "A"), _track("b", "B")])
    db.sync_playlist_tracks(pid, [_track("a", "A")])          # B tombstoned
    assert _by_title(db, pid)["B"]["derived_status"] == "removed"
    db.sync_playlist_tracks(pid, [_track("a", "A")])          # B still gone → purged
    assert "B" not in _by_title(db, pid)


def test_readded_track_revives_same_row(db):
    pid = db.add_playlist("PL1", "P")
    db.sync_playlist_tracks(pid, [_track("a", "A"), _track("b", "B")])
    orig_id = _by_title(db, pid)["B"]["id"]
    db.mark_playlist_seen(pid)

    db.sync_playlist_tracks(pid, [_track("a", "A")])          # B tombstoned
    db.sync_playlist_tracks(pid, [_track("a", "A"), _track("b", "B")])  # B re-added

    b = _by_title(db, pid)["B"]
    assert b["id"] == orig_id          # revived, not reinserted
    assert b["removed_at"] is None and b["is_new"]


def test_duplicate_source_ids_collapse(db):
    pid = db.add_playlist("PL1", "P")
    added, _ = db.sync_playlist_tracks(pid, [_track("a", "A", 0), _track("a", "A", 5)])
    assert added == 1
    assert len(db.get_playlist_track_rows(pid)) == 1


def test_match_state_preserved_across_sync(db):
    pid = db.add_playlist("PL1", "P")
    db.sync_playlist_tracks(pid, [_track("a", "A")])
    row = db.get_playlist_track_rows(pid)[0]
    db.set_playlist_track_match(row["id"], "have", "/music/a.mp3")

    db.sync_playlist_tracks(pid, [_track("a", "A", position=3)])   # metadata changes
    row2 = db.get_playlist_track_rows(pid)[0]
    assert row2["match_status"] == "have"
    assert row2["matched_file_path"] == "/music/a.mp3"
    assert row2["position"] == 3
