# Plan: Extract the Music Grabber from BPM Tagger

Status: **Phase 0 (audit + plan)**. Written against BPM-Tagger `main` @ `eae3f0c`
(v2.18.2). Nothing in BPM-Tagger is modified by this plan's first five phases.

Music-Grabber becomes a standalone, optional companion to a Navidrome library: its own
repo, own SQLite DB, own Docker image (`gatoserio/music-grabber`). Coupling to
BPM-Tagger is HTTP and/or the shared music folder, never a shared DB or Python imports.
Decisions already taken (not re-opened here): the split itself and its reasons
(ToS/legal exposure of the Deezer-ARL / yt-dlp code, heavy fast-breaking deps, different
release cadence); the player (Run/Listen) stays in BPM-Tagger; Mediarium integration is
only a post-import hook; no Torznab/qBittorrent facade; licence is AGPL-3.0-or-later.

## 0. Findings that shape the plan

Things the audit turned up that were not obvious from the brief:

1. **The grabber's reach into the core is small; the core's reach into the grabber is
   larger.** `grabber/` imports only six things from the rest of the package
   (`bpm.pipeline`, `bpm.tags`, `bpm.loudness`, `bpm.lyrics`, `integrations.lrclib`,
   `integrations.navidrome`) plus `text` and `deezer_catalog`. In the other direction,
   16 modules outside `grabber/` import `grabber.matching`,
   `grabber.tagging`, `grabber.path_template` or `grabber.providers.*` (30 files mention the
   grabber in some form), because those
   modules turned into general-purpose helpers (cover embedding, fuzzy scoring, the
   metadata editor). Those helpers have to stay in BPM-Tagger, so the code is
   **duplicated, not moved** (section 2).
2. **`spotify_track_id` is never written to a file tag.** It exists only in the
   `tracks.spotify_track_id` DB column, set by `record_managed_track()`.
   `library_match()` consults it first ("a stamped id wins outright"). Without a shared
   DB that fact has to live in Music-Grabber's own ledger (section 3).
3. **`managed` has no behavioural effect on the watcher.** Nothing in `scan/` reads it.
   It feeds Stats ("managed vs unmanaged") and the Duplicates view. What keeps the
   watcher away from a grabbed file is the **hash stamp** (`file_hash = size:mtime`,
   taken after the BPM tag write, with `copy2` preserving mtime). That is the real
   semantic to preserve (section 3).
4. **There is no `Music-raw` folder in BPM-Tagger.** `MUSIC_DIR` is the single watched
   root, and the watcher is recursive. "Intake folder" therefore means: any directory
   under BPM-Tagger's `MUSIC_DIR` (typically a `Music-raw/` subfolder, or a separate
   mount that BPM-Tagger also sees). That is an operator convention, and this plan
   treats it as configuration (`INTAKE_DIR`), not code. **Open question Q1.**
5. **The "debounced Navidrome rescan" is not debounced.** `worker.py` calls
   `_trigger_navidrome_rescan()` once per finished track. BPM-Tagger's watcher does
   debounce it (60 s) via `hooks.library_changed`. Music-Grabber must implement real
   coalescing (section 2.4); the Mediarium hook needs it too.
6. **A rescan before the BPM tag is written is harmful.** With `PRESERVE_MTIME=true`,
   Navidrome's quick scan will not notice a later BPM tag write (see the comment in
   `integrations/navidrome.py`). In *intake* mode, BPM-Tagger's watcher triggers the
   Navidrome rescan after it tags the file, so Music-Grabber's own Navidrome hook must
   default to **off** in that mode.
7. **Spotify playlists are rows in a table the player also uses.** `playlists` /
   `playlist_tracks` hold `spotify | navidrome | local` sources, and Run/Listen use all
   three. Spotify-sourced rows stop updating once the grabber is gone. This is the
   largest user-visible loss (section 4.2), with a proposed mitigation.
8. **Monochrome is dead code** (`MONOCHROME_ON_HOLD = True`). It is not ported.
9. **The Subsonic API in BPM-Tagger exposes `isrc`, `path`, `duration`, `bpm` per song
   and supports OpenSubsonic API keys.** That makes it the right "BPM-Tagger HTTP"
   surface for the library index. The admin session API is not (it needs the admin
   password, breaks under TOTP, and shares the login lockout).

## 1. Boundary inventory

### 1.1 grabber → rest of `bpm_tagger`

