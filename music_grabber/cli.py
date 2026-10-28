"""The command line: ``python -m music_grabber <command>``.

Management commands run in-process on the database (no engine threads), or against a running
instance with ``--remote URL`` (+ ``--token`` / ``API_TOKEN``). Every command accepts the
global ``--json`` for scripts. ``run``, ``spotify-login`` and ``version`` live in main.py.
"""

import argparse
import json
import os
import sys
import time

from .ops import Ops, OpsError

COMMANDS_HELP = """\
run                       start the sync thread, the download workers and (with API_TOKEN) the API
spotify-login             one-time Spotify connect
status                    what is going on
sync [PLAYLIST_ID]        reconcile now
playlist list|show|add|remove|enable|disable|queue-missing|mirror
queue [list|history|show|add|retry|retry-failed|cancel|delete|clear-completed|priority]
inbox [list|show|preview|choose|search|research|research-all|skip]
settings [show|set|test-deezer|test-ntfy]
users [list|add|passwd|role|delete]   accounts for the web UI (also the way back in after a lost password)
keys [list|create|revoke]             API keys for other apps (scoped; the key is shown once)
library list [QUERY]      the tracks the library index knows (what search marks as owned)
library refresh           reload the library index
ledger rebuild            re-add ledger rows for files carrying our Spotify id tag
import-bpm-tagger DB      import grab_* data from a BPM Tagger database
import-env SETTINGS_JSON  print a config block from a BPM Tagger settings.json
version
"""


