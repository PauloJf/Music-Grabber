"""The whole loop, with every outside world faked: sync → queue → workers → placement →
ledger → the next sync sees the files → restart. These catch what unit tests of the parts miss
(the first cut crashed on its second start)."""

import os
import time

from test_sync_engine import FakeClient, Notifier, _t

from music_grabber.analysis import IntakeFolderAnalysis
from music_grabber.db import Database
from music_grabber.providers.base import DownloadedFile, Provider, ProviderCandidate
from music_grabber.sync_engine import GrabberService


class Perfect(Provider):
    """Always finds an exact match and 'downloads' a few bytes of mp3."""
    name = "fake"

    def __init__(self):
        self.downloads = []

    def search(self, meta, limit=8):
        return [ProviderCandidate(provider="fake", provider_track_id=meta.title, title=meta.title,
                                  artist=meta.artist, album=meta.album, duration_ms=meta.duration_ms,
                                  isrc=meta.isrc, quality="MP3_128")]

    def download(self, cand, dest_dir, progress_cb=None):
        self.downloads.append(cand.provider_track_id)
        p = os.path.join(dest_dir, "src.mp3")
        with open(p, "wb") as fh:
            fh.write(b"\x00" * 128)
        return DownloadedFile(path=p, ext="mp3", provider="fake")


def make(tmp_path, **cfg):
    c = {"db_path": str(tmp_path / "data" / "mg.db"), "music_dir": str(tmp_path / "music"),
         "output_format": "mp3-128", "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}",
         "auto_accept_threshold": 0.85, "ask_threshold": 0.55, "provider_order": "", "grab_workers": 3}
    c.update(cfg)
    os.makedirs(c["music_dir"], exist_ok=True)
    db = Database(c["db_path"])
    return c, db


def service(c, db, tracks, provider=None, **kw):
    svc = GrabberService(c, db, notifier=kw.pop("notifier", None), **kw)
    svc.client = FakeClient(tracks)
    svc.sync.client = svc.client
    svc.pool.pipeline.providers = [provider or Perfect()]
    return svc


def drain(svc, limit=50):
    """Run the queue to empty on this thread (what the pool threads do in a loop)."""
    n = 0
    while n < limit:
        item = svc.db.claim_next_grab()
        if not item:
            return n
        svc.pool.pipeline.process_item(item)
        n += 1
    raise AssertionError("queue never drained")


PLAYLIST = [_t(f"s{i}", f"Song {i}", "The Band", position=i, isrc=f"GBAAA000000{i}") for i in range(1, 5)]


def test_full_cycle_then_restart_never_regrabs(tmp_path):
    c, db = make(tmp_path)
    db.add_playlist("PL1", "Mix")
    # One of the four tracks is already in the library (matched by ISRC from its tags).
    from audio_fixtures import write_flac
    from mutagen.flac import FLAC
    owned = tmp_path / "music" / "Old" / "Collection" / "01 - Song 1.flac"
    owned.parent.mkdir(parents=True)
    write_flac(owned)
    a = FLAC(str(owned))
    a["title"], a["artist"], a["isrc"] = "Song 1", "The Band", "GBAAA0000001"
    a.save()

    prov = Perfect()
    svc = service(c, db, PLAYLIST, prov)
    svc.sync.sync_all()                                       # reconcile → queue the 3 missing
    assert sorted(i["title"] for i in db.get_queue()) == ["Song 2", "Song 3", "Song 4"]
    assert db.get_playlist_track_rows(1)[0]["match_status"] == "have"

    assert drain(svc) == 3
    assert sorted(prov.downloads) == ["Song 2", "Song 3", "Song 4"]
    placed = sorted(os.path.relpath(os.path.join(d, f), c["music_dir"])
                    for d, _s, fs in os.walk(c["music_dir"]) for f in fs if f.endswith(".mp3"))
    assert len(placed) == 3 and all(p.startswith("The Band/") for p in placed)
    assert db.get_grabbed_count() == 3 and db.get_grabbed_total() == 3
    assert {i["status"] for i in db.get_queue()} == {"done"}

    svc.sync.sync_all()                                       # the library changed, not the playlist
    assert {r["match_status"] for r in db.get_playlist_track_rows(1)} == {"have"}
    assert drain(svc) == 0 and len(prov.downloads) == 3        # nothing re-grabbed

    # Restart: a new process opens the same files. No crash, no state lost, still no re-grab.
    db2 = Database(c["db_path"])
    prov2 = Perfect()
    svc2 = service(c, db2, PLAYLIST, prov2)
    svc2.pool.start()                                          # startup recovery runs here
    svc2.pool.stop()
    svc2.pool.join(5)
    svc2.sync.sync_all()
    assert drain(svc2) == 0 and prov2.downloads == []
    assert db2.get_grabbed_total() == 3 and db2.get_grabbed_count() == 3


