# Music Grabber

**Watch your Spotify playlists, browse Deezer, and fill the gaps in your Navidrome library.**

Music Grabber looks at what you already own, finds what's missing, downloads it (Deezer through
your own account, then YouTube), converts everything to one format, tags it and files it in your
music folder. It is a standalone companion to a [Navidrome](https://www.navidrome.org/) library,
with a web UI (search Deezer, queue songs, an inbox for ambiguous matches, live progress), a CLI and
a JSON API, plus accounts with a login page.

## Where it comes from

Music Grabber began as a feature of [**BPM Tagger**](https://github.com/PauloJf/BPM-Tagger), my
tool that detects the tempo and loudness of every track in a Navidrome library and writes the
result into the files. Early on, BPM Tagger grew a "grabber": a way to turn Spotify playlists into
files, so that new music arrived already analysed. It worked, and it grew into a real subsystem with
its own queue, scoring, providers, transcoding, tagging and ledger.

That subsystem had very little in common with the rest of BPM Tagger:

- **Different risk.** It talks to Deezer with a personal ARL cookie and to YouTube through
  yt-dlp, which can breach those services' terms. BPM Tagger is a public, image-published tool;
  that exposure didn't belong in it.
- **Different dependencies.** streamrip and yt-dlp break often and move on their own schedule,
  while BPM Tagger ships heavy audio-analysis libraries (librosa, essentia) on a calm one.
- **Different cadence.** A fix for a broken downloader shouldn't be a BPM Tagger release, and a
  tempo-detection change shouldn't rebuild a downloader.

So it was **spun off**. Music Grabber was extracted from BPM Tagger v2.18.2
(`eae3f0c2662f590ac8cf0334a282389147152d2f`) with its git history preserved (`git log --follow`
reaches back into BPM Tagger), then rebuilt as its own project: its own repository, SQLite database,
Docker image and release cycle, under the same licence (AGPL-3.0-or-later). Since then it has
outgrown the original: pluggable library sources, a post-download analysis port, a Deezer browsing
UI, per-user accounts and a security review.

The two still work well together, but only through a **shared music folder and, optionally, HTTP**,
never a shared database or Python imports (a test enforces it). Drop Music Grabber's output into a
folder BPM Tagger watches and the new files are analysed automatically, or run Music Grabber alone.
BPM Tagger can drop its own grabber once Music Grabber is stable; see
[Moving from BPM Tagger](#moving-from-bpm-tagger).

Details: [`docs/provenance.md`](docs/provenance.md) (exactly what moved and how),
[`docs/plans/extraction.md`](docs/plans/extraction.md) (the plan and its reasoning),
[`docs/security.md`](docs/security.md), [`CHANGELOG.md`](CHANGELOG.md); the version is in [`VERSION`](VERSION).

> **Legal / ToS.** This tool can download from Deezer using *your own* ARL and from YouTube via
> yt-dlp. Those uses may breach those services' terms and local law. That exposure is why it
> was split out of BPM Tagger, lives in its own private repo, and has its image published only
> when you create a GitHub release. Use it only on your own account, for music you are entitled
> to have, at your own risk.

**Contents:** [Where it comes from](#where-it-comes-from) · [How it works](#how-it-works) ·
[Quick start](#quick-start) · [Spotify](#spotify-setup) · [Deezer](#deezer-arl) ·
[Compose examples](#compose-examples) · [Using it](#using-it) · [Library and ledger](#library-and-the-ledger) · [Playlist mirror](#spotify-playlists-in-navidrome-the-mirror) ·
[BPM Tagger, Navidrome, Mediarium](#working-with-bpm-tagger-navidrome-and-mediarium) ·
[Moving from BPM Tagger](#moving-from-bpm-tagger) · [Configuration](#configuration) ·
[Stability](#stability) · [Troubleshooting](#troubleshooting) · [Development](#development) · [Releasing](#releasing)

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
docker compose build                      # or pull gatoserio/music-grabber once a release is published
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
python -m music_grabber keys list               # API keys for other apps (create, revoke too)
python -m music_grabber ledger rebuild          # re-add ledger rows for files carrying our Spotify id tag
```

In Docker run them inside the container (`docker compose exec music-grabber python -m
music_grabber status`), or from anywhere against a running instance with `--remote
http://host:8099 --token ...` (or `MG_REMOTE_URL` + `API_TOKEN`). `--json` works before or after
the command. Exit codes: 0 ok, 1 failed, 2 invalid request or configuration. When a track is
ambiguous the ntfy ping carries the command to run; with `UI_PUBLIC_URL` set it also links to the
item in the web UI.

### Web UI

With `API_TOKEN` set, the same process serves the app at `http://host:8099/`. It is built for a phone
first (tab bar, thumb-sized targets, safe areas) and works as well on a desktop.

- **Discover**: search Deezer for songs, artists and albums (search as you type, recent searches,
  `/` to focus). Open an **artist** (popular tracks, albums vs singles and EPs, related artists) or an
  **album** (track list, select some or queue what's missing). Every song shows its state live:
  *Queue*, *Queued*, a progress ring while it downloads, *In library*, *Needs you*, or *Failed* with a
  retry. Preview 30 seconds before you decide.
- **Instant feedback**: pressing *Queue* flips the row at once, a toast confirms (with **Undo**), a
  floating activity button shows what's running, and you get a toast when a download finishes or fails.
- **Queue**: Now / Up next / Failed / Finished, retry, cancel, move to front, per-item event log.
- **Inbox**: ambiguous matches with scored candidates: hear 30 seconds of each (and of the original), then download one, search again, or skip.
- **Library**: watched Spotify playlists with coverage, sync, queue missing, tracks by status.
  *Owned tracks* lists everything the index knows; open a row's **Related** to jump to its artist and album on Deezer and see "fans also like".
- **Settings**: Spotify, Deezer ARL (with a test button), ntfy, formats, thresholds, path template
  with a live example; changes apply without a restart.

Cover art and previews come from Deezer's CDN (the only external hosts the page may contact; strict
CSP otherwise). It signs in with a username and password (the session is kept for that browser tab only). The Docker image
builds it; from a checkout: `cd frontend && npm ci && npm run build` (`npm run dev` proxies to
`:8099`). Browsing needs Deezer's public catalogue to be reachable; queued downloads don't.

### JSON API

Set `API_TOKEN` and the API listens on `API_PORT`. Every `/api/*` call needs
`Authorization: Bearer <token>`; there are no cookies and no CSRF surface. Routes mirror the CLI:
`GET /api/status`; `/api/playlists` (`POST {"value": url}`, `PATCH {"enabled": false}`, `DELETE`,
`POST /api/playlists/<id>/sync`, `/enqueue-missing`); `POST /api/sync`; `/api/queue` and
`/api/queue/<id>/{retry,cancel,priority}`; `/api/inbox` and `/api/inbox/<id>/{choose,search,research,skip}`;
`/api/browse/{search,artist/<id>,album/<id>,queue,undo}` (catalogue + queueing) and `/api/activity`; accounts: `/api/auth/{state,login,setup,logout}`, `/api/me`, `/api/users` (admin); `/api/settings` (secrets come back masked); `/api/spotify/*`; `GET /api/library?q=&limit=&offset=` (the tracks the index knows), `GET /api/library/related?path=` (its Deezer artist, album and related artists), `POST /api/library/refresh`; `/api/keys` (admin; see below); `/api/releases`, `/api/suggest`, `/api/artist-prefs/<deezer artist id>` (new releases and suggestions, below); open:
`GET /healthz` (just `{"ok": true}`) and the Spotify `/callback` (which only accepts a single-use `state` this instance
issued). Errors are `{"error": "<code>", "message": "..."}` with a matching HTTP status. Publish
the port on loopback only, and put TLS in front before exposing it further. Ten wrong tokens in a minute
lock that address out for a minute (HTTP 429). The token is an always-admin key; people sign in with
an account (below). See [`docs/security.md`](docs/security.md) for the review and residual risks.

### Integration API (for other apps)

To let another app (BPM Tagger, a script) request music without handing it the admin token, create an
**API key**: Account page, or `python -m music_grabber keys create "BPM Tagger" --scopes request`. The key
(`mgk_...`) is shown once, stored hashed, named, revocable (`keys revoke ID`), and limited to scopes:
`request` (create and read requests), `browse` (search the catalogue), `library` (is it owned?) and
`suggestions` (read the suggestions and new releases). A key can
only call `/api/v1`; every other route answers 403. Use it as `Authorization: Bearer mgk_...`, from a server
(not from a browser page).

```bash
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json"   -d '{"dz_id": "3135556", "source_ref": "bpm:42"}' http://host:8099/api/v1/requests
```

- `POST /api/v1/requests` takes `dz_id` (a Deezer track id), or `isrc`, or `title` + `artist` (+ `album`,
  `duration_ms`, `isrc`), and an optional `source_ref` of yours. It answers `queued` (201, with `id`),
  `in_library`, `already_queued`, or `already_requested` (the same `source_ref` again; a failed one may be
  requested again). `"force": true` queues a track you already own.
- `GET /api/v1/requests/<id>`: `status` (`pending`, `searching`, `downloading`, `awaiting_user`, `done`,
  `failed`...), `progress`, `error`. A key sees only the requests it made.
- `GET /api/v1/library/has?isrc=...` (or `title` + `artist`): `{"have": true|false}`.
- `GET /api/v1/search?q=&type=track|artist|album`, `/artist/<id>`, `/album/<id>`: the Discover data.
- `GET /api/v1/suggestions` (`gaps` and `artists`, each empty when its switch is off) and
  `GET /api/v1/releases?days=`: read-only, the unfiltered view (no account's hides or dismissals apply).

The queue shows who asked ("requested by BPM Tagger").

### Accounts and signing in

The first time you open the web UI it asks you to create the administrator account (username and
password; it also asks for `API_TOKEN` once, to prove you own the installation). After that everyone
signs in with a username and password. Administrators add people on the **Account** page; a regular
user can browse, queue and use the Inbox but cannot open Settings. Sessions last 14 days from last
use and end on Sign out, a password change or a reset. API clients keep using `Authorization:
Bearer <API_TOKEN>` (an admin) or a session token from `POST /api/auth/login`.

```bash
python -m music_grabber users list
python -m music_grabber users add alice --role admin    # prompts for the password
python -m music_grabber users passwd alice              # also the way back after a lost password
```

### Settings

`settings show` / `settings set KEY VALUE ...` (or `POST /api/settings`) change what makes sense at
runtime: `output_format`, `path_template`, `provider_order`, `deezer_quality`, `deezer_arl`,
`auto_accept_threshold`, `ask_threshold`, `grab_dry_run`, `lyrics_enabled`, `write_spotify_id_tag`,
`spotify_sync_minutes`, `grab_workers`, `ntfy_url`, `ntfy_topic`, `ntfy_notify_done`,
`ui_public_url`, `releases_enabled`, `releases_notify`, `suggest_gaps`, `suggest_artists`, `suggest_stars`. Values are validated, saved to `settings.json` (mode 0600, next to the database)
and applied to the running instance without a restart (`grab_workers` and `spotify_sync_minutes`
need one). Settings saved this way override the environment from then on. Credentials that come
from the environment (Spotify secret, API token, library/hook keys) are never written to the file.

## New releases and suggestions

Off by default. With `RELEASES_ENABLED=true` a background job (first pass about a minute after start,
then every `RELEASES_REFRESH_HOURS`) looks at **your artists**: library artists with at least 3 tracks,
artists with at least 2 tracks on a watched playlist, and the artists you **follow**. It finds each one on
Deezer, reads their albums, singles and EPs, and the **New releases** page (link on Discover) lists what came
out in the last 90 days, newest first. Queue a whole album from its page as usual.

- **Matching is careful.** A track's ISRC names its Deezer artist exactly; failing that, a name search is
  accepted only when the name is equal (with the most fans winning among namesakes). Anything less sure is
  left out and re-tried after 30 days, never guessed. A big library is worked through a few dozen artists
  per pass; the page says how many are matched.
- **Hide and follow are per account.** *Hide artist* and *Not interested* (one release) take things off your
  page only; a *Hidden* list brings them back. *Follow new releases* on an artist's page adds an artist you
  do not own. The shared API token acts as one extra user.
- **Light on Deezer.** Calls share the existing rate limiter; each artist is read at most once per refresh
  period; if Deezer is unreachable the page keeps showing what it last learned.
- `python -m music_grabber releases [list|refresh]` and `GET /api/releases` do the same from a script;
  `POST /api/releases/refresh` (administrators) checks now.

**For you** (the same background pass, its own page) adds two lists, each with its own switch:

- **Albums you're missing**: albums by artists you own (3+ tracks) that nothing in your library matches, or
  that you own at most half of when Deezer gives the album's length ("you have 2 of 10"). A deluxe edition
  counts as the album; singles and EPs are not suggested. Up to 5 per artist, newest first.
- **Artists you may like**: Deezer's "fans also like" lists for your top 30 artists, weighted by how many
  tracks you own and, with `LIBRARY_INDEX=opensubsonic`, how many you **starred** (read from BPM Tagger or
  Navidrome with the library key: that is that library user's taste, not each Grabber account's). Artists
  you own, follow, hid or dismissed never appear; each tile says who it is "because" of.
- **Switches**, all in Settings (*New releases and suggestions*) and applied at once, no restart: the
  master switch (`RELEASES_ENABLED`), `SUGGEST_GAPS`, `SUGGEST_ARTISTS`, `SUGGEST_STARS`. Switching a part off
  hides it and stops fetching what it needs (no "related" calls without the artists list, no star reads
  without the stars switch); what was already learned is kept for when you turn it back on.
- **ntfy ping** (`RELEASES_NOTIFY`, off by default): one notification per pass listing releases not announced
  before (up to 8 names, then "and N more"), linking to the New releases page when `UI_PUBLIC_URL` is set.
  The first time it is switched on, everything already known is marked as announced without a ping; a release
  older than 30 days when found is never announced. It is one shared topic, so per-account hides do not apply
  to it, and it is best effort (a failed ping is logged, not retried).
- Per account, like the releases: *Not interested* on a tile, *Hide artist*, and *Hide from suggestions* on an
  artist's page; a *Hidden suggestions* list undoes them. Nothing here ever downloads on its own.

## Library and the ledger

"Do we already have it?" is answered, in order, by the **ledger** (files Music Grabber filed, by
Spotify id, as long as the file is still there), by the **Spotify id tag** inside a file, by an equal
**ISRC**, then by a fuzzy title + artist + duration score.

Files Music Grabber grabs carry the Spotify track id in a `SPOTIFY_TRACK_ID` tag (ID3 `TXXX`, or a
Vorbis comment in flac/opus). Other software ignores it. It means a grabbed file that you move or
rename is still "have", and `ledger rebuild` can restore the ledger after a lost or fresh database by
reading the tags (it only adds rows; it never changes a file or an existing row). Only
`LIBRARY_INDEX=filesystem` can read the tag: the OpenSubsonic API does not expose custom tags.
Files that already existed are never rewritten, so the ones from a BPM Tagger import stay untagged and
keep the ledger rows the import made. `WRITE_SPOTIFY_ID_TAG=false` stops tagging new files.

## Spotify playlists in Navidrome (the mirror)

Off by default. With `PLAYLIST_MIRROR=navidrome` (and `NAVIDROME_URL`, `NAVIDROME_USER`,
`NAVIDROME_PASS`) every watched playlist is written to Navidrome as `Spotify: <name>` (`MIRROR_PREFIX`),
holding the tracks Music Grabber matched in your library, in Spotify order. BPM Tagger imports Navidrome
playlists, so they are usable in its Run and Listen pages again: they appear in its list of importable
Navidrome playlists (they belong to the Navidrome user you configured, and are private), and you import
them there once.

It writes to your Navidrome, so it stays within narrow rules:

- **One-way, and only its own playlists.** Edits you make to a mirrored playlist in Navidrome are replaced
  the next time the Spotify side changes. A playlist is ours only if Music Grabber created it (it records
  the id and writes `music-grabber:<spotify id>` into its comment); anything else in your Navidrome is
  never updated or deleted. A same-named playlist that is not ours is left alone and reported as *name taken*.
- **No needless writes.** Contents are replaced in one request, and only when the list differs from what was
  written last time. A mirror you deleted in Navidrome is not recreated until you ask (`--reset`).
- **Tracks not in Navidrome yet are left out** until its next scan, then added; Spotify local files and
  unmatched tracks are never mirrored. Paths are matched by their ends, so Navidrome's library root may
  differ from yours; an ambiguous match is skipped rather than guessed.
- **Nothing is deleted** unless you run `playlist mirror ID --remove` (our own playlist only). Unwatching a
  playlist or turning the mirror off leaves what was written where it is.

```bash
python -m music_grabber playlist mirror            # mirror every watched playlist now
python -m music_grabber playlist mirror 3 --off    # leave playlist 3 out (--on to include it again)
python -m music_grabber playlist mirror 3 --reset  # try again after "deleted in Navidrome" / "name taken"
python -m music_grabber playlist mirror 3 --remove # delete our Navidrome playlist and stop
```

The Playlists page shows the state of each mirror with a switch and *Mirror now*. It runs after each
sync and a few minutes after new files are placed (so Navidrome has scanned them); BPM Tagger picks the
playlists up on its own Navidrome sync schedule, so a change can take a while to reach its player.
Writing playlists needs an administrator in the web UI/API.

## Working with BPM Tagger, Navidrome and Mediarium

**BPM Tagger, intake mode (recommended).** Mount the same music folder into both containers (each
may use its own path, e.g. `/music` in one and `/library` in the other: set Music Grabber's
`MUSIC_DIR`/`INTAKE_DIR` to its own view of it) and file new tracks into a subfolder BPM Tagger watches
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
can make it wait (blocking one worker) and record whether the BPM tag appeared. Either way, Music
Grabber keeps looking (read-only, about once a minute, for 24 hours) and adds the BPM and ReplayGain
loudness BPM Tagger wrote to the queue item's history, also after BPM Tagger has moved the file.

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
player stay in BPM Tagger. Spotify playlists reach BPM Tagger's Run/Listen pages again through the
[Navidrome mirror](#spotify-playlists-in-navidrome-the-mirror) (opt-in). What you still lose (playlist
diff/merge/split on them, the Suggestions page) is listed in the guide.

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
| `WRITE_SPOTIFY_ID_TAG` | `true` | write the Spotify track id into files it grabs (`SPOTIFY_TRACK_ID` tag; never into existing files); see [Library and the ledger](#library-and-the-ledger) |
| `LIBRARY_INDEX` | `filesystem` | `filesystem` \| `opensubsonic` |
| `LIBRARY_URL`, `LIBRARY_API_KEY` (or `LIBRARY_USER` + `LIBRARY_PASS`) | | for `opensubsonic`: BPM Tagger's Subsonic API or Navidrome |
| `ANALYSIS` | `none` | `none` \| `intake` \| `api` |
| `INTAKE_DIR` | | for `intake`/`api`: a directory BPM Tagger's watcher covers |
| `ANALYSIS_WAIT_SECONDS` | `0` | `intake`/`api`: wait up to this long for the BPM tag (blocks one worker) |
| `BPM_TAGGER_URL`, `BPM_TAGGER_USERNAME`, `BPM_TAGGER_PASSWORD` | | for `api`: BPM Tagger's admin login |
| `POST_IMPORT_HOOKS` | | comma list: `mediarium`, `navidrome` |
| `MEDIARIUM_URL`, `MEDIARIUM_API_KEY`, `MEDIARIUM_API_KEY_HEADER` | `` / `` / `X-Api-Key` | |
| `NAVIDROME_URL`, `NAVIDROME_USER`, `NAVIDROME_PASS` | | for the `navidrome` hook and the playlist mirror |
| `PLAYLIST_MIRROR` | | `navidrome` writes watched playlists to Navidrome (off when empty); see [the mirror](#spotify-playlists-in-navidrome-the-mirror) |
| `MIRROR_PREFIX` | `Spotify: ` | prefix of the mirrored playlists' names |
| `RELEASES_ENABLED` | `false` | turns on [New releases and suggestions](#new-releases-and-suggestions) (reads Deezer's public catalogue in the background); also a switch in Settings |
| `SUGGEST_GAPS` | `true` | suggestions: albums you are missing from artists you own (needs `RELEASES_ENABLED`; also in Settings) |
| `SUGGEST_ARTISTS` | `true` | suggestions: artists you may like (needs `RELEASES_ENABLED`; also in Settings) |
| `RELEASES_NOTIFY` | `false` | one ntfy ping when new releases from your artists turn up (needs `RELEASES_ENABLED` and ntfy; also in Settings) |
| `SUGGEST_STARS` | `true` | use the songs you starred in BPM Tagger or Navidrome to weight your taste (`LIBRARY_INDEX=opensubsonic` only; also in Settings) |
| `RELEASES_REFRESH_HOURS` | `24` | how often each artist's releases are re-read |
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

## Stability

From 1.0.0, within 1.x these do not change in a way that breaks an existing setup: the **CLI**
commands and flags, the **JSON API** routes and their fields, the **environment variable names**
and the **`settings.json` keys**. New ones may be added; one that must go is deprecated for a
release first. The **database** migrates forward only: opening it with a newer version upgrades it
in place, after copying the old file to `<db>.bak-<version>` (the version that is starting), so going back means restoring
that copy. The web UI's look and its internal routes are not part of the promise.

### Not in 1.0

Decided, not forgotten (the questions are in [`docs/plans/extraction.md`](docs/plans/extraction.md), section 8):

- **A "scan just these files" trigger in BPM Tagger.** `ANALYSIS=api` can only start a full-library
  scan (and not with admin two-factor). The supported setup is BPM Tagger in watch mode with
  `ANALYSIS=intake`; Music Grabber then reads back the BPM and loudness BPM Tagger wrote.
- **BPM Tagger's old Suggestions page**, as such. Since 1.1 the *For you* page (albums you're missing, artists you may
  like) and *New releases* replace it; see [New releases and suggestions](#new-releases-and-suggestions).
- **A verified Mediarium contract.** The scan hook treats any 2xx answer from `POST /api/music/import/scan`
  as success and does not read the body, and it uses the API key you give it as is.

## Troubleshooting

- **Every track shows as missing.** `status` → *Library*. If it says 0 tracks the index cannot see
  your files: mount the library at the path `LIBRARY_DIR`/`MUSIC_DIR` names, or switch to
  `LIBRARY_INDEX=opensubsonic`. A refresh that sees under half of a known library is treated as a
  failure and keeps the old cache, so a briefly unmounted share does not re-queue everything. If the
  index holds **no files at all** on an install that has grabbed or imported before, sync still
  matches but queues nothing by itself (`status` says so, and the log warns once): a wrong mount
  must not turn a whole playlist into downloads. Fix the mount, or use *Queue missing* on the
  playlist (`playlist queue-missing ID`) if the library really is empty.
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
pip install -r requirements-pillow.txt   # a current Pillow over streamrip's pin (pip prints a conflict note: expected)
ruff check . && pytest -q
cd frontend && npm ci && npm run typecheck && npm test && npm run build
```

Tests never touch the network (a socket guard fails any non-loopback connection) and need no
credentials; Spotify, Deezer, yt-dlp, Mediarium, BPM Tagger and ntfy are faked. See
[`CLAUDE.md`](CLAUDE.md) for the project rules.

## Licence

AGPL-3.0-or-later, same as BPM Tagger. See [`LICENSE`](LICENSE).

## Releasing

1. Set `VERSION`, add a `## <version>` section to `CHANGELOG.md`, merge to `main`.
2. One-time: add the repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (a Docker Hub
   access token with write access to `gatoserio/music-grabber`).
3. Create a GitHub release with the tag `v<VERSION>` (mark it *pre-release* for a beta).
   `docker-publish.yml` then verifies tag, version, changelog and tests, builds amd64 + arm64 and pushes
   `<version>` (plus `<major>.<minor>`, `<major>` and `latest` for stable releases).

Nothing else publishes an image: not a push, a plain tag, a schedule or a pull request. *Actions →
Publish Docker image → Run workflow* (type `publish`) republishes the current `VERSION` by hand.
