# Integration API, and suggestions with new releases: design notes (no code yet)

Two post-1.0 features, described the same way as [`q3-q5-design.md`](q3-q5-design.md): what it does,
what users see, what it adds, how it is tested without a network, the risks, a rough size. Part 2
reverses the "Suggestions page: dropped" decision (Q4 in [`extraction.md`](extraction.md)); it is
1.1 work, not 1.0.

Working assumptions (owner to confirm, listed again at the end): hides and follows are **per user**;
the releases feed covers owned artists above a threshold, watched-playlist artists and explicit
follows; new releases are a page first, ntfy later; v1 taste = library + stars, Spotify later.

## Part 1. Integration API (for BPM Tagger and other apps)

**What exists.** `/api/browse/{search,artist,album,queue}` and `/api/queue` already search and
request over bearer auth. What is missing is safe external access, not new capability.

**Decision (owner): BPM Tagger gets "request a track" plus an "Open in Music Grabber" deep link. No
embedded browse.** BPM Tagger already holds Deezer ids (its Related panel and artist info), so a
request carries an exact id and needs no matching. Browsing inside BPM Tagger would rebuild the
Discover UI that was just extracted, and would need per-track state (owned / queued / failed) on both
sides. Server-to-server only: BPM Tagger's backend calls Grabber; a browser never holds the key.

**What users see.**
- Settings (admin) gets an **API keys** section: create a named key with scopes, see last used,
  revoke. The key is shown once.
- The Queue shows where an item came from ("requested from BPM Tagger").
- In BPM Tagger (separate change, separate repo): a request button where it lists tracks it knows
  are missing, and a link out to `UI_PUBLIC_URL/artist/<id>`, `/album/<id>` or `/?q=`. Both are
  hidden unless the integration is configured.

**Additions.**
- Schema: `api_keys(id, name, key_hash, scopes, created_by, created_at, last_used_at, revoked_at)`
  (SHA-256 of a 256-bit random key, like session tokens); `grab_queue.requested_by` and
  `source_ref` (additive columns).
- Scopes: `browse` (GET search/artist/album), `request` (create and read requests), `library`
  (`has`). A key never reaches `/api/settings`, `/api/users`, `/api/spotify` or writes outside its
  scope; `API_TOKEN` stays the admin key.
- Contract, versioned and thin over `Ops`:
  - `GET /api/v1/search`, `/artist/<dz_id>`, `/album/<dz_id>`
  - `POST /api/v1/requests` with `{dz_id}` or `{isrc|title+artist(+album)}` and optional
    `source_ref`; idempotent on `(requested_by, source_ref)`; already-owned answers
    `in_library`, in flight answers `already_queued` (existing `browse_queue` outcomes).
  - `GET /api/v1/requests/<id>` (status, progress, error) and `GET /api/v1/library/has?isrc=…`
  - Errors keep the existing `{error, message}` shape.
- CLI: `keys list|create|revoke`. Docs: README "JSON API", `docs/security.md`.
- Not built: webhooks (SSRF surface; BPM Tagger's watcher sees the file anyway), CORS.

**Tests (no network).** Scope enforcement per route, revoked/unknown key, key shown once and stored
hashed, throttle shared with other wrong tokens, idempotent request, request by ISRC with a fake
Deezer, `requested_by` shown in queue output.

**Risks.** A leaked key can queue downloads (bounded by scope, revocable, last-used visible);
`MAX_QUEUE_BATCH` and the existing queue dedupe limit abuse. The contract becomes public surface:
`/v1` is kept stable and additions are additive.

**Size.** Small to medium: about 400-600 lines plus tests, 2-3 days. The BPM Tagger side is its own
small change there.

## Part 2. Suggestions and new releases

**What it does.** A new **For you** area in the web UI with three surfaces, fed by a background job
(never on page view).

1. **New releases** from artists you own, watch or follow. The first run records a baseline so the
   back catalogue is not dumped as "new".
2. **Gaps in artists you own**: albums you only partly have ("3 of 12 tracks"), missing albums.
3. **Artists you may like**: Deezer related artists aggregated over your top artists, shown with
   "because you have X, Y"; owned and hidden artists excluded.

Every item uses the existing queue flow (preview, Queue, Undo, state ring).

**Taste signals** (weighted, each optional):
- Library artists by tracks/albums owned (`library_tracks`; both index modes) and artists on watched
  playlists. Always available.
- Stars and play counts via OpenSubsonic `getStarred2` and `getAlbumList2` (`frequent`, `highest`)
  with the existing `LIBRARY_URL`/`LIBRARY_API_KEY`; works on BPM Tagger and Navidrome. A key is one
  library user, so this signal is per library user, not per Grabber account.
- Spotify top / saved / followed artists: later, needs new scopes and an admin re-authorisation.

**Candidates** come from Deezer's public API: `artist/{id}/related`, `/top`, `/albums` (carries
`release_date`, `record_type`). Everything degrades to empty when Deezer is unreachable
(`CatalogError`).

