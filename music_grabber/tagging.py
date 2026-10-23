"""Write full metadata + embed cover art onto a downloaded, transcoded file (§5).

BPM itself is written separately by bpm.tags.write_bpm_tag in the analyzing_bpm
step, so this module only handles the descriptive tags + artwork.
"""

import base64
import ipaddress
import logging
import os
import socket
from urllib.parse import urljoin, urlsplit

import mutagen
import requests
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (APIC, ID3, ID3NoHeaderError, TALB, TDRC, TIT2, TPE1,
                         TPE2, TPOS, TRCK, TSRC, TXXX)
from mutagen.mp4 import MP4, MP4Cover

log = logging.getLogger(__name__)

# The Spotify track id, written into files Music Grabber grabs so the library index can answer
# "do we have this track?" from the file itself (docs/plans/q3-q5-design.md). Other software
# ignores unknown tags.
# ID3 TXXX description / Vorbis comment key. The grabber only produces mp3, flac and opus
# (transcode.PROFILES), so there is no MP4 variant.
SPOTIFY_ID_TAG = "SPOTIFY_TRACK_ID"


def _track_disc(n) -> str:
    return str(n) if n else ""


def sniff_image_mime(data: bytes) -> str:
    """Best-effort image MIME from magic bytes; defaults to JPEG."""
    if not data:
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def write_track_tags(file_path: str, meta: dict, preserve_mtime: bool = True,
                     spotify_id_tag: bool = True) -> str | None:
    """Write descriptive tags (title/artist/album/album_artist/track/disc/year/isrc, and the
    Spotify track id unless ``spotify_id_tag`` is off).

    Returns None on success, or a short warning string on failure so callers can
    surface it (the download itself still succeeded).

    preserve_mtime restores the file's timestamps afterwards, same as
    bpm.tags.write_bpm_tag — editing tags from the UI shouldn't make a file look
    freshly modified to Navidrome or a backup tool."""
    ext = os.path.splitext(file_path)[1].lower()
    sid = (meta.get("spotify_track_id") or "").strip() if spotify_id_tag else ""
    try:
        st = os.stat(file_path) if preserve_mtime else None
        if ext == ".mp3":
            try:
                tags = ID3(file_path)
            except ID3NoHeaderError:
                tags = ID3()
            if meta.get("title"):        tags["TIT2"] = TIT2(encoding=3, text=meta["title"])
            if meta.get("artist"):       tags["TPE1"] = TPE1(encoding=3, text=meta["artist"])
            if meta.get("album"):        tags["TALB"] = TALB(encoding=3, text=meta["album"])
            if meta.get("album_artist"): tags["TPE2"] = TPE2(encoding=3, text=meta["album_artist"])
            if meta.get("track_no"):     tags["TRCK"] = TRCK(encoding=3, text=_track_disc(meta["track_no"]))
            if meta.get("disc_no"):      tags["TPOS"] = TPOS(encoding=3, text=_track_disc(meta["disc_no"]))
            if meta.get("year"):         tags["TDRC"] = TDRC(encoding=3, text=str(meta["year"]))
            if meta.get("isrc"):         tags["TSRC"] = TSRC(encoding=3, text=meta["isrc"])
            if sid:                      tags[f"TXXX:{SPOTIFY_ID_TAG}"] = TXXX(encoding=3, desc=SPOTIFY_ID_TAG, text=sid)
            tags.save(file_path)
        else:
            audio = mutagen.File(file_path, easy=True)
            if audio is None:
                return
            def setk(k, v):
                if v:
                    audio[k] = str(v)
            setk("title", meta.get("title"))
            setk("artist", meta.get("artist"))
            setk("album", meta.get("album"))
            setk("albumartist", meta.get("album_artist"))
            setk("tracknumber", _track_disc(meta.get("track_no")))
            setk("discnumber", _track_disc(meta.get("disc_no")))
            setk("date", meta.get("year"))
            setk("isrc", meta.get("isrc"))
            if sid:
                audio[SPOTIFY_ID_TAG] = sid                 # Vorbis comments take any key
            audio.save()
        if st is not None:
            os.utime(file_path, (st.st_atime, st.st_mtime))
    except Exception as exc:
        log.warning("Tag write failed for %s: %s", os.path.basename(file_path), exc)
        return f"tag write failed: {exc}"
    return None


