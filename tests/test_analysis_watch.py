"""AnalysisWatcher: BPM and loudness written by BPM Tagger show up on the queue item's history."""

import os
from datetime import datetime, timedelta, timezone

import pytest
from audio_fixtures import minimal_mp3_bytes, write_flac
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TBPM, TXXX

from music_grabber import analysis as A
from music_grabber.analysis_watch import AnalysisWatcher
from music_grabber.db import Database


def _mp3(path, *, bpm=None, gain=None, peak=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(minimal_mp3_bytes())
    tags = ID3()
    if bpm is not None: tags.add(TBPM(encoding=3, text=str(bpm)))
    if gain is not None: tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=f"{gain} dB"))
    if peak is not None: tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_PEAK", text=str(peak)))
    tags.save(str(path))
    return str(path)


def test_read_analysis_mp3_flac_and_untagged(tmp_path):
    f = _mp3(tmp_path / "a.mp3", bpm=117, gain="-9.90", peak="0.987")
    assert A.read_analysis(f) == {"bpm": 117.0, "gain_db": -9.9, "peak": 0.987}
    assert A.describe_analysis(A.read_analysis(f)) == "117 BPM, ReplayGain -9.9 dB (peak 0.987)"
    assert A.read_analysis(_mp3(tmp_path / "b.mp3")) == {}
    fl = tmp_path / "c.flac"
    write_flac(fl)
    audio = FLAC(str(fl))
    audio["BPM"], audio["REPLAYGAIN_TRACK_GAIN"] = "98", "+2.50 dB"
    audio.save()
    assert A.read_analysis(str(fl)) == {"bpm": 98.0, "gain_db": 2.5}
    assert A.read_analysis(str(tmp_path / "missing.mp3")) == {}


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "mg.db"))


def _filed(db, path, sid="s1", detail="filed in intake; BPM Tagger will analyse it"):
    i = db.enqueue_grab({"spotify_track_id": sid, "title": "Song", "artist": "Band"})
    db.transition(i, "done", "placed")
    db.update_grab(i, final_path=path)
    if detail:
        db.add_grab_event(i, "analysis", detail)
    return i


def _results(db, i):
    return [e["detail"] for e in db.get_grab_events(i) if e["event"] == "analysis_result"]


def _watcher(db, root, **kw):
    return AnalysisWatcher(db, str(root), **kw)


def test_result_is_recorded_once_bpm_tagger_has_tagged_the_file(db, tmp_path):
    f = tmp_path / "raw" / "a.mp3"
    _mp3(f)
    i = _filed(db, str(f))
    w = _watcher(db, tmp_path)
    assert w.run_once() == 0 and _results(db, i) == []                 # not tagged yet
    _mp3(f, bpm=117, gain="-9.9", peak="0.98")                          # BPM Tagger finishes
    assert w.run_once() == 1
    assert _results(db, i) == ["117 BPM, ReplayGain -9.9 dB (peak 0.98)"]
    assert w.run_once() == 0 and len(_results(db, i)) == 1             # once only


def test_the_file_is_never_modified_by_the_check(db, tmp_path):
    f = _mp3(tmp_path / "a.mp3", bpm=120, gain="-7.0")
    _filed(db, f)
    before, mt = open(f, "rb").read(), os.stat(f).st_mtime_ns
    _watcher(db, tmp_path).run_once()
    assert open(f, "rb").read() == before and os.stat(f).st_mtime_ns == mt


def test_a_file_bpm_tagger_moved_is_found_through_the_library_index(db, tmp_path):
    _filed(db, str(tmp_path / "Music-raw" / "gone.mp3"), sid="sp9")      # the stored path no longer exists
    _mp3(tmp_path / "Sepultura" / "Chaos A.D" / "01 - Refuse Resist.mp3", bpm=117, gain="-9.9")
    w = _watcher(db, tmp_path)
    assert w.run_once() == 0                                              # index does not know it yet
    db.library_upsert([{"path": "Sepultura/Chaos A.D/01 - Refuse Resist.mp3", "source": "filesystem",
                        "title": "Refuse / Resist", "spotify_track_id": "sp9"}])
    assert w.run_once() == 1


