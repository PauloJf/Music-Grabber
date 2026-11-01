"""GET /api/stats/summary: its own token, 404 when disabled, the JSON shape and the Lisbon calendar windows."""

from datetime import datetime, timezone

import pytest
import test_api
from test_api import H, TOKEN

from music_grabber import stats

app_ops = test_api.app_ops          # the API fixture, reused
STATS_TOKEN = "s" * 10 + "-a-stats-token"
SH = {"Authorization": f"Bearer {STATS_TOKEN}"}
URL = "/api/stats/summary"


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


@pytest.fixture
def client(app_ops):
    app, ops = app_ops
    ops.config["stats_api_token"] = STATS_TOKEN
    return app.test_client()


def put(db, sql, *args):
    with db._connect() as conn:
        conn.execute(sql, args)
        conn.commit()


def ledger(db, n, placed_at):
    put(db, "INSERT INTO grabbed_files (rel_path, abs_path, provider, placed_at) VALUES (?,?,?,?)",
        f"a/{n}.mp3", f"/m/a/{n}.mp3", "deezer", placed_at)


def queue(db, status, provider=None, updated="2026-11-01T10:00:00+00:00"):
    put(db, "INSERT INTO grab_queue (status, provider, updated_at) VALUES (?,?,?)", status, provider, updated)


# ── auth ──────────────────────────────────────────────────────────────────────
def test_disabled_without_a_token_is_404_even_for_the_admin_token(app_ops):
    app, ops = app_ops
    c = app.test_client()
    assert c.get(URL).status_code == 404
    assert c.get(URL, headers=H).status_code == 404
    assert c.get(URL, headers={"Authorization": "Bearer "}).status_code == 404


def test_missing_and_wrong_tokens_are_401(client):
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get(URL, headers={"Authorization": STATS_TOKEN}).status_code == 401
    assert client.get(URL, headers={"Authorization": f"Bearer {STATS_TOKEN}x"}).status_code == 401


def test_the_admin_token_does_not_open_it(client):
    assert client.get(URL, headers=H).status_code == 401


def test_the_stats_token_opens_nothing_else(client):
    assert client.get("/api/status", headers=SH).status_code == 401
    assert client.get("/api/library", headers=SH).status_code == 401


def test_the_right_token_works_without_a_session_and_is_not_cached(client):
    r = client.get(URL, headers=SH)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"


def test_wrong_tokens_count_towards_the_lockout(app_ops):
    from music_grabber.api import AuthThrottle, create_app
    _, ops = app_ops
    ops.config["stats_api_token"] = STATS_TOKEN
    c = create_app(ops, TOKEN, throttle=AuthThrottle(max_failures=3)).test_client()
    for _ in range(3):
        assert c.get(URL, headers={"Authorization": "Bearer bad"}).status_code == 401
    assert c.get(URL, headers=SH).status_code == 429


def test_a_short_stats_token_is_a_config_error():
    from music_grabber import wiring
    with pytest.raises(wiring.ConfigError, match="STATS_API_TOKEN"):
        wiring.validate_api({"stats_api_token": "short"})
    wiring.validate_api({"stats_api_token": STATS_TOKEN})
    wiring.validate_api({})


def test_the_token_is_a_masked_env_only_secret():
    from music_grabber import config
    assert "stats_api_token" in config.ENV_ONLY_KEYS and "stats_api_token" in config.SECRET_KEYS


# ── shape ─────────────────────────────────────────────────────────────────────
def test_shape_on_an_empty_install(client):
    d = client.get(URL, headers=SH).get_json()
    for k in ("queue_pending", "queue_awaiting_user", "queue_in_flight", "queue_done", "queue_failed", "queue_total",
              "grabbed_week", "grabbed_month", "grabbed_year", "grabbed_all", "last_grab_at", "last_grab_epoch",
              "provider_success_rate_pct", "grabs_done", "grabs_failed", "playlists_total", "playlists_watched",
              "library_tracks", "library_artists", "managed_files", "releases_enabled", "releases_new",
              "releases_unannounced", "releases_announced", "dismissed", "artists_starred", "generated_at", "timezone"):
        assert k in d, k
    assert d["grabbed_all"] == 0 and d["last_grab_at"] == "" and d["last_grab_epoch"] == 0
    assert d["provider_success_rate_pct"] == 0.0 and d["providers"] == {}
    assert d["timezone"] == "Europe/Lisbon"


def test_values_are_flat_numbers_at_most_two_levels_deep(app_ops):
    db = app_ops[1].db
    queue(db, "done", "deezer")
    queue(db, "failed", "ytdlp")
    d = stats.summary(db, {}, now=utc(2026, 11, 1, 12))
    for k, v in d.items():
        if isinstance(v, dict):
            assert all(not isinstance(x, dict) for sub in v.values() for x in sub.values()), k
        else:
            assert isinstance(v, (int, float, str, bool)), k
    assert d["providers"]["deezer"] == {"done": 1, "failed": 0, "success_rate_pct": 100.0}


