# Moving the grabber from BPM Tagger to Music Grabber

For anyone who has been running BPM Tagger with `GRABBER_ENABLED=true`. Takes about ten
minutes; nothing in BPM Tagger is modified, so you can back out at any point.

## What moves, what doesn't

| In BPM Tagger | Becomes |
|---|---|
| Watched **Spotify** playlists and their tracks | Music Grabber's own database |
| Grab queue, candidates, event history | imported, in-flight items go back to *pending* |
| Lifetime "tracks grabbed" counter | imported |
| Files the grabber filed (`managed`) | the **ledger**: "already have it" answers stay stable |
| Spotify refresh token | only with `--include-tokens`, otherwise log in again |
| Deezer ARL, output format, path template, thresholds, ntfy | env vars with the **same names** |
| **Navidrome and Local playlists** | stay in BPM Tagger |
| BPM detection, the player, Run mode | stay in BPM Tagger |

**What you lose for now:** Spotify playlists as sources for BPM Tagger's Run/Listen pages and
its playlist diff/merge/split (those rows live in BPM Tagger's table and stop syncing once its
grabber is off), the Suggestions page, and the Spotify search page. Everything else has a
Music Grabber equivalent (Inbox, Queue, Playlists in the web UI; `queue add` for a manual grab).

## 1. Stop the grabber in BPM Tagger

Set `GRABBER_ENABLED: "false"` and restart it, so nothing writes to its database while you copy.
Everything else in BPM Tagger keeps running.

## 2. Set up Music Grabber

Pick a compose file from [`docs/compose/`](compose/):
[`with-bpm-tagger.yml`](compose/with-bpm-tagger.yml) is the usual one. Create the intake folder
(`mkdir -p /srv/music/Music-raw`, any subfolder of BPM Tagger's `MUSIC_DIR` works).

Carry over your settings. This prints a config block from BPM Tagger's `settings.json`; secrets
are listed by name and **never printed**, so copy `DEEZER_ARL` and `NTFY_TOPIC` over yourself:

```bash
python -m music_grabber import-env /path/to/bpm-tagger-data/settings.json
```

`UI_PUBLIC_URL` is deliberately not copied: BPM Tagger's address is not Music Grabber's, and
pings link to `UI_PUBLIC_URL`/inbox/<id>. Set it to where you reach Music Grabber's web UI.

`SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` and `SPOTIFY_REDIRECT_URI` come from BPM Tagger's
*environment*, not its settings file. Reuse the same Spotify app, and add Music Grabber's
redirect URI to it in the Spotify dashboard.

## 3. Import (dry run first)

Mount a *copy or read-only view* of BPM Tagger's data directory into the Music Grabber container
(or run the command on the host). Its database is opened read-only and never written.

```bash
docker compose run --rm -v /path/to/bpm-tagger-data:/bpm:ro music-grabber \
  python -m music_grabber import-bpm-tagger /bpm/bpm_tagger.db --source-music-dir /music --dry-run
```

`--source-music-dir` is BPM Tagger's `MUSIC_DIR` *as BPM Tagger saw it* (inside its container,
usually `/music`). If some managed files are not under it the import says so (they would keep
BPM Tagger's absolute path in the ledger): fix the option and redo the import into an empty
database. On Windows, Git Bash rewrites a bare `/music` into `C:/Program Files/Git/music`; use
PowerShell, or prefix the command with `MSYS_NO_PATHCONV=1`. If the dry run looks right, run it again without `--dry-run`. Add
`--include-tokens` to also carry over the Spotify login (it is a credential), otherwise run
`spotify-login` afterwards. A second run into a database that already has data needs `--merge`;
merging is safe to repeat and only adds what is new.

## 4. Check

```bash
docker compose up -d
docker compose exec music-grabber python -m music_grabber status
```

`status` should show Spotify connected, your playlists with sensible have/missing counts, and the
queue you had. Ambiguous items that were waiting in BPM Tagger's inbox are in Music Grabber's
(`inbox`, or the Inbox page). Some of them have no candidates (BPM Tagger had cleared them, or the
search found nothing); `inbox research-all` queues **every** Inbox item for a fresh search (it replaces the candidates you
already have), so use `inbox search ITEM "query"` for single ones if you want to keep those. If a playlist you already own shows lots of *missing*, check
`Library` in `status`: the library index must see your files (same mount path, or
`LIBRARY_INDEX=opensubsonic`).

## Backing out

Music Grabber never touched BPM Tagger's database. Set `GRABBER_ENABLED: "true"` again and BPM
Tagger carries on where it left off.
