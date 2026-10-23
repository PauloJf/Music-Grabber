"""Tiny valid audio files built without any audio libraries (no numpy/soundfile/ffmpeg)."""

import struct


def minimal_flac_bytes(seconds: int = 1) -> bytes:
    """A FLAC stream with only a STREAMINFO block (no audio frames): enough for mutagen
    to read, tag, embed pictures and save, which is all the tagging tests need."""
    sr, channels, bps, total = 44100, 2, 16, 44100 * seconds
    packed = (sr << 44) | ((channels - 1) << 41) | ((bps - 1) << 36) | total
    streaminfo = (struct.pack(">HH", 4096, 4096) + b"\x00\x00\x00" + b"\x00\x00\x00"
                  + struct.pack(">Q", packed) + b"\x00" * 16)
    assert len(streaminfo) == 34
    return b"fLaC" + b"\x80" + len(streaminfo).to_bytes(3, "big") + streaminfo


def write_flac(path, seconds: int = 1) -> None:
    with open(path, "wb") as fh:
        fh.write(minimal_flac_bytes(seconds))


def minimal_mp3_bytes(frames: int = 12) -> bytes:
    """A few silent MPEG-1 Layer III frames (128 kbps, 44.1 kHz): enough for mutagen to
    recognise an mp3 and for ID3 tags to be written in front of it."""
    return (b"\xff\xfb\x90\x00" + b"\x00" * 413) * frames
