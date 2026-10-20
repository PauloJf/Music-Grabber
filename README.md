# Music Grabber

Spotify playlist watcher + downloader companion for [Navidrome](https://www.navidrome.org/)
libraries, split out of [BPM Tagger](https://github.com/PauloJf/BPM-Tagger).

**v0.2.0-dev, private, early.** Headless for now: no web UI yet (see the roadmap). Origin and
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

It does **not** detect BPM. That belongs to BPM Tagger: file into a folder BPM Tagger
watches and it tags the new files itself (see *Working with BPM Tagger* below).

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
| `LIBRARY_INDEX` | `filesystem` | `filesystem` \| `opensubsonic` |
| `LIBRARY_URL`, `LIBRARY_API_KEY` (or `LIBRARY_USER` + `LIBRARY_PASS`) | | for `opensubsonic`: BPM Tagger's Subsonic API or Navidrome |
| `ANALYSIS` | `none` | `none` \| `intake` \| `api` |
| `INTAKE_DIR` | | for `intake`/`api`: a directory BPM Tagger's watcher covers |
| `ANALYSIS_WAIT_SECONDS` | `0` | `intake`/`api`: wait up to this long for the BPM tag (blocks one worker) |
| `BPM_TAGGER_URL`, `BPM_TAGGER_USERNAME`, `BPM_TAGGER_PASSWORD` | | for `api`: BPM Tagger's admin login |
| `POST_IMPORT_HOOKS` | | comma list: `mediarium`, `navidrome` |
| `MEDIARIUM_URL`, `MEDIARIUM_API_KEY`, `MEDIARIUM_API_KEY_HEADER` | | header defaults to `X-Api-Key` (`Authorization` sends a Bearer token) |
| `NAVIDROME_URL`, `NAVIDROME_USER`, `NAVIDROME_PASS` | | for the `navidrome` hook |
| `HOOK_DEBOUNCE_SECONDS` / `HOOK_MAX_WAIT_SECONDS` / `HOOK_MIN_INTERVAL_SECONDS` | `45` / `600` / `60` | hook coalescing |

Secrets (`SPOTIFY_CLIENT_SECRET`, `LIBRARY_API_KEY`, `LIBRARY_PASS`, `BPM_TAGGER_PASSWORD`,
`MEDIARIUM_API_KEY`, `NAVIDROME_PASS`) are read from the environment only and never written
to `settings.json`. A configuration that cannot work (unknown index, `intake` without
`INTAKE_DIR`, a listed hook with no URL) makes `run` exit with code 2 and say what to set.

## Working with BPM Tagger

Music Grabber never imports BPM Tagger and shares no database with it. The coupling is a
folder and, optionally, HTTP.

**Intake (recommended).** Mount the same music folder into both containers, then file new
tracks into a subfolder BPM Tagger watches:

```yaml
# Music Grabber
MUSIC_DIR: /music                 # BPM Tagger's MUSIC_DIR (the whole library)
ANALYSIS: intake
INTAKE_DIR: /music/Music-raw      # a subfolder; BPM Tagger's watcher is recursive
LIBRARY_INDEX: filesystem         # scans /music, so intake and library are both covered
```

BPM Tagger's watcher sees each placed file (it is written under a `.part` name and renamed
into place, so it never sees a half-written file), detects the BPM, writes the tag and, if
configured, asks Navidrome to rescan *after* tagging. So leave the `navidrome` hook off in
this mode: a rescan before the BPM tag exists is one Navidrome's quick scan never revisits.
Music Grabber never touches a file after placing it, so BPM Tagger analyses it exactly once.

**Library index over HTTP.** If Music Grabber cannot see the library files, let it ask
BPM Tagger (or Navidrome) instead: `LIBRARY_INDEX=opensubsonic`, `LIBRARY_URL=http://bpm-tagger:5000`,
`LIBRARY_API_KEY=<an OpenSubsonic API key>`. On BPM Tagger turn on `SUBSONIC_ENABLED` and
create the key in its settings. Newly grabbed tracks only show up there once BPM Tagger has
indexed them; meanwhile the ledger (matching by Spotify id) keeps them from being grabbed
twice.

**`ANALYSIS=api`.** For a BPM Tagger that runs one-shot scans instead of watching: also asks it
to scan (coalesced). It logs in with the admin password, so it does not work with admin
two-factor; prefer `intake` with the watcher.

## Post-import hooks

`POST_IMPORT_HOOKS=mediarium` calls Mediarium's `POST /api/music/import/scan` after files are
filed. An album of twelve tracks is one request (trailing debounce, a max wait so a long run
still fires, a minimum interval between scans). If Mediarium's music module is off it
answers 404 and the hook pauses for an hour; if the key is wrong or not allowed to call that
route (401/403) it logs one clear line and disables itself until restart. Off by default.

## Roadmap

See [`docs/plans/extraction.md`](docs/plans/extraction.md) section 6.

- **0.1.0**: skeleton move, own DB, filesystem library index, ledger, tests, CI.
- **0.2.0** (this): OpenSubsonic (BPM Tagger / Navidrome) library index; intake-folder and API
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
