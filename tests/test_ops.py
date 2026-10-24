"""Ops: the operations layer behind both the API and the CLI."""

import json
import os

import pytest

from conftest import posix_only
from music_grabber.db import Database
from music_grabber.ops import MASK, Ops, OpsError
from music_grabber.spotify import SpotifyAuthError, SpotifyError
from music_grabber.sync_engine import GrabberService


class FakeClient:
    """Stands in for SpotifyClient on the service."""

    def __init__(self, connected=True):
        self.connected = connected
        self.meta = {"spotify_id": "PL1", "name": "Road Trip", "snapshot_id": "s", "image_url": "u",
                     "track_count": 12, "owner": ""}
        self.raise_on_meta = None
        self.exchanged = []

    def is_connected(self):
        return self.connected

    def get_playlist_meta(self, pid):
        if self.raise_on_meta:
            raise self.raise_on_meta
        return {**self.meta, "spotify_id": pid}

    def get_my_playlists(self):
        return [{"spotify_id": "PL1", "name": "Road Trip"}, {"spotify_id": "PL2", "name": "Other"}]

    def search_tracks(self, q, limit=20):
        return [{"spotify_track_id": "t1", "title": q}]

    def status(self):
        return {"configured": True, "connected": self.connected, "scope": "", "redirect_uri": "http://x/cb"}

    def exchange_code(self, code):
        self.exchanged.append(code)

    def disconnect(self):
        self.connected = False

    def authorize_url(self, state):
        return f"https://accounts.example/authorize?state={state}"


@pytest.fixture
def ops(tmp_path):
    cfg = {"db_path": str(tmp_path / "mg.db"), "music_dir": str(tmp_path / "music"),
           "provider_order": "ytdlp", "output_format": "mp3-128", "auto_accept_threshold": 0.85,
           "ask_threshold": 0.55, "path_template": "{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}"}
    db = Database(cfg["db_path"])
    svc = GrabberService(cfg, db)
    svc.client = FakeClient()
    svc.sync.client = svc.client
    svc.pool.pipeline.providers = []
    return Ops(svc, cfg)


def _item(ops, status="pending", sid="s1", **kw):
    i = ops.db.enqueue_grab({"spotify_track_id": sid, "title": "Song", "artist": "Band", **kw})
    if status != "pending":
        ops.db.transition(i, status, "t")
    return i


# ── status ────────────────────────────────────────────────────────────────────
def test_status_summarises_everything(ops):
    ops.db.add_playlist("PL1", "Mix")
    _item(ops)
    s = ops.status()
    assert s["version"] and s["spotify"]["connected"] and s["queue"] == {"pending": 1}
    assert s["playlists"]["total"] == 1 and s["library"]["index"] == "filesystem"
    assert s["analysis"] == "none" and s["providers"] == [] and s["dry_run"] is False
    json.dumps(s)                                   # JSON-able


def test_status_last_sync_is_the_newer_of_memory_and_the_db(ops):
    assert ops.status()["last_synced_epoch"] is None
    ops.db.set_last_sync(1000)
    assert ops.status()["last_synced_epoch"] == 1000
    ops.svc.sync.last_synced_epoch = 2000.5
    assert ops.status()["last_synced_epoch"] == 2000.5
    ops.db.set_last_sync(3000)
    assert ops.status()["last_synced_epoch"] == 3000


# ── playlists ─────────────────────────────────────────────────────────────────
def test_playlist_add_uses_spotify_meta_when_connected(ops):
    d = ops.playlist_add("https://open.spotify.com/playlist/PL1?si=x")
    assert d["playlist"]["name"] == "Road Trip" and d["playlist"]["track_count"] == 12
    assert ops.playlists_list()["playlists"][0]["spotify_id"] == "PL1"


def test_playlist_add_offline_uses_the_id_as_a_placeholder_name(ops):
    ops.svc.client.connected = False
    d = ops.playlist_add("spotify:playlist:ABC")
    assert d["playlist"]["name"] == "ABC" and d["connected"] is False


@pytest.mark.parametrize("exc, code, http", [(SpotifyAuthError("x"), "spotify_auth", 401),
                                             (SpotifyError("404"), "spotify_error", 502)])
def test_playlist_add_maps_spotify_errors(ops, exc, code, http):
    ops.svc.client.raise_on_meta = exc
    with pytest.raises(OpsError) as e:
        ops.playlist_add("PL1")
    assert (e.value.code, e.value.http) == (code, http)
    assert ops.playlists_list()["playlists"] == []


def test_playlist_add_rejects_garbage_and_unknown_ids_are_404(ops):
    with pytest.raises(OpsError) as e:
        ops.playlist_add("   ")
    assert e.value.code == "invalid_playlist"
    for call in (lambda: ops.playlist_get(99), lambda: ops.playlist_remove(99),
                 lambda: ops.playlist_enable(99), lambda: ops.sync(99), lambda: ops.enqueue_missing(99)):
        with pytest.raises(OpsError) as e:
            call()
        assert e.value.http == 404


