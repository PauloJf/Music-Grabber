"""Keyless Deezer public-catalog lookups.

Deezer's public API needs no account, key or OAuth. Only the handful of endpoints the
grabber needs are carried here (a subset of BPM Tagger's integrations/deezer_catalog.py,
duplicated on purpose; docs/plans/extraction.md §1.1):

    track/{id}            ISRC / 30-s preview URL for a Deezer track id
    track/isrc:{ISRC}     the track for an ISRC (download candidate, source preview)

Module-level functions, ``requests`` with short timeouts, ``deezer_limiter.acquire()``
before every call, and log-and-return-empty on any failure.
"""

import logging
from typing import Optional

import requests

from .ratelimit import deezer_limiter

log = logging.getLogger(__name__)

_TIMEOUT = 8


def _get(path: str, params: Optional[dict] = None) -> dict:
    """One rate-limited GET against api.deezer.com. Raises on HTTP error."""
    deezer_limiter.acquire()
    resp = requests.get(f"https://api.deezer.com/{path.lstrip('/')}",
                        params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json() or {}


def track_isrc(dz_track_id: str) -> str:
    """The ISRC for a Deezer track id (upper-cased), "" on failure.

    Fetched lazily on enqueue only — the catalog list endpoints don't carry it."""
    if not dz_track_id:
        return ""
    try:
        d = _get(f"track/{dz_track_id}")
    except Exception as exc:
        log.debug("Deezer track ISRC lookup failed for %s: %s", dz_track_id, exc)
        return ""
    return (d.get("isrc") or "").upper()


def track_preview_url(dz_track_id: str) -> str:
    """The 30-second preview MP3 URL for a Deezer track id, "" on failure
    or when Deezer has no preview for the track.

    Same ``track/{id}`` endpoint as ``track_isrc()`` — kept separate because the
    call sites differ and both are cheap + cached upstream."""
    if not dz_track_id:
        return ""
    try:
        d = _get(f"track/{dz_track_id}")
    except Exception as exc:
        log.debug("Deezer preview lookup failed for %s: %s", dz_track_id, exc)
        return ""
    return d.get("preview") or ""


def track_detail_by_isrc(isrc: str) -> Optional[dict]:
    """The raw Deezer track object for an ISRC: {} when Deezer has no such ISRC,
    None when the lookup itself failed (network, rate limit, HTTP error).

    Unlike ``track_by_isrc`` this keeps the whole object (title, artist, album,
    duration, ``readable``), which the grabber turns into a download candidate."""
    isrc = (isrc or "").strip().upper()
    if not isrc:
        return {}
    try:
        d = _get(f"track/isrc:{isrc}")
    except Exception as exc:
        log.debug("Deezer ISRC lookup failed for %s: %s", isrc, exc)
        return None
    return d if d.get("id") and not d.get("error") else {}


def track_by_isrc(isrc: str) -> dict:
    """Resolve an ISRC to {"dz_track_id": str, "preview_url": str}; {} on failure.

    Used to preview the *source* Spotify track an inbox item is trying to fulfil —
    most Spotify-sourced items carry an ISRC."""
    if not isrc:
        return {}
    try:
        d = _get(f"track/isrc:{isrc.strip().upper()}")
    except Exception as exc:
        log.debug("Deezer ISRC lookup failed for %s: %s", isrc, exc)
        return {}
    if not d.get("id"):
        return {}
    return {"dz_track_id": str(d["id"]), "preview_url": d.get("preview") or ""}
