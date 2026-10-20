"""A minimal OpenSubsonic client: just enough for the library index and the Navidrome hook.

Works against BPM Tagger's optional Subsonic API (``SUBSONIC_ENABLED``) and against
Navidrome itself. Auth is either an OpenSubsonic API key or the classic salted token
(``u`` + ``t`` + ``s``); the two are never combined.

Error messages never include the request URL or parameters, which carry the credential.
"""

import hashlib
import logging
import secrets
from typing import Optional

import requests

log = logging.getLogger(__name__)

API_VERSION = "1.16.1"
CLIENT_NAME = "music-grabber"


class SubsonicError(Exception):
    """The server was unreachable or answered with an error. ``status`` is the HTTP
    status when there was one."""

    def __init__(self, message: str, status: Optional[int] = None, code: Optional[int] = None):
        super().__init__(message)
        self.status, self.code = status, code


def auth_params(user: str = "", password: str = "", api_key: str = "") -> dict:
    params = {"v": API_VERSION, "c": CLIENT_NAME, "f": "json"}
    if api_key:
        params["apiKey"] = api_key
    else:
        salt = secrets.token_hex(6)
        params.update(u=user, t=hashlib.md5((password + salt).encode()).hexdigest(), s=salt)
    return params


def has_credentials(user: str = "", password: str = "", api_key: str = "") -> bool:
    return bool(api_key or (user and password))


def call(base_url: str, endpoint: str, params: Optional[dict] = None, *, user: str = "",
         password: str = "", api_key: str = "", timeout: float = 20, session=None) -> dict:
    """GET ``{base_url}/rest/{endpoint}`` and return the inner ``subsonic-response``.
    Raises SubsonicError on a network failure, HTTP error or a ``status != ok`` reply."""
    http = session or requests
    q = {**auth_params(user, password, api_key), **(params or {})}
    try:
        resp = http.get(f"{base_url.rstrip('/')}/rest/{endpoint}", params=q, timeout=timeout)
    except requests.RequestException as exc:
        raise SubsonicError(f"{endpoint}: request failed ({type(exc).__name__})") from None
    if resp.status_code != 200:
        raise SubsonicError(f"{endpoint}: HTTP {resp.status_code}", status=resp.status_code)
    try:
        inner = (resp.json() or {}).get("subsonic-response") or {}
    except ValueError:
        raise SubsonicError(f"{endpoint}: not a Subsonic JSON response") from None
    if inner.get("status") != "ok":
        err = inner.get("error") or {}
        raise SubsonicError(f"{endpoint}: {err.get('message') or 'error response'}",
                            code=err.get("code"))
    return inner
