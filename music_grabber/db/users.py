"""User accounts and login sessions. Session tokens are stored only as SHA-256 hashes."""

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _user(row) -> dict:
    d = dict(row)
    d.pop("password_hash", None)
    d["prefs"] = json.loads(d.get("prefs") or "{}")
    return d


class UsersMixin:
    def count_users(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, username: str, password_hash: str, role: str) -> dict:
        """Raises ``ValueError('exists')`` for a taken username (case-insensitive)."""
        try:
            with self._connect() as conn:
                cur = conn.execute("INSERT INTO users (username, password_hash, role, prefs, created_at) "
                                   "VALUES (?, ?, ?, '{}', ?)", (username, password_hash, role, _iso(_now())))
                conn.commit()
                uid = cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("exists") from None
        return self.get_user(uid)

    def get_user(self, user_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return _user(row) if row else None

    def get_user_credentials(self, username: str) -> Optional[dict]:
        """The user row *with* password_hash, for login only."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [_user(r) for r in rows]

    def count_admins(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]

    def set_user_password(self, user_id: int, password_hash: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
            conn.commit()

    def set_user_role(self, user_id: int, role: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
            conn.commit()

    def set_user_prefs(self, user_id: int, prefs: dict) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET prefs=? WHERE id=?", (json.dumps(prefs), user_id))
            conn.commit()

    def touch_login(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET last_login=? WHERE id=?", (_iso(_now()), user_id))
            conn.commit()

    def delete_user(self, user_id: int) -> bool:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            cur = conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            conn.commit()
            return cur.rowcount > 0

    # ── sessions ──────────────────────────────────────────────────────────────
    def create_session(self, user_id: int, token: str, ttl_days: int) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(now),))     # housekeeping
            conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen) "
                         "VALUES (?, ?, ?, ?, ?)",
                         (token_hash(token), user_id, _iso(now), _iso(now + timedelta(days=ttl_days)), _iso(now)))
            conn.commit()

    def session_user(self, token: str, ttl_days: int) -> Optional[dict]:
        """The user a live session token belongs to; slides the expiry forward at most hourly."""
        now = _now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT u.*, s.last_seen AS _seen FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash=? AND s.expires_at > ?", (token_hash(token), _iso(now))).fetchone()
            if not row:
                return None
            if datetime.fromisoformat(row["_seen"]) < now - timedelta(hours=1):
                conn.execute("UPDATE sessions SET last_seen=?, expires_at=? WHERE token_hash=?",
                             (_iso(now), _iso(now + timedelta(days=ttl_days)), token_hash(token)))
                conn.commit()
        d = _user(row)
        d.pop("_seen", None)
        return d

    def delete_session(self, token: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))
            conn.commit()

    def delete_user_sessions(self, user_id: int, keep_token: Optional[str] = None) -> None:
        with self._connect() as conn:
            if keep_token:
                conn.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user_id, token_hash(keep_token)))
            else:
                conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            conn.commit()