class _Parsers:
    """add_parser that gives every (sub)command ``--json`` too, so it works on either side
    of the command: ``--json status`` and ``status --json``."""

    def __init__(self, sub):
        self._sub = sub
        self._common = argparse.ArgumentParser(add_help=False)
        # SUPPRESS: only set the attribute when given here, never overwrite the global one.
        self._common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                                  help="machine-readable output")

    def add(self, name):
        return self._sub.add_parser(name, parents=[self._common])

    def nested(self, parser, dest="action"):
        return _Parsers(parser.add_subparsers(dest=dest))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="music_grabber", description="Spotify playlist watcher + downloader.",
                                epilog=COMMANDS_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--remote", default=os.environ.get("MG_REMOTE_URL", ""),
                   help="talk to a running instance's API instead of the local database")
    p.add_argument("--token", default="", help="API token for --remote (default: $API_TOKEN)")
    top = _Parsers(p.add_subparsers(dest="command"))
    for name in ("run", "spotify-login", "version", "status"):
        top.add(name)

    s = top.add("sync")
    s.add_argument("playlist_id", nargs="?", type=int)

    pl = top.nested(top.add("playlist"))
    pl.add("list")
    x = pl.add("show")
    x.add_argument("id", type=int)
    x.add_argument("--status", default="", choices=["", "have", "missing", "queued", "removed"])
    x = pl.add("add")
    x.add_argument("value", help="Spotify playlist URL, URI or id")
    for a in ("remove", "enable", "disable", "queue-missing"):
        pl.add(a).add_argument("id", type=int)
    x = pl.add("mirror")
    x.add_argument("id", type=int, nargs="?", help="a playlist id (default: every watched playlist)")
    g = x.add_mutually_exclusive_group()
    g.add_argument("--on", action="store_true", help="include this playlist in the mirror")
    g.add_argument("--off", action="store_true", help="leave this playlist out (what was written stays)")
    g.add_argument("--reset", action="store_true", help="try again after 'deleted remotely' / 'name taken'")
    g.add_argument("--remove", action="store_true", help="delete our Navidrome playlist and stop mirroring it")

    q = top.nested(top.add("queue"))
    x = q.add("list")
    x.add_argument("--status", default="")
    q.add("history")
    q.add("show").add_argument("id", type=int)
    x = q.add("add")
    x.add_argument("title")
    x.add_argument("--artist", default="")
    x.add_argument("--album", default="")
    x.add_argument("--spotify-id", default="")
    for a in ("retry", "cancel", "delete"):
        q.add(a).add_argument("id", type=int)
    q.add("retry-failed")
    q.add("clear-completed")
    x = q.add("priority")
    x.add_argument("id", type=int)
    x.add_argument("priority", type=int)

    ib = top.nested(top.add("inbox"))
    ib.add("list")
    ib.add("show").add_argument("id", type=int)
    x = ib.add("preview")
    x.add_argument("id", type=int)
    x.add_argument("candidate_id", type=int, nargs="?", help="omit to preview the track being looked for")
    x = ib.add("choose")
    x.add_argument("id", type=int)
    x.add_argument("candidate_id", type=int)
    x = ib.add("search")
    x.add_argument("id", type=int)
    x.add_argument("query", nargs="+")
    ib.add("research").add_argument("id", type=int)
    ib.add("research-all")
    ib.add("skip").add_argument("id", type=int)

    st = top.nested(top.add("settings"))
    st.add("show")
    x = st.add("set")
    x.add_argument("pairs", nargs="+", metavar="KEY VALUE")
    st.add("test-deezer")
    st.add("test-ntfy")

    ks = top.nested(top.add("keys"))
    ks.add("list")
    x = ks.add("create")
    x.add_argument("name")
    x.add_argument("--scopes", default="request", help="comma list of: browse, request, library")
    ks.add("revoke").add_argument("key_id", type=int)

    us = top.nested(top.add("users"))
    us.add("list")
    x = us.add("add")
    x.add_argument("username")
    x.add_argument("--role", default="user", choices=["user", "admin"])
    for a in ("passwd", "delete"):
        us.add(a).add_argument("username")
    x = us.add("role")
    x.add_argument("username")
    x.add_argument("role", choices=["user", "admin"])

    lb = top.nested(top.add("library"))
    lb.add("refresh")
    x = lb.add("list")
    x.add_argument("query", nargs="*", help="words that must all appear in title, artist, album or path")
    x.add_argument("--limit", type=int, default=100)
    x.add_argument("--offset", type=int, default=0)
    top.nested(top.add("ledger")).add("rebuild")

    x = top.add("import-bpm-tagger")
    x.add_argument("db", help="path to BPM Tagger's SQLite database (opened read-only)")
    x.add_argument("--source-music-dir", default="/music",
                   help="BPM Tagger's MUSIC_DIR as it saw it (default /music)")
    x.add_argument("--include-tokens", action="store_true", help="also copy the Spotify refresh token")
    x.add_argument("--dry-run", action="store_true", help="report what would be imported, change nothing")
    x.add_argument("--merge", action="store_true", help="add to a database that already has data")
    top.add("import-env").add_argument("settings_json")
    return p


# ── output ────────────────────────────────────────────────────────────────────
def _emit(args, data, text=None, out=None):
    out = out or sys.stdout
    if args.json or text is None:
        print(json.dumps(data, indent=2, default=str), file=out)
    else:
        print(text, file=out)


def _table(rows, cols):
    """cols: [(header, key_or_callable)]."""
    def cell(r, k):
        v = k(r) if callable(k) else r.get(k)
        return "" if v is None else str(v)
    grid = [[h for h, _ in cols]] + [[cell(r, k) for _, k in cols] for r in rows]
    widths = [max(len(row[i]) for row in grid) for i in range(len(cols))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in grid)


def _ago(epoch):
    if not epoch:
        return "never"
    s = int(time.time() - epoch)
    return f"{s}s ago" if s < 120 else f"{s // 60} min ago" if s < 7200 else f"{s // 3600} h ago"


