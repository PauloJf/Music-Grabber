import socket

import pytest
import requests


def test_non_loopback_connections_are_blocked():
    with pytest.raises(RuntimeError, match="blocked"):
        requests.get("https://api.spotify.com/v1/me", timeout=1)
    with pytest.raises(RuntimeError, match="blocked"):
        socket.create_connection(("93.184.216.34", 80), timeout=1)


def test_loopback_is_allowed_for_local_fake_servers():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        c = socket.create_connection(srv.getsockname(), timeout=2)
        c.close()
    finally:
        srv.close()
