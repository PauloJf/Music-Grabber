# Q3 and Q5: design notes (no code yet)

Two open questions from [`extraction.md`](extraction.md) section 8. Each is described the same way:
what it does, what users see, what it adds, how it is tested without a network, the risks, a rough
size. The recommendation is at the end; the owner decides.

## Q3. Spotify id written into grabbed files

**What it does.** When Music Grabber tags a file it also writes the Spotify track id into a custom
tag: ID3 `TXXX:SPOTIFY_TRACK_ID`, Vorbis comment `SPOTIFY_TRACK_ID`, MP4
`----:com.gatoserio:spotify_track_id`. The filesystem library index reads it back, so "do we already
have this Spotify track?" can be answered from the files themselves, not only from the ledger table.

**What users see.**
- New grabs carry one extra tag. Players, Navidrome and BPM Tagger ignore unknown tags.
- A grabbed file that is moved, renamed or copied to another folder still counts as "have"
  (today the ledger row points at the old path, and a missing path means "re-grab").
- A lost or fresh database can be rebuilt from the files: `ledger rebuild` scans the library and
  inserts a ledger row for every file that has the tag. It only adds rows.
- Nothing is ever written into files that already exist. No backfill: the ~860 files from a
  migration stay untagged (they keep their ledger rows from the import).

**Additions.**
- Config: `WRITE_SPOTIFY_ID_TAG` (default `true`; `false` restores today's files). Documented in
  README, `.env.example`, compose files (the docs test enforces it).
- Schema: additive `spotify_track_id` column on `library_tracks`, indexed; `read_tags` returns it;
  `library_match` consults the index by id after the ledger. No change to `grabbed_files`.
- Code: `write_track_tags` (one place, before placement, so intake mode still never touches a file
  after placing it), `read_tags`/`FilesystemSource`, `LibraryIndex.find_by_spotify_id`, one CLI command.

**Tests (no network).** Round-trip per format with the generated audio in `tests/audio_fixtures.py`
(write then read back for mp3, flac, m4a); mtime preserved; the id survives a BPM Tagger-style
rewrite of other tags (mutagen preserves unknown frames; to be confirmed once in the container rig
used for the 1.0 verification); index matches a moved file; `ledger rebuild` adds rows and is
idempotent; the flag off writes nothing.

**Risks.**
- It changes files the user owns. Mitigation: new grabs only, one flag to turn it off, documented.
- It helps only `LIBRARY_INDEX=filesystem`. OpenSubsonic (BPM Tagger/Navidrome) does not expose
  custom tags, so that mode gets nothing from it.
- Marginal today: ISRC is already written and matched, and the ledger already answers "have" for
  everything Music Grabber filed. The id adds value when a file's ISRC is wrong/missing (Spotify
  re-linked recordings) and for rebuild/move. In the real database used to verify the import, 375 of
  863 ledger rows had no Spotify id and none of them would gain a tag.

**Size.** Small: roughly 150-250 lines plus tests, 1-2 days. Low risk.

## Q5. Navidrome playlist mirror

**What it does.** Writes each watched Spotify playlist to Navidrome as a normal playlist containing
the tracks Music Grabber has matched in the library, in Spotify order. BPM Tagger already imports
Navidrome playlists, so a mirrored Spotify playlist is usable again for Run and Listen, which is the
one big loss in the migration ("what you lose for now").

**What users see.**
- Off by default. With `PLAYLIST_MIRROR=navidrome` (reusing `NAVIDROME_URL/USER/PASS`), each
  watched playlist appears in Navidrome as `Spotify: <name>` (`MIRROR_PREFIX`, changeable).
- It updates after a sync that changed the playlist or its matches, and after new files are placed
  and Navidrome has scanned them. Tracks that are not in the library yet are simply absent until
  they are; Spotify local files and unmatched tracks are never mirrored.
- It is **one-way and owned by Music Grabber**: edits made to a mirrored playlist in Navidrome are
  replaced at the next change. Stated in the README and the Playlists page.
- CLI `playlist mirror ID|--all` (do it now), `playlist mirror ID --off|--on`, `--reset`; the
  Playlists page gets a per-playlist toggle and a "mirrored, N tracks, synced 3 min ago" line;
  `status` shows it.

**How it avoids duplicates and overwrites (the rules).**
1. Ownership is explicit. A remote playlist is ours only if its id is recorded in our table **and**
   its Subsonic `comment` carries the marker `music-grabber:<spotify playlist id>`. Everything else
   in the user's Navidrome is never read for writing, updated or deleted.
2. First sync looks for an existing playlist with our marker (a restored database does not create a
   second copy); otherwise creates one and sets the marker.
3. A name collision with a playlist we do not own is never overwritten: the mirror reports
   "name taken" for that playlist and does nothing.
4. Updates replace the contents of our own playlist in one request (`createPlaylist` with
   `playlistId` and the ordered `songId` list), and only when the desired song list differs from the
   hash we stored after the last write; an unchanged playlist causes no write at all.
5. If the user deleted the mirror in Navidrome, it is not silently recreated: the playlist shows
   "deleted remotely" until `--reset`.