def test_queue_counts_provider_rate_and_last_grab(app_ops):
    db = app_ops[1].db
    for st in ("pending", "pending", "searching", "awaiting_user", "skipped"):
        queue(db, st)
    for _ in range(3):
        queue(db, "done", "deezer")
    queue(db, "failed", "deezer")
    queue(db, "done", "ytdlp")
    queue(db, "failed", None)
    ledger(db, 1, "2026-10-30T08:00:00+00:00")
    ledger(db, 2, "2026-10-31T09:30:00+00:00")
    d = stats.summary(db, {}, now=utc(2026, 11, 1, 12))
    assert (d["queue_pending"], d["queue_searching"], d["queue_awaiting_user"], d["queue_skipped"]) == (2, 1, 1, 1)
    assert d["queue_in_flight"] == 4 and d["queue_done"] == 4 and d["queue_failed"] == 2 and d["queue_total"] == 11
    assert d["grabs_done"] == 4 and d["grabs_failed"] == 2 and d["provider_success_rate_pct"] == 66.7
    assert d["providers"]["deezer"]["success_rate_pct"] == 75.0 and "" not in d["providers"]
    assert d["last_grab_at"] == "2026-10-31T09:30:00+00:00"
    assert d["last_grab_epoch"] == int(utc(2026, 10, 31, 9, 30).timestamp())


def test_last_grab_falls_back_to_the_newest_finished_queue_item(app_ops):
    db = app_ops[1].db
    queue(db, "done", "deezer", updated="2026-10-01T00:00:00+00:00")
    queue(db, "failed", updated="2026-11-01T00:00:00+00:00")
    assert stats.summary(db, {}, now=utc(2026, 11, 1))["last_grab_at"] == "2026-10-01T00:00:00+00:00"


def test_playlists_library_and_releases(app_ops):
    db = app_ops[1].db
    db.add_playlist("sp1", "One")
    db.add_playlist("sp2", "Two")
    put(db, "UPDATE playlists SET enabled=0 WHERE spotify_id='sp2'")
    for i, art in enumerate(["dua lipa", "dua lipa", "prince"]):
        put(db, "INSERT INTO library_tracks (path, norm_artist) VALUES (?,?)", f"p{i}", art)
    put(db, "INSERT INTO artist_map (norm_name, name, dz_artist_id, active) VALUES ('dua lipa','Dua Lipa','7',1)")
    for album, date_ in (("1", "2026-10-20"), ("2", "2026-10-25"), ("3", "2025-01-01")):
        put(db, "INSERT INTO release_cache (dz_artist_id, dz_album_id, release_date) VALUES ('7',?,?)", album, date_)
    put(db, "INSERT INTO release_notified (dz_album_id, notified_at) VALUES ('2', '2026-10-30T00:00:00+00:00')")
    put(db, "INSERT INTO item_dismissals (user_id, kind, ref) VALUES (1,'release','1')")
    put(db, "INSERT INTO artist_taste (norm_name, starred) VALUES ('dua lipa', 3)")
    d = stats.summary(db, {"releases_enabled": True}, now=utc(2026, 11, 1))
    assert d["playlists_total"] == 2 and d["playlists_watched"] == 1
    assert d["library_tracks"] == 3 and d["library_artists"] == 2
    assert d["releases_enabled"] is True
    assert d["releases_new"] == 1                  # album 1 is dismissed, 3 is older than the look-back
    assert d["releases_unannounced"] == 1          # album 2 was pinged, album 1 was not
    assert d["releases_announced"] == 1 and d["releases_announced_week"] == 1
    assert d["dismissed"] == 1 and d["artists_starred"] == 1


# ── calendar windows (Europe/Lisbon, Monday weeks) ────────────────────────────
def test_window_starts_in_winter_time():
    s = stats.window_starts(utc(2026, 11, 1, 12))                 # a Sunday, Lisbon is on UTC+0
    assert s == {"week": utc(2026, 10, 26), "month": utc(2026, 11, 1), "year": utc(2026, 1, 1)}


def test_window_starts_in_summer_time_are_an_hour_earlier_in_utc():
    s = stats.window_starts(utc(2026, 7, 15, 10))                 # a Wednesday, Lisbon is on UTC+1
    assert s["week"] == utc(2026, 7, 12, 23) and s["month"] == utc(2026, 6, 30, 23)
    assert s["year"] == utc(2026, 1, 1)                           # January is UTC+0


def test_the_local_date_not_the_utc_date_decides_the_window():
    # 23:30 UTC on 30 June is already 1 July 00:30 in Lisbon (UTC+1): the month has turned.
    assert stats.window_starts(utc(2026, 6, 30, 23, 30))["month"] == utc(2026, 6, 30, 23)
    assert stats.window_starts(utc(2026, 6, 30, 22, 30))["month"] == utc(2026, 5, 31, 23)
    # Sunday 23:30 UTC in summer is Monday 00:30 locally: a new week.
    assert stats.window_starts(utc(2026, 7, 12, 23, 30))["week"] == utc(2026, 7, 12, 23)
    assert stats.window_starts(utc(2026, 7, 12, 22, 30))["week"] == utc(2026, 7, 5, 23)


def test_grabbed_windows_include_the_boundary_and_exclude_the_instant_before(app_ops):
    db = app_ops[1].db
    now = utc(2026, 7, 15, 10)
    ledger(db, 1, "2026-07-12T22:59:59+00:00")      # Sunday 23:59:59 Lisbon: last week
    ledger(db, 2, "2026-07-12T23:00:00+00:00")      # Monday 00:00:00 Lisbon: this week
    ledger(db, 3, "2026-06-30T23:00:00+00:00")      # 1 July 00:00 Lisbon: this month
    ledger(db, 4, "2026-06-30T22:59:59+00:00")      # June
    ledger(db, 5, "2026-01-01T00:00:00+00:00")      # this year, first instant
    ledger(db, 6, "2025-12-31T23:59:59+00:00")      # last year
    d = stats.summary(db, {}, now=now)
    assert (d["grabbed_week"], d["grabbed_month"], d["grabbed_year"], d["grabbed_all"]) == (1, 3, 5, 6)