| From | To | What for | Disposition |
|---|---|---|---|
| `worker.py` | `bpm.pipeline.detect_bpm` | BPM on the staged file | **Interface**: `PostDownloadAnalysis` (2.2). Music-Grabber never ships librosa/essentia. |
| `worker.py` | `bpm.tags.write_bpm_tag`, `get_file_hash` | write BPM tag; stamp hash | **Interface** for the tag (done by BPM-Tagger's watcher in intake mode). `get_file_hash` is a one-liner (`f"{size}:{mtime}"`), **duplicated**. |
| `worker.py` | `bpm.loudness.analyze_loudness` | LUFS on staged file | **Dropped**. BPM-Tagger measures loudness itself when it indexes the file. |
| `worker.py` | `bpm.lyrics.write_lyrics` / `is_synced`, `integrations.lrclib.fetch_lyrics` | opt-in LRCLIB lyrics, embedded | **Duplicate** the 88-line LRCLIB client and the *embed-only* path of `write_lyrics` (sidecar mode is not needed for grabbed files). Opt-in (`LYRICS_ENABLED`), off by default. |
| `worker.py` | `integrations.navidrome._trigger_navidrome_rescan` | rescan after each file | **Replace** with a coalescing post-import hook (2.4); Navidrome hook is one of three hook targets (Navidrome, Mediarium, none). |
| `worker.py`, `sync_engine.py` | `notifier.send_grabber` (`notify/ntfy.py`) | ntfy pings | **Duplicate** a ~40-line `Notifier` (only the `_post`/`send_grabber` half of `NotificationManager`; the batching half is scan-specific). |
| `matching.py` | `text.*` (normalizers) | title/artist normalization | **Duplicate** `text.py` verbatim (136 lines, stdlib-only). It is already a deliberately self-contained module; copying keeps BPM-Tagger's index and Music-Grabber's matching speaking the same dialect. A drift test is proposed in Phase 2 (2.5). |
| `providers/deezer.py`, `enqueue.py`, `suggestions.py` | `integrations.deezer_catalog` (`track_detail_by_isrc`, `track_isrc`, search) | keyless Deezer catalogue lookups | **Duplicate the needed subset** (`_get`, `track_detail_by_isrc`, `track_isrc`, `track_preview_url`, `track_by_isrc`; about 80 of 304 lines). The rest of the file is BPM-Tagger's Related-panel feature and stays there. |
| `sync_engine.py` | `tagger.index_tags()` | refresh tag index before matching | **Interface**: `LibraryIndex.refresh()` (2.1). |
| `worker.py`, `providers`, `sync_engine.py` | `tagger` object (`self.tagger`) | only held, `index_tags` is the sole real use | **Dropped**: the `tagger` parameter disappears from `GrabberService`/`GrabPool`/`GrabPipeline`. |
| `matching.library_match` | `db.find_by_spotify_id`, `find_by_isrc`, `find_candidates_by_norm` (tracks table) | "do we already have it?" | **Interface**: ledger + `LibraryIndex` (2.1, section 3). |
| `db/grabber.py` | `BPMDatabase._connect`, `_sync_track_artists`, `tracks` upsert (`record_managed_track`), `app_counters` | queue persistence, managed-track row | **Move** queue/candidate/event/oauth code into Music-Grabber's own `db` package. `record_managed_track` is **replaced** by the `grabbed_files` ledger (section 3). `_sync_track_artists` (artist-link table for BPM-Tagger's UI) is dropped. |
| `config` keys | see 1.4 | | **Move** into Music-Grabber's own config. |
| `integrations/isrc.py` (used by `web/api/tracks.py`, not by `grabber/`) | `grabber.providers.base.TrackMeta`, `DeezerProvider` | "Find ISRC" / bulk fill | Crosses both ways, see 1.2. |

### 1.2 rest of `bpm_tagger` → grabber

Everything here **stays in BPM-Tagger** and is cleaned up in the later, separate removal
step (section 5.2). The right column says what that cleanup has to do; Music-Grabber is
unaffected.

| From (BPM-Tagger) | Imports / calls | BPM-Tagger cleanup |
|---|---|---|
| `main.py`, `scan/scanner.py` (`self.grabber`), `web/api/scan.py` (stops grabber pool before restart) | `grabber.sync_engine.GrabberService` | delete wiring behind `GRABBER_ENABLED` |
| `db/tracks.py`, `db/playlists.py` | read `grab_queue` (source stats, "queued" counts) | stub counts to zero / drop the columns' UI use |
| `db/base.py` | `_create_grabber_tables`, `_migrate_playlists_schema`, startup `DELETE FROM grab_*`, `grabbed_total` seed | keep `playlists`/`playlist_tracks` creation (player still needs them); drop `grab_*`/`oauth_tokens`/`suggestions` creation; leave existing tables in place for existing DBs |
| `web/api/queue.py`, `inbox.py`, `spotify.py`, `suggestions.py` (Part A), `settings.py` (`/api/settings/grabber`, `test-deezer`, `test-monochrome`), `stats.py` (`_grabber_stats`) | `GrabberService`, `db.get_grab_*`, `GRAB_TERMINAL`, `grabber.providers.*`, `grabber.suggestions` | delete blueprints/handlers |
| `web/api/playlists.py` | `grabber.spotify` (`parse_playlist_id`, `SpotifyError`), `grabber.tagging.read_cover`, `/api/grabber/status`, Spotify-source branches (`source="spotify"` create/sync, `enqueue-missing`) | keep Local/Navidrome paths; remove Spotify branches |
| `web/api/tracks.py` | `grabber.matching` (normalizers), `grabber.path_template.render/unique_path`, `grabber.tagging.*` (metadata editor: tags, cover embed/resize/read); `grabber.client` for ISRC/metadata lookups | **move the helpers to neutral modules** (`bpm_tagger/tagging.py`, `path_template.py`; `matching.py` split: `score`/`library_match` stay for Navidrome sync); Spotify client use disappears |
| `web/api/images.py`, `web/subsonic/covers.py` | `grabber.tagging` (`embed_cover`, `resize_cover`, `read_cover`); Spotify image search via `grabber.client` | same neutral-module move; drop Spotify image source |
| `web/api/playlist_ops.py`, `web/api/suggestions.py` (Part B, Related panel) | `grabber.matching.library_match`, normalizers | same: `matching.py` becomes a neutral module |
| `integrations/navidrome.py` (`score`), `star_sync.py`, `navidrome_playlists.py` (`library_match`, normalizers), `deezer_catalog.py` (`normalize_artist`) | `grabber.matching` | repoint to the neutral `matching.py` (the "non-core importers stay on the re-export" decision from `core-decoupling.md` gets revisited) |
| `integrations/isrc.py`, `integrations/metadata.py` | `grabber.providers.base.TrackMeta`, `DeezerProvider`, Spotify client | rewrite to keyless sources (Deezer catalogue, MusicBrainz); drop the ARL- and Spotify-backed branches |
| `integrations/periodic_sync.py` | `grabber.sync.sync_playlist` for Spotify playlists | remove the `spotify` branch |
| `notify/ntfy.py` | `send_grabber` | delete |
| `config.py` | grabber keys (1.4) | delete |
| `tests/` | see 1.8 | delete / adapt |
| `frontend/` | see 1.7 | delete pages, gate removal |
| `requirements.txt`, `Dockerfile`, `README.md`, `DOCKERHUB_README.md`, `CLAUDE.md`, `docker-compose.yml`, `.env.example` | `yt-dlp`, `streamrip`, GRABBER docs | remove; `rapidfuzz` and `pillow` **stay** (still used by Navidrome sync / covers) |

