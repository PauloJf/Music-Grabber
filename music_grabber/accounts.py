"""Passwords and usernames for the optional user accounts (stdlib only: scrypt).

Hash format: ``scrypt$N$r$p$<salt b64>$<hash b64>``. The parameters are stored with the hash so
they can be raised later without locking anyone out.
"""

import base64
import hashlib
import hmac
import os
import re

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
MIN_PASSWORD, MAX_PASSWORD = 10, 200            # the cap keeps a huge "password" from being a CPU lever
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
ROLES = ("admin", "user")


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(_derive(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P))}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt" or len(password) > MAX_PASSWORD:
            return False
        got = _derive(password, base64.b64decode(salt), int(n), int(r), int(p))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# Verified against when the username does not exist, so a login takes as long either way.
DUMMY_HASH = hash_password("not-a-real-password")


def check_username(name: str) -> str | None:
    return None if USERNAME_RE.match(name or "") else \
        "username must be 3-32 characters: letters, digits, dot, dash or underscore, starting with a letter or digit"


def check_password(password: str, username: str = "") -> str | None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        return f"password must be at least {MIN_PASSWORD} characters"
    if len(password) > MAX_PASSWORD:
        return f"password must be at most {MAX_PASSWORD} characters"
    if username and password.lower() == username.lower():
        return "password must not be the same as the username"
    return None
