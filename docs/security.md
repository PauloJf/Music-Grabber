# Security review

Reviewed on branch `phase-7/security-help` with bandit, pip-audit, npm audit and a manual read of
the API, settings, OAuth, download and file-placement paths.

## Does it have a username and password?

No. There are no user accounts. The API and web UI are protected by **one shared bearer token**
(`API_TOKEN`, at least 16 characters, compared in constant time). The web UI asks for it once and keeps
it in `sessionStorage` (cleared when the tab closes; *Sign out* in Settings clears it sooner). Anyone
holding the token has full control, including the Deezer ARL setting (write-only: it is masked on read).
That fits a single-owner homelab tool. Per-user accounts are not planned; if several people need
separate access, put the app behind an authenticating reverse proxy (Authelia, Authentik, Caddy
basic auth) and keep the token as the second factor.

## Threat model

A single owner on a home network or a small VPS. The attacker is anyone who can reach the port,
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
| 3 | Medium | Pillow 10.4 has known CVEs; streamrip 2.1.0 pins `Pillow<11` so it cannot be upgraded | Mitigated: only JPEG/PNG/WEBP are decoded, decompression bombs capped at 40 MP, input only from the size-capped fetch above. **Residual risk**, revisit when streamrip lifts the pin |
| 4 | Medium | `react-router` advisory in the UI bundle | Upgraded to 7.18.4, `npm audit` reports 0 vulnerabilities |
| 5 | Low | `update_grab(**fields)` built SQL column names from keyword names | Allowlist of real columns, else `ValueError` |
| 6 | Low | `ntfy_url` / `ui_public_url` settings accepted any string | Must be http(s) with a host and no embedded credentials |
| 7 | Low | `/healthz` disclosed the version | Returns `{"ok": true}` only; version stays behind the token at `/api/status` |
| 8 | Low | Unexpected errors returned an HTML error page | JSON `internal_error` with no detail; the traceback goes to the server log |
| 9 | Low | SQLite file (holds the Spotify refresh token) used the default umask | `chmod 0600` on the DB and its WAL/SHM files |
| 10 | Low | MD5/SHA-1 flagged by bandit | They are protocol/ID uses (Subsonic token auth, a cache key); marked `usedforsecurity=False` |
| 11 | Bug | ntfy pings with a non-Latin-1 character in the title (an en dash, é, ♫) were never sent (`UnicodeEncodeError` swallowed) | Headers are RFC 2047 encoded |

## Residual risks and advice

- One token = full access. Use a long random value; rotate by changing `API_TOKEN` and restarting.
- Plain HTTP exposes the token on the wire. Use TLS (reverse proxy) beyond loopback/LAN.
- The lockout is per source address; behind a reverse proxy all clients share the proxy's address,
  so a flood of wrong tokens also locks out the owner for a minute. Authenticate at the proxy too.
- The container runs as root by default; mount only the music, config and data folders.
- The Deezer ARL is a session credential for your own account and is stored in `settings.json`
  (mode 0600). Anyone with filesystem access to the data volume can read it.
