"""Q5: the Navidrome playlist mirror, against a stateful loopback fake of Navidrome's Subsonic API
(docs/plans/q3-q5-design.md). Nothing leaves the machine."""

import logging

import pytest
from fake_servers import FakeServer, FakeTimers, Reply
from test_sync_engine import _t

from music_grabber.db import Database
from music_grabber.mirror import MARKER, PlaylistMirror, SongIndex


def ok(**body):
    return {"subsonic-response": {"status": "ok", "version": "1.16.1", **body}}


def failed(code, message):
    return {"subsonic-response": {"status": "failed", "error": {"code": code, "message": message}}}


class FakeNavidrome:
    """Songs plus playlists with Navidrome's semantics: createPlaylist(name, songId...) creates and
    returns the playlist, createPlaylist(playlistId, songId...) replaces its songs, updatePlaylist
    sets name/comment/public, getPlaylists lists them with their comment."""

    def __init__(self, songs, user_ok=True, display_paths=False, meta=None):
        self.songs = [{"id": f"nd-{i}", "path": p, **(meta or {}).get(p, {})} for i, p in enumerate(songs)]
        self.display_paths = display_paths      # newer Navidrome: a display path, not the file's
        self.playlists: dict = {}
        self.user_ok = user_ok
        self._n = 0

    def add_song(self, path):
        self.songs.append({"id": f"nd-{len(self.songs)}", "path": path})

    def seed_playlist(self, name, comment="", songs=()):
        self._n += 1
        pid = f"pl-{self._n}"
        self.playlists[pid] = {"id": pid, "name": name, "comment": comment, "songs": list(songs)}
        return pid

    def handler(self, req):
        if not self.user_ok:
            return Reply(200, failed(40, "Wrong username or password"))
        q, name = req.query, req.path.rsplit("/", 1)[-1]
        if name == "search3":
            off, n = int(q.get("songOffset", 0)), int(q.get("songCount", 20))
            page = self.songs[off:off + n]
            if self.display_paths:
                page = [{**s, "path": "Display/" + s["id"] + ".mp3"} for s in page]
            return ok(searchResult3={"song": page})
        if name == "getPlaylists":
            return ok(playlists={"playlist": [{k: v for k, v in p.items() if k != "songs"} | {"songCount": len(p["songs"])}
                                              for p in self.playlists.values()]})
        if name == "getPlaylist":
            p = self.playlists[q["id"]]
            return ok(playlist={"id": p["id"], "name": p["name"], "entry": [{"id": i} for i in p["songs"]]})
        if name == "createPlaylist":
            ids = req.query_all.get("songId", [])
            if q.get("playlistId"):
                if ids:                                  # real Navidrome: without songs this is a no-op
                    self.playlists[q["playlistId"]]["songs"] = ids
                return ok()
            pid = self.seed_playlist(q["name"], songs=ids)
            return ok(playlist={"id": pid, "name": q["name"], "songCount": len(ids)})
        if name == "updatePlaylist":
            p = self.playlists[q["playlistId"]]
            for k in ("name", "comment"):
                if k in q:
                    p[k] = q[k]
            drop = {int(i) for i in req.query_all.get("songIndexToRemove", [])}
            p["songs"] = [s for i, s in enumerate(p["songs"]) if i not in drop]
            return ok()
        if name == "deletePlaylist":
            self.playlists.pop(q["id"], None)
            return ok()
        return Reply(404, {})


def writes(srv):
    return [r for r in srv.requests if r.path.rsplit("/", 1)[-1] in ("createPlaylist", "updatePlaylist", "deletePlaylist")]


def walks(srv):
    return srv.calls("/rest/search3")


PATHS = ["Band/LP/01 - One.mp3", "Band/LP/02 - Two.mp3", "Band/LP/03 - Three.mp3"]


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "mg.db"))


def playlist(db, spotify_id="PL1", name="Mix", have=PATHS):
    pid = db.add_playlist(spotify_id, name)
    db.sync_playlist_tracks(pid, [_t(f"{spotify_id}-{i}", f"Song {i}", position=i) for i in range(len(have))])
    for row, path in zip(db.get_playlist_track_rows(pid), have):
        db.set_playlist_track_match(row["id"], "have" if path else "missing", path)
    return pid