def fmt_status(d: dict) -> str:
    sp, pl, q = d["spotify"], d["playlists"], d["queue"]
    lines = [
        f"Music Grabber {d['version']}" + ("  [DRY RUN]" if d.get("dry_run") else ""),
        f"Spotify     {'connected' if sp['connected'] else 'NOT connected'}"
        f"{'' if sp['configured'] else ' (client not configured)'}"
        + (f"  last error: {sp['last_error']}" if sp.get("last_error") else ""),
        f"Library     {d['library']['tracks']} tracks via {d['library']['index']}"
        + ("  EMPTY: auto-queueing paused, check the library mount" if d['library'].get("auto_queue_paused") else ""),
        f"Analysis    {d['analysis']}   hooks: {', '.join(d['hooks']) or 'none'}"
        + ("\nMirror      navidrome" + ("  DISABLED (Navidrome rejected the credentials)" if d['mirror']['disabled'] else "")
           if d.get("mirror", {}).get("enabled") else ""),
        f"Providers   {', '.join(d['providers']) or 'none (set DEEZER_ARL or PROVIDER_ORDER)'}",
        f"Playlists   {pl['watched']}/{pl['total']} watched: {pl['have']} have, {pl['missing']} missing, "
        f"{pl['queued']} queued",
        "Queue       " + (", ".join(f"{n} {s}" for s, n in sorted(q.items())) or "empty"),
        f"Grabbed     {d['grabbed_total']} total, {d['managed_files']} files in the ledger",
        f"Last sync   {_ago(d.get('last_synced_epoch'))}",
    ]
    return "\n".join(lines)


def fmt_candidates(cands) -> str:
    return _table(cands, [("id", "id"), ("score", lambda c: f"{(c.get('score') or 0):.2f}"),
                          ("provider", "provider"), ("title", "title"), ("artist", "artist"),
                          ("quality", "quality")])


def fmt_item(it) -> str:
    return (f"#{it['id']} [{it['status']}] {it.get('artist') or ''} - {it.get('title') or ''}"
            + (f"  ({it['note']})" if it.get("note") else "")
            + (f"  ERROR: {it['error']}" if it.get("error") else ""))


# ── dispatch ──────────────────────────────────────────────────────────────────
def make_ops(args, config, build_service=None):
    if args.remote:
        from .remote import RemoteOps
        token = args.token or os.environ.get("API_TOKEN", "")
        if not token:
            raise OpsError("token_required", "--remote needs --token or $API_TOKEN", 2)
        return RemoteOps(args.remote, token)
    if build_service is None:
        from .main import build_service
    return Ops(build_service(config), config)


def run_command(args, config, *, out=None, build_service=None) -> int:
    """Run a management command. Returns the process exit code."""
    out = out or sys.stdout
    try:
        if args.command == "import-env":
            return _import_env(args, out)
        if args.command == "import-bpm-tagger":
            return _import_bpm(args, config, out, build_service)
        ops = make_ops(args, config, build_service)
        return _dispatch(args, ops, out)
    except OpsError as e:
        print(f"error: {e}" + (f" [{e.code}]" if e.code else ""), file=sys.stderr)
        return 2 if e.http in (400, 409) else 1


def _dispatch(args, ops, out) -> int:
    c, a = args.command, getattr(args, "action", None)
    emit = lambda data, text=None: _emit(args, data, text, out)       # noqa: E731

    if c == "status":
        d = ops.status()
        emit(d, fmt_status(d))
    elif c == "sync":
        d = ops.sync(args.playlist_id)
        emit(d, "Synced. Queue: " + (", ".join(f"{n} {s}" for s, n in sorted(d['queue'].items())) or "empty"))
    elif c == "ledger":
        d = ops.ledger_rebuild()
        emit(d, f"{d['tagged_files']} tagged file(s): {d['added']} added to the ledger, "
                f"{d['already_in_ledger']} already there")
    elif c == "library" and a == "list":
        d = ops.library_list(" ".join(args.query), args.limit, args.offset)
        first = d["offset"] + 1 if d["tracks"] else 0
        emit(d, _table(d["tracks"], [("artist", lambda t: t.get("artist") or t.get("album_artist")), ("title", "title"),
                                     ("album", "album"), ("year", "year"), ("path", "path")])
             + f"\n\n{first}-{d['offset'] + len(d['tracks'])} of {d['total']}"
             + (f"  (next: --offset {d['offset'] + len(d['tracks'])})" if d["offset"] + len(d["tracks"]) < d["total"] else ""))
    elif c == "library":
        d = ops.library_refresh()
        emit(d, f"Library: {d['seen']} files, {d['updated']} updated, {d['removed']} removed")
    elif c == "playlist":
        _playlist(args, a, ops, emit)
    elif c == "queue":
        _queue(args, a or "list", ops, emit)
    elif c == "inbox":
        _inbox(args, a or "list", ops, emit)
    elif c == "settings":
        _settings(args, a or "show", ops, emit)
    elif c == "users":
        _users(args, a or "list", ops, emit)
    elif c == "keys":
        _keys(args, a or "list", ops, emit)
    else:
        print(f"unknown command: {c}", file=sys.stderr)
        return 2
    return 0


