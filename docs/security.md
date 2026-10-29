# Security review

Reviewed on branch `phase-7/security-help` with bandit, pip-audit, npm audit and a manual read of
the API, settings, OAuth, download and file-placement paths.

## Accounts and sign-in

Yes, since phase 8. The web UI signs people in with a **username and password**:

- First visit: the login page offers to create the first account (an administrator). That endpoint
  only works while no account exists **and** needs the server's `API_TOKEN`, so merely reaching the
  port is not enough to claim the installation.
- Passwords: 10 to 200 characters, hashed with scrypt (N=2^14, r=8, p=1, random 16-byte salt,
  parameters stored with the hash). Login takes the same time for unknown usernames.
- Sign-in returns a random 256-bit **session token**, stored server-side only as a SHA-256 hash,
  valid 14 days from the last use, revoked by Sign out, a password change (other devices) or an
  admin reset/delete. The browser keeps it in `sessionStorage` and sends it as a bearer header; there
  are no cookies, so still no CSRF surface.
- Roles: **admin** (everything, including Settings, Spotify and managing people) and **user**
  (browse, queue, Inbox, playlists, own account). The last administrator cannot be deleted or demoted.
- `API_TOKEN` stays: it is an always-admin machine key for the CLI `--remote`, scripts and as
  first-run/recovery proof. Lost the only admin password? `python -m music_grabber users passwd NAME`
  on the server.
- Wrong passwords and wrong tokens share one lockout (10 failures a minute per address).

## Threat model

One owner, or a few trusted people, on a home network or a small VPS. The attacker is anyone who can reach the port,
a malicious or compromised remote catalogue (Deezer/Spotify metadata and URLs), and malicious
web pages visited from the owner's browser.

## What is already sound

- Every `/api/*` route needs the token; only `/healthz` and the OAuth `/callback` are open.
- No cookies, so no CSRF; Spotify `state` is single-use with a 10-minute lifetime.
- Strict CSP (same-origin scripts, only Deezer/Spotify image CDNs, Deezer previews), `nosniff`,
  `no-referrer`, `frame-ancestors 'none'`, `Cache-Control: no-store`.
- No `shell=True`, no `eval`/`pickle`, all SQL values parameterised; file names from the catalogue are
  sanitised and placement stays inside the music folder; React escapes all rendered text.
- Secrets are env-only (Spotify) or write-only (Deezer ARL, ntfy topic) and never logged.

## Findings and fixes

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | Medium | Wrong tokens could be guessed at unlimited speed | Per-address lockout: 10 wrong tokens in 60 s gives 429 with `Retry-After` (`AuthThrottle`) |
| 2 | Medium | Cover download followed any URL from the catalogue (SSRF to LAN/localhost/cloud metadata, unbounded size) | `fetch_cover` accepts public http(s) hosts only, re-checks every redirect, caps size at 15 MB |
| 3 | Medium | Pillow 10.4 has known CVEs; streamrip 2.1.0 pins `Pillow<11` so a plain install resolves to it | Fixed: `requirements-pillow.txt` installs Pillow >=12.3 after `requirements.txt` (CI, the publish workflow and the image all do; the image build asserts the major version). Still in place as defence in depth: only JPEG/PNG/WEBP are decoded, decompression bombs capped at 40 MP, input only from the size-capped fetch above |
| 4 | Medium | `react-router` advisory in the UI bundle | Upgraded to 7.18.4, `npm audit` reports 0 vulnerabilities |
| 5 | Low | `update_grab(**fields)` built SQL column names from keyword names | Allowlist of real columns, else `ValueError` |
| 6 | Low | `ntfy_url` / `ui_public_url` settings accepted any string | Must be http(s) with a host and no embedded credentials |
| 7 | Low | `/healthz` disclosed the version | Returns `{"ok": true}` only; version stays behind the token at `/api/status` |
| 8 | Low | Unexpected errors returned an HTML error page | JSON `internal_error` with no detail; the traceback goes to the server log |
| 9 | Low | SQLite file (holds the Spotify refresh token) used the default umask | `chmod 0600` on the DB and its WAL/SHM files |
| 10 | Low | MD5/SHA-1 flagged by bandit | They are protocol/ID uses (Subsonic token auth, a cache key); marked `usedforsecurity=False` |
| 11 | Bug | ntfy pings with a non-Latin-1 character in the title (an en dash, é, ♫) were never sent (`UnicodeEncodeError` swallowed) | Headers are RFC 2047 encoded |

