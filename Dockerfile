# Music Grabber: Spotify playlist watcher + downloader companion for a Navidrome library.
# Image: gatoserio/music-grabber (published on GitHub releases; see .github/workflows/docker-publish.yml)

# ── Web UI bundle (frontend/dist), served by the API process ────────────────────────────
FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Application image ──────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# ffmpeg: every download is transcoded to one output format.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# streamrip and yt-dlp break often and change cadence independently of this code, so the
# dependency layer is its own: rebuilding with a fresh yt-dlp doesn't touch the app layer.
COPY requirements.txt requirements-pillow.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# A current Pillow over streamrip's <11 pin (see requirements-pillow.txt), and fail the build if it did not take.
RUN pip install --no-cache-dir -r requirements-pillow.txt \
    && python -c "import PIL; assert int(PIL.__version__.split('.')[0]) >= 12, PIL.__version__"

COPY music_grabber/ music_grabber/
COPY --from=frontend /fe/dist ./frontend/dist
COPY VERSION CHANGELOG.md ./

# /data: SQLite DB, settings.json, grab_tmp. /music: where files are filed (and, unless
# LIBRARY_DIR says otherwise, what is scanned for "do we already have it?").
RUN mkdir -p /data /music
VOLUME ["/data"]

# The API listens on all interfaces inside the container but is off unless API_TOKEN is set;
# publish it on loopback only (see docker-compose.yml) unless you front it with TLS.
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data MUSIC_DIR=/music API_HOST=0.0.0.0 API_PORT=8099
EXPOSE 8099

CMD ["python", "-m", "music_grabber"]
