# Music Grabber

Spotify playlist watcher + downloader companion for [Navidrome](https://www.navidrome.org/)
libraries, split out of [BPM Tagger](https://github.com/PauloJf/BPM-Tagger).

**Private, early.** A CLI, a JSON API and a small web UI (inbox, queue, playlists). Version in
[`VERSION`](VERSION), history in [`CHANGELOG.md`](CHANGELOG.md). Origin: extracted from BPM Tagger
v2.18.2 (`eae3f0c2662f590ac8cf0334a282389147152d2f`) with its history kept, see
[`docs/provenance.md`](docs/provenance.md); the design and the reasons for the split are in
[`docs/plans/extraction.md`](docs/plans/extraction.md).

> **Legal / ToS.** This tool can download from Deezer using *your own* ARL and from YouTube via
> yt-dlp. Those uses may breach those services' terms and local law. That exposure is the reason
> this lives in its own private repo and its image is never published by automation. Use it only
> on your own account, for music you are entitled to have, at your own risk.

**Contents:** [How it works](#how-it-works) · [Quick start](#quick-start) ·
[Spotify](#spotify-setup) · [Deezer](#deezer-arl) · [Compose examples](#compose-examples) ·
[Using it](#using-it) · [BPM Tagger, Navidrome, Mediarium](#working-with-bpm-tagger-navidrome-and-mediarium) ·
[Moving from BPM Tagger](#moving-from-bpm-tagger) · [Configuration](#configuration) ·
[Troubleshooting](#troubleshooting) · [Development](#development)

## How it works

1. **Sync.** Reads the Spotify playlists you watch (Authorization Code OAuth; the refresh token
   lives in its own SQLite database).
2. **Reconcile.** For each track it asks the library index whether you already have it: a file this
   tool filed earlier (the *ledger*, by Spotify id), an equal ISRC, or a fuzzy title + artist +
   duration score. Result per track: have / missing / queued.
3. **Download.** Missing tracks are queued. Providers run in `PROVIDER_ORDER`: Deezer via
   [streamrip](https://github.com/nathom/streamrip) with your ARL, then yt-dlp. A candidate scoring
   above `AUTO_ACCEPT_THRESHOLD` (or matching the ISRC) downloads at once; anything ambiguous waits
   in the **inbox** and pings you on ntfy.
4. **Finish.** ffmpeg transcodes to one `OUTPUT_FORMAT`, tags and cover art are written, and the
   file is filed by `PATH_TEMPLATE` atomically (written under a `.part` name, then renamed) and
   recorded in the ledger. Optional post-import hooks then tell Mediarium or Navidrome.

It does **not** detect BPM: that belongs to [BPM Tagger](#working-with-bpm-tagger-navidrome-and-mediarium).
File into a folder it watches and it tags the new files itself. The two share a folder and, if
you like, HTTP; never a database or Python imports.

## Quick start

```bash
git clone git@github.com:PauloJf/Music-Grabber.git && cd Music-Grabber
# edit docker-compose.yml: your music path, Spotify keys, WATCH_PLAYLISTS, DEEZER_ARL
docker compose build                      # there is no published image: build it yourself
docker compose run --rm music-grabber python -m music_grabber spotify-login   # once
docker compose up -d
docker compose exec music-grabber python -m music_grabber status
```

Add `API_TOKEN` (16+ characters) and publish port 8099 on loopback to get the web UI at
`http://127.0.0.1:8099/`.

### Spotify setup

1. Create an app at <https://developer.spotify.com/dashboard>. Add a redirect URI. Spotify's
   rules for these have been changing (it currently wants HTTPS, or a *loopback IP literal* such
   as `http://127.0.0.1:8099/callback`, not `localhost`); follow the dashboard's own hints. The
   URI does not need to be reachable from the internet.
2. Put the client id and secret and the same redirect URI in the environment
   (`SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`).
3. Connect once, either way:
   - **Terminal:** `python -m music_grabber spotify-login`. Open the printed URL, approve, then paste
     back the URL Spotify redirected to (the page may show an error, only the `code` matters).
   - **Browser:** with the API on, click *Connect Spotify* in the web UI. The redirect URI must
     then point at this instance's `/callback`.
4. Watch playlists with `WATCH_PLAYLISTS=<url>,<url>`, `playlist add <url>`, or the Playlists page.

The tool only reads your playlists (`playlist-read-private`, `playlist-read-collaborative`).

### Deezer ARL

Log in to Deezer in a browser and copy the value of the `arl` cookie into `DEEZER_ARL`. It is a
session credential: never logged, never in the repo, masked in the API and CLI. A free account
gives 128 kbps MP3; 320 kbps and FLAC need a paid subscription (`DEEZER_QUALITY`). When Deezer
rejects the ARL (they expire) you get one ntfy alert and downloads fall back to yt-dlp until you
set a fresh one (`settings set deezer_arl ...` works without a restart; `settings test-deezer`
checks it). Without an ARL only yt-dlp is used.

## Compose examples

Ready-to-adapt files in [`docs/compose/`](docs/compose/):

| File | What |
|---|---|
| [`standalone.yml`](docs/compose/standalone.yml) | Music Grabber on its own; files go straight into your library |
| [`with-bpm-tagger.yml`](docs/compose/with-bpm-tagger.yml) | **Shared folder with BPM Tagger** (intake mode): Music Grabber files into `/music/Music-raw`, BPM Tagger's watcher tags what arrives |
| [`full-stack.yml`](docs/compose/full-stack.yml) | Navidrome + BPM Tagger + Music Grabber, with the library index over BPM Tagger's OpenSubsonic API and an optional Mediarium hook |

They read secrets from a `.env` next to the file (see [`.env.example`](.env.example)); nothing
secret is written into them. A test keeps their variable names in step with the code.

## Using it

### Command line

```bash
python -m music_grabber status                  # what is going on (add --json for scripts)
python -m music_grabber playlist add https://open.spotify.com/playlist/...
python -m music_grabber sync                    # reconcile now (or: sync <playlist id>)
python -m music_grabber queue                   # in-flight work; also: history, show, retry, cancel ...
python -m music_grabber inbox                   # ambiguous matches waiting for you
python -m music_grabber inbox show 12           # candidates with scores, plus the next commands
python -m music_grabber inbox choose 12 41      # download candidate 41 for item 12
python -m music_grabber inbox search 12 "weeknd blinding lights"   # try another search
python -m music_grabber settings set output_format flac
```

In Docker run them inside the container (`docker compose exec music-grabber python -m
music_grabber status`), or from anywhere against a running instance with `--remote
http://host:8099 --token ...` (or `MG_REMOTE_URL` + `API_TOKEN`). `--json` works before or after
the command. Exit codes: 0 ok, 1 failed, 2 invalid request or configuration. When a track is
ambiguous the ntfy ping carries the command to run; with `UI_PUBLIC_URL` set it also links to the
item in the web UI.

### Web UI

With `API_TOKEN` set, the same process serves a small UI at `http://host:8099/`: **Inbox**
(candidates with scores, *Download this*, search for something else, skip), **Queue** (active /
failed / history, retry, cancel, move to front, per-item event log) and **Playlists** (coverage,
watch toggle, sync, queue missing, tracks by status), plus *Connect Spotify*. It signs in with the
API token (kept for that browser tab only), loads nothing from outside its own origin, and works
on a phone. The Docker image builds it; from a checkout: `cd frontend && npm ci && npm run build`
(`npm run dev` proxies to a running instance on `:8099`). Settings and the BPM Tagger import stay
on the command line.

### JSON API

Set `API_TOKEN` and the API listens on `API_PORT`. Every `/api/*` call needs
`Authorization: Bearer <token>`; there are no cookies and no CSRF surface. Routes mirror the CLI:
`GET /api/status`; `/api/playlists` (`POST {"value": url}`, `PATCH {"enabled": false}`, `DELETE`,
`POST /api/playlists/<id>/sync`, `/enqueue-missing`); `POST /api/sync`; `/api/queue` and
`/api/queue/<id>/{retry,cancel,priority}`; `/api/inbox` and `/api/inbox/<id>/{choose,search,research,skip}`;
`/api/settings` (secrets come back masked); `/api/spotify/*`; `POST /api/library/refresh`; open:
`GET /healthz` and the Spotify `/callback` (which only accepts a single-use `state` this instance
issued). Errors are `{"error": "<code>", "message": "..."}` with a matching HTTP status. Publish
the port on loopback only, and put TLS in front before exposing it further.

### Settings

`settings show` / `settings set KEY VALUE ...` (or `POST /api/settings`) change what makes sense at
runtime: `output_format`, `path_template`, `provider_order`, `deezer_quality`, `deezer_arl`,
`auto_accept_threshold`, `ask_threshold`, `grab_dry_run`, `lyrics_enabled`,
`spotify_sync_minutes`, `grab_workers`, `ntfy_url`, `ntfy_topic`, `ntfy_notify_done`,
`ui_public_url`. Values are validated, saved to `settings.json` (mode 0600, next to the database)
and applied to the running instance without a restart (`grab_workers` and `spotify_sync_minutes`
need one). Settings saved this way override the environment from then on. Credentials that come
from the environment (Spotify secret, API token, library/hook keys) are never written to the file.

## Working with BPM Tagger, Navidrome and Mediarium

**BPM Tagger, intake mode (recommended).** Mount the same music folder into both containers at the
same path and file new tracks into a subfolder BPM Tagger watches
([`with-bpm-tagger.yml`](docs/compose/with-bpm-tagger.yml)):

```yaml
# Music Grabber
MUSIC_DIR: /music                 # BPM Tagger's MUSIC_DIR (the whole library)
ANALYSIS: intake
INTAKE_DIR: /music/Music-raw      # a subfolder; BPM Tagger's watcher is recursive
LIBRARY_INDEX: filesystem         # scans /music, so intake and library are both covered
```

BPM Tagger's watcher sees each placed file, detects the BPM, writes the tag and, if configured, asks
Navidrome to rescan *after* tagging. Leave Music Grabber's own `navidrome` hook off in this mode: a
rescan before the BPM tag exists is one Navidrome's quick scan never revisits. Music Grabber never
touches a file after placing it, so BPM Tagger analyses it exactly once. `ANALYSIS_WAIT_SECONDS`
can make it wait (blocking one worker) and record whether the BPM tag appeared.

**Library index over HTTP.** If Music Grabber cannot see the library files, let it ask BPM Tagger
(or Navidrome): `LIBRARY_INDEX=opensubsonic`, `LIBRARY_URL=http://bpm-tagger:5000`,
`LIBRARY_API_KEY=<an OpenSubsonic API key>` (on BPM Tagger: `SUBSONIC_ENABLED=true`, create the key
in its settings). New tracks only appear there once BPM Tagger has indexed them; until then the
ledger (matching by Spotify id) keeps them from being grabbed twice. See
[`full-stack.yml`](docs/compose/full-stack.yml).

**`ANALYSIS=api`.** For a BPM Tagger that runs one-shot scans instead of watching: also asks it to
scan (coalesced). It logs in with BPM Tagger's admin password, so it does not work with admin
two-factor; prefer `intake` with the watcher.

**Navidrome without BPM Tagger.** `POST_IMPORT_HOOKS=navidrome` (+ `NAVIDROME_URL`, `_USER`, `_PASS`)
asks Navidrome to rescan after files are filed: one request per burst, not one per track.

**Mediarium.** `POST_IMPORT_HOOKS=mediarium` (+ `MEDIARIUM_URL`, `MEDIARIUM_API_KEY`) calls
`POST /api/music/import/scan` after files are filed. An album of twelve tracks is one request
(trailing debounce, a max wait so a long run still fires, a minimum interval between scans). If
Mediarium's music module is off it answers 404 and the hook pauses for an hour; if the key is wrong
or not allowed to call that route (401/403) it logs one clear line and disables itself until
restart. The key goes in `X-Api-Key` by default (`MEDIARIUM_API_KEY_HEADER=Authorization` sends a
Bearer token). Off by default.

## Moving from BPM Tagger

If you have been running BPM Tagger's built-in grabber, follow
[`docs/migration-from-bpm-tagger.md`](docs/migration-from-bpm-tagger.md). In short:

```bash
python -m music_grabber import-env /path/to/bpm-tagger-data/settings.json       # prints a config block, never secrets
python -m music_grabber import-bpm-tagger /path/to/bpm_tagger.db --dry-run      # read-only look
python -m music_grabber import-bpm-tagger /path/to/bpm_tagger.db --source-music-dir /music
```

It copies Spotify playlists and tracks, the queue with candidates and events, the lifetime counter
and every `managed` track (as a ledger row). Navidrome and Local playlists, BPM detection and the
player stay in BPM Tagger. What you lose for now (Spotify playlists as Run/Listen sources, the
Suggestions page) is listed in the guide.

## Configuration

Names match BPM Tagger's grabber settings, so an existing compose block carries over. Everything is
an environment variable; a few can also be changed at runtime ([Settings](#settings)).

| Variable | Default | |
|---|---|---|
| `MUSIC_DIR` | `/music` | where grabbed files are filed (BPM Tagger's `MUSIC_DIR` meaning) |
| `LIBRARY_DIR` | = `MUSIC_DIR` | what the filesystem index scans for tracks you already have (point at a parent when you file into a subfolder) |
| `DATA_DIR` | `/data` | base for the default `DB_PATH` |
| `DB_PATH` | `/data/music_grabber.db` | `settings.json` and `grab_tmp/` live next to it |
| `LOG_LEVEL` | `INFO` | |
| `SPOTIFY_CLIENT_ID` / `_SECRET` / `_REDIRECT_URI` | | env only; never written to `settings.json` |
| `SPOTIFY_SYNC_MINUTES` | `30` | |
| `WATCH_PLAYLISTS` | | URLs, URIs or ids, comma/newline separated |
| `PROVIDER_ORDER` | `deezer,ytdlp` | |
| `DEEZER_ARL`, `DEEZER_QUALITY` | `` / `MP3_128` | `MP3_128` \| `MP3_320` \| `FLAC` |
| `OUTPUT_FORMAT` | `mp3-128` | `mp3-320`, `mp3-128`, `flac`, `opus-192` |
| `PATH_TEMPLATE` | `{AlbumArtist}/{Album}/{TrackNo:02d} - {Title}.{ext}` | |
| `AUTO_ACCEPT_THRESHOLD` / `ASK_THRESHOLD` | `0.85` / `0.55` | |
| `GRAB_WORKERS` | `1` | 1 to 3 |
| `GRAB_DRY_RUN` | `false` | search and score, never download |
| `LYRICS_ENABLED` | `false` | embed LRCLIB lyrics |
| `LIBRARY_INDEX` | `filesystem` | `filesystem` \| `opensubsonic` |
| `LIBRARY_URL`, `LIBRARY_API_KEY` (or `LIBRARY_USER` + `LIBRARY_PASS`) | | for `opensubsonic`: BPM Tagger's Subsonic API or Navidrome |
| `ANALYSIS` | `none` | `none` \| `intake` \| `api` |
| `INTAKE_DIR` | | for `intake`/`api`: a directory BPM Tagger's watcher covers |
| `ANALYSIS_WAIT_SECONDS` | `0` | `intake`/`api`: wait up to this long for the BPM tag (blocks one worker) |
| `BPM_TAGGER_URL`, `BPM_TAGGER_USERNAME`, `BPM_TAGGER_PASSWORD` | | for `api`: BPM Tagger's admin login |
| `POST_IMPORT_HOOKS` | | comma list: `mediarium`, `navidrome` |
| `MEDIARIUM_URL`, `MEDIARIUM_API_KEY`, `MEDIARIUM_API_KEY_HEADER` | `` / `` / `X-Api-Key` | |
| `NAVIDROME_URL`, `NAVIDROME_USER`, `NAVIDROME_PASS` | | for the `navidrome` hook |
| `HOOK_DEBOUNCE_SECONDS` / `HOOK_MAX_WAIT_SECONDS` / `HOOK_MIN_INTERVAL_SECONDS` | `45` / `600` / `60` | hook coalescing |
| `NTFY_URL`, `NTFY_TOPIC` | | an empty topic disables ntfy |
| `NTFY_NOTIFY_DONE` | `true` | a ping per finished track; ambiguity, failures and credential problems always ping |
| `UI_PUBLIC_URL` | | makes pings link to the item in the web UI |
| `API_TOKEN` | | enables the API and UI; 16+ characters; env only |
| `API_HOST` / `API_PORT` | `127.0.0.1` (`0.0.0.0` in the image) / `8099` | |
| `UI_DIR` | `frontend/dist` | where the built web UI is looked for |
| `MG_REMOTE_URL` | | CLI only: default for `--remote` |

Secrets (`SPOTIFY_CLIENT_SECRET`, `LIBRARY_API_KEY`, `LIBRARY_PASS`, `BPM_TAGGER_PASSWORD`,
`MEDIARIUM_API_KEY`, `NAVIDROME_PASS`, `API_TOKEN`) are read from the environment only. A
configuration that cannot work (unknown index, `intake` without `INTAKE_DIR`, a listed hook with no
URL, a short `API_TOKEN`) makes `run` exit with code 2 and say what to set.

## Troubleshooting

- **Every track shows as missing.** `status` → *Library*. If it says 0 tracks the index cannot see
  your files: mount the library at the path `LIBRARY_DIR`/`MUSIC_DIR` names, or switch to
  `LIBRARY_INDEX=opensubsonic`. A refresh that sees under half of a known library is treated as a
  failure and keeps the old cache, so a briefly unmounted share does not re-queue everything.
- **"Spotify is not connected".** Run `spotify-login`, or *Connect Spotify* in the UI. If Spotify
  complains about the redirect URI, it must match the dashboard entry exactly (see Spotify setup).
- **Deezer login failed ping.** The ARL expired. Set a fresh one; downloads use yt-dlp meanwhile.
- **A track sits in the inbox.** The best match scored under `AUTO_ACCEPT_THRESHOLD`. Pick a
  candidate, search for something else, or skip it. Raising the threshold makes more go to the
  inbox; lowering it risks wrong files.
- **BPM Tagger never tags the new files.** Check it is running in `watch` mode and that both
  containers see the same folder (same `/music`), with `INTAKE_DIR` inside BPM Tagger's `MUSIC_DIR`.
- **API refuses to start / exit code 2.** Read the message: it names the setting. `API_TOKEN` needs
  16+ characters (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
- **Nothing downloads.** `status` → *Providers*. With no ARL only yt-dlp runs; with
  `GRAB_DRY_RUN=true` nothing is ever downloaded.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ruff check . && pytest -q
cd frontend && npm ci && npm run typecheck && npm test && npm run build
```

Tests never touch the network (a socket guard fails any non-loopback connection) and need no
credentials; Spotify, Deezer, yt-dlp, Mediarium, BPM Tagger and ntfy are faked. See
[`CLAUDE.md`](CLAUDE.md) for the project rules.

## Licence

AGPL-3.0-or-later, same as BPM Tagger. See [`LICENSE`](LICENSE).
