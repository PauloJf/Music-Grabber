# Music Grabber: project context

## Identity

| Field | Value |
|---|---|
| Name | Music Grabber |
| Version | see `VERSION` (single source of truth) |
| Repo | `PauloJf/Music-Grabber` (**private**; never make it public) |
| Image | `gatoserio/music-grabber`, published **manually only** |
| Licence | AGPL-3.0-or-later (same as BPM Tagger) |
| Git identity | `Paulo <PauloJf@users.noreply.github.com>` (GitHub rejects pushes exposing `paulo@gatoserio.dev`, error GH007) |

## What it is

A standalone, optional companion to a Navidrome library: watches your Spotify playlists,
reconciles them against what the library already has (ISRC, then a fuzzy score), downloads
the missing tracks (Deezer via your own ARL, then yt-dlp), transcodes to one format, tags,
and files them with a path template. Extracted from BPM Tagger; the plan and the reasoning
live in `docs/plans/extraction.md`, the origin in `docs/provenance.md`.

## Hard rules

- **Never import BPM Tagger** (or any detector: librosa, essentia, numpy). Coupling is HTTP
  and/or the shared music folder, never a shared DB or Python imports.
  `tests/test_isolation.py` enforces it.
- **No real network in tests**, no credentials anywhere in the repo. `tests/conftest.py`
  fails any non-loopback connection. Mock Spotify, Deezer, yt-dlp, Mediarium, ntfy.
- **Never publish an image from automation.** `docker-publish.yml` is `workflow_dispatch`
  only (guarded by `tests/test_workflows.py`).
- Don't touch the BPM Tagger repo from here. Removing its grabber is a separate, later step
  that must not ship as a `VERSION` bump to BPM Tagger's `main` until Music Grabber is stable.
- Secrets (`SPOTIFY_CLIENT_SECRET`, `DEEZER_ARL`) are never logged, never echoed; the Spotify
  ones are env-only and never written to `settings.json`.

## Layout

| Path | Purpose |
|---|---|
| `music_grabber/main.py` | CLI entry (`run`, `spotify-login`, `version`) |
| `music_grabber/config.py` | env to config, `settings.json` overrides, `__version__` |
| `music_grabber/db/` | own SQLite schema; mixins: `queue`, `playlists`, `ledger`, `library` (index cache) |
| `music_grabber/sync_engine.py` | `SpotifySync` thread + `GrabberService` |
| `music_grabber/worker.py` | `GrabPipeline` (search, score, download, transcode, tag, place, ledger), `GrabPool` |
| `music_grabber/library.py` | `LibrarySource` port, `FilesystemSource`, `LibraryIndex` |
| `music_grabber/analysis.py` | `PostDownloadAnalysis` port |
| `music_grabber/matching.py`, `text.py` | scoring and normalizers (`text.py` is a pinned duplicate of BPM Tagger's) |
| `music_grabber/providers/` | Deezer (streamrip) and yt-dlp, lazily imported |
| `music_grabber/spotify.py` | Spotify Web API client (Authorization Code flow) |
| `tests/` | pytest; `audio_fixtures.py` builds tiny valid audio with no audio libraries |

## Commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
pytest -q
python -m music_grabber version
```

## Conventions

- Match the surrounding code: compact guards are fine (ruff ignores E701/E702/E731).
- New behaviour gets a test; new env var gets a line in `docker-compose.yml`, `.env.example`
  and the README.
- Update `CHANGELOG.md` with every user-visible change; CI fails if `VERSION` has no section.
- Bump `VERSION` only for a release; it is not an automatic publish trigger here.
- One branch and PR per phase of `docs/plans/extraction.md` section 6; open questions go in
  the PR description.