**Mapping library artists to Deezer artists** is the fragile part. Order: a track with an ISRC
resolves to its Deezer artist id (`track/isrc:`); else a name search accepted only at high
confidence (exact normalised name, then best fan count with a track or album overlap check); else
unmapped and left out, never guessed. Results, including "no match", are cached.

**Hide and follow.**
- `artist_prefs(user_id, dz_artist_id, name, follow, hide_releases, hide_suggestions)`: hide from
  the releases page, from suggestions, or both; follow an artist you do not own.
- A per-item "not interested" for one album or suggested artist.
- A **Hidden** list to undo any of it. Nothing is deleted; hiding only filters.

**Additions.**
- Schema (all additive): `artist_map(norm_name, library_artist, dz_artist_id, confidence,
  checked_at)`, `release_cache(dz_artist_id, dz_album_id, title, kind, release_date, first_seen_at)`,
  `suggestion_cache(user_id, kind, ref, score, because, computed_at)`, `artist_prefs`,
  `item_dismissals(user_id, kind, ref)`.
- Code: `suggest.py` (scoring, pure and testable), `db/suggest.py` mixin, a refresh job on the
  sync thread's cadence (daily default; `SUGGEST_REFRESH_HOURS`, `SUGGEST_ENABLED`, default off until
  1.1 proves it), `Ops.suggestions_*`, routes `/api/suggest/{releases,gaps,artists,prefs}`, a page
  with tabs, and the deep-link target for BPM Tagger. New env vars go in `docker-compose.yml`,
  `.env.example` and the README (the docs test enforces it).
- Rate limiting: reuse `ratelimit.py` (Deezer allows roughly 50 requests per 5 s); the first
  mapping pass over a large library is slow and progress-reported, later passes touch only new
  artists and a rotating slice of releases.
- Later, optional: `GET /api/v1/suggestions` for other apps, and a ntfy ping for new releases.

**Tests (no network).** A fake Deezer in `tests/fake_servers.py` (search, `isrc:`, related,
albums with dates). Cases: ISRC mapping, low-confidence name left unmapped, baseline on first run
then only newer releases, hidden artist absent from feed and suggestions, hide is per user, owned
artists excluded, gap counts, scoring weights with and without stars, cache reuse (no repeat
calls), Deezer down degrades to empty, refresh respects the rate limit, feature off sends no
request.

**Risks.**
- Deezer's `related` quality is uneven and the API is not contractually stable: keep it behind
  `Catalog` helpers, cache, degrade quietly.
- Wrong artist mapping would produce wrong releases: strict confidence, "no match" is cached and
  visible, an admin can correct a mapping later.
- Stars are per library user: documented, and a Grabber account with no linked signal falls back
  to library-only.
- Notification noise: off by default.

**Size.** Medium to large: releases feed plus mapping about 700-900 lines (the foundation), gaps
about 200, suggestions about 300, UI about 500, plus tests and the fake Deezer. 1-2 weeks in three
PRs.

## Order

1. Part 1: keys, `requested_by`, `/api/v1/requests`, CLI, docs. (One PR.)
2. New releases feed with the artist map, cache and hide. (Foundation of Part 2.)
3. Gaps, artists you may like, stars signal.
4. BPM Tagger: request button and deep link, in that repo, after step 1 ships. It must not ride a
   `VERSION` bump to BPM Tagger's `main` before Music Grabber is stable (see `extraction.md` 5.2).

## Open questions for the owner

- **Q7.** Hides and follows per user (assumed) or shared by the household?
- **Q8.** Which artists feed the releases page: owned with at least N tracks (N = 3?), watched
  playlists, explicit follows. Right set?
- **Q9.** ntfy for new releases: page only first (assumed), or ping from the start?
- **Q10.** v1 taste = library + stars (assumed); is Spotify top artists wanted in 1.1 or later?