def resize_cover(image_bytes: bytes, max_px: int = 1200) -> bytes:
    """Downscale cover art to <= max_px on the long edge, re-encoded as JPEG.
    No-op (returns input) if Pillow is unavailable or the image is already small."""
    if not image_bytes:
        return image_bytes
    try:
        import io

        from PIL import Image
        # Only the three formats a cover really is get decoded (a GIF comes back untouched), and
        # decompression bombs are capped: defence in depth next to the current Pillow (docs/security.md).
        Image.MAX_IMAGE_PIXELS = 40_000_000
        img = Image.open(io.BytesIO(image_bytes), formats=["JPEG", "PNG", "WEBP"])
        if max(img.size) <= max_px:
            return image_bytes
        img = img.convert("RGB")
        img.thumbnail((max_px, max_px))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=90)
        return out.getvalue()
    except Exception as exc:
        log.debug("Cover resize skipped: %s", exc)
        return image_bytes


MAX_COVER_BYTES = 15 * 1024 * 1024
MAX_COVER_REDIRECTS = 3


def _public_http_url(url: str) -> bool:
    """http(s) only, and the host must resolve to public addresses: a cover URL comes from a
    remote catalogue, so it must not be able to point the grabber at the LAN or localhost."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
        return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)
    except (OSError, ValueError):
        return False


def fetch_cover(url: str) -> bytes | None:
    if not url:
        return None
    try:
        for _ in range(MAX_COVER_REDIRECTS + 1):
            if not _public_http_url(url):
                log.debug("Cover fetch refused (not a public http(s) URL): %s", url)
                return None
            with requests.get(url, timeout=15, stream=True, allow_redirects=False) as resp:
                if resp.is_redirect:
                    url = urljoin(url, resp.headers.get("Location", ""))
                    continue
                resp.raise_for_status()
                data = resp.raw.read(MAX_COVER_BYTES + 1, decode_content=True)
                if len(data) > MAX_COVER_BYTES:
                    log.debug("Cover fetch refused (over %d bytes): %s", MAX_COVER_BYTES, url)
                    return None
                return resize_cover(data)
        return None
    except Exception as exc:
        log.debug("Cover fetch failed (%s): %s", url, exc)
        return None


def read_cover(file_path: str) -> tuple[bytes, str] | None:
    """Extract embedded cover art → (bytes, mime), or None."""
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".mp3":
            apics = ID3(file_path).getall("APIC")
            if apics:
                return apics[0].data, apics[0].mime or "image/jpeg"
        elif ext == ".flac":
            pics = FLAC(file_path).pictures
            if pics:
                return pics[0].data, pics[0].mime or "image/jpeg"
        elif ext in (".m4a", ".aac", ".mp4"):
            covr = MP4(file_path).get("covr")
            if covr:
                fmt = "image/png" if covr[0].imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
                return bytes(covr[0]), fmt
        elif ext in (".ogg", ".opus"):
            audio = mutagen.File(file_path)
            b64 = audio.get("metadata_block_picture") if audio else None
            if b64:
                pic = Picture(base64.b64decode(b64[0]))
                return pic.data, pic.mime or "image/jpeg"
    except Exception as exc:
        log.debug("read_cover failed for %s: %s", os.path.basename(file_path), exc)
    return None


def embed_cover(file_path: str, image_bytes: bytes, mime: str | None = None,
                preserve_mtime: bool = True) -> str | None:
    """Embed cover art. Returns None on success or a warning string on failure.

    preserve_mtime restores the file's timestamps afterwards (see
    write_track_tags)."""
    if not image_bytes:
        return None
    # Derive the MIME from the actual bytes unless the caller forced one — a small
    # PNG cover skips resize_cover's JPEG re-encode, so a hardcoded image/jpeg
    # label would mis-tag it (and set the wrong MP4Cover format flag).
    if mime is None:
        mime = sniff_image_mime(image_bytes)
    ext = os.path.splitext(file_path)[1].lower()
    try:
        st = os.stat(file_path) if preserve_mtime else None
        if ext == ".mp3":
            try:
                tags = ID3(file_path)
            except ID3NoHeaderError:
                tags = ID3()
            tags.delall("APIC")
            tags["APIC"] = APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_bytes)
            tags.save(file_path)
        elif ext == ".flac":
            audio = FLAC(file_path)
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.data = image_bytes
            audio.clear_pictures()
            audio.add_picture(pic)
            audio.save()
        elif ext in (".m4a", ".aac", ".mp4"):
            audio = MP4(file_path)
            fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
            audio["covr"] = [MP4Cover(image_bytes, imageformat=fmt)]
            audio.save()
        elif ext in (".ogg", ".opus"):
            audio = mutagen.File(file_path)
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.data = image_bytes
            audio["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
            audio.save()
        if st is not None:
            os.utime(file_path, (st.st_atime, st.st_mtime))
    except Exception as exc:
        log.warning("Cover embed failed for %s: %s", os.path.basename(file_path), exc)
        return f"cover embed failed: {exc}"
    return None