### 1.3 Shared tables and columns

| Table / column | Used by grabber | Used by BPM-Tagger | Disposition |
|---|---|---|---|
| `grab_queue`, `grab_candidates`, `grab_events` | yes | read-only stats | **Move** (own schema; same columns, so export/import is 1:1). |
| `oauth_tokens` (`service='spotify'`) | yes | no | **Move.** Token rows are exported (opt-in; they are credentials) or the user re-authorizes. |
| `app_counters` (`grabbed_total`) | yes | generic table | **Move** the counter; BPM-Tagger keeps the table. |
| `playlists`, `playlist_tracks` | Spotify rows | Spotify + Navidrome + Local; Run/Listen | **Duplicate, narrowed**: Music-Grabber keeps a Spotify-only copy of the schema (drops `source`, `navidrome_id`, local-playlist columns, `pinned`, `description`). BPM-Tagger keeps its table. |
| `suggestions` | `SuggestionsEngine` (Deezer-derived) | `/suggestions` page | **Not extracted in 0.1.0.** See 1.6. Table stays in BPM-Tagger. |
| `dismissed_dupes` | no (created in the same function by accident) | Duplicates page | **Stays.** |
| `tracks.managed`, `tracks.spotify_track_id` | written by `record_managed_track` | Stats, Duplicates display | **Replace** with Music-Grabber's `grabbed_files` ledger (section 3). The columns stay in BPM-Tagger, unused by new grabs. |
| `tracks.isrc/title/artist/album/album_artist/duration_ms/norm_*` | read by `library_match` | owned by the scanner's tag index | **Interface**: read through `LibraryIndex` (2.1). |
| `tracks.loudness_*`, `bpm*` | written by `record_managed_track` | owned by scanner | **Dropped** from the grabber side; BPM-Tagger fills them when it indexes the file. |

### 1.4 Setting keys / env vars

Music-Grabber reuses the **same env var names** where the meaning is unchanged, so an
existing `docker-compose.yml` block can be copied over.