def test_bpm_without_loudness_waits_for_the_file_to_settle(db, tmp_path):
    f = _mp3(tmp_path / "a.mp3", bpm=140)
    i = _filed(db, f)
    now = {"t": os.path.getmtime(f) + 10}
    w = _watcher(db, tmp_path, clock=lambda: now["t"], settle_seconds=300)
    assert w.run_once() == 0                                              # just tagged: loudness may follow
    now["t"] += 400
    assert w.run_once() == 1 and _results(db, i) == ["140 BPM"]


def test_only_recent_finished_items_that_were_filed_for_analysis_are_checked(db, tmp_path):
    f = _mp3(tmp_path / "a.mp3", bpm=100, gain="-5")
    old = _filed(db, f, sid="old")
    plain = _filed(db, f, sid="plain", detail="")                         # no analysis configured for it
    pending = db.enqueue_grab({"spotify_track_id": "p", "title": "T", "artist": "A"})
    db.add_grab_event(pending, "analysis", "x")                           # not finished
    w = _watcher(db, tmp_path, now=lambda: datetime.now(timezone.utc) + timedelta(hours=30))
    assert w.run_once() == 0                                              # all older than the 24 h window
    assert _watcher(db, tmp_path).run_once() == 1 and _results(db, old) and not _results(db, plain)
    assert not _results(db, pending)


class FakeIndex:
    """Stands in for LibraryIndex: a refresh makes the moved file known, like a real scan would."""
    def __init__(self, db, rows=(), fail=False):
        self.db, self.rows, self.fail, self.refreshes = db, list(rows), fail, 0

    def refresh(self):
        self.refreshes += 1
        if self.fail:
            raise RuntimeError("share unmounted")
        self.db.library_upsert(self.rows)


def test_a_missing_file_makes_the_watcher_refresh_the_index_and_then_finds_it(db, tmp_path):
    _filed(db, str(tmp_path / "Music-raw" / "gone.mp3"), sid="sp9")
    _mp3(tmp_path / "Sepultura" / "01.mp3", bpm=117, gain="-9.9")
    idx = FakeIndex(db, [{"path": "Sepultura/01.mp3", "source": "filesystem", "title": "T", "spotify_track_id": "sp9"}])
    w = _watcher(db, tmp_path, index=idx)
    assert w.run_once() == 1 and idx.refreshes == 1          # found in the same pass, no 30-minute wait


def test_nothing_is_refreshed_when_every_file_is_where_it_was_filed(db, tmp_path):
    f = _mp3(tmp_path / "a.mp3")                              # present, just not tagged yet
    _filed(db, f)
    idx = FakeIndex(db)
    _watcher(db, tmp_path, index=idx).run_once()
    assert idx.refreshes == 0


def test_the_refresh_is_throttled_and_slower_for_old_items(db, tmp_path):
    i = _filed(db, str(tmp_path / "gone.mp3"), sid="x")
    idx = FakeIndex(db)
    t = {"now": 1000.0}
    w = _watcher(db, tmp_path, index=idx, clock=lambda: t["now"], refresh_fast=120, refresh_slow=900)
    w.run_once()
    assert idx.refreshes == 1
    t["now"] += 60
    w.run_once()
    assert idx.refreshes == 1                                 # too soon
    t["now"] += 61
    w.run_once()
    assert idx.refreshes == 2                                 # 2 minutes passed
    # the same item, filed more than an hour ago: only every 15 minutes
    with db._connect() as conn:
        conn.execute("UPDATE grab_queue SET updated_at=? WHERE id=?",
                     ((datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(), i))
        conn.commit()
    t["now"] += 130
    w.run_once()
    assert idx.refreshes == 2
    t["now"] += 800
    w.run_once()
    assert idx.refreshes == 3


def test_a_failing_refresh_does_not_break_the_check(db, tmp_path):
    f = _mp3(tmp_path / "a.mp3", bpm=120, gain="-5")
    _filed(db, f, sid="here")
    _filed(db, str(tmp_path / "gone.mp3"), sid="gone")
    idx = FakeIndex(db, fail=True)
    assert _watcher(db, tmp_path, index=idx).run_once() == 1 and idx.refreshes == 1
