"""Opening an existing database again must work (every restart does it)."""

from music_grabber.db import Database


def test_reopening_a_database_is_a_no_op(tmp_path):
    path = str(tmp_path / "mg.db")
    db = Database(path)
    db.bump_grabbed_total(2)
    for _ in range(3):                       # restart, restart, restart
        db = Database(path)
    assert db.get_grabbed_total() == 2


def test_reopen_keeps_data_and_does_not_reseed_the_counter(tmp_path):
    path = str(tmp_path / "mg.db")
    db = Database(path)
    db.add_playlist("PL1", "Mix")
    db.record_grabbed_file(spotify_track_id="s", isrc="", rel_path="a.mp3", abs_path="/m/a.mp3",
                           queue_item_id=None, provider="x", output_format="mp3", placed_hash="h")
    assert db.get_grabbed_total() == 0       # seeded (from an empty ledger) when the DB was created
    db.bump_grabbed_total(5)
    db = Database(path)
    assert db.get_grabbed_total() == 5       # never re-seeded from the ledger on reopen
    assert [p["spotify_id"] for p in db.list_playlists()] == ["PL1"]
