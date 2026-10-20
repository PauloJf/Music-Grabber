# Music Grabber: Spotify playlist watcher + downloader companion for a Navidrome library.
# Image: gatoserio/music-grabber (published manually only; see .github/workflows/docker-publish.yml)
FROM python:3.12-slim

# ffmpeg: every download is transcoded to one output format.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# streamrip and yt-dlp break often and change cadence independently of this code, so the
# dependency layer is its own: rebuilding with a fresh yt-dlp doesn't touch the app layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY music_grabber/ music_grabber/
COPY VERSION CHANGELOG.md ./

# /data: SQLite DB, settings.json, grab_tmp. /music: where files are filed (and, unless
# LIBRARY_DIR says otherwise, what is scanned for "do we already have it?").
RUN mkdir -p /data /music
VOLUME ["/data"]

ENV PYTHONUNBUFFERED=1 DATA_DIR=/data MUSIC_DIR=/music

CMD ["python", "-m", "music_grabber"]