def test_playlist_enable_disable_remove(ops):
    pid = ops.playlist_add("PL1")["playlist"]["id"]
    assert ops.playlist_enable(pid, False)["enabled"] is False
    assert ops.db.get_playlist(pid)["enabled"] == 0
    ops.playlist_remove(pid)
    assert ops.playlists_list()["playlists"] == []


def test_sync_requires_a_connection(ops):
    ops.svc.client.connected = False
    with pytest.raises(OpsError) as e:
        ops.sync()
    assert e.value.code == "spotify_disconnected" and e.value.http == 409


# ── queue ─────────────────────────────────────────────────────────────────────
def test_queue_add_dedupes_and_validates(ops):
    assert ops.queue_add({"title": "X", "artist": "Y", "spotify_track_id": "sp1"})["id"]
    with pytest.raises(OpsError) as e:
        ops.queue_add({"title": "X", "artist": "Y", "spotify_track_id": "sp1"})
    assert e.value.code == "already_queued" and e.value.http == 409
    with pytest.raises(OpsError):
        ops.queue_add({"artist": "only an artist"})


def test_retry_only_failed_or_skipped(ops):
    failed, pending = _item(ops, "failed", "a"), _item(ops, "pending", "b")
    assert ops.queue_retry(failed) == {"ok": True}
    assert ops.db.get_grab_item(failed)["status"] == "pending"
    with pytest.raises(OpsError) as e:
        ops.queue_retry(pending)
    assert e.value.code == "not_retryable"
    _item(ops, "failed", "c")
    _item(ops, "failed", "d")
    assert ops.queue_retry_failed()["requeued"] == 2


def test_cancel_delete_rules(ops):
    live, done = _item(ops, "pending", "a"), _item(ops, "done", "b")
    with pytest.raises(OpsError) as e:
        ops.queue_delete(live)
    assert e.value.code == "not_deletable"
    ops.queue_cancel(live)
    assert ops.db.get_grab_item(live)["status"] == "skipped"
    with pytest.raises(OpsError) as e:
        ops.queue_cancel(done)
    assert e.value.code == "already_terminal"
    ops.queue_delete(done)
    assert ops.db.get_grab_item(done) is None
    with pytest.raises(OpsError) as e:
        ops.queue_item(done)
    assert e.value.http == 404


def test_priority_and_clear_completed(ops):
    a, b = _item(ops, "pending", "a"), _item(ops, "done", "b")
    assert ops.queue_priority(a, "5")["priority"] == 5
    with pytest.raises(OpsError):
        ops.queue_priority(a, "high")
    assert ops.queue_clear_completed()["removed"] == 1 and ops.db.get_grab_item(b) is None


def test_queue_item_detail_has_candidates_and_events(ops):
    i = _item(ops, "awaiting_user")
    ops.db.add_grab_candidates(i, [{"provider": "deezer", "title": "Song", "rank": 0, "score": 0.7}])
    d = ops.queue_item(i)
    assert d["item"]["id"] == i and len(d["candidates"]) == 1 and d["events"]


# ── inbox ─────────────────────────────────────────────────────────────────────
def test_inbox_lists_awaiting_items_with_candidates(ops):
    i = _item(ops, "awaiting_user")
    _item(ops, "pending", "other")
    ops.db.add_grab_candidates(i, [{"provider": "deezer", "title": "Song", "rank": 0}])
    items = ops.inbox_list()["items"]
    assert [x["id"] for x in items] == [i] and len(items[0]["candidates"]) == 1


def test_inbox_choose_search_research_skip(ops):
    i = _item(ops, "awaiting_user")
    ops.db.add_grab_candidates(i, [{"provider": "deezer", "title": "Song", "rank": 0}])
    cand = ops.db.get_grab_candidates(i)[0]["id"]
    with pytest.raises(OpsError) as e:
        ops.inbox_choose(i, 99999)
    assert e.value.code == "invalid_candidate"
    ops.inbox_choose(i, cand)
    row = ops.db.get_grab_item(i)
    assert row["status"] == "pending" and row["chosen_candidate_id"] == cand

    j = _item(ops, "awaiting_user", "j")
    ops.inbox_search(j, "  better query ")
    assert ops.db.get_grab_item(j)["search_override"] == "better query"
    with pytest.raises(OpsError):
        ops.inbox_search(j, "x")                    # no longer awaiting_user
    k = _item(ops, "awaiting_user", "k")
    ops.db.update_grab(k, search_override="old")
    ops.inbox_research(k)
    assert ops.db.get_grab_item(k)["search_override"] is None
    m = _item(ops, "awaiting_user", "m")
    ops.inbox_skip(m)
    assert ops.db.get_grab_item(m)["status"] == "skipped"
    with pytest.raises(OpsError) as e:
        ops.inbox_search(_item(ops, "awaiting_user", "n"), "  ")
    assert e.value.code == "query_required"
    _item(ops, "awaiting_user", "o")
    assert ops.inbox_research_all()["requeued"] >= 1


