"""Suggestions from what the background pass learned (docs/plans/integration-and-suggestions.md, part 2).

Pure functions over the cache tables: no network here. Two surfaces:

* **gaps**: albums by artists you own that nothing in your library matches ("you have none of it"), or
  that you own at most half of when Deezer says how long it is;
* **artists you may like**: Deezer's related artists of the artists you listen to, weighted by how many
  tracks you own and how many you starred, minus anyone you own, hid or dismissed.
"""

import math
from typing import Optional

from .text import normalize_title

SEEDS = 30                    # artists whose "related" lists feed the suggestions
GAPS_PER_ARTIST = 5
PARTIAL_AT_MOST = 0.5         # owned / album length at or below this counts as a partial album
MIN_OWNED_FOR_GAPS = 3


def _album_key(title: Optional[str]) -> str:
    return normalize_title(title)


def same_album(a: str, b: str) -> bool:
    """Equal after normalising, or one contains the other (a "Deluxe" or "Remastered" edition)."""
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 5 and (a in b or b in a))


def seeds(db, limit: int = SEEDS) -> list[dict]:
    """Your top artists as [{norm, name, dz_id, weight}]: tracks owned and tracks starred, damped by log."""
    owned, starred = db.library_artist_counts(), db.artist_taste()
    out = []
    for a in db.active_mapped():
        w = math.log2(1 + owned.get(a["norm"], 0)) + 2 * math.log2(1 + starred.get(a["norm"], 0))
        if w > 0:
            out.append({**a, "weight": w})
    return sorted(out, key=lambda a: -a["weight"])[:limit]


def _hidden(db, user_id: int) -> set:
    return {p["dz_artist_id"] for p in db.get_artist_prefs(user_id) if p["hide_suggestions"]}


def gaps(db, user_id: int, per_artist: int = GAPS_PER_ARTIST, limit: int = 60) -> list[dict]:
    owned_counts = db.library_artist_counts()
    artists = [a for a in db.active_mapped() if owned_counts.get(a["norm"], 0) >= MIN_OWNED_FOR_GAPS]
    hidden, dismissed = _hidden(db, user_id), db.dismissed_refs(user_id, "album")
    library = db.library_album_counts([a["norm"] for a in artists])
    out = []
    for a in artists:
        if a["dz_id"] in hidden:
            continue
        have = [(_album_key(alb), n) for alb, n in library.get(a["norm"], {}).items() if alb]
        found = []
        for alb in db.albums_of(a["dz_id"]):
            if alb["kind"] != "album" or alb["dz_album_id"] in dismissed or not alb["norm_title"]:
                continue
            owned = sum(n for key, n in have if same_album(alb["norm_title"], key))
            total = alb["track_count"] or 0
            if owned == 0:
                found.append({**alb, "owned": 0, "state": "missing"})
            elif total and owned / total <= PARTIAL_AT_MOST:
                found.append({**alb, "owned": owned, "state": "partial"})
        found.sort(key=lambda r: r["release_date"] or "", reverse=True)
        out.extend(found[:per_artist])
    out.sort(key=lambda r: r["release_date"] or "", reverse=True)
    return [{k: r[k] for k in ("dz_artist_id", "dz_album_id", "title", "release_date", "cover_url", "artist_name",
                               "track_count", "owned", "state")} for r in out[:limit]]


def artists(db, user_id: int, limit: int = 24) -> list[dict]:
    seed = {s["dz_id"]: s for s in seeds(db)}
    have = {a["dz_id"] for a in db.active_mapped()}
    hidden, dismissed = _hidden(db, user_id), db.dismissed_refs(user_id, "artist")
    followed = {p["dz_artist_id"] for p in db.get_artist_prefs(user_id) if p["follow"]}
    scored: dict[str, dict] = {}
    for r in db.related_of(list(seed)):
        rid = r["rel_id"]
        if rid in have or rid in hidden or rid in dismissed or rid in followed:
            continue
        s = seed[r["dz_artist_id"]]
        cur = scored.setdefault(rid, {"dz_id": rid, "name": r["rel_name"], "image_url": r["image_url"], "fans": r["fans"],
                                      "score": 0.0, "because": []})
        cur["score"] += s["weight"] / (1 + r["rank"] / 4)
        cur["because"].append((s["weight"], s["name"]))
    ranked = sorted(scored.values(), key=lambda r: -r["score"])[:limit]
    for r in ranked:
        r["because"] = [n for _, n in sorted(r["because"], reverse=True)[:2]]
        r["score"] = round(r["score"], 2)
    return ranked
