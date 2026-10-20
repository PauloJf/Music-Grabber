# Changelog

## Unreleased

### Redesign
- **The web UI is rebuilt** around browsing: Discover (search Deezer for songs, artists, albums),
  artist pages (popular, albums vs singles, related artists), album pages (queue all / missing /
  selected), 30-second previews, and a live state on every song (queue, queued, progress ring,
  in library, needs you, failed + retry). Instant feedback: optimistic *Queuing…*, toasts with
  **Undo**, a floating activity tray, and a toast when a download finishes or fails. New Settings
  page. Mobile-first with a tab bar and a distinctive "liner notes" look (warm paper and ink, serif
  names, one vermilion signal colour, light/dark by system preference); no external fonts.
- API: `/api/browse/*` and `/api/activity`; CSP allows Deezer/Spotify image CDNs and Deezer previews only.

### Release
- `docker-publish.yml` now publishes `gatoserio/music-grabber` (linux/amd64 + arm64) to Docker Hub when a
  **GitHub release is published**, after checking that the tag is `v<VERSION>`, `CHANGELOG.md` has a
  section for it and the tests pass. Stable releases also move `:latest`, `:<major>.<minor>` and
  `:<major>`; pre-releases only get their exact tag. A typed-confirmation manual run remains.

### Accounts
- **User accounts and a login page.** Username + password sign-in (scrypt hashes, server-side
  sessions that expire after 14 days of disuse), a first-run screen that creates the administrator
  (needs `API_TOKEN`), roles (admin / user), and an **Account** page: theme, preview volume,
  change password, and for admins add / promote / reset / delete people. Settings, Spotify and
  accounts are admin-only. `API_TOKEN` keeps working as an admin key for scripts and recovery.
- CLI: `users list|add|passwd|role|delete` (passwords from a hidden prompt or stdin).
- API: `/api/auth/{state,login,setup,logout}`, `/api/me`, `/api/me/{password,prefs}`, `/api/users`.

### Security
- Security review (`docs/security.md`): wrong-token lockout (429), SSRF/size guard on cover downloads,
  stricter URL settings, column allowlist in the queue update, owner-only database file, `/healthz`
  no longer reveals the version, JSON for unexpected errors, `react-router` upgraded (0 npm
  advisories). Pillow stays pinned by streamrip (mitigated, documented).

### Added
- **Help** page (`/help`, the **?** in the header): setup order, finding and queueing music, badge
  legend, Queue/Inbox, playlists, settings, troubleshooting and safe-use tips.

### Fixed
- ntfy pings whose title contained a non-Latin-1 character (an en dash, accents) were silently never sent.
- The Deezer ISRC lookup on enqueue never ran since the extraction (a wrong relative import hidden by
  a best-effort `except`); queueing a catalogue track now resolves its ISRC.

### Documentation
- README rewritten around a contents list, setup (Spotify OAuth, Deezer ARL), a troubleshooting
  section and the full configuration table; every variable the code reads is documented, and
  `tests/test_docs.py` fails if one is not.
- Compose examples in `docs/compose/`: standalone, a **shared folder with BPM Tagger** (intake), and
  the full Navidrome + BPM Tagger + Music Grabber stack. Tests keep them honest: only variables the
  code reads, no literal secrets, loopback-only ports, one shared `/music`.
- `docs/migration-from-bpm-tagger.md`: what moves and what does not, step by step, how to back out.

### Added
- **Web UI** (React + TypeScript + Vite + Tailwind-as-utilities, BPM Tagger's tokens and
  conventions), served by the API process: Inbox (candidates, scores, choose / search / skip),
  Queue (active / failed / history, retry, cancel, priority, event log), Playlists (coverage,
  watch, sync, queue missing, tracks by status), *Connect Spotify*. Token sign-in, strict
  same-origin CSP, stacked-card tables on phones. The Docker image builds it in a separate stage;
  CI runs `tsc`, `vitest` and the build.
- **CLI** over a shared operations layer: `status`, `sync`, `playlist`, `queue`, `inbox`
  (`show` lists candidates with scores and the next commands), `settings`, `library refresh`;
  `--json` before or after any command; `--remote URL --token T` drives a running instance.
- **JSON API** (set `API_TOKEN`, 16+ characters): bearer-token auth, no cookies; routes mirror the
  CLI; typed errors; Spotify OAuth `/callback` guarded by a single-use `state`; secrets masked.
