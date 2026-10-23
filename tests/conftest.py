"""Shared fixtures.

The suite must never touch a real service (Spotify, Deezer, YouTube, Mediarium, ntfy…).
Every outbound socket connection to a non-loopback address fails the test. Loopback stays
open so tests can run a throwaway local HTTP server as a fake peer.
"""

import ipaddress
import socket
import sys

import pytest

# For tests that assert POSIX file modes or separators; CI (Linux) is the reference.
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes / paths")

_real_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo


def _is_loopback(host) -> bool:
    if host in ("localhost", "127.0.0.1", "::1", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "no_proxy")


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    # A proxy configured in the environment is a loopback hop that would let a stray
    # request leave the machine, so drop it: requests then connects directly and the
    # guard below stops it.
    for var in _PROXY_VARS:
        monkeypatch.delenv(var, raising=False)

    def guarded_connect(self, address, *a, **k):
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            raise RuntimeError(f"real network access blocked in tests: {address[0]}")
        return _real_connect(self, address, *a, **k)

    def guarded_getaddrinfo(host, *a, **k):
        if isinstance(host, str) and not _is_loopback(host):
            raise RuntimeError(f"real DNS lookup blocked in tests: {host}")
        return _real_getaddrinfo(host, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
