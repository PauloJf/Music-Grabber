# Music Grabber

Spotify playlist watcher + downloader companion for [Navidrome](https://www.navidrome.org/)
libraries, split out of [BPM Tagger](https://github.com/PauloJf/BPM-Tagger).

**v0.1.0, private, early.** Headless for now: no web UI yet (see the roadmap). Origin and
history: [`docs/provenance.md`](docs/provenance.md), extracted from BPM Tagger v2.18.2
(`eae3f0c2662f590ac8cf0334a282389147152d2f`). The design and the reasons for the split:
[`docs/plans/extraction.md`](docs/plans/extraction.md).

> **Legal / ToS.** This tool can download from Deezer using *your own* ARL and from YouTube
> via yt-dlp. Those uses may breach those services' terms and local law. That exposure is
> the reason this lives in its own private repo and its image is never published by
> automation. Use it only on your own account, for music you are entitled to have, at your
> own risk.

## What it does

1. **Sync.** Reads the Spotify playlists you watch (Authorization Code OAuth; the refresh
   token is stored in its own SQLite DB).
2. **Reconcile.** For each track, asks the library index whether you already have it: a file
   this tool filed earlier (ledger, by Spotify id), an equal ISRC, or a fuzzy title +
   artist + duration score. Result per track: have / missing / queued.
3. **Download.** Missing tracks are queued. Providers run in `PROVIDER_ORDER`: Deezer via
   [streamrip](https://github.com/nathom/streamrip) with your ARL, then yt-dlp. A candidate
   scoring above `AUTO_ACCEPT_THRESHOLD` (or matching ISRC) downloads at once; anything
   ambiguous waits in the inbox (`awaiting_user`) with an ntfy ping.
4. **Finish.** ffmpeg transcodes to one `OUTPUT_FORMAT`, tags and cover art are written,
   then the file is filed by `PATH_TEMPLATE` (atomically) and recorded in the ledger.

It does **not** detect BPM. That belongs to BPM Tagger: point this tool at a folder BPM
Tagger watches and it will tag the new files itself (intake mode, coming in 0.2.0).

## Quick start (Docker)

```bash
git clone git@github.com:PauloJf/Music-Grabber.git && cd Music-Grabber
# edit docker-compose.yml: your music path, Spotify keys, WATCH_PLAYLISTS, DEEZER_ARL
docker compose build
docker compose run --rm music-grabber python -m music_grabber spotify-login   # once
docker compose up -d
docker compose logs -f
```

There is no published image: build it yourself.

### Spotify setup

1. Create an app at <https://developer.spotify.com/dashboard>; add a redirect URI such as
   `http://127.0.0.1:8099/callback` (it does not need to be reachable).
2. Put the client id/secret and the same redirect URI in the environment.
3. Run `python -m music_grabber spotify-login`: open the printed URL, approve, then paste
   back the URL Spotify redirected to (the page may show an error; only the `code` matters).

### Deezer ARL

Log in to Deezer in a browser and copy the `arl` cookie into `DEEZER_ARL`. It is a session
credential: it is never logged and never written to the repo. A free account yields
128 kbps MP3; 320 kbps / FLAC need a paid subscription. Without an ARL only yt-dlp is used.

## Configuration

Names match BPM Tagger's grabber settings, so an existing compose block carries over.

| Variable | Default | |
|---|---|---|
| `MUSIC_DIR` | `/music` | where grabbed files are filed |
| `LIBRARY_DIR` | = `MUSIC_DIR` | what is scanned for tracks you already have (point at a parent when you file into a subfolder) |
| `DB_PATH` | `/data/music_grabber.db` | `settings.json` and `grab_tmp/` live next to it |
| `SPOTIFY_CLIENT_ID` / `_SECRET` / `_REDIRECT_URI` | | env only; never written to `settings.json` |
| `SPOTIFY_SYNC_MINUTES` | `30` | |
| `WATCH_PLAYLISTS` | | URLs, URIs or ids, comma/newline separated |
| `PROVIDER_ORDER` | `deezer,ytdlp` | |
| `DEEZER_ARL`, `DEEZER_QUALITY` | | `MP3_128` \| `MP3_320` \| `FLAC` |
| `OUTPUT_FORMAT` | `mp3-128` | `mp3-320`, `mp3-128`, `flac`, `opus-192` |
| `PATH_TEMPLATE` | `{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}` | |
| `AUTO_ACCEPT_THRESHOLD` / `ASK_THRESHOLD` | `0.85` / `0.55` | |
| `GRAB_WORKERS` | `1` | 1 to 3 |
| `GRAB_DRY_RUN` | `false` | search and score, never download |
| `LYRICS_ENABLED` | `false` | embed LRCLIB lyrics |
| `NTFY_URL`, `NTFY_TOPIC` | | empty topic disables |
| `UI_PUBLIC_URL` | | used for click-through links in pings |
| `LOG_LEVEL` | `INFO` | |

## Roadmap

See [`docs/plans/extraction.md`](docs/plans/extraction.md) section 6.

- **0.1.0** (this): skeleton move, own DB, filesystem library index, ledger, tests, CI.
- **0.2.0**: OpenSubsonic (BPM Tagger / Navidrome) library index; intake-folder and API
  analysis modes; Mediarium and Navidrome post-import hooks.
- **0.3.0**: JSON API + CLI (`sync`, `queue`, `inbox`, `status`), settings persistence, ntfy,
  and an import tool for existing BPM Tagger `grab_*` data.
- **0.4.0**: minimal web UI (playlists, queue, ambiguity inbox).
- **0.5.0**: docs polish, compose examples, migration guide.

Until the CLI lands, an ambiguous track waits in the database inbox (`awaiting_user`) and
the ntfy ping tells you it is there; there is nothing to click yet.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ruff check . && pytest -q
```

Tests never touch the network (a socket guard fails any non-loopback connection) and need
no credentials; Spotify, Deezer and yt-dlp are faked. See [`CLAUDE.md`](CLAUDE.md) for the
project rules.

## Licence

AGPL-3.0-or-later, same as BPM Tagger. See [`LICENSE`](LICENSE).
