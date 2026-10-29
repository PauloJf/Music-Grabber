"""Browsing your own library by artist, album and track (the Artists / Albums views).

Pure functions over the cached library rows: no network, no files. The index has no ids, so artists and albums are
addressed by name and matched on a normalised form (case, accents, "feat." and edition words such as "(Deluxe)" do
not split an album in two).

* An album belongs to its **album artist**, else the track artist. "Various Artists" compilations are their own
  bucket.
* An artist also *appears on* albums where they are credited on a track ("A, B" credits count for each name).
"""

from collections import Counter, defaultdict
from typing import Optional

from .text import extract_feat, normalize_artist_name, normalize_title, split_artist_credits

VARIOUS = {"various artists", "various", "va", "v a", "varios artistas", "vários artistas", "varios", "diversos"}
NO_ALBUM = "(no album)"


def akey(name: Optional[str]) -> str:
    return normalize_artist_name(name)


def albkey(title: Optional[str]) -> str:
    return normalize_title(title)


def names_in(artist: Optional[str]) -> list[str]:
    """The individual names in a credit string: "A feat. B", "A, B" and "A / B" are two artists each."""
    base, feats = extract_feat(artist)
    # extract_feat leaves the dot of "feat." on the next name (text.py is pinned to BPM Tagger's copy), so trim it here
    return split_artist_credits(base) + [f.lstrip(" .,;:-") for f in feats if f.lstrip(" .,;:-")]


def owner_of(row: dict) -> str:
    """The artist an album is filed under."""
    name = (row.get("album_artist") or "").strip()
    if name:
        return name
    credits = names_in(row.get("artist"))
    return credits[0] if credits else (row.get("artist") or "").strip() or "Unknown artist"


def credited(row: dict) -> list[str]:
    """Every artist name a track is credited to (the album artist included)."""
    names = [owner_of(row)] + names_in(row.get("artist"))
    seen, out = set(), []
    for n in names:
        k = akey(n)
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    return out


def _common(values) -> str:
    c = Counter(v for v in values if v)
    return c.most_common(1)[0][0] if c else ""


def _album_groups(rows: list[dict]) -> dict:
    """{(owner key, album key): [rows]}"""
    groups: dict = defaultdict(list)
    for r in rows:
        groups[(akey(owner_of(r)), albkey(r.get("album")))].append(r)
    return groups


def _album_summary(key: tuple, rows: list[dict]) -> dict:
    years = [r["year"] for r in rows if r.get("year")]
    return {"artist_key": key[0], "album_key": key[1], "artist": _common(owner_of(r) for r in rows),
            "title": _common((r.get("album") or "").strip() for r in rows) or NO_ALBUM, "year": max(years) if years else None,
            "tracks": len(rows)}


def artists(rows: list[dict], query: str = "") -> list[dict]:
    names: dict[str, Counter] = defaultdict(Counter)
    tracks: Counter = Counter()
    albums: dict[str, set] = defaultdict(set)
    for r in rows:
        ak = akey(owner_of(r))
        ab = (ak, albkey(r.get("album")))
        for n in credited(r):
            k = akey(n)
            names[k][n] += 1
            tracks[k] += 1
            albums[k].add(ab)
    out = [{"key": k, "name": c.most_common(1)[0][0], "tracks": tracks[k], "albums": len(albums[k] - {(k, "")})}
           for k, c in names.items()]
    q = akey(query)
    if q:
        out = [a for a in out if q in akey(a["name"])]
    return sorted(out, key=lambda a: (a["key"] in VARIOUS, akey(a["name"])))


def artist_page(rows: list[dict], name: str) -> Optional[dict]:
    key = akey(name)
    if not key:
        return None
    own = [r for r in rows if akey(owner_of(r)) == key]
    appears = [r for r in rows if akey(owner_of(r)) != key and any(akey(n) == key for n in credited(r))]
    if not own and not appears:
        return None
    display = _common(n for r in own + appears for n in credited(r) if akey(n) == key) or name

    def summarise(rs):
        return sorted((_album_summary(k, g) for k, g in _album_groups(rs).items()),
                      key=lambda a: (a["title"] == NO_ALBUM, -(a["year"] or 0), akey(a["title"])))
    return {"key": key, "name": display, "tracks": len(own) + len(appears), "albums": summarise(own), "appears_on": summarise(appears)}


def owned_index(rows: list[dict], artist: str) -> tuple[Counter, set]:
    """What you own by one artist (credited on the track, so collaborations count): tracks per normalised album title,
    and the normalised titles of all their tracks."""
    key = akey(artist)
    per_album: Counter = Counter()
    titles: set = set()
    for r in rows:
        if any(akey(n) == key for n in credited(r)):
            per_album[albkey(r.get("album"))] += 1
            titles.add(normalize_title(r.get("title")))
    return per_album, titles


def albums(rows: list[dict], query: str = "") -> list[dict]:
    out = [_album_summary(k, g) for k, g in _album_groups(rows).items() if k[1]]
    q = akey(query)
    if q:
        out = [a for a in out if q in akey(a["title"]) or q in akey(a["artist"])]
    return sorted(out, key=lambda a: (akey(a["title"]), akey(a["artist"])))


def album_page(rows: list[dict], artist: str, album: str) -> Optional[dict]:
    ak, bk = akey(artist), ("" if album == NO_ALBUM else albkey(album))
    group = [r for r in rows if akey(owner_of(r)) == ak and albkey(r.get("album")) == bk]
    if not group:
        return None
    tracks = sorted(group, key=lambda r: (r.get("disc_no") or 1, r.get("track_no") or 10_000, akey(r.get("title"))))
    summary = _album_summary((ak, bk), group)
    return {**summary, "discs": len({r.get("disc_no") or 1 for r in group}),
            "tracks_list": [{"path": r["path"], "title": r.get("title") or r["path"].rsplit("/", 1)[-1],
                             "artist": r.get("artist") or "", "track_no": r.get("track_no"), "disc_no": r.get("disc_no"),
                             "duration_ms": r.get("duration_ms")} for r in tracks]}