def _playlist(args, a, ops, emit):
    if a in (None, "list"):
        d = ops.playlists_list()
        emit(d, _table(d["playlists"], [
            ("id", "id"), ("on", lambda p: "yes" if p.get("enabled") else "no"), ("name", "name"),
            ("have", "have_count"), ("missing", "missing_count"), ("queued", "queued_count"),
            ("spotify id", "spotify_id")]))
    elif a == "show":
        d = ops.playlist_get(args.id, args.status)
        emit(d, f"{d['playlist']['name']}\n" + _table(d["tracks"], [
            ("#", "position"), ("status", "derived_status"), ("artist", "artist"), ("title", "title")]))
    elif a == "add":
        d = ops.playlist_add(args.value)
        note = "" if d["connected"] else "  (Spotify not connected: the name is filled in by the first sync)"
        emit(d, f"Watching #{d['playlist']['id']} {d['playlist']['name']}{note}")
    elif a == "remove":
        emit(ops.playlist_remove(args.id), f"Removed playlist #{args.id}")
    elif a == "queue-missing":
        d = ops.enqueue_missing(args.id)
        emit(d, f"Queued {d['enqueued']} missing track(s) from playlist #{args.id}")
    elif a == "mirror":
        _mirror(args, ops, emit)
    elif a in ("enable", "disable"):
        emit(ops.playlist_enable(args.id, a == "enable"), f"Playlist #{args.id} {a}d")


def _mirror(args, ops, emit):
    act = next((n for n in ("on", "off", "reset", "remove") if getattr(args, n)), "now")
    if args.id is None and act != "now":
        raise OpsError("invalid_action", f"--{act} needs a playlist id")
    d = ops.mirror_run() if args.id is None else ops.playlist_mirror(args.id, act)
    if act in ("on", "off"):
        emit(d, f"Playlist #{args.id} mirror {act}")
    elif act == "remove":
        emit(d, "Removed our Navidrome playlist" if d["removed"] else "No playlist of ours in Navidrome")
    else:
        rows = [{"id": i, **m} for i, m in (d.get("playlists") or {}).items()
                if args.id is None or int(i) == args.id]
        emit(d, _table(rows, [("id", "id"), ("state", "state"), ("tracks", "matched"),
                              ("not in Navidrome", "unresolved"), ("note", lambda r: r["error"] or r["note"])]))