- **Settings persistence**: validated runtime changes saved to `settings.json` (0600) and applied
  live (providers, thresholds, output format, path template, ntfy) without a restart.
- **ntfy**: the ambiguity ping carries the CLI command to run; `NTFY_NOTIFY_DONE` silences the
  per-track "done" ping; `settings test-ntfy` / `test-deezer`; credential problems alert once.
- **Import tool**: `import-bpm-tagger DB` (read-only source, `--dry-run`, `--merge`,
  `--include-tokens`) copies Spotify playlists, queue/candidates/events, the counter and managed
  tracks (as ledger rows); `import-env` prints a config block from BPM Tagger's settings.json
  without ever printing secrets.
- **OpenSubsonic library index** (`LIBRARY_INDEX=opensubsonic`, `LIBRARY_URL`, `LIBRARY_API_KEY`
  or `LIBRARY_USER` + `LIBRARY_PASS`): learns what the library has over HTTP from BPM Tagger's
  Subsonic API (`SUBSONIC_ENABLED` there) or from Navidrome. Walks every song with an empty
  `search3` query (falling back to `getAlbumList2` + `getAlbum`), loads the same cache as the
  filesystem source, and never prunes on an error or a partial listing.
- **Analysis modes** (`ANALYSIS=none|intake|api`, `INTAKE_DIR`, `ANALYSIS_WAIT_SECONDS`):
  `intake` files into a folder BPM Tagger's watcher covers and can optionally wait to confirm
  the BPM tag appeared; `api` adds a coalesced scan request to BPM Tagger's HTTP API
  (`BPM_TAGGER_URL`, `BPM_TAGGER_PASSWORD`; not usable with admin two-factor).
- **Post-import hooks** (`POST_IMPORT_HOOKS=mediarium,navidrome`, off by default): one coalesced
  request per burst of placements (trailing debounce, max wait, minimum interval, one dirty
  follow-up). Mediarium: `POST /api/music/import/scan` with `MEDIARIUM_URL` +
  `MEDIARIUM_API_KEY`; a 404 (music module off) pauses it for an hour, 401/403 log one clear
  line and disable it, 429/5xx/network errors back off exponentially. Navidrome: `startScan`.
- Startup validation: a configuration that names something that cannot work (unknown index,
  missing `INTAKE_DIR`, a hook with no URL, ...) exits with code 2 and says what to set.

### Changed
- The Navidrome rescan is no longer fired once per downloaded track; it is a coalesced hook.
- The ledger's `rel_path` is relative to the library root (not the filing root), so it is the
  same whichever analysis mode or intake subfolder filed the file.

### Fixed
- Reopening an existing database crashed (`UNIQUE constraint failed: app_counters.key`) while
  seeding the lifetime counter; every second start of the app hit it.

## 0.1.0 (unreleased)

First cut of the Music Grabber as its own project, extracted from BPM Tagger v2.18.2
(see `docs/provenance.md` and `docs/plans/extraction.md`).

### Added
- Own package, config (same env names as BPM Tagger's grabber settings), SQLite database
  and Docker image definition (`gatoserio/music-grabber`, published manually only).
- `python -m music_grabber [run | spotify-login | version]`; `WATCH_PLAYLISTS` registers
  playlists without any network call.
- Library index: a `LibrarySource` port with a standalone filesystem source, loading a
  local cache that matching queries (ISRC, fuzzy). A failed or partial refresh never wipes
  the cache.
- `grabbed_files` ledger (replaces BPM Tagger's `managed` flag and Spotify-id stamp).
- `PostDownloadAnalysis` port (`NoAnalysis` for now); post-placement callback hook point.
- Atomic placement (`.mg_part_<id>.part` then `os.replace`) and startup recovery for items
  that crashed after placing their file.
- Test suite with no real network access: a socket-level guard fails any non-loopback
  connection, Spotify/Deezer/yt-dlp are faked, plus golden tests pinning the duplicated
  text normalizers to BPM Tagger.

### Changed
- No BPM or loudness detection: those belong to BPM Tagger (watch its intake folder).
- The temp file used while placing a track no longer has an audio extension.

### Removed
- Monochrome provider (was on hold), suggestions engine, web UI (a minimal one returns in a
  later release; use `spotify-login` and the logs for now).