@pytest.fixture
def nav():
    return FakeNavidrome(PATHS)


def run(db, nav, **kw):
    """Run passes against the fake; returns (mirror, server)."""
    srv = FakeServer(nav.handler)
    srv.__enter__()
    m = PlaylistMirror(db, srv.url, "mirror-user", "s3cr3t-pw", **kw)
    return m, srv


@pytest.fixture
def served(db, nav):
    made = []

    def make(**kw):
        m, srv = run(db, nav, **kw)
        made.append((m, srv))
        return m, srv
    yield make
    for m, srv in made:
        m.close()
        srv.__exit__()


def songs_of(nav, rid):
    return [next(s["path"] for s in nav.songs if s["id"] == i) for i in nav.playlists[rid]["songs"]]


def test_first_pass_creates_an_owned_playlist_in_spotify_order(db, nav, served):
    pid = playlist(db)
    m, srv = served()
    m.run_pass()
    (rid, remote), = nav.playlists.items()
    assert remote["name"] == "Spotify: Mix" and songs_of(nav, rid) == PATHS
    assert remote["comment"] == f"{MARKER}PL1"                              # ownership marker
    st = db.get_mirror(pid)
    assert st["state"] == "ok" and st["remote_id"] == rid and st["matched"] == 3 and st["unresolved"] == 0


def test_an_unchanged_playlist_causes_no_write_and_no_song_walk(db, nav, served):
    playlist(db)
    m, srv = served()
    m.run_pass()
    srv.requests.clear()
    m.run_pass()
    m.run_pass(retry_unresolved=True)
    assert writes(srv) == [] and walks(srv) == []                            # one getPlaylists each, nothing else


def test_a_changed_match_list_replaces_the_contents_in_one_request(db, nav, served):
    pid = playlist(db)
    m, srv = served()
    m.run_pass()
    srv.requests.clear()
    rows = db.get_playlist_track_rows(pid)
    db.set_playlist_track_match(rows[1]["id"], "missing", None)             # a file went away
    m.run_pass()
    (rid,) = nav.playlists
    assert songs_of(nav, rid) == [PATHS[0], PATHS[2]]
    assert [r.path.rsplit("/", 1)[-1] for r in writes(srv)] == ["createPlaylist"]
    assert srv.calls("/rest/createPlaylist")[0].query["playlistId"] == rid


def test_a_playlist_that_loses_every_match_is_emptied_not_deleted(db, nav, served):
    pid = playlist(db)
    m, srv = served()
    m.run_pass()
    (rid,) = nav.playlists
    for r in db.get_playlist_track_rows(pid):
        db.set_playlist_track_match(r["id"], "missing", None)
    m.run_pass()
    assert nav.playlists[rid]["songs"] == [] and db.get_mirror(pid)["state"] == "ok" and db.get_mirror(pid)["matched"] == 0
    assert not srv.calls("/rest/deletePlaylist")


def test_a_same_named_playlist_that_is_not_ours_is_left_alone(db, nav, served):
    pid = playlist(db)
    mine = nav.seed_playlist("Spotify: Mix", comment="my own", songs=["nd-0"])
    m, srv = served()
    m.run_pass()
    assert writes(srv) == [] and nav.playlists[mine]["songs"] == ["nd-0"] and len(nav.playlists) == 1
    assert db.get_mirror(pid)["state"] == "name_taken"


def test_a_restored_database_adopts_the_marked_playlist_instead_of_duplicating(db, nav, served, tmp_path):
    playlist(db)
    m, srv = served()
    m.run_pass()
    fresh = Database(str(tmp_path / "restored.db"))                         # the state table is gone
    playlist(fresh)
    m2, srv2 = served()
    m2.db = fresh
    m2.run_pass()
    assert len(nav.playlists) == 1 and fresh.get_mirror(1)["remote_id"] in nav.playlists
    assert not [r for r in srv2.requests if r.path.endswith("createPlaylist") and "name" in r.query]


def test_a_playlist_the_user_deleted_is_not_recreated_until_reset(db, nav, served):
    pid = playlist(db)
    m, srv = served()
    m.run_pass()
    nav.playlists.clear()
    m.run_pass()
    assert nav.playlists == {} and db.get_mirror(pid)["state"] == "deleted_remotely"
    m.run_pass()
    assert nav.playlists == {}
    m.reset(pid)
    m.run_pass()
    assert len(nav.playlists) == 1 and db.get_mirror(pid)["state"] == "ok"