def test_choosing_a_candidate_of_another_item_is_refused(ops):
    a, b = _item(ops, "awaiting_user", "a"), _item(ops, "awaiting_user", "b")
    ops.db.add_grab_candidates(b, [{"provider": "deezer", "title": "x", "rank": 0}])
    other = ops.db.get_grab_candidates(b)[0]["id"]
    with pytest.raises(OpsError) as e:
        ops.inbox_choose(a, other)
    assert e.value.code == "invalid_candidate"


# ── settings ──────────────────────────────────────────────────────────────────
@posix_only
def test_settings_update_validates_persists_and_applies_live(ops, tmp_path):
    pipe = ops.svc.pool.pipeline
    r = ops.settings_update({"output_format": "flac", "auto_accept_threshold": "0.9",
                             "grab_dry_run": "true", "provider_order": "ytdlp, deezer",
                             "path_template": "{Artist}/{Title}.{ext}"})
    assert set(r["applied"]) == {"output_format", "auto_accept_threshold", "grab_dry_run",
                                 "provider_order", "path_template"} and r["restart_required"] == []
    assert pipe.output_format == "flac" and pipe.auto_accept == 0.9 and pipe.dry_run is True
    assert pipe.path_template == "{Artist}/{Title}.{ext}"
    saved = json.load(open(ops.settings_path))
    assert saved["output_format"] == "flac" and saved["grab_dry_run"] is True
    assert saved["provider_order"] == "ytdlp,deezer"
    assert oct(os.stat(ops.settings_path).st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("changes, fragment", [
    ({"output_format": "wav"}, "one of"),
    ({"provider_order": "deezer,napster"}, "provider_order"),
    ({"path_template": "{Artist}/{Title}"}, "{ext}"),
    ({"path_template": ""}, "empty"),
    ({"auto_accept_threshold": 2}, "between"),
    ({"spotify_sync_minutes": "soon"}, "number"),
    ({"grab_dry_run": "maybe"}, "true or false"),
    ({"ask_threshold": 0.95}, "cannot be above"),
    ({"ntfy_url": "file:///etc/passwd"}, "http"),
    ({"ui_public_url": "javascript:alert(1)"}, "http"),
    ({"ntfy_url": "https://user:pw@ntfy.example"}, "credentials"),
    ({"api_token": "x"}, "unknown or read-only"),
    ({"spotify_client_secret": "x"}, "unknown or read-only"),
])
def test_settings_validation(ops, changes, fragment):
    with pytest.raises(OpsError, match=fragment):
        ops.settings_update(changes)
    assert not os.path.exists(ops.settings_path)    # nothing half-applied


def test_secret_settings_are_masked_and_the_mask_does_not_overwrite(ops):
    ops.settings_update({"deezer_arl": "real-arl", "ntfy_topic": "my-topic"})
    shown = ops.settings_get()["settings"]
    assert shown["deezer_arl"] == MASK and shown["ntfy_topic"] == MASK
    ops.settings_update({"deezer_arl": MASK, "output_format": "flac"})
    assert ops.config["deezer_arl"] == "real-arl"
    ops.settings_update({"deezer_arl": ""})
    assert ops.config["deezer_arl"] == ""
    assert "deezer_arl" not in json.load(open(ops.settings_path))


def test_restart_required_keys_are_reported(ops):
    assert ops.settings_update({"grab_workers": 2})["restart_required"] == ["grab_workers"]


def test_changing_ntfy_rebuilds_the_notifier(ops):
    assert ops.svc.pool.pipeline.notifier is None
    ops.settings_update({"ntfy_url": "http://ntfy.test", "ntfy_topic": "t"})
    assert ops.svc.pool.pipeline.notifier is not None and ops.svc.sync.notifier is ops.svc.pool.pipeline.notifier
    ops.settings_update({"ntfy_topic": ""})
    assert ops.svc.pool.pipeline.notifier is None


def test_test_ntfy_needs_configuration_and_sends_through_the_notifier(ops, monkeypatch):
    with pytest.raises(OpsError) as e:
        ops.test_ntfy()
    assert e.value.http == 409
    sent = []
    monkeypatch.setattr("music_grabber.notify.Notifier._post", lambda self, *a, **k: sent.append(a))
    ops.settings_update({"ntfy_url": "http://ntfy.test", "ntfy_topic": "t"})
    assert ops.test_ntfy() == {"ok": True} and sent[0][0] == "Music Grabber test"


def test_test_deezer_uses_the_stored_arl_and_reports_the_provider_verdict(ops, monkeypatch):
    with pytest.raises(OpsError) as e:
        ops.test_deezer()
    assert e.value.code == "arl_required"
    seen = []
    monkeypatch.setattr("music_grabber.providers.deezer.DeezerProvider.verify",
                        lambda self: seen.append(self.arl) or None)
    ops.config["deezer_arl"] = "stored"
    assert ops.test_deezer(MASK)["ok"] is True and seen == ["stored"]
    assert ops.test_deezer("typed")["ok"] and seen[-1] == "typed"


# ── spotify OAuth state ───────────────────────────────────────────────────────
def test_oauth_state_is_single_use(ops):
    st = ops.spotify_authorize_url()["state"]
    assert ops.spotify_callback("CODE", st) == {"ok": True}
    assert ops.svc.client.exchanged == ["CODE"]
    with pytest.raises(OpsError) as e:
        ops.spotify_callback("CODE", st)
    assert e.value.code == "invalid_state"


def test_oauth_unknown_or_expired_state_and_missing_code(ops, monkeypatch):
    with pytest.raises(OpsError) as e:
        ops.spotify_callback("C", "forged")
    assert e.value.code == "invalid_state"
    st = ops.spotify_authorize_url()["state"]
    import music_grabber.ops as O
    monkeypatch.setattr(O.time, "time", lambda: 1e12)
    with pytest.raises(OpsError) as e:
        ops.spotify_callback("C", st)
    assert e.value.code == "invalid_state"
    monkeypatch.undo()
    st = ops.spotify_authorize_url()["state"]
    with pytest.raises(OpsError) as e:
        ops.spotify_callback("", st)
    assert e.value.code == "missing_code"


def test_spotify_playlists_flags_watched_ones(ops):
    ops.playlist_add("PL1")
    got = {p["spotify_id"]: p["watched"] for p in ops.spotify_playlists()["playlists"]}
    assert got == {"PL1": True, "PL2": False}
    ops.svc.client.connected = False
    with pytest.raises(OpsError):
        ops.spotify_playlists()
    with pytest.raises(OpsError):
        ops.spotify_search("x")


def test_library_refresh_reports_a_failing_source(ops, tmp_path):
    with pytest.raises(OpsError) as e:
        ops.library_refresh()                       # the music dir does not exist
    assert e.value.code == "library_error" and e.value.http == 502
    os.makedirs(tmp_path / "music")
    assert ops.library_refresh()["seen"] == 0


# ── library listing ───────────────────────────────────────────────────────────
def _seed_library(ops):
    rows = [("A/One/01 - Alpha.mp3", "Alpha", "Zed", "One", 1), ("A/One/02 - Beta.mp3", "Beta", "Zed", "One", 2),
            ("B/Two/01 - 100% Pure_Gold.mp3", "100% Pure_Gold", "Amy", "Two", 1), ("C/03 - Gamma.mp3", "Gamma", "amy", "Three", 3)]
    ops.db.library_upsert([{"path": p, "source": "filesystem", "title": t, "artist": a, "album": al, "track_no": n,
                            "norm_title": t.lower(), "norm_artist": a.lower()} for p, t, a, al, n in rows])


def test_library_list_pages_orders_and_totals(ops):
    _seed_library(ops)
    d = ops.library_list()
    assert d["total"] == 4 and d["index"] == "filesystem" and [t["artist"].lower() for t in d["tracks"]] == ["amy", "amy", "zed", "zed"]
    assert [t["title"] for t in d["tracks"][2:]] == ["Alpha", "Beta"]            # album, then track number
    page = ops.library_list(limit=2, offset=1)
    assert page["total"] == 4 and len(page["tracks"]) == 2 and page["offset"] == 1
    assert ops.library_list(limit=10_000)["limit"] == 500 and ops.library_list(limit=0)["limit"] == 1
    json.dumps(d)


def test_library_list_search_is_case_insensitive_all_words_and_literal(ops):
    _seed_library(ops)
    assert [t["title"] for t in ops.library_list("ZED beta")["tracks"]] == ["Beta"]
    assert ops.library_list("amy")["total"] == 2                                  # artist, any case
    assert ops.library_list("100%")["total"] == 1 and ops.library_list("%")["total"] == 1   # % and _ are not wildcards
    assert ops.library_list("_")["total"] == 1 and ops.library_list("nothing here")["total"] == 0


def test_library_list_rejects_bad_paging(ops):
    with pytest.raises(OpsError) as e:
        ops.library_list(limit="x")
    assert e.value.code == "invalid_paging" and e.value.http == 400
