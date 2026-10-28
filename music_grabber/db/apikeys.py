"""Scoped API keys for other apps. Keys are stored only as SHA-256 hashes; the plain key is
shown once, when it is created."""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

TOUCH_EVERY = timedelta(minutes=1)          # last_used_at is a hint, not an audit log


def _now() -> datetime:
    return datetime.now(timezone.utc)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _key(row) -> dict:
    d = dict(row)
    d.pop("key_hash", None)
    d["scopes"] = [s for s in (d.get("scopes") or "").split(",") if s]
    return d


class ApiKeysMixin:
    def create_api_key(self, name: str, key: str, scopes: list[str], created_by: str) -> dict:
        """Raises ``ValueError('exists')`` for a taken name (case-insensitive)."""
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "INSERT INTO api_keys (name, key_hash, scopes, created_by, created_at) VALUES (?,?,?,?,?)",
                    (name, key_hash(key), ",".join(scopes), created_by, _now().isoformat()))
                conn.commit()
                kid = cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("exists") from None
        return self.get_api_key(kid)

    def get_api_key(self, key_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
        return _key(row) if row else None

    def list_api_keys(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM api_keys ORDER BY id").fetchall()
        return [_key(r) for r in rows]

    def api_key_for(self, key: str) -> Optional[dict]:
        """The live (not revoked) key row for a presented key, else None."""
        now = _now()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE key_hash=? AND revoked_at IS NULL",
                               (key_hash(key),)).fetchone()
            if not row:
                return None
            last = row["last_used_at"]
            if not last or datetime.fromisoformat(last) < now - TOUCH_EVERY:
                conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (now.isoformat(), row["id"]))
                conn.commit()
        return _key(row)

    def revoke_api_key(self, key_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                               (_now().isoformat(), key_id))
            conn.commit()
            return cur.rowcount > 0