def test_tracks_not_in_navidrome_yet_are_skipped_and_picked_up_after_new_files(db, nav, served):
    pid = playlist(db, have=PATHS + ["Band/LP/04 - Four.mp3"])
    m, srv = served()
    m.run_pass()
    (rid,) = nav.playlists
    assert songs_of(nav, rid) == PATHS and db.get_mirror(pid)["unresolved"] == 1
    nav.add_song("Band/LP/04 - Four.mp3")                                    # scanned by Navidrome since
    srv.requests.clear()
    m.run_pass()                                                             # a periodic pass does not walk again
    assert walks(srv) == [] and len(nav.playlists[rid]["songs"]) == 3
    m.run_pass(retry_unresolved=True)                                        # a placement did
    assert len(nav.playlists[rid]["songs"]) == 4 and db.get_mirror(pid)["unresolved"] == 0


def test_paths_match_across_different_library_roots_but_never_ambiguously(db):
    idx = SongIndex([{"id": "a", "path": "Music/Band/LP/01 - One.mp3"}, {"id": "b", "path": "Band/LP/02 - Two.mp3"},
                     {"id": "c", "path": "X/Same/01 - Dup.mp3"}, {"id": "d", "path": "Y/Same/01 - Dup.mp3"},
                     {"id": "e", "path": "Band/LP/05 - Case.MP3"}])
    assert idx.find("Band/LP/01 - One.mp3") == "a"                          # Navidrome's root is one level up
    assert idx.find("Band/LP/02 - Two.mp3") == "b"                          # exact
    assert idx.find("/mnt/music/Band/LP/02 - Two.mp3") == "b"               # ours is deeper
    assert idx.find("Band/LP/05 - case.mp3") == "e"
    assert idx.find("Same/01 - Dup.mp3") is None and idx.find("Nope/x.mp3") is None


def test_tracks_resolve_by_isrc_or_name_when_navidrome_reports_display_paths(db):
    from music_grabber.text import normalize_artist, normalize_title
    paths = ["A/one.flac", "A/two.flac", "A/three.flac", "A/four.flac"]
    nav = FakeNavidrome(paths, display_paths=True, meta={
        paths[0]: {"isrc": ["GBAAA0000001"], "title": "Different", "artist": "Other"},
        paths[1]: {"title": "Two", "artist": "The Band", "duration": 200},
        paths[2]: {"title": "Three", "artist": "The Band", "duration": 200},
        paths[3]: {"title": "Three", "artist": "The Band", "duration": 400}})   # same name, another recording
    db.library_upsert([
        {"path": paths[0], "isrc": "gbaaa0000001", "norm_title": "x", "norm_artist": "y"},
        {"path": paths[1], "norm_title": normalize_title("Two"), "norm_artist": normalize_artist("The Band"), "duration_ms": 201000},
        {"path": paths[2], "norm_title": normalize_title("Three"), "norm_artist": normalize_artist("The Band"), "duration_ms": 199000},
        {"path": paths[3], "norm_title": normalize_title("Three"), "norm_artist": normalize_artist("The Band"), "duration_ms": 400000}])
    pid = playlist(db, have=paths + ["A/untagged.flac"])
    with FakeServer(nav.handler) as srv:
        m = PlaylistMirror(db, srv.url, "u", "p")
        m.run_pass()
        m.close()
    (rid,) = nav.playlists
    assert nav.playlists[rid]["songs"] == ["nd-0", "nd-1", "nd-2", "nd-3"]        # ISRC, name, name + duration x2
    assert db.get_mirror(pid)["unresolved"] == 1                                  # the file Navidrome cannot be matched to


def test_the_per_playlist_switch_and_a_disabled_playlist_are_respected(db, nav, served):
    a = playlist(db, "PLA", "A")
    b = playlist(db, "PLB", "B")
    c = playlist(db, "PLC", "C")
    db.set_playlist_mirror(a, False)
    db.set_playlist_enabled(c, False)
    m, srv = served()
    m.run_pass()
    assert [p["name"] for p in nav.playlists.values()] == ["Spotify: B"] and db.get_mirror(b)["state"] == "ok"
    assert db.get_mirror(a) is None and db.get_mirror(c) is None