def _queue(args, a, ops, emit):
    if a == "list":
        d = ops.queue_list(args.status if hasattr(args, "status") else "")
        emit(d, _table(d["items"], [("id", "id"), ("status", "status"), ("pri", "priority"),
                                    ("artist", "artist"), ("title", "title"), ("provider", "provider")]))
    elif a == "history":
        d = ops.queue_history()
        emit(d, _table(d["items"], [("id", "id"), ("status", "status"), ("artist", "artist"),
                                    ("title", "title"), ("provider", "provider")]))
    elif a == "show":
        d = ops.queue_item(args.id)
        text = fmt_item(d["item"])
        if d["candidates"]:
            text += "\n\n" + fmt_candidates(d["candidates"])
        text += "\n\n" + "\n".join(f"{e['created_at']}  {e['event']}  {e.get('detail') or ''}" for e in d["events"])
        emit(d, text)
    elif a == "add":
        meta = {"title": args.title, "artist": args.artist, "album": args.album,
                "spotify_track_id": args.spotify_id or None}
        d = ops.queue_add(meta)
        emit(d, f"Queued #{d['id']}")
    elif a == "retry-failed":
        d = ops.queue_retry_failed()
        emit(d, f"Re-queued {d['requeued']} failed item(s)")
    elif a == "clear-completed":
        d = ops.queue_clear_completed()
        emit(d, f"Removed {d['removed']} completed item(s)")
    elif a == "priority":
        emit(ops.queue_priority(args.id, args.priority), f"#{args.id} priority {args.priority}")
    else:
        fn = {"retry": ops.queue_retry, "cancel": ops.queue_cancel, "delete": ops.queue_delete}[a]
        emit(fn(args.id), f"#{args.id}: {a} ok")


def _inbox(args, a, ops, emit):
    if a == "list":
        d = ops.inbox_list()
        emit(d, "\n".join(f"{fmt_item(i)}  [{len(i['candidates'])} candidate(s)]" for i in d["items"])
             or "Inbox is empty.")
    elif a == "show":
        d = ops.queue_item(args.id)
        it = d["item"]
        emit(d, fmt_item(it) + f"\n  isrc {it.get('isrc') or '-'}, album {it.get('album') or '-'}"
             f", {int((it.get('duration_ms') or 0) / 1000)}s\n\n"
             + (fmt_candidates(d["candidates"]) if d["candidates"] else "(no candidates)")
             + f"\n\nResolve: inbox choose {it['id']} <candidate id> | inbox search {it['id']} <query> "
               f"| inbox skip {it['id']}")
    elif a == "preview":
        d = ops.inbox_preview(args.id, args.candidate_id)
        clip = ops.preview_clips().path(d["clip"]) if d.get("clip") and hasattr(ops, "preview_clips") else None
        emit(d, d["url"] or (f"30 s clip: {clip}" if clip else "") or (f"open to listen: {d['open_url']}" if d["open_url"] else "")
             or f"no preview: {d['reason']}")
    elif a == "choose":
        emit(ops.inbox_choose(args.id, args.candidate_id), f"#{args.id}: queued candidate {args.candidate_id}")
    elif a == "search":
        emit(ops.inbox_search(args.id, " ".join(args.query)), f"#{args.id}: searching again")
    elif a == "research":
        emit(ops.inbox_research(args.id), f"#{args.id}: searching again")
    elif a == "research-all":
        d = ops.inbox_research_all()
        emit(d, f"Re-queued {d['requeued']} item(s)")
    elif a == "skip":
        emit(ops.inbox_skip(args.id), f"#{args.id}: skipped")


def _read_password(confirm=True) -> str:
    """From stdin when piped (scripts), else a hidden prompt; never an argument (shell history)."""
    import getpass
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass("Password: ")
    if confirm and getpass.getpass("Again: ") != pw:
        raise OpsError("invalid_account", "the two passwords differ")
    return pw


def _users(args, a, ops, emit):
    users = ops.users_list()["users"]
    if a == "list":
        emit({"users": users}, _table(users, [("USER", "username"), ("ROLE", "role"), ("LAST LOGIN", "last_login")])
             if users else "No accounts yet: the web UI offers to create the first admin.")
        return
    if a == "add":
        d = ops.users_create(args.username, _read_password(), args.role)
        emit(d, f"Created {d['user']['role']} '{d['user']['username']}'")
        return
    match = [u for u in users if u["username"].lower() == args.username.lower()]
    if not match:
        raise OpsError("not_found", f"no user '{args.username}'", 404)
    uid = match[0]["id"]
    if a == "passwd":
        ops.users_update(uid, {"password": _read_password()})
        emit({"ok": True}, f"Password changed for '{match[0]['username']}'; they are signed out everywhere")
    elif a == "role":
        ops.users_update(uid, {"role": args.role})
        emit({"ok": True}, f"'{match[0]['username']}' is now {args.role}")
    elif a == "delete":
        ops.users_delete(uid)
        emit({"ok": True}, f"Deleted '{match[0]['username']}'")


