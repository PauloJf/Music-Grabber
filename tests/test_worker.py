"""GrabPipeline: download → transcode → tag → place → ledger, plus the analysis port and hooks."""

import os

import pytest

from music_grabber.db import Database
from music_grabber.providers.base import (DownloadedFile, Provider, ProviderAuthError,
                                               ProviderCandidate)
from music_grabber.analysis import AnalysisResult, PlacedFile
from music_grabber.library import file_token
from music_grabber import worker as worker_mod
from music_grabber.worker import GrabPipeline


class FakeProvider(Provider):
    name = "fake"
    lossless = True

    def __init__(self, quality_match=True):
        self.quality_match = quality_match

    def search(self, meta, limit=8):
        if self.quality_match:
            # Identical metadata → score 1.0 (auto-accept).
            return [ProviderCandidate(provider="fake", provider_track_id="1",
                    title=meta.title, artist=meta.artist, album=meta.album,
                    duration_ms=meta.duration_ms, isrc=meta.isrc, quality="LOSSLESS")]
        # Poor match → below auto-accept.
        return [ProviderCandidate(provider="fake", provider_track_id="9",
                title="Something Unrelated", artist="Other Person",
                duration_ms=99000, quality="LOW")]

    def download(self, cand, dest_dir, progress_cb=None):
        p = os.path.join(dest_dir, "src.mp3")
        with open(p, "wb") as fh:
            fh.write(b"\x00" * 256)
        if progress_cb:
            progress_cb(1.0)
        return DownloadedFile(path=p, ext="mp3", provider="fake")


def _config(tmp_path, **over):
    cfg = {
        "db_path": str(tmp_path / "data" / "mg.db"),
        "music_dir": str(tmp_path / "music"),
        "output_format": "mp3-320",
        "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}",
        "auto_accept_threshold": 0.85,
        "ask_threshold": 0.55,
    }
    cfg.update(over)
    os.makedirs(cfg["music_dir"], exist_ok=True)
    os.makedirs(os.path.dirname(cfg["db_path"]), exist_ok=True)
    return cfg


def _enqueue(db):
    return db.enqueue_grab({
        "spotify_track_id": "s1", "title": "Blinding Lights", "artist": "The Weeknd",
        "album": "After Hours", "album_artist": "The Weeknd", "duration_ms": 200000,
        "isrc": "USUG11904206", "track_no": 3, "disc_no": 1, "year": 2020, "cover_url": ""})