def test_a_spotify_rename_follows_unless_the_user_renamed_it_in_navidrome(db, nav, served):
    pid = playlist(db)
    m, srv = served()
    m.run_pass()
    (rid,) = nav.playlists
    db.update_playlist_sync(pid, "snap2", "Mix v2", "", 3)
    m.run_pass()
    assert nav.playlists[rid]["name"] == "Spotify: Mix v2"
    nav.playlists[rid]["name"] = "My favourites"                             # the user's own rename
    db.update_playlist_sync(pid, "snap3", "Mix v3", "", 3)
    m.run_pass()
    assert nav.playlists[rid]["name"] == "My favourites"


def test_remove_deletes_only_our_playlist_and_stops_mirroring(db, nav, served):
    pid = playlist(db)
    other = nav.seed_playlist("Something else")
    m, srv = served()
    m.run_pass()
    assert m.remove(pid) is True
    assert list(nav.playlists) == [other] and db.get_mirror(pid) is None
    assert not db.get_playlist(pid)["mirror"]
    m.run_pass()
    assert list(nav.playlists) == [other]                                    # switched off, not recreated
    assert m.remove(pid) is False


def test_an_empty_playlist_creates_nothing(db, nav, served):
    pid = playlist(db, have=[None, None])
    m, srv = served()
    m.run_pass()
    assert nav.playlists == {} and writes(srv) == [] and db.get_mirror(pid)["state"] == "empty"


def test_rejected_credentials_disable_the_mirror_once_and_never_leak_them(db, nav, served, caplog):
    playlist(db)
    nav.user_ok = False
    m, srv = served()
    caplog.set_level(logging.INFO)
    assert m._fire() is None and m.disabled
    n = len(srv.requests)
    m.run_pass()
    assert len(srv.requests) == n                                            # silent from now on
    assert sum("disabled until restart" in r.message for r in caplog.records) == 1
    assert "s3cr3t-pw" not in caplog.text and "mirror-user" not in caplog.text


def test_unreachable_navidrome_backs_off_and_keeps_the_retry_flag(db):
    playlist(db)
    m = PlaylistMirror(db, "http://127.0.0.1:9", "u", "p", timeout=2)       # nothing listens there
    m._retry_unresolved = True
    delay = m._fire()
    assert delay and delay >= 60 and m._retry_unresolved and not m.disabled
    m.close()


def test_a_burst_of_placements_is_one_pass_after_the_debounce(db, nav, served):
    playlist(db)
    t = FakeTimers()
    m, srv = served(clock=t.clock, timer_factory=t.factory, debounce=120, max_wait=900, min_interval=300)
    from music_grabber.analysis import PlacedFile
    for _ in range(12):
        m.on_placed(PlacedFile(path="/m/x.mp3", rel_path="x.mp3", meta={}))
    assert nav.playlists == {}
    t.advance(121)
    assert len(nav.playlists) == 1 and len(srv.calls("/rest/getPlaylists")) == 1


def test_an_old_database_gets_the_mirror_schema_and_deleting_a_playlist_drops_its_state(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE playlists (id INTEGER PRIMARY KEY AUTOINCREMENT, spotify_id TEXT UNIQUE NOT NULL, "
              "name TEXT, snapshot_id TEXT, enabled INTEGER DEFAULT 1, image_url TEXT, track_count INTEGER DEFAULT 0, "
              "last_synced_at TEXT, created_at TEXT)")
    c.execute("INSERT INTO playlists (spotify_id, name) VALUES ('X', 'Old')")
    c.commit()
    c.close()
    db = Database(path)
    (p,) = db.list_playlists()
    assert p["mirror"] == 1
    db.save_mirror(p["id"], state="ok", matched=2)
    db.save_mirror(p["id"], unresolved=1)                                    # upsert keeps the other fields
    assert db.get_mirror(p["id"])["matched"] == 2 and db.get_mirror(p["id"])["unresolved"] == 1
    db.delete_playlist(p["id"])
    assert db.get_mirror(p["id"]) is None
    with pytest.raises(ValueError):
        db.save_mirror(1, secret="x")