| Key / env | Moves as | Note |
|---|---|---|
| `GRABBER_ENABLED` | **dropped** | the process *is* the grabber |
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`, `SPOTIFY_SYNC_MINUTES` | same | secrets env-only, never persisted to `settings.json` (as today) |
| `DEEZER_ARL`, `DEEZER_QUALITY`, `PROVIDER_ORDER` (`deezer,ytdlp`) | same | ARL env/secret-file only, never logged |
| `OUTPUT_FORMAT`, `PATH_TEMPLATE`, `AUTO_ACCEPT_THRESHOLD`, `ASK_THRESHOLD`, `GRAB_WORKERS`, `GRAB_DRY_RUN`, `LYRICS_ENABLED` | same | |
| `NTFY_URL`, `NTFY_TOPIC` | same | |
| `UI_PUBLIC_URL` | same | used for ntfy click-through links |
| `MUSIC_DIR` | same meaning: *filing root* | new optional `INTAKE_DIR` overrides it in intake mode |
| `DB_PATH` | same (`/data/music_grabber.db`) | `grab_tmp/` lives next to it, as today |
| `MONOCHROME_*` | **dropped** | on hold |
| `INDEX_TAGS` | **dropped** from the grabber (BPM-Tagger keeps it) | Music-Grabber's filesystem index has its own switch |
| `WRITE_TAGS`, `PRESERVE_MTIME`, `BPM_MIN/MAX`, `USE_*` | **not ported** | analysis belongs to BPM-Tagger |
| `NAVIDROME_URL/USER/PASS` | same names, used only by the *Navidrome* post-import hook and nothing else | |
| *new* | `LIBRARY_INDEX` (`filesystem` \| `opensubsonic`), `LIBRARY_DIR`, `LIBRARY_URL`, `LIBRARY_API_KEY`; `ANALYSIS` (`none` \| `intake` \| `api`); `INTAKE_DIR`; `POST_IMPORT_HOOKS`; `MEDIARIUM_URL`, `MEDIARIUM_API_KEY`; `API_TOKEN`, `API_PORT` | introduced in the phase that needs each |

### 1.5 Constants

`GRAB_TERMINAL`, `GRAB_NONTERMINAL` move with the DB code. `TRACK_SORTS` is unrelated to
the grabber and stays. `PROVIDER_ORDER` default, `AUTH_ALERT_INTERVAL`, `FFMPEG_TIMEOUT`,
`PROFILES` (transcode), `_VARIANT_TOKENS` move with their modules.

### 1.6 Web API surface and the Suggestions question

Grabber-only blueprints and what they become:

| BPM-Tagger route group | Music-Grabber (Phase 3 headless API / CLI) |
|---|---|
| `/api/spotify/*` (status, authorize-url, callback, playlists, search, disconnect) | `/api/spotify/*`, same shapes; the OAuth callback is the one route a browser must reach |
| `/api/playlists` (Spotify rows), `/sync`, `/enqueue-missing` | `/api/playlists`, `/api/playlists/<id>/sync`, `/enqueue-missing`; CLI `sync` |
| `/api/queue/*` | `/api/queue/*`; CLI `queue` |
| `/api/inbox/*` | `/api/inbox/*`; CLI `inbox` |
| `/api/grabber/status`, `/api/settings/grabber`, `test-deezer` | `/api/status`, `/api/settings`, `/api/settings/test-deezer`; CLI `status` |
| Inbox candidate previews (`/api/inbox/candidates/<id>/preview`, `source-preview`) | **deferred to Phase 4** (UI-only convenience; needs the Deezer-preview subset of `deezer_catalog`, which is duplicated in Phase 1 anyway) |
| `/api/suggestions*` Part A (Deezer-derived artist/track suggestions, queue/queue-album) | **Not extracted in 0.1.0.** It is a recommendation engine seeded from the library; it needs the library index to be rich (starred weighting, play counts), which is BPM-Tagger data. Part B (Related panel) is not grabber-gated and stays regardless. **Open question Q4**: drop the suggestions page, or later re-home it as `music-grabber suggest` on top of the library index. |
| Stats "grabber" block | `status` command / API |

### 1.7 Frontend (inside BPM-Tagger's SPA)

Pages: `Queue.tsx` (172 lines), `Inbox.tsx` (301), `Suggestions.tsx` (235),
`Playlists.tsx` (591, Spotify + Local + Navidrome), `PlaylistDetail.tsx` (686),
`PlaylistDiff.tsx` (202). Components/hooks: `GrabberGate.tsx`, `useGrabberStatus.ts`,
`useSuggestionQueue.ts`, `QueueButton`, `QueueActions`, `QueueSimilar`,
`PlaylistSuggestions`, `ArtistModal` (queue-album), `Search.tsx` (Spotify search & grab),
the grabber section of `Settings.tsx`, and the grabber block of `Stats.tsx`.

Mixed-use files (`Playlists`, `PlaylistDetail`, `PlaylistDiff`, `QueueSimilar`,
`ArtistModal`, `Settings`, `Stats`, `Nav`) serve the player too; they are **edited**, not
deleted, in the BPM-Tagger cleanup. Music-Grabber's Phase 4 UI is a new, much smaller
SPA (section 4), not a port of these files.

### 1.8 Tests

| BPM-Tagger test | Disposition |
|---|---|
| `test_matching` (190 lines), `test_path_template` (93), `test_tagging` (96), `test_enqueue` (97), `test_grab_events` (62), `test_grab_delete` (125) | **Port** (DB fixtures adapt to the new `db` package) |
| `test_worker` (333), `test_providers` (398) | **Port**; already mock providers/yt-dlp/streamrip, re-checked for any real-network path |
| `test_inbox_preview` (140), `test_api_grabber` (463) | Phase 3 (API ports) / Phase 4 (previews); *not* in Phase 1 |
| `test_playlist_sync` (180), `test_playlist_sync_scope` | Port the Spotify half; Navidrome/local halves stay |
| `test_suggestions` | not extracted (Q4) |
| `test_core_isolation`, `test_navidrome_*`, `test_star_sync`, … | stay; touched only in the BPM-Tagger cleanup |

New in Music-Grabber: an **isolation test** asserting `music_grabber` imports no
`bpm_tagger` module and that BPM-Tagger is not installed in its test env, and an AST
layering test mirroring `test_core_isolation.py`'s style (no module-level import of
`flask` from the engine packages, so the headless CLI stays light).

## 2. Dispositions and the two ports

**Rule of thumb applied above:** tiny stdlib helpers are duplicated; anything with its
own state or policy is replaced by an interface; anything the player still needs stays
in BPM-Tagger. The duplicates are small enough (text 136, deezer subset ~80, lrclib 88,
ntfy ~40, tag-embed/read already in `tagging.py`) that drift risk is low; the one that
matters, `text.py`, gets a drift check (2.5).

### 2.1 Port: `LibraryIndex` ("what does the library already have?")

Used by sync reconciliation (have / missing / queued), enqueue dedupe, and ISRC matching.

```python
@dataclass(frozen=True)
class LibraryTrack:
    path: str            # POSIX, relative to the library root
    title: str | None; artist: str | None; album: str | None; album_artist: str | None
    duration_ms: int | None; isrc: str | None
    track_no: int | None; disc_no: int | None; year: int | None
    norm_title: str; norm_artist: str

class LibrarySource(Protocol):        # the thing that varies
    name: str
    def snapshot(self, known: dict[str, str]) -> Iterator[LibraryChange]: ...
        # yields upserts/deletes; `known` is {path: change-token} from the cache
        # so an implementation can skip unchanged entries

class LibraryIndex:                   # the thing that does not
    def refresh(self) -> RefreshStats          # drive a LibrarySource into the cache
    def find_by_isrc(self, isrc) -> list[LibraryTrack]
    def find_candidates(self, norm_artist, norm_title) -> list[LibraryTrack]
    def has_path(self, path) -> bool
```

Both implementations are *loaders* into one cache table, `library_tracks` in
Music-Grabber's own DB (columns above plus `change_token`, `source`). All queries are
local indexed SQL; the loader only runs at `refresh()` (start of each sync cycle, and on
demand). That keeps matching identical across implementations (one `library_match`, one
set of tests) and keeps Spotify sync from making an HTTP call per track.

1. **`FilesystemSource` (standalone; default).** Walks `LIBRARY_DIR` with `os.scandir`,
   key `size:mtime` (the same cheap change token BPM-Tagger uses), reads tags with
   mutagen (duplicated `read_tags`/`_read_isrc`, ~70 lines), prunes vanished paths.
   Incremental after the first pass; the first pass over ~50k files is minutes, so it
   runs in the sync thread with progress in `status`. Needs the library mounted
   read-only; no BPM-Tagger, no Navidrome.
2. **`OpenSubsonicSource` (BPM-Tagger HTTP; also works against Navidrome).** Walks the
   library over the OpenSubsonic API with an API key (stateless, no session, no CSRF, no
   admin password, unaffected by TOTP). BPM-Tagger's songs carry `path`, `duration`,
   `isrc` (list), `title/artist/album/albumArtist/track/discNumber/year`. The walk is
   `search3` with `query=""` paged by `songCount` (<=1000) / `songOffset`, falling back
   to `getAlbumList2` + `getAlbum` if a server rejects the empty query. To be verified
   against BPM-Tagger's `subsonic_search_songs("")` at the start of Phase 2; if it does
   not return everything, the album walk is the primary path. Change token = a hash of
   the fields we store (the API gives no mtime), so deletes are detected by
   "not seen in this pass" with a safety valve: a pass that returns zero tracks, or
   fewer than half the cache, is treated as a failed refresh and prunes nothing.
   Requires `SUBSONIC_ENABLED=true` and an API key on the BPM-Tagger side. **Not** the
   admin `/api/tracks` API: that needs the admin password and breaks under TOTP.

A third, smaller extension (a purpose-built `GET /api/library/index` on BPM-Tagger) is
**not** proposed: it would mean changing BPM-Tagger, and the OpenSubsonic route already
carries everything needed.

**Identity beyond tags** is the ledger (section 3), not the index: the Spotify-id fast
path in `library_match` reads `grabbed_files`, then verifies the file with
`index.has_path()` or `os.path.exists()` so a deleted track is re-grabbed rather than
reported as "have" forever.

### 2.2 Port: `PostDownloadAnalysis` ("BPM-tag the file")

Today the worker transcodes, tags, then **analyses BPM on the staged file before it
enters the library**, so a crash can never leave an untagged orphan file in the library.
Music-Grabber cannot run detectors, so the pipeline becomes:

```
stage  ->  transcode  ->  tags + cover + (lyrics)  ->  place (atomic)  ->  record in ledger
                                                          \-> analysis.after_place(placed)
                                                          \-> post-import hooks (2.4)
```

```python
class PostDownloadAnalysis(Protocol):
    name: str
    destination_root: str                        # where this mode wants files filed
    def after_place(self, placed: PlacedFile) -> AnalysisResult: ...   # never raises
```

1. **`NoAnalysis` (standalone).** Files go straight into `MUSIC_DIR`; no BPM is
   computed. Navidrome hook may be on.
2. **`IntakeFolderAnalysis`.** `destination_root = INTAKE_DIR` (a directory BPM-Tagger's
   watcher covers). `after_place` is a no-op beyond optional confirmation: if
   `analysis_wait_seconds > 0` it polls the file's tags for a BPM until the timeout and
   records the outcome as a queue event (`bpm: 128.0` or `bpm: not yet`). The queue item
   is `done` at placement either way, so a BPM-Tagger outage never blocks grabbing.
   Placement rules for BPM-Tagger's watcher (`on_created`, `on_modified`, `on_moved`;
   10 s debounce; skips unchanged hashes): write the temp file with a **non-audio
   extension** (`.mg_part_<id>.part`, today it is `.mp3`, which the watcher would
   schedule), then `os.replace` into place so there is a single `moved` event whose
   destination has an audio extension. Use `copy2` so the staged mtime is preserved.
3. **`ApiAnalysis`.** Intake placement plus an HTTP nudge to BPM-Tagger for setups where
   it runs `MODE=scan_unscanned` (cron-style) instead of `watch`. The only existing
   trigger is `POST /api/scan/start` (admin session + CSRF; scans the whole library
   according to `MODE`), which is heavy and needs the admin password. It is therefore
   **opt-in, low priority, and coalesced** (at most one call per N minutes). If it turns
   out to be unusable under TOTP, the plan is to document "use watch mode" rather than
   engineer around it. **Open question Q2:** is a per-file or per-folder trigger on the
   BPM-Tagger side acceptable as a later, separate BPM-Tagger change?

Loudness and BPM columns that `record_managed_track` used to fill are filled by
BPM-Tagger itself when its watcher indexes the new file. Nothing is lost except the
"analysis before placement" crash-safety property, which is replaced by:
* atomic placement (`.part` then `os.replace`),
* the ledger row written **before** the hooks fire, and
* startup recovery that finishes any item left in `placing` (re-checks whether the final
  file exists and is the right size; completes or re-queues).

### 2.3 Other interfaces

* **`Notifier`**: ntfy only, same `send_grabber` signature, never raises. Ambiguity,
  failure, ARL-rejected (throttled to once per 6 h per provider instance, as today) and
  Spotify-disconnected pings keep their wording.
* **Provider** interface (`Provider.search/download`, `ProviderAuthError`,
  `SearchResults.note`) moves unchanged. Providers stay lazily imported so
  `streamrip` / `yt_dlp` are needed only when used, and tests inject fakes.

### 2.4 Post-import hooks (Navidrome, Mediarium)

One small `PostImportHooks` runner called after each placement. Each hook is
independent, off by default, never raises into the pipeline, and **coalesces**:

* trailing-edge debounce (`HOOK_DEBOUNCE_SECONDS`, default 45 s, reset by each new
  placement) with a max-wait cap (default 10 min) so a long download run still fires;
* a minimum interval between calls (default 60 s);
* a "dirty" flag so files placed *during* a scan request schedule exactly one follow-up.

**Mediarium** (`MEDIARIUM_URL` + `MEDIARIUM_API_KEY`, both required to enable): `POST
{url}/api/music/import/scan` with the key. Any 2xx is success (a running scan is returned
if one is already running, which is fine). `404` = Mediarium's music module is off:
log once at INFO, back off for an hour, never retry in a tight loop, never fail a grab.
`401`/`403` = the key is wrong or **not allowed to call this route** (the plan does not
assume an API key can reach admin routes): log one clear WARNING line naming the status
and the route, and disable the hook until restart or settings change, so it does not hit
a lock-out or spam logs. Network errors back off exponentially. The API key is never
logged. This lands in Phase 2 with tests for debounce, max-wait, dirty follow-up, 404,
401, 403, 5xx and network errors, all against a fake HTTP server.

**Navidrome** (`NAVIDROME_URL/USER/PASS`): same runner, `startScan`. Default **off** when
`ANALYSIS=intake` (finding 6), on-able for `none`.

### 2.5 Drift guard for duplicated helpers

A test (Phase 2) with a frozen corpus of ~100 title/artist strings (feat., remix,
diacritics, "A & B", CJK) asserts `music_grabber.text` produces golden output. The golden
file is generated from BPM-Tagger's `text.py` at extraction time and recorded in
`docs/provenance.md` with the source SHA, so a later divergence is a conscious decision,
not an accident. No runtime dependency between the repos.

## 3. `managed` flag and hash-stamping without a shared DB

What each did, and where each goes:

| Today (BPM-Tagger) | In Music-Grabber |
|---|---|
| `tracks.managed = 1` marks a grabbed file; used for Stats and Duplicates labelling | `grabbed_files` ledger row (own DB): `{id, spotify_track_id, isrc, rel_path, abs_path, queue_item_id, provider, output_format, placed_hash, placed_at}`. "Managed" = "has a ledger row". The status/stats API reports managed-vs-library counts from it. |
| `tracks.spotify_track_id` stamped on the row; `library_match` checks it first, so a grabbed file is always "have" regardless of ISRC/fuzzy luck or queue history | The same lookup runs against `grabbed_files` (indexed on `spotify_track_id`), verified with `has_path` / `os.path.exists`. Survives queue cleanup ("Clear completed") exactly as the DB column did. |
| Optional recovery of the id from tags | Phase 2 writes the Spotify id into a custom tag (`TXXX:SPOTIFY_TRACK_ID` / Vorbis `SPOTIFY_TRACK_ID` / MP4 `----:com.gatoserio:spotify_track_id`) at tagging time, and the filesystem source reads it back. This makes the ledger rebuildable from the files (`music-grabber ledger rebuild`) and lets the id travel when files are moved. BPM-Tagger ignores unknown tags. **Flag for owner (Q3):** it adds a tag the old grabber never wrote. |
| `file_hash = size:mtime` taken **after** the BPM tag write, `copy2` preserving mtime, so the watcher sees "unchanged" and does not re-analyse a freshly grabbed file | **Standalone / `none` mode:** same trick for our own bookkeeping. `placed_hash` is `size:mtime` of the final file (via `copy2` + `os.replace`); the filesystem index uses it as its change token, so Music-Grabber's own index never re-reads a file it just wrote. |
| (same) | **Intake mode:** the goal inverts. BPM-Tagger must see the file as **new** (no `tracks` row, so `needs_analysis` is true) so it analyses and tags it. Not stamping is exactly right, and BPM-Tagger's own post-tag hash refresh handles the anti-loop on *its* side. The only requirement on Music-Grabber is to not touch the file after placement (nothing may rewrite tags later, or the mtime change would trigger a second analysis). Hooks and ledger writes never touch the file. |
| `record_managed_track` wrote bpm/loudness/title/... into `tracks` | not written. BPM-Tagger's tag index reads title/artist/album/ISRC/duration from the file's tags; Music-Grabber writes all of them before placement (`write_track_tags` is unchanged), so the index sees the same data. |

Consequence to state plainly: in intake mode a grabbed file appears in BPM-Tagger with
`managed = 0`, so BPM-Tagger's Stats "managed vs unmanaged" split stops counting new
grabs as managed (that panel is removed in the cleanup anyway).

## 4. UI strategy

### 4.1 Recommendation: headless first, minimal UI later

1. **Phase 3: API + CLI.** A Flask JSON API (same stack as BPM-Tagger, so patterns and
   reviewer muscle memory carry over) behind a static bearer token (`API_TOKEN`), plus
   a CLI over the same service layer: `music-grabber sync | queue | inbox | status`,
   with `--json`. The CLI works in-process on the DB for one-shots and over HTTP against
   a running instance. The only browser-facing route is the Spotify OAuth callback.
2. **Phase 4: a minimal web UI** (Vite + React + TypeScript + Tailwind, following
   BPM-Tagger's conventions, without its player, PWA or design-system weight): Playlists
   (watch toggle, sync, have/missing counts), Queue (status, retry, cancel), Ambiguity
   Inbox (candidates, choose / re-search / skip). Login by the same bearer token.

Why headless first: the engine, matching, ports and hooks are where the risk is, and the
UI is a thin client over the API. Building the API first makes the UI a pure
presentation exercise and makes the CLI/ntfy flow (the way this tool is mostly used:
ping, resolve, done) available a phase earlier.

### 4.2 What is lost in the meantime (Phases 1-3, and in 0.1.0 generally)

* **No ambiguity-inbox UI** until Phase 4: resolve via `music-grabber inbox` (list,
  `choose <item> <n>`, `search <item> "<q>"`, `skip`). ntfy click-through links point at
  the future UI route; until then the ping text includes the CLI command to run.
* No queue page (use `queue`), no playlist management page (use `playlists add <url>`).
* **Candidate audio previews** (Deezer 30 s clips in the Inbox) return in Phase 4.
* **Spotify playlists as Run/Listen sources and in Playlist diff/merge/split.** This is
  the biggest functional loss: those rows live in BPM-Tagger's table and stop syncing
  once the grabber is removed from it. Proposed mitigation (Phase 3, optional, not
  0.1.0): `playlist mirror` writes each watched Spotify playlist to **Navidrome** as a
  Subsonic playlist (`createPlaylist`/`updatePlaylist`, matched tracks only), which
  BPM-Tagger already imports through its existing Navidrome-playlist source. **Open
  question Q5.**
* Search-and-grab (Spotify search page), Suggestions page (Q4), the Stats grabber panel
  (replaced by `status`), per-track "queue similar".

## 5. Migration and cleanup

### 5.1 Existing users with data in `grab_*` tables

A one-way, read-only export from the BPM-Tagger DB, then an import in Music-Grabber
(Phase 3). BPM-Tagger is never written to.

* `music-grabber import-bpm-tagger --db /path/to/bpm_tagger.db [--include-tokens]
  [--dry-run]` opens the source DB **read-only** (`file:...?mode=ro`, `immutable=0`),
  verifies the schema (`grab_queue`, `playlists`, ...), and copies:
  * `grab_queue`, `grab_candidates`, `grab_events` (ids remapped; candidate FKs follow);
  * Spotify `playlists` + `playlist_tracks` (only `source = 'spotify'` rows);
  * `oauth_tokens` **only with `--include-tokens`** (refresh token is a credential;
    default is "re-authorize via the OAuth flow");
  * `app_counters.grabbed_total`;
  * `tracks WHERE managed = 1` → `grabbed_files` ledger (paths made relative to
    `MUSIC_DIR`; `spotify_track_id`, `isrc`, hash). This is what keeps "already have"
    answers stable for the existing library.
* Idempotent (natural keys: spotify id, queue `created_at`+sid, path), transactional,
  with a summary and a refusal to run against a non-empty target unless `--merge`.
* Settings: `music-grabber import-env` prints a Music-Grabber env block from a
  BPM-Tagger `settings.json` / compose env (names are the same, section 1.4); secrets
  are never echoed.
* Items in non-terminal states are imported as `pending`; `awaiting_user` stay in the
  inbox with their candidates.
* Provenance and a **migration guide** (Phase 5): stop the grabber in BPM-Tagger
  (`GRABBER_ENABLED=false`), export, start Music-Grabber, verify `status`, set intake
  folder, then (later) remove it from BPM-Tagger.

### 5.2 Later cleanup of BPM-Tagger (separate step, not in this repo's phases)

Order, each its own PR in BPM-Tagger:

1. **Neutral modules first** (no behaviour change): split `grabber.matching` into
   `bpm_tagger/matching.py` (score / library_match for Navidrome sync), move
   `grabber.tagging` → `bpm_tagger/tagging.py`, `grabber.path_template` →
   `bpm_tagger/path_template.py`; repoint the ~16 importers. Extends the layering test.
2. **Remove the wiring**: `main.py`, `scanner.grabber`, restart hook, `periodic_sync`
   Spotify branch, grabber blueprints, settings endpoints, `send_grabber`, config keys.
3. **Frontend**: delete grabber-only pages/components, edit the mixed-use ones
   (section 1.7), remove `GrabberGate`/nav entries/Stats block.
4. **Schema**: stop creating `grab_*`, `oauth_tokens`, `suggestions` for new DBs; **never
   drop them from existing DBs** (rollback safety and a re-run of the export).
5. **Dependencies and image**: drop `yt-dlp`, `streamrip` (keep `rapidfuzz`, `pillow`);
   docs: README, DOCKERHUB_README, CLAUDE.md, compose, `.env.example`; link to
   Music-Grabber's README.

**Release rule (hard):** this must not ship as a `VERSION` bump to BPM-Tagger's `main`
until Music-Grabber is stable, because a pushed `VERSION` bump *is* BPM-Tagger's Docker
Hub release trigger (`docker-publish.yml`). "Stable" is defined as: Music-Grabber
>= 1.0.0, the import tool verified on at least one real migrated DB, and the intake
flow verified end to end against a running BPM-Tagger. Until then the cleanup lives on a
branch in BPM-Tagger, and can ship as a beta (`vX.Y.Z-beta.N` tag; `docker-beta.yml`
never touches `:latest`) for testers who want to try the split first. Removal is a
breaking change for grabber users and gets a major or minor bump with a prominent
CHANGELOG entry and a one-release deprecation notice (the grabber logs a startup
warning pointing at Music-Grabber) before the code goes.

## 6. Phases (this repo)

| Phase | Branch | Content |
|---|---|---|
| 0 | `phase-0/extraction-plan` | this document |
| 1 | `phase-1/skeleton` | history-preserving move; `music_grabber/` package; own config/db; ports as interfaces with minimal defaults (ledger-only index, `NoAnalysis`); ported tests; ruff/pytest/CI; Dockerfile; compose; README; CHANGELOG; VERSION 0.1.0; CLAUDE.md; dispatch-only publish workflow |
| 2 | `phase-2/ports-and-hooks` | `FilesystemSource`, `OpenSubsonicSource`, `NoAnalysis`/`IntakeFolderAnalysis`/`ApiAnalysis`, ledger tag, Mediarium + Navidrome hooks, drift test |
| 3 | `phase-3/api-cli` | Flask API, CLI, settings persistence, ntfy, export/import tool; optional playlist mirror |
| 4 | `phase-4/ui` | minimal web UI |
| 5 | `phase-5/docs` | docs polish, compose examples, migration guide |

Each PR is based on the previous branch while that one is unmerged. **Merge, don't
squash**, Phase 1: the extracted BPM-Tagger history (a merge of unrelated histories) is
the point.

## 7. Risks

* **Library-index cost on first scan** (filesystem, large libraries): incremental,
  progress-reported; expected one-time minutes.
* **OpenSubsonic walk fidelity** (empty-query `search3`, paging cap): verify early in
  Phase 2; album-walk fallback.
* **Duplicate-helper drift** (text, deezer subset): golden-corpus test, provenance doc.
* **Deezer ARL / yt-dlp breakage** is the reason for the split; Music-Grabber's image
  can now ship a yt-dlp bump on its own cadence.
* **Intake race**: a user running both a BPM-Tagger scan and a grab at the same time is
  fine; BPM-Tagger's 10 s debounce plus atomic `os.replace` placement cover partial files.
* **Legal/ToS**: the repo is private and the image is never published by automation
  (manual `workflow_dispatch` only).

## 8. Open questions for the owner

* **Q1.** `Music-raw` is not in BPM-Tagger's code; confirm it is a subfolder (or a mount)
  of BPM-Tagger's `MUSIC_DIR`. Assumed so; `INTAKE_DIR` is just a path.
* **Q2.** `ApiAnalysis`: is a lightweight per-folder scan trigger on BPM-Tagger acceptable
  later, or should "run BPM-Tagger in watch mode" be the only supported intake setup?
* **Q3.** Writing a `SPOTIFY_TRACK_ID` tag into grabbed files: OK? It is what makes the
  ledger rebuildable, but it is new behaviour.
* **Q4.** Suggestions page: drop, or later re-home on the library index?
* **Q5.** Spotify playlists as Run/Listen sources: is the Navidrome playlist mirror
  (Phase 3, optional) the right way to keep them, or is losing them acceptable?
* **Q6.** Mediarium's `POST /api/music/import/scan` response shape (to tell "started"
  from "already running") and whether the API key can be scoped to that route only.
  Phase 2 treats any 2xx as success and does not depend on the body.
