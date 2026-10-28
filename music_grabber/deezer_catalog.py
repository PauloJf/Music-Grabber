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


# ── browsing (the web UI's Discover pages) ──────────────────────────────────────
# Unlike the lookups above, these raise CatalogError: a browse page should say "Deezer is
# unreachable" rather than show an empty result that looks like "no such artist".

class CatalogError(Exception):
    """Deezer could not be reached or answered with an error."""


def _call(path: str, params: Optional[dict] = None) -> dict:
    try:
        d = _get(path, params)
    except Exception as exc:
        raise CatalogError(f"Deezer is unreachable ({type(exc).__name__})") from None
    err = d.get("error") if isinstance(d, dict) else None
    if err:
        raise CatalogError(err.get("message") or "Deezer returned an error")
    return d


def _year(date: str) -> Optional[int]:
    return int(date[:4]) if date and date[:4].isdigit() else None


def _img(o: dict, *keys) -> str:
    for k in keys:
        if o.get(k):
            return o[k]
    return ""


def track_view(t: dict, album: Optional[dict] = None) -> dict:
    """A Deezer track object in our shape. Deezer durations are seconds; ours are ms."""
    al = t.get("album") or album or {}
    ar = t.get("artist") or {}
    return {
        "dz_id": str(t.get("id")), "title": t.get("title") or "", "artist": ar.get("name") or "",
        "artist_id": str(ar["id"]) if ar.get("id") else "", "album": al.get("title") or "",
        "album_id": str(al["id"]) if al.get("id") else "",
        "duration_ms": int(t.get("duration") or 0) * 1000 or None,
        "preview_url": t.get("preview") or "", "cover_url": _img(al, "cover_medium", "cover_big", "cover"),
        "explicit": bool(t.get("explicit_lyrics")), "track_no": t.get("track_position"),
        "disc_no": t.get("disk_number"), "year": _year(al.get("release_date") or t.get("release_date") or ""),
    }


def artist_view(a: dict) -> dict:
    return {"dz_id": str(a.get("id")), "name": a.get("name") or "",
            "image_url": _img(a, "picture_medium", "picture_big", "picture"),
            "fans": a.get("nb_fan"), "album_count": a.get("nb_album")}


def album_view(a: dict, artist: Optional[dict] = None) -> dict:
    ar = a.get("artist") or artist or {}
    return {"dz_id": str(a.get("id")), "title": a.get("title") or "", "artist": ar.get("name") or "",
            "artist_id": str(ar["id"]) if ar.get("id") else "",
            "cover_url": _img(a, "cover_medium", "cover_big", "cover"),
            "year": _year(a.get("release_date") or ""), "release_date": a.get("release_date") or "",
            "track_count": a.get("nb_tracks"),
            "kind": a.get("record_type") or "album", "explicit": bool(a.get("explicit_lyrics"))}


def track_page(ref: str) -> dict:
    """One track in our shape, with its ISRC, by Deezer id or ``isrc:<ISRC>``. ``{}`` when Deezer has
    no such track; raises CatalogError when it could not be asked."""
    try:
        d = _get(f"track/{ref}")
    except Exception as exc:
        raise CatalogError(f"Deezer is unreachable ({type(exc).__name__})") from None
    if not isinstance(d, dict) or d.get("error") or not d.get("id"):
        return {}
    t = track_view(d)
    t["isrc"] = (d.get("isrc") or "").upper()
    return t


def search(kind: str, query: str, limit: int = 25) -> list:
    """kind: track | artist | album."""
    if kind not in ("track", "artist", "album"):
        raise CatalogError(f"cannot search for '{kind}'")
    data = _call("search" if kind == "track" else f"search/{kind}", {"q": query, "limit": limit}).get("data", [])
    shape = {"track": track_view, "artist": artist_view, "album": album_view}[kind]
    return [shape(x) for x in data if x and x.get("id")]


def artist_page(dz_id: str, top_limit: int = 10, album_limit: int = 50, related_limit: int = 12) -> dict:
    """Everything an artist page shows. The artist call must succeed; the rest degrades to empty."""
    info = artist_view(_call(f"artist/{dz_id}"))
    def part(path, params, shape):
        try:
            return [shape(x) for x in _call(path, params).get("data", []) if x and x.get("id")]
        except CatalogError:
            return []
    return {"artist": info,
            "top": part(f"artist/{dz_id}/top", {"limit": top_limit}, track_view),
            "albums": part(f"artist/{dz_id}/albums", {"limit": album_limit}, album_view),
            "related": part(f"artist/{dz_id}/related", {"limit": related_limit}, artist_view)}


def album_page(dz_id: str) -> dict:
    d = _call(f"album/{dz_id}")
    album = album_view(d)
    tracks = [track_view(t, album={"id": d.get("id"), "title": d.get("title"), "cover_medium": d.get("cover_medium"),
                                    "release_date": d.get("release_date")})
              for t in (d.get("tracks") or {}).get("data", []) if t and t.get("id")]
    for i, t in enumerate(tracks, 1):
        t["track_no"] = t["track_no"] or i
        t["artist"] = t["artist"] or album["artist"]
    return {"album": album, "tracks": tracks}


def artist_albums(dz_id: str, limit: int = 100) -> list:
    """An artist's albums, singles and EPs with their release dates. Deezer does not promise an
    order, so an artist with more than ``limit`` releases is read from both ends."""
    first = _call(f"artist/{dz_id}/albums", {"limit": limit})
    data = list(first.get("data", []))
    total = first.get("total") or 0
    if total > len(data):
        data += _call(f"artist/{dz_id}/albums", {"limit": limit, "index": max(0, total - limit)}).get("data", [])
    seen, out = set(), []
    for x in data:
        if x and x.get("id") and x["id"] not in seen:
            seen.add(x["id"])
            out.append(album_view(x))
    return out