## Residual risks and advice

- **API keys** (for other apps) are scoped (`request`, `browse`, `library`), stored as SHA-256 hashes, shown
  once, revocable, and refused on every route outside `/api/v1`; wrong keys share the lockout. A leaked
  key can still queue downloads within its scopes: revoke it, and keep keys on a server, never in a browser.
- **Album covers** (`GET /api/library/cover/<name>`) are open like the preview clips: an `<img>` cannot send a bearer token.
  The link is an opaque bundle of up to three library paths plus an optional Deezer image address, signed with an HMAC of
  the API token and valid for 6 hours. A link we did not sign is refused; the path must also be in the library index and
  resolve inside the library folder (no `..`, no absolute paths, symlinks resolved); a redirect goes only to `https` on
  `dzcdn.net`. Thumbnails are cached under `covers/` beside the database; only JPEG, PNG and WEBP are decoded.
- The `API_TOKEN` is still a full-admin key. Use a long random value; rotate it by changing `API_TOKEN` and restarting.
- Plain HTTP exposes the token on the wire. Use TLS (reverse proxy) beyond loopback/LAN.
- The lockout is per source address; behind a reverse proxy all clients share the proxy's address,
  so a flood of wrong tokens also locks out the owner for a minute. Authenticate at the proxy too.
- **The container runs as root** and the image has **no `HEALTHCHECK`** (the API is off unless `API_TOKEN` is
  set, so a check would have to be conditional). Mount only the music, config and data folders. If you
  want a non-root user, run it with `user:` / `--user` and make the data and music folders writable by
  that uid; the app only writes to `/data` and the music folder.
- The Deezer ARL is a session credential for your own account and is stored in `settings.json`
  (mode 0600). Anyone with filesystem access to the data volume can read it.

## Dependency audit (2026-10-23, before 1.0)

- **`pip-audit -r requirements.txt`: Pillow 10.4.0, 63 advisories, fixed in 12.1.1 and later. Resolved
  by `requirements-pillow.txt` (Pillow >=12.3).** `streamrip` 2.1.0 (the latest release) requires
  `Pillow<11`, so the two cannot be one `pip install`: pip would refuse to resolve them together. The
  override is a second step, `pip install -r requirements-pillow.txt`, run after `requirements.txt` in
  the Dockerfile (which then fails the build unless `PIL.__version__` is 12 or newer), `ci.yml` and
  `docker-publish.yml`. pip prints a "dependency conflict" note for `streamrip` at that point and exits 0;
  that note is expected. Why it is safe: Music Grabber calls Pillow for cover resizing (JPEG/PNG/WEBP
  checked on Pillow 13; a GIF is still not decoded). It uses only streamrip's `DeezerClient` and `Config`;
  streamrip's own Pillow code (`media/artwork.py`, a cover-downscale helper) belongs to its media
  pipeline, which is never called. The whole suite and the `DeezerClient` import were checked on Pillow 13. The real Deezer download path needs an ARL
  and cannot be exercised in tests, so check one real download after upgrading. `tests/test_workflows.py`
  guards that every environment installs the override. Nothing else in `requirements.txt` has an advisory.
- **`npm audit` (frontend): 13 findings (2 critical, 6 high, 5 moderate), all in build and test
  tooling**: `vitest`/`tinypool` (critical), `vite`/`esbuild`, `tailwindcss` and their transitive
  packages (`braces`, `micromatch`, `chokidar`, `fast-glob`, `postcss-*`). None of it ships in the
  bundle (the runtime dependencies are React, react-router and react-query, with no advisories) or runs
  in the container (it only serves the built files). The fixes are major upgrades (vite 8, vitest 5,
  tailwindcss 4) and `npm audit fix` without `--force` changes nothing. They affect whoever runs
  the dev server or the tests on their own machine; plan them as their own PR.