def test_pipeline_happy_path_records_ledger_row(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _enqueue(db)
    pipe = GrabPipeline(cfg, db, providers=[FakeProvider()])

    status = pipe.process_item(db.get_grab_item(item_id))
    assert status == "done"

    row = db.get_grab_item(item_id)
    assert row["status"] == "done"
    final = row["final_path"]
    assert final and os.path.exists(final)
    # Rendered to the template, in the target format, under the filing root.
    assert final.replace("\\", "/").endswith("The Weeknd/After Hours/03 - Blinding Lights.mp3")

    # "Managed" == "has a ledger row": the Spotify-id fast path and the stats source.
    (led,) = db.find_grabbed_by_spotify_id("s1")
    assert led["rel_path"] == "The Weeknd/After Hours/03 - Blinding Lights.mp3"
    assert led["abs_path"] == final
    assert led["isrc"] == "USUG11904206" and led["provider"] == "fake"
    assert led["queue_item_id"] == item_id
    assert db.get_grabbed_count() == 1
    assert db.get_grabbed_total() == 1


def test_placed_file_is_stable_and_leaves_no_temp_files(tmp_path):
    """Nothing may touch the file after placement (a BPM Tagger watcher that picks it up
    must see a stable file), and the temp copy must never carry an audio extension."""
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _enqueue(db)
    GrabPipeline(cfg, db, providers=[FakeProvider()]).process_item(db.get_grab_item(item_id))

    final = db.get_grab_item(item_id)["final_path"]
    (led,) = db.find_grabbed_by_spotify_id("s1")
    assert led["placed_hash"] == file_token(final)
    leftovers = [f for _d, _s, fs in os.walk(cfg["music_dir"]) for f in fs if f.startswith(".mg_part")]
    assert leftovers == []


def test_part_file_uses_non_audio_extension(tmp_path, monkeypatch):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        return real_replace(src, dst)

    monkeypatch.setattr("music_grabber.worker.os.replace", spy)
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _enqueue(db)
    GrabPipeline(cfg, db, providers=[FakeProvider()]).process_item(db.get_grab_item(item_id))
    assert seen and seen[0].startswith(".mg_part_") and seen[0].endswith(".part")


def test_same_target_path_is_suffixed_not_overwritten(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    pipe = GrabPipeline(cfg, db, providers=[FakeProvider()])
    first = _enqueue(db)
    pipe.process_item(db.get_grab_item(first))
    # A different Spotify id that renders to the same path.
    second = db.enqueue_grab({"spotify_track_id": "s2", "title": "Blinding Lights",
                              "artist": "The Weeknd", "album": "After Hours",
                              "album_artist": "The Weeknd", "duration_ms": 200000,
                              "isrc": "USUG11904206", "track_no": 3, "disc_no": 1})
    pipe.process_item(db.get_grab_item(second))
    p1, p2 = db.get_grab_item(first)["final_path"], db.get_grab_item(second)["final_path"]
    assert p1 != p2 and p2.endswith("(2).mp3") and os.path.exists(p1) and os.path.exists(p2)


class RecordingAnalysis:
    name = "recording"

    def __init__(self, root=None, boom=False):
        self.root, self.boom, self.placed = root, boom, []

    def destination_root(self, config):
        return self.root or config["music_dir"]

    def after_place(self, placed: PlacedFile):
        self.placed.append(placed)
        if self.boom:
            raise RuntimeError("analysis exploded")
        return AnalysisResult(ok=True, detail="bpm: not yet")


def test_analysis_port_gets_placed_file_and_may_redirect_destination(tmp_path):
    intake = str(tmp_path / "intake")
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _enqueue(db)
    analysis = RecordingAnalysis(root=intake)
    GrabPipeline(cfg, db, providers=[FakeProvider()], analysis=analysis).process_item(
        db.get_grab_item(item_id))

    (placed,) = analysis.placed
    assert placed.path.startswith(intake) and os.path.exists(placed.path)
    assert placed.rel_path == "The Weeknd/After Hours/03 - Blinding Lights.mp3"
    assert placed.queue_item_id == item_id and placed.meta["isrc"] == "USUG11904206"
    # Nothing was filed under the plain music dir, and the outcome is an audit event.
    assert not os.listdir(cfg["music_dir"])
    assert any(e["event"] == "analysis" and e["detail"] == "bpm: not yet"
               for e in db.get_grab_events(item_id))


def test_analysis_or_hook_failure_never_fails_a_finished_grab(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _enqueue(db)
    calls = []

    def bad_hook(placed):
        calls.append(placed.path)
        raise RuntimeError("hook exploded")

    pipe = GrabPipeline(cfg, db, providers=[FakeProvider()],
                        analysis=RecordingAnalysis(boom=True), on_placed=[bad_hook, calls.append])
    assert pipe.process_item(db.get_grab_item(item_id)) == "done"
    assert db.get_grab_item(item_id)["status"] == "done"
    assert len(calls) == 2  # the failing hook did not stop the next one


def test_recover_placed_completes_item_from_existing_file(tmp_path):
    """A crash after os.replace but before 'done': startup recovery finishes the item
    from the placed file instead of downloading it again."""
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _enqueue(db)
    final = os.path.join(cfg["music_dir"], "The Weeknd", "After Hours", "03 - Blinding Lights.mp3")
    os.makedirs(os.path.dirname(final))
    with open(final, "wb") as fh:
        fh.write(b"audio")
    db.update_grab(item_id, final_path=final, provider="fake")
    db.transition(item_id, "placing")

    pipe = GrabPipeline(cfg, db, providers=[])
    assert pipe.recover_placed(db.get_grab_item(item_id)) is True
    assert db.get_grab_item(item_id)["status"] == "done"
    assert db.find_grabbed_by_spotify_id("s1")[0]["abs_path"] == final


def test_recover_placed_without_a_file_leaves_item_alone(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _enqueue(db)
    db.update_grab(item_id, final_path=str(tmp_path / "nope.mp3"))
    db.transition(item_id, "placing")
    assert GrabPipeline(cfg, db, providers=[]).recover_placed(db.get_grab_item(item_id)) is False
    assert db.get_grab_item(item_id)["status"] == "placing"


def test_grab_events_recorded(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _enqueue(db)
    GrabPipeline(cfg, db, providers=[FakeProvider()]).process_item(db.get_grab_item(item_id))
    events = [e["event"] for e in db.get_grab_events(item_id)]
    assert "searching" in events and "downloading" in events and "done" in events


def test_dry_run_stops_at_awaiting_user(tmp_path):
    cfg = _config(tmp_path, grab_dry_run=True)
    db = Database(cfg["db_path"])
    item_id = _enqueue(db)
    status = GrabPipeline(cfg, db, providers=[FakeProvider()]).process_item(db.get_grab_item(item_id))
    assert status == "awaiting_user"
    assert db.get_grab_item(item_id)["final_path"] is None


def test_low_score_routes_to_awaiting_user(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _enqueue(db)
    status = GrabPipeline(cfg, db,
                          providers=[FakeProvider(quality_match=False)]).process_item(db.get_grab_item(item_id))
    assert status == "awaiting_user"
    # Candidates are still recorded for the inbox (M5).
    assert len(db.get_grab_candidates(item_id)) == 1


def test_inbox_choose_resumes_to_done(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _enqueue(db)
    pipe = GrabPipeline(cfg, db, providers=[FakeProvider(quality_match=False)])

    # Ambiguous → awaiting_user with a recorded candidate.
    assert pipe.process_item(db.get_grab_item(item_id)) == "awaiting_user"
    cand = db.get_grab_candidates(item_id)[0]

    # User chooses that candidate → item returns to pending → worker resumes.
    db.update_grab(item_id, chosen_candidate_id=cand["id"])
    db.transition(item_id, "pending", "chose")
    assert pipe.process_item(db.get_grab_item(item_id)) == "done"

    row = db.get_grab_item(item_id)
    assert row["status"] == "done" and row["final_path"]
    # Tagged with the ORIGINAL track meta, not the candidate's.
    assert row["final_path"].replace("\\", "/").endswith("Blinding Lights.mp3")


def test_inbox_search_override_used_for_query(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _enqueue(db)

    class RecordingProvider(FakeProvider):
        def __init__(self):
            super().__init__(quality_match=True)
            self.queries = []

        def search(self, meta, limit=8):
            self.queries.append(meta.title)
            return super().search(meta, limit)

    prov = RecordingProvider()
    pipe = GrabPipeline(cfg, db, providers=[prov])
    db.update_grab(item_id, search_override="my custom query")
    pipe.process_item(db.get_grab_item(item_id))
    assert "my custom query" in prov.queries


class RejectedProvider(FakeProvider):
    """Its credentials are rejected: at search (an expired ARL), or only at
    download (a candidate found before the ARL expired)."""
    name = "deezer"

    def __init__(self, reject_search=True):
        super().__init__(quality_match=True)
        self.reject_search = reject_search
        self.downloads = 0

    def search(self, meta, limit=8):
        if self.reject_search:
            raise ProviderAuthError("Deezer rejected the ARL (expired or invalid)")
        cands = super().search(meta, limit)
        for c in cands:
            c.provider = "deezer"
        return cands

    def download(self, cand, dest_dir, progress_cb=None):
        self.downloads += 1
        raise ProviderAuthError("Deezer rejected the ARL (expired or invalid)")


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_grabber(self, title, body, **kw):
        self.sent.append((title, body))


def test_rejected_credentials_fall_back_and_alert_once(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    rejected, notifier = RejectedProvider(), FakeNotifier()
    pipe = GrabPipeline(cfg, db, providers=[rejected, FakeProvider()],
                        notifier=notifier)

    item_id = _enqueue(db)
    assert pipe.process_item(db.get_grab_item(item_id)) == "done"  # fell back to "fake"
    details = [e["detail"] for e in db.get_grab_events(item_id)]
    assert any("rejected the ARL" in (d or "") for d in details)
    alerts = [t for t, _ in notifier.sent if "login failed" in t]
    assert alerts == ["Deezer login failed"]

    # A second item hits the same wall: no second credential alert.
    item2 = db.enqueue_grab({"spotify_track_id": "s2", "title": "Save Your Tears",
                             "artist": "The Weeknd", "album": "After Hours",
                             "album_artist": "The Weeknd", "duration_ms": 215000})
    pipe.process_item(db.get_grab_item(item2))
    assert [t for t, _ in notifier.sent if "login failed" in t] == ["Deezer login failed"]


def test_rejected_credentials_only_provider_fails_with_reason(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    notifier = FakeNotifier()
    rejected = RejectedProvider(reject_search=False)
    pipe = GrabPipeline(cfg, db, providers=[rejected], notifier=notifier)
    item_id = _enqueue(db)
    assert pipe.process_item(db.get_grab_item(item_id)) == "failed"
    assert rejected.downloads == 1  # no pointless retry with a dead credential
    assert "rejected the ARL" in db.get_grab_item(item_id)["error"]
    failed = [b for t, b in notifier.sent if t.startswith("Grab failed")]
    assert failed and "rejected the ARL" in failed[0]


# ── search note + Spotify ISRC backfill ───────────────────────────────────────
class NotingProvider(FakeProvider):
    def search(self, meta, limit=8):
        from music_grabber.providers.base import SearchResults
        return SearchResults(super().search(meta, limit), "ISRC X isn't in Deezer's catalogue; searched by title instead")


def test_search_note_saved_on_item_and_event_then_cleared_on_research(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _enqueue(db)
    pipe = GrabPipeline(cfg, db, providers=[NotingProvider(quality_match=False)])
    assert pipe.process_item(db.get_grab_item(item_id)) == "awaiting_user"
    assert "isn't in Deezer's catalogue" in db.get_grab_item(item_id)["note"]
    assert any(e["event"] == "note" for e in db.get_grab_events(item_id))
    # A later search with nothing to say clears the stale note.
    pipe.providers = [FakeProvider(quality_match=False)]
    pipe._search_and_score(worker_mod.TrackMeta(title="t", artist="a"),
                           worker_mod.TrackMeta(title="t", artist="a"), item_id)
    assert not db.get_grab_item(item_id)["note"]


class FakeSpotify:
    def __init__(self, isrc="USUG11904206", connected=True, boom=False):
        self.isrc, self.connected, self.boom, self.asked = isrc, connected, boom, []

    def is_connected(self):
        return self.connected

    def get_track_isrc(self, track_id):
        self.asked.append(track_id)
        if self.boom:
            raise RuntimeError("spotify down")
        return self.isrc


def _no_isrc_item(db):
    return db.enqueue_grab({"spotify_track_id": "s9", "title": "Blinding Lights", "artist": "The Weeknd",
                            "album": "After Hours", "duration_ms": 200000, "isrc": ""})


def test_missing_isrc_is_backfilled_from_spotify(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _no_isrc_item(db)
    pipe = GrabPipeline(cfg, db, providers=[FakeProvider()])
    pipe._spotify = FakeSpotify()
    item = db.get_grab_item(item_id)
    pipe._backfill_isrc(item)
    assert item["isrc"] == "USUG11904206"
    assert db.get_grab_item(item_id)["isrc"] == "USUG11904206"
    assert any("fetched from Spotify" in (e["detail"] or "") for e in db.get_grab_events(item_id))


@pytest.mark.parametrize("spotify", [FakeSpotify(connected=False), FakeSpotify(boom=True),
                                     FakeSpotify(isrc="")])
def test_isrc_backfill_is_best_effort(tmp_path, spotify):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    item_id = _no_isrc_item(db)
    pipe = GrabPipeline(cfg, db, providers=[FakeProvider()])
    pipe._spotify = spotify
    item = db.get_grab_item(item_id)
    pipe._backfill_isrc(item)  # must not raise
    assert not item["isrc"]
    assert pipe.process_item(db.get_grab_item(item_id)) == "done"


def test_backfill_skips_items_that_already_have_an_isrc_or_no_spotify_id(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["db_path"])
    pipe = GrabPipeline(cfg, db, providers=[FakeProvider()])
    pipe._spotify = fake = FakeSpotify()
    pipe._backfill_isrc(db.get_grab_item(_enqueue(db)))                     # has an ISRC
    pipe._backfill_isrc({"id": 1, "isrc": "", "spotify_track_id": ""})      # nothing to ask about
    assert fake.asked == []


def test_spotify_get_track_isrc_uses_single_track_endpoint(monkeypatch):
    from music_grabber.spotify import SpotifyClient
    c = SpotifyClient({}, db=None)
    seen = []
    monkeypatch.setattr(c, "_get", lambda path, params=None: seen.append(path) or
                        {"external_ids": {"isrc": "usug11904206"}})
    assert c.get_track_isrc("abc") == "USUG11904206"
    assert seen == ["/tracks/abc"]
    assert c.get_track_isrc("") == ""
