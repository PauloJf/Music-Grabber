# Changelog

## Unreleased

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