def test_deleting_a_grabbed_file_requeues_it_on_the_next_sync(tmp_path):
    c, db = make(tmp_path)
    db.add_playlist("PL1", "Mix")
    svc = service(c, db, PLAYLIST[:1])
    svc.sync.sync_all()
    drain(svc)
    svc.sync.sync_all()                                       # the index sees the file, as the next sync would
    (row,) = db.find_grabbed_by_spotify_id("s1")
    os.remove(row["abs_path"])
    for it in db.get_queue():
        db.delete_grab(it["id"])                              # history cleared, as a user might
    svc.sync.sync_all()
    assert [i["status"] for i in db.get_queue()] == ["pending"]    # missing again, so re-grabbed


def test_intake_mode_files_into_the_intake_folder_and_the_library_still_sees_it(tmp_path):
    intake = str(tmp_path / "music" / "Music-raw")
    c, db = make(tmp_path)
    db.add_playlist("PL1", "Mix")
    svc = service(c, db, PLAYLIST[:2], analysis=IntakeFolderAnalysis(intake))
    svc.sync.sync_all()
    drain(svc)
    files = [os.path.join(d, f) for d, _s, fs in os.walk(intake) for f in fs]
    assert len(files) == 2 and all(f.endswith(".mp3") and ".mg_part" not in f for f in files)
    assert {os.path.relpath(r["abs_path"], c["music_dir"]).split(os.sep)[0]
            for r in db.find_grabbed_by_spotify_id("s1") + db.find_grabbed_by_spotify_id("s2")} == {"Music-raw"}
    assert db.find_grabbed_by_spotify_id("s1")[0]["rel_path"].startswith("Music-raw/")
    svc.sync.sync_all()                                       # library index scans /music, intake included
    assert db.library_count() == 2 and {r["match_status"] for r in db.get_playlist_track_rows(1)} == {"have"}
    assert drain(svc) == 0


def test_three_worker_threads_finish_every_item_exactly_once(tmp_path):
    c, db = make(tmp_path)
    db.add_playlist("PL1", "Mix")
    tracks = [_t(f"s{i}", f"Song {i}", "The Band", position=i) for i in range(1, 13)]
    prov = Perfect()
    svc = service(c, db, tracks, prov)
    svc.sync.sync_all()
    assert len(db.get_queue()) == 12
    svc.pool.start()
    try:
        deadline = time.time() + 30
        while time.time() < deadline and any(i["status"] != "done" for i in db.get_queue()):
            time.sleep(0.1)
    finally:
        svc.pool.stop()
        svc.pool.join(5)
    assert {i["status"] for i in db.get_queue()} == {"done"}
    assert sorted(prov.downloads) == sorted(t["title"] for t in tracks)           # once each
    assert db.get_grabbed_count() == 12 and len({r["abs_path"] for r in
                                                 sum((db.find_grabbed_by_spotify_id(t["spotify_track_id"]) for t in tracks), [])}) == 12


def test_startup_recovery_finishes_a_crashed_placement_and_requeues_the_rest(tmp_path):
    c, db = make(tmp_path)
    db.add_playlist("PL1", "Mix")
    svc = service(c, db, [])
    placed = db.enqueue_grab({"spotify_track_id": "s1", "title": "Placed", "artist": "A"})
    final = os.path.join(c["music_dir"], "A", "Placed.mp3")
    os.makedirs(os.path.dirname(final))
    open(final, "wb").write(b"x")
    db.update_grab(placed, final_path=final, provider="fake")
    db.transition(placed, "placing")
    stuck = db.enqueue_grab({"spotify_track_id": "s2", "title": "Mid-download", "artist": "A"})
    db.transition(stuck, "downloading")
    waiting = db.enqueue_grab({"spotify_track_id": "s3", "title": "Needs you", "artist": "A"})
    db.transition(waiting, "awaiting_user")
    svc.pool.stop()
    svc.pool.start()
    svc.pool.stop()
    svc.pool.join(5)
    assert db.get_grab_item(placed)["status"] == "done" and db.find_grabbed_by_spotify_id("s1")
    assert db.get_grab_item(stuck)["status"] in ("pending", "searching", "awaiting_user", "failed", "done")
    assert db.get_grab_item(waiting)["status"] == "awaiting_user"                  # left for the human


def test_an_ambiguous_match_waits_in_the_inbox_and_pings_once(tmp_path):
    class Vague(Perfect):
        def search(self, meta, limit=8):
            return [ProviderCandidate(provider="fake", provider_track_id="x", title="Something else",
                                      artist="Other", duration_ms=1000, quality="LOW")]

    c, db = make(tmp_path)
    db.add_playlist("PL1", "Mix")
    n = Notifier()
    svc = service(c, db, PLAYLIST[:1], Vague(), notifier=n)
    svc.pool.pipeline.notifier = n
    svc.sync.sync_all()
    assert drain(svc) == 1
    (item,) = db.get_queue("awaiting_user")
    assert db.get_grab_candidates(item["id"]) and db.get_grabbed_count() == 0
    assert [t for t, _b, _k in n.sent] == ["Needs review: The Band – Song 1"]
    # Resolving it from the inbox downloads the chosen candidate.
    from music_grabber.ops import Ops
    ops = Ops(svc, c)
    ops.inbox_choose(item["id"], db.get_grab_candidates(item["id"])[0]["id"])
    svc.pool.pipeline.providers = [Perfect()]
    drain(svc)
    assert db.get_grab_item(item["id"])["status"] == "done" and db.get_grabbed_count() == 1
