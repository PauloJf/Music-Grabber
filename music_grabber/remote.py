"""``RemoteOps``: the same operations as ``Ops``, performed against a running instance's API.

Lets the CLI manage a container from outside it (``--remote http://host:8099`` plus the API
token) without opening the same SQLite file from a second process.
"""

import requests

from .ops import OpsError


class RemoteOps:
    def __init__(self, url: str, token: str, session=None, timeout: float = 120):
        self.url = url.rstrip("/")
        self._token = token
        self._http = session or requests
        self._timeout = timeout

    def _req(self, method: str, path: str, json=None, params=None) -> dict:
        try:
            resp = self._http.request(method, f"{self.url}{path}", json=json, params=params,
                                      headers={"Authorization": f"Bearer {self._token}"},
                                      timeout=self._timeout)
        except requests.RequestException as exc:
            raise OpsError("unreachable", f"cannot reach {self.url} ({type(exc).__name__})", 502) from None
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            raise OpsError(data.get("error", f"http_{resp.status_code}"),
                           data.get("message") or f"HTTP {resp.status_code}", resp.status_code)
        return data

    def _get(self, path, **params):
        return self._req("GET", path, params={k: v for k, v in params.items() if v not in (None, "")})

    def _post(self, path, body=None):
        return self._req("POST", path, json=body if body is not None else {})

    # status / library / settings
    def status(self): return self._get("/api/status")
    def library_list(self, query="", limit=100, offset=0):
        return self._get("/api/library", q=query, limit=limit, offset=offset)
    def library_refresh(self): return self._post("/api/library/refresh")
    def mirror_status(self): return self._get("/api/mirror")
    def mirror_run(self, pid=None):
        return self._post("/api/mirror/run" if pid is None else f"/api/playlists/{int(pid)}/mirror",
                          {"action": "now"} if pid is not None else None)
    def playlist_mirror(self, pid, action="now"):
        return self._post(f"/api/playlists/{int(pid)}/mirror", {"action": action})
    def ledger_rebuild(self): return self._post("/api/ledger/rebuild")
    def settings_get(self): return self._get("/api/settings")
    def settings_update(self, changes): return self._post("/api/settings", changes)
    def test_deezer(self, arl=None): return self._post("/api/settings/test-deezer", {"deezer_arl": arl})
    def users_list(self): return self._get("/api/users")
    def users_create(self, username, password, role="user"):
        return self._post("/api/users", {"username": username, "password": password, "role": role})
    def users_update(self, uid, changes): return self._req("PATCH", f"/api/users/{int(uid)}", changes)
    def users_delete(self, uid): return self._req("DELETE", f"/api/users/{int(uid)}")
    def test_ntfy(self): return self._post("/api/settings/test-ntfy")

    # spotify
    def spotify_status(self): return self._get("/api/spotify/status")
    def spotify_authorize_url(self): return self._get("/api/spotify/authorize-url")
    def spotify_disconnect(self): return self._post("/api/spotify/disconnect")
    def spotify_playlists(self): return self._get("/api/spotify/playlists")
    def spotify_search(self, query): return self._get("/api/spotify/search", q=query)

    # playlists
    def playlists_list(self): return self._get("/api/playlists")
    def playlist_get(self, pid, status=""): return self._get(f"/api/playlists/{int(pid)}", status=status)
    def playlist_add(self, value): return self._post("/api/playlists", {"value": value})
    def playlist_remove(self, pid): return self._req("DELETE", f"/api/playlists/{int(pid)}")
    def playlist_enable(self, pid, enabled=True):
        return self._req("PATCH", f"/api/playlists/{int(pid)}", json={"enabled": bool(enabled)})
    def sync(self, pid=None):
        return self._post("/api/sync" if pid is None else f"/api/playlists/{int(pid)}/sync")
    def enqueue_missing(self, pid): return self._post(f"/api/playlists/{int(pid)}/enqueue-missing")

    # queue
    def queue_list(self, status=""): return self._get("/api/queue", status=status)
    def queue_history(self): return self._get("/api/queue/history")
    def queue_item(self, item_id): return self._get(f"/api/queue/{int(item_id)}")
    def queue_add(self, meta): return self._post("/api/queue", meta)
    def queue_retry(self, item_id): return self._post(f"/api/queue/{int(item_id)}/retry")
    def queue_retry_failed(self): return self._post("/api/queue/retry-failed")
    def queue_cancel(self, item_id): return self._post(f"/api/queue/{int(item_id)}/cancel")
    def queue_delete(self, item_id): return self._req("DELETE", f"/api/queue/{int(item_id)}")
    def queue_clear_completed(self): return self._post("/api/queue/clear-completed")
    def queue_priority(self, item_id, priority):
        return self._post(f"/api/queue/{int(item_id)}/priority", {"priority": priority})

    # inbox
    def inbox_list(self): return self._get("/api/inbox")
    def inbox_preview(self, item_id, candidate_id=None):
        return self._get(f"/api/inbox/{int(item_id)}/preview", candidate=candidate_id)
    def inbox_choose(self, item_id, candidate_id):
        return self._post(f"/api/inbox/{int(item_id)}/choose", {"candidate_id": candidate_id})
    def inbox_search(self, item_id, query):
        return self._post(f"/api/inbox/{int(item_id)}/search", {"query": query})
    def inbox_research(self, item_id): return self._post(f"/api/inbox/{int(item_id)}/research")
    def inbox_research_all(self): return self._post("/api/inbox/research-all")
    def inbox_skip(self, item_id): return self._post(f"/api/inbox/{int(item_id)}/skip")
