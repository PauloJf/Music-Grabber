"""delete_grab(): removing a queue item also removes its candidates and events.

The Delete action on the queue page (failed/skipped rows) drops the bookkeeping
row and its children in one transaction — explicitly, so it's correct on older
databases that predate ON DELETE CASCADE.
"""

from music_grabber.db import Database
from music_grabber import matching as m
from music_grabber.library import LibraryIndex


def _db(tmp_path):
    return Database(str(tmp_path / "mg.db"))


def _enqueue(db, title="song"):
    return db.enqueue_grab({"title": title, "artist": "A", "spotify_track_id": f"sp_{title}"})


def test_delete_grab_removes_item_and_children(tmp_path):
    db = _db(tmp_path)
    item_id = _enqueue(db)
    db.add_grab_candidates(item_id, [{"provider": "deezer", "title": "song", "rank": 0}])
    db.add_grab_event(item_id, "failed", "no provider match")
    assert db.get_grab_item(item_id) is not None
    assert db.get_grab_candidates(item_id)
    assert db.get_grab_events(item_id)

    assert db.delete_grab(item_id) is True

    assert db.get_grab_item(item_id) is None
    assert db.get_grab_candidates(item_id) == []
    assert db.get_grab_events(item_id) == []


def test_delete_grab_missing_returns_false(tmp_path):
    db = _db(tmp_path)
    assert db.delete_grab(999999) is False


def test_delete_completed_grabs_only_removes_done(tmp_path):
    db = _db(tmp_path)
    d, f = _enqueue(db, "done1"), _enqueue(db, "fail1")
    db.transition(d, "done", "filed")
    db.transition(f, "failed", "no match")
    db.add_grab_event(d, "info", "extra")

    removed = db.delete_completed_grabs()

    assert removed == 1
    assert db.get_grab_item(d) is None
    assert db.get_grab_events(d) == []
    # Failed item and its history survive.
    assert db.get_grab_item(f) is not None


def test_delete_completed_grabs_none_is_zero(tmp_path):
    db = _db(tmp_path)
    _enqueue(db, "still-pending")
    assert db.delete_completed_grabs() == 0


def test_grabbed_total_counter_survives_clear(tmp_path):
    db = _db(tmp_path)
    assert db.get_grabbed_total() == 0
    db.bump_grabbed_total()
    db.bump_grabbed_total()
    assert db.get_grabbed_total() == 2
    # A completed item then cleared — the lifetime tally must not drop.
    d = _enqueue(db, "done1")
    db.transition(d, "done", "filed")
    db.delete_completed_grabs()
    assert db.get_grabbed_total() == 2


def _ledger(db, i, sid=None, root="/music"):
    db.record_grabbed_file(spotify_track_id=sid or f"sp{i}", isrc="", rel_path=f"g{i}.mp3",
                           abs_path=f"{root}/g{i}.mp3", queue_item_id=None, provider="fake",
                           output_format="mp3-128", placed_hash=f"h{i}")


def test_grabbed_total_seeds_from_ledger_when_counter_is_missing(tmp_path):
    path = str(tmp_path / "mg.db")
    db = Database(path)
    for i in range(3):
        _ledger(db, i)
    # A database whose counter row is gone (e.g. imported): reopening re-seeds
    # grabbed_total from the ledger so the lifetime tally starts meaningful.
    with db._connect() as conn:
        conn.execute("DELETE FROM app_counters")
        conn.commit()
    assert Database(path).get_grabbed_total() == 3


def test_grabbed_track_still_matches_after_completed_cleared(tmp_path):
    """A grabbed file is recognised as 'have' by its ledger-stamped spotify_track_id even
    with no ISRC and a title/artist that would never fuzzy-match, so clearing the
    completed queue row can never cause the sync to re-grab it."""
    music = tmp_path / "music"
    music.mkdir()
    (music / "g.mp3").write_bytes(b"x")
    db = _db(tmp_path)
    db.record_grabbed_file(spotify_track_id="sp_keep", isrc="", rel_path="g.mp3",
                           abs_path=str(music / "g.mp3"), queue_item_id=None, provider="fake",
                           output_format="mp3-128", placed_hash="h1")
    idx = LibraryIndex(db, None, root=str(music))
    assert idx.find_by_spotify_id("sp_keep")[0]["file_path"] == "g.mp3"

    # The Spotify-side metadata (what a sync reconciles) differs entirely, yet the
    # stamped id still resolves it to the grabbed file.
    sp = {"title": "Proper Title", "artist": "Proper Artist", "album": None,
          "duration_ms": 200000, "isrc": "", "spotify_track_id": "sp_keep",
          "norm_title": m.normalize_title("Proper Title"),
          "norm_artist": m.normalize_artist("Proper Artist")}
    assert m.library_match(sp, idx) == "g.mp3"


def test_ledger_hit_for_a_deleted_file_is_not_a_match(tmp_path):
    """Delete a grabbed track and the next sync must see it as missing again."""
    db = _db(tmp_path)
    _ledger(db, 1, sid="sp_gone", root=str(tmp_path / "music"))   # file never created
    idx = LibraryIndex(db, None, root=str(tmp_path / "music"))
    assert idx.find_by_spotify_id("sp_gone") == []
    sp = {"title": "Zzz", "artist": "Nobody", "spotify_track_id": "sp_gone", "isrc": ""}
    assert m.library_match(sp, idx) is None


def test_delete_grab_leaves_siblings_intact(tmp_path):
    db = _db(tmp_path)
    a, b = _enqueue(db, "a"), _enqueue(db, "b")
    db.add_grab_event(a, "failed", "a-fail")
    db.add_grab_event(b, "failed", "b-fail")

    db.delete_grab(a)

    assert db.get_grab_item(a) is None
    assert db.get_grab_item(b) is not None
    assert any(e["detail"] == "b-fail" for e in db.get_grab_events(b))