def _keys(args, a, ops, emit):
    if a == "list":
        keys = ops.keys_list()["keys"]
        rows = [{**k, "scopes": ",".join(k["scopes"]), "state": "revoked" if k["revoked_at"] else "active"} for k in keys]
        emit({"keys": keys}, _table(rows, [("ID", "id"), ("NAME", "name"), ("SCOPES", "scopes"), ("STATE", "state"),
                                           ("LAST USED", "last_used_at")]) if keys else "No API keys yet.")
    elif a == "create":
        d = ops.keys_create(args.name, args.scopes, "cli")
        emit(d, f"Created key '{d['key']['name']}' ({','.join(d['key']['scopes'])}). Copy it now, it is not shown again:\n{d['token']}")
    elif a == "revoke":
        ops.keys_revoke(args.key_id)
        emit({"ok": True}, f"Revoked key {args.key_id}")


def _settings(args, a, ops, emit):
    if a == "show":
        d = ops.settings_get()
        emit(d, "\n".join(f"{k} = {v}" for k, v in d["settings"].items()))
    elif a == "set":
        if len(args.pairs) % 2:
            raise OpsError("invalid_setting", "settings set takes KEY VALUE pairs")
        changes = dict(zip(args.pairs[::2], args.pairs[1::2]))
        d = ops.settings_update(changes)
        emit(d, "Applied: " + ", ".join(d["applied"])
             + (f"\nRestart needed for: {', '.join(d['restart_required'])}" if d["restart_required"] else ""))
    elif a == "test-deezer":
        d = ops.test_deezer()
        emit(d, d["message"] or f"Deezer: {d['error']}")
    elif a == "test-ntfy":
        emit(ops.test_ntfy(), "ntfy test sent")


def _import_bpm(args, config, out, build_service):
    from .db import Database
    from .importer import ImportFailed, import_bpm_tagger
    target = Database(config["db_path"])
    try:
        rep = import_bpm_tagger(args.db, target,
                                library_root=config.get("library_dir") or config["music_dir"],
                                source_music_dir=args.source_music_dir,
                                include_tokens=args.include_tokens, dry_run=args.dry_run, merge=args.merge)
    except ImportFailed as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    d = rep.to_dict()
    if args.json:
        _emit(args, d, out=out)
    else:
        print(("DRY RUN, nothing was written\n" if rep.dry_run else "") + "Imported: "
              + (", ".join(f"{n} {k}" for k, n in rep.counts.items()) or "nothing")
              + ("\nSkipped (already present): " + ", ".join(f"{n} {k}" for k, n in rep.skipped.items())
                 if rep.skipped else "")
              + "".join(f"\nNote: {w}" for w in rep.warnings), file=out)
    return 0


def _import_env(args, out):
    from .importer import ImportFailed, env_from_settings
    try:
        d = env_from_settings(args.settings_json)
    except ImportFailed as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(d, indent=2), file=out)
        return 0
    print("# Music Grabber config from your BPM Tagger settings", file=out)
    for k, v in d["env"].items():
        print(f"{k}={v}", file=out)
    for var in d["copy_by_hand"]:
        print(f"# {var}= (set in BPM Tagger; copy it over yourself, it is not printed here)", file=out)
    for var in d.get("set_to_your_own", []):
        print(f"# {var}= (BPM Tagger's address is not copied: set Music Grabber's own, e.g. "
              f"http://host:8099)", file=out)
    print("# SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET / SPOTIFY_REDIRECT_URI come from your BPM Tagger "
          "environment, not settings.json", file=out)
    return 0