6. Unwatching or disabling a Spotify playlist, or turning the mirror off, leaves the remote
   playlist in place. Music Grabber deletes nothing in 1.0; `playlist mirror ID --remove` is the
   only delete and only for a playlist that passes rule 1.

**Switched off by.** Leaving `PLAYLIST_MIRROR` empty (default) or the per-playlist toggle. Neither
touches what was already written.

**Additions.**
- Config: `PLAYLIST_MIRROR`, `MIRROR_PREFIX`. Reuses the Navidrome credentials and `subsonic.py`.
- Schema: `playlists.mirror` (default 1, only matters when the feature is on) and a new
  `playlist_mirrors(playlist_id PK, remote_id, remote_name, content_hash, synced_at, last_error)`.
- Code: a `PlaylistMirror` (new module) driven by the sync engine after matching, coalesced like
  the post-import hooks (`Coalescer`: debounce, max wait, minimum interval) so an album of twelve
  tracks is one write. It resolves each matched file to a Navidrome song id: a walk of Navidrome's
  songs (the existing `search3` pager, which today throws the song `id` away and must keep it) and
  a path match, exact first, then the shortest unique path suffix, since Navidrome's library root
  and ours can differ; ambiguous or missing matches are skipped and counted.
  The walk only runs when a playlist's desired list changed.
- Ops/API/CLI/UI: `Ops.playlist_mirror(...)`, `POST /api/playlists/<id>/mirror`, the CLI commands
  above, a toggle and a status line in the web UI.

**Tests (no network).** Extend `tests/fake_servers.py` with a loopback fake Navidrome
(`getPlaylists`, `createPlaylist`, `updatePlaylist`, `deletePlaylist`, `search3`) and check: first
create sets the marker; a second run with nothing changed makes zero writes; order follows Spotify;
unowned same-name playlist is untouched and reported; restored database adopts the marked playlist
instead of duplicating; remote delete is not recreated until reset; unresolved tracks are skipped
and counted; wrong credentials disable the mirror with one log line; the feature off sends no
request at all; secrets never appear in logs. Plus the docs tests for the new env vars.

**Risks.**
- It writes to the user's Navidrome. Mitigated by default off, rules 1-6, one-way ownership, and
  that nothing is deleted without an explicit command.
- Navidrome's Subsonic implementation (replace semantics of `createPlaylist`, `comment` support,
  what `createPlaylist` returns) varies by version. Before release it needs one run against a real
  Navidrome container with a scratch library, like the BPM Tagger intake check done for 1.0.
- Path matching between two libraries is the fragile part; the suffix rule and "skip and count"
  keep it from putting the wrong track in a playlist, at the price of occasional gaps.
- A very large library makes the song walk slow (about one request per 500 songs); it only runs on
  change.
- Timing: new files appear in Navidrome (and, in intake mode, are tagged by BPM Tagger) before a
  scan; tracks join the playlist on the next mirror pass. BPM Tagger imports Navidrome playlists on
  its own schedule, so a change can take two cycles to reach the player.

**Size.** Medium: roughly 500-700 lines plus tests and the fake server, a small UI addition, 3-5
days including the container check.

## Recommendation

- **Build Q5 for 1.0.** It restores the one loss the migration guide has to apologise for (Spotify
  playlists in Run/Listen), it is off by default, and the rules above keep it away from anything the
  user did not get from Music Grabber. It needs the real-Navidrome check, so start it early.
- **Defer Q3 to 1.1.** The files are the user's, the benefit is invisible today (ISRC and the ledger
  already answer "have"), it does nothing for the OpenSubsonic index, and it cannot be added to
  existing files without a second, riskier decision. Adding the tag later is purely additive; the
  only cost of waiting is that files grabbed before it ships will not carry it (they would be
  covered by the ledger, as the migrated ones are).
- Working assumptions, to be confirmed by the owner: Q4 (Suggestions dropped; Discover covers it). Not in 1.0: Q2
  (watch mode stays the only intake) and Q6 (any 2xx from Mediarium counts as success).

## As built (owner decision: both before 1.0)

Q3 is #15, Q5 is #16 (stacked on it). Where they differ from the text above:

- **Q3 has no MP4 variant.** The grabber only produces mp3, flac and opus, and the old m4a tag path
  was already broken, so only ID3 `TXXX:SPOTIFY_TRACK_ID` and the Vorbis comment exist.
- **Q5 ownership is the recorded id *or* the `music-grabber:<id>` comment**, not both: the id alone
  recovers from a failed marker write, the marker alone lets a restored database adopt the playlist.
- **Q5 song matching** is path (exact, then unique suffix), then the file's ISRC, then its artist +
  title within 3 s of its duration. Checked against a real Navidrome: newer versions report a display
  path (`Artist/Album/01-01 - Title.mp3`) instead of the real one, so paths alone resolved nothing.
- **Emptying a mirrored playlist** uses `updatePlaylist songIndexToRemove`: `createPlaylist` with a
  `playlistId` and no songs is a no-op there.
- Writing (mirror, ledger rebuild) is administrators only in the web UI/API.
