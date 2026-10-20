# Changelog

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
