"""The read-only numbers behind ``GET /api/stats/summary`` (a dashboard feed, e.g. Glance's custom-api).

Flat JSON: numbers are numbers, sizes would be bytes, the calendar windows (``week`` from Monday, ``month``,
``year``) follow Europe/Lisbon, and every timestamp is UTC RFC 3339.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    STATS_TZ = ZoneInfo("Europe/Lisbon")
except Exception:                       # no tz database installed (the image ships tzdata): UTC windows
    STATS_TZ = timezone.utc

QUEUE_STATES = ("pending", "searching", "awaiting_user", "downloading", "transcoding", "tagging", "placing",
                "done", "failed", "skipped")
IN_FLIGHT = QUEUE_STATES[:7]
RELEASE_DAYS = 90                       # the same look-back the releases feed uses by default


def window_starts(now: Optional[datetime] = None, tz=STATS_TZ) -> dict:
    """UTC instants at which the current calendar week (Monday), month and year began in ``tz``."""
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    starts = {"week": midnight - timedelta(days=local.weekday()),
              "month": midnight.replace(day=1),
              "year": midnight.replace(month=1, day=1)}
    return {k: v.astimezone(timezone.utc) for k, v in starts.items()}


def _rate(done: int, failed: int) -> float:
    return round(100.0 * done / (done + failed), 1) if done + failed else 0.0


def _epoch(stamp: Optional[str]) -> int:
    try:
        dt = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return 0
    return int((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp())


def summary(db, config: dict, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    starts = window_starts(now)
    iso = {k: v.isoformat() for k, v in starts.items()}
    counts = db.get_queue_counts()
    out: dict = {"generated_at": now.isoformat(), "timezone": getattr(STATS_TZ, "key", "UTC")}
    for state in QUEUE_STATES:
        out[f"queue_{state}"] = counts.get(state, 0)
    out["queue_in_flight"] = sum(counts.get(s, 0) for s in IN_FLIGHT)
    out["queue_total"] = sum(counts.values())

    for window in ("week", "month", "year"):
        out[f"grabbed_{window}"] = db.stats_grabbed_since(iso[window])
    out["grabbed_all"] = db.stats_grabbed_since(None)
    last = db.stats_last_grab()
    out["last_grab_at"] = last or ""
    out["last_grab_epoch"] = _epoch(last)

    outcomes = db.stats_provider_outcomes()
    done = sum(o["done"] for o in outcomes.values())
    failed = sum(o["failed"] for o in outcomes.values())
    out["grabs_done"], out["grabs_failed"] = done, failed
    out["provider_success_rate_pct"] = _rate(done, failed)
    out["providers"] = {name: {"done": o["done"], "failed": o["failed"], "success_rate_pct": _rate(o["done"], o["failed"])}
                        for name, o in sorted(outcomes.items()) if name}

    playlists = db.list_playlists()
    out["playlists_total"] = len(playlists)
    out["playlists_watched"] = sum(1 for p in playlists if p.get("enabled"))
    out["playlists_missing_tracks"] = sum(p.get("missing_count", 0) for p in playlists if p.get("enabled"))
    out["library_tracks"] = db.library_count()
    out["library_artists"] = db.stats_library_artists()
    out["managed_files"] = db.get_grabbed_count()

    out["releases_enabled"] = bool(config.get("releases_enabled"))
    since_date = (now - timedelta(days=RELEASE_DAYS)).date().isoformat()
    out.update(db.stats_release_counts(since_date, (now - timedelta(days=7)).isoformat()))
    return out
