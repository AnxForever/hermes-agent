"""Authentication primitives for the Hermes data-plane API server.

This module is the home of:

- ``users`` table schema (DDL exported so ``api_server.py`` can run it at
  init time without duplicating SQL).
- Password hashing via ``hashlib.pbkdf2_hmac`` (SHA-256, 200 000 iterations,
  16-byte salt). We picked PBKDF2 because neither ``argon2-cffi`` nor
  ``bcrypt`` is in the dependency tree right now and dragging another
  C-extension into the gateway image isn't worth it for the first cut.
  Hashes are stored as ``pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>`` so
  a future migration to argon2/bcrypt can detect-by-prefix and re-hash on
  next login.
- HS256 JWT helpers wrapping PyJWT (already a transitive dep, see
  ``pyproject.toml``). The signing key is the same ``API_SERVER_KEY`` env
  var that legacy Bearer auth checks against — one secret, two surfaces.
- ``check_bearer`` middleware factory used by ``api_server._check_auth``
  to do dual-path validation (JWT first, fall back to legacy API key).

Downstream integration points (TODO when wiring this in):
- ``api_server._init_db``: run :data:`USERS_TABLE_DDL` and
  :data:`USER_SKILL_OVERRIDES_DDL` next to the existing ``responses``
  table.
- ``api_server._check_auth``: replace its body with a call to
  :func:`check_bearer`.
- ``api_server`` routes ``/v1/auth/*``: import :func:`verify_password`,
  :func:`hash_password`, :func:`encode_jwt`.

No code in this module talks to aiohttp directly — that's intentional.
It keeps the unit tests free of HTTP fixtures.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import jwt  # PyJWT, pinned in pyproject.toml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default token lifetime. Long enough for a working session, short enough
#: that a stolen token expires before too much damage is done. Override per
#: call by passing ``ttl_seconds`` to :func:`encode_jwt`.
JWT_TTL_SECONDS = 12 * 60 * 60  # 12h

#: JWT signing algorithm. HS256 is fine for a shared-secret deployment.
#: If we ever move to multi-tenant SaaS with rotating keys, swap to RS256
#: and stash key material in the ``users`` table.
JWT_ALGORITHM = "HS256"

#: Default role assigned to new users when ``role`` isn't specified.
DEFAULT_ROLE = "user"

#: PBKDF2 iteration count. NIST minimum is 1000; OWASP recommends
#: 600k+ for SHA-256, but the gateway runs login on a single thread
#: alongside live agent traffic, so 200k is the pragmatic ceiling. Bump
#: when we move auth to a worker pool.
PBKDF2_ITERATIONS = 200_000
PBKDF2_HASH_LEN = 32  # 256 bits
PBKDF2_SALT_LEN = 16  # 128 bits

#: Hash-format prefix so future migrations can detect-and-rehash.
_HASH_PREFIX = "pbkdf2_sha256"


# ---------------------------------------------------------------------------
# SQL DDL (re-run idempotently by api_server at startup)
# ---------------------------------------------------------------------------


USERS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    handle TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_seen REAL,
    disabled INTEGER NOT NULL DEFAULT 0
)
"""

USER_SKILL_OVERRIDES_DDL = """
CREATE TABLE IF NOT EXISTS user_skill_overrides (
    user_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, skill_name)
)
"""

MCP_SERVERS_DDL = """
CREATE TABLE IF NOT EXISTS mcp_servers (
    name TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '[]',
    env_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    scope TEXT NOT NULL DEFAULT 'global',
    created_at REAL NOT NULL
)
"""

ALL_DDL: Tuple[str, ...] = (
    USERS_TABLE_DDL,
    USER_SKILL_OVERRIDES_DDL,
    MCP_SERVERS_DDL,
)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Hash ``password`` with PBKDF2-SHA256 + per-password random salt.

    Returns a string of the form ``pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>``.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    salt = secrets.token_bytes(PBKDF2_SALT_LEN)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=PBKDF2_HASH_LEN,
    )
    return f"{_HASH_PREFIX}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time compare ``password`` against a stored hash string.

    Returns ``False`` for any malformed input rather than raising — callers
    in login flows shouldn't need to distinguish "wrong password" from
    "corrupt hash row" at the API layer.
    """
    if not password or not stored:
        return False
    try:
        prefix, iter_str, salt_hex, hash_hex = stored.split("$", 3)
    except ValueError:
        return False
    if prefix != _HASH_PREFIX:
        return False
    try:
        iterations = int(iter_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=len(expected),
    )
    return hmac.compare_digest(actual, expected)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenPayload:
    """Decoded JWT claims relevant to the API server."""

    user_id: str
    handle: str
    role: str
    issued_at: int
    expires_at: int

    def to_user_dict(self) -> Dict[str, Any]:
        """Shape expected by ``request['user']`` downstream."""
        return {
            "user_id": self.user_id,
            "handle": self.handle,
            "role": self.role,
            "auth_source": "jwt",
        }


def encode_jwt(
    *,
    user_id: str,
    handle: str,
    role: str,
    signing_key: str,
    ttl_seconds: int = JWT_TTL_SECONDS,
    now: Optional[int] = None,
) -> Tuple[str, int]:
    """Sign and return ``(jwt_string, expires_at_epoch_seconds)``.

    Caller is responsible for storing/returning the expiry to the client
    in whatever wire format is appropriate (``expires_in`` for OAuth-style
    responses, ``exp`` claim is already inside the token).
    """
    if not signing_key:
        raise ValueError(
            "signing_key is empty — refuse to issue an unsigned JWT. "
            "Configure API_SERVER_KEY before enabling /v1/auth/login."
        )
    issued_at = int(now if now is not None else time.time())
    expires_at = issued_at + int(ttl_seconds)
    claims = {
        "sub": user_id,
        "handle": handle,
        "role": role,
        "iat": issued_at,
        "exp": expires_at,
    }
    token = jwt.encode(claims, signing_key, algorithm=JWT_ALGORITHM)
    # PyJWT >=2 returns str already, but older versions returned bytes —
    # normalize defensively.
    if isinstance(token, bytes):
        token = token.decode("ascii")
    return token, expires_at


def decode_jwt(token: str, *, signing_key: str) -> Optional[TokenPayload]:
    """Validate ``token`` and return its claims, or ``None`` on failure.

    Returns ``None`` for every failure mode (expired, bad signature,
    malformed) so the caller can fall through to legacy API-key checks
    without a noisy stack trace.
    """
    if not token or not signing_key:
        return None
    try:
        claims = jwt.decode(token, signing_key, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        logger.debug("JWT decode failed: %s", exc)
        return None
    try:
        return TokenPayload(
            user_id=str(claims["sub"]),
            handle=str(claims["handle"]),
            role=str(claims.get("role", DEFAULT_ROLE)),
            issued_at=int(claims["iat"]),
            expires_at=int(claims["exp"]),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("JWT decoded but missing required claims")
        return None


# ---------------------------------------------------------------------------
# Dual-path bearer check
# ---------------------------------------------------------------------------


def check_bearer(
    *,
    authorization_header: str,
    api_key: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate a ``Authorization: Bearer …`` header.

    Returns ``(user_dict, None)`` on success or ``(None, reason_string)``
    on failure. ``user_dict`` is the shape downstream handlers stash on
    ``request['user']``.

    Validation order:
      1. JWT signed with ``api_key`` (the primary path for the new web
         clients).
      2. Legacy raw-API-key compare (``hmac.compare_digest`` against
         ``api_key``) — keeps the Feishu adapter and existing dashboard
         working without re-issuing tokens.

    If both fail, the returned reason is non-empty so callers can shape a
    consistent 401 response. The reason string is meant for logs, not for
    the wire — it may name which path was tried.
    """
    if not authorization_header:
        return None, "missing Authorization header"
    if not authorization_header.startswith("Bearer "):
        return None, "Authorization header must use Bearer scheme"
    token = authorization_header[len("Bearer "):].strip()
    if not token:
        return None, "empty Bearer token"

    if not api_key:
        # Local-only mode: api_server treats this as "auth disabled" and
        # never even calls us — but be defensive in case a caller does.
        return None, "no API key configured on server"

    payload = decode_jwt(token, signing_key=api_key)
    if payload is not None:
        return payload.to_user_dict(), None

    # Fall back to legacy raw key match.
    if hmac.compare_digest(token, api_key):
        return (
            {
                "user_id": "_legacy_key_",
                "handle": "_legacy_key_",
                "role": "admin",
                "auth_source": "legacy_api_key",
            },
            None,
        )

    return None, "token is neither a valid JWT nor the legacy API key"


# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------


def make_user_id() -> str:
    """Stable opaque user id. Hex so it survives URL/HTTP encoding."""
    return secrets.token_hex(12)


def bootstrap_admin_password_from_env() -> Optional[str]:
    """Look up ``HERMES_BOOTSTRAP_ADMIN_PASSWORD`` from the environment.

    Returns the password (one-shot use) or ``None`` if unset/empty. The
    caller is expected to delete the row creation log line after first
    use; we don't persist the plaintext anywhere.
    """
    pwd = os.environ.get("HERMES_BOOTSTRAP_ADMIN_PASSWORD", "").strip()
    return pwd or None


# ---------------------------------------------------------------------------
# AuthStore — SQLite-backed user/override/MCP CRUD
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserRecord:
    user_id: str
    handle: str
    role: str
    password_hash: str
    created_at: float
    last_seen: Optional[float]
    disabled: bool


class AuthStore:
    """Thin SQLite wrapper for users, skill overrides, and MCP servers.

    Opens its own connection so the response-store transaction lifetime
    doesn't bleed into auth flows. Both stores live in the same db file —
    SQLite's WAL mode handles multi-connection writes serialised at the
    file level, which is fine for our throughput (single-instance gateway).
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        # check_same_thread=False because aiohttp runs handlers on the
        # main loop thread but auth helpers may be called from anywhere
        # (bootstrap, tests). We serialise writes via SQLite's own locks.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            from hermes_state import apply_wal_with_fallback  # type: ignore
            apply_wal_with_fallback(self._conn, db_label="response_store.db (auth)")
        except Exception:
            # Best-effort — WAL is not load-bearing for correctness.
            pass
        for ddl in ALL_DDL:
            self._conn.execute(ddl)
        self._conn.commit()

    # -- user CRUD --------------------------------------------------

    def create_user(
        self,
        *,
        handle: str,
        password: str,
        role: str = DEFAULT_ROLE,
        user_id: Optional[str] = None,
    ) -> UserRecord:
        if not handle:
            raise ValueError("handle required")
        uid = user_id or make_user_id()
        now = time.time()
        pwd_hash = hash_password(password)
        try:
            self._conn.execute(
                "INSERT INTO users (user_id, handle, role, password_hash, "
                "created_at, last_seen, disabled) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (uid, handle, role, pwd_hash, now, None),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"user with handle {handle!r} already exists") from exc
        return UserRecord(
            user_id=uid,
            handle=handle,
            role=role,
            password_hash=pwd_hash,
            created_at=now,
            last_seen=None,
            disabled=False,
        )

    def get_user_by_handle(self, handle: str) -> Optional[UserRecord]:
        row = self._conn.execute(
            "SELECT user_id, handle, role, password_hash, created_at, "
            "last_seen, disabled FROM users WHERE handle = ?",
            (handle,),
        ).fetchone()
        return _row_to_user(row) if row else None

    def get_user_by_id(self, user_id: str) -> Optional[UserRecord]:
        row = self._conn.execute(
            "SELECT user_id, handle, role, password_hash, created_at, "
            "last_seen, disabled FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return _row_to_user(row) if row else None

    def touch_last_seen(self, user_id: str) -> None:
        self._conn.execute(
            "UPDATE users SET last_seen = ? WHERE user_id = ?",
            (time.time(), user_id),
        )
        self._conn.commit()

    def count_users(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM users").fetchone()
        return int(row[0]) if row else 0

    def list_users(self) -> List[UserRecord]:
        rows = self._conn.execute(
            "SELECT user_id, handle, role, password_hash, created_at, "
            "last_seen, disabled FROM users ORDER BY created_at ASC"
        ).fetchall()
        return [_row_to_user(r) for r in rows]

    def update_user_password(self, user_id: str, new_password: str) -> bool:
        """Reset a user's password hash. Returns True if a row was changed."""
        cur = self._conn.execute(
            "UPDATE users SET password_hash = ? WHERE user_id = ?",
            (hash_password(new_password), user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def set_user_role(self, user_id: str, role: str) -> bool:
        cur = self._conn.execute(
            "UPDATE users SET role = ? WHERE user_id = ?", (role, user_id)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def set_user_disabled(self, user_id: str, disabled: bool) -> bool:
        cur = self._conn.execute(
            "UPDATE users SET disabled = ? WHERE user_id = ?",
            (1 if disabled else 0, user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_user(self, user_id: str) -> bool:
        """Delete a user + cascade their skill overrides.

        MCP server entries are scoped globally so they are NOT cascaded —
        admin-installed servers survive a user delete.
        """
        # Remove dependent rows first to keep referential intent honest
        # even though we don't declare FK constraints.
        self._conn.execute(
            "DELETE FROM user_skill_overrides WHERE user_id = ?", (user_id,)
        )
        cur = self._conn.execute(
            "DELETE FROM users WHERE user_id = ?", (user_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    # -- skill overrides --------------------------------------------

    def set_skill_override(
        self, *, user_id: str, skill_name: str, enabled: bool
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO user_skill_overrides "
            "(user_id, skill_name, enabled, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, skill_name, 1 if enabled else 0, time.time()),
        )
        self._conn.commit()

    def get_skill_overrides(self, user_id: str) -> Dict[str, bool]:
        rows = self._conn.execute(
            "SELECT skill_name, enabled FROM user_skill_overrides "
            "WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {name: bool(enabled) for name, enabled in rows}

    # -- mcp servers ------------------------------------------------

    def upsert_mcp_server(
        self,
        *,
        name: str,
        command: str,
        args: Optional[Iterable[str]] = None,
        env: Optional[Dict[str, str]] = None,
        enabled: bool = True,
        scope: str = "global",
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO mcp_servers "
            "(name, command, args_json, env_json, enabled, scope, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, COALESCE("
            "  (SELECT created_at FROM mcp_servers WHERE name = ?), ?))",
            (
                name,
                command,
                json.dumps(list(args or [])),
                json.dumps(dict(env or {})),
                1 if enabled else 0,
                scope,
                name,
                time.time(),
            ),
        )
        self._conn.commit()

    def list_mcp_servers(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT name, command, args_json, env_json, enabled, scope, "
            "created_at FROM mcp_servers ORDER BY name ASC"
        ).fetchall()
        result = []
        for name, command, args_json, env_json, enabled, scope, created_at in rows:
            try:
                args = json.loads(args_json or "[]")
            except json.JSONDecodeError:
                args = []
            try:
                env = json.loads(env_json or "{}")
            except json.JSONDecodeError:
                env = {}
            result.append(
                {
                    "name": name,
                    "command": command,
                    "args": args,
                    "env": env,
                    "enabled": bool(enabled),
                    "scope": scope,
                    "created_at": created_at,
                }
            )
        return result

    def delete_mcp_server(self, name: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM mcp_servers WHERE name = ?", (name,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    # -- bootstrap --------------------------------------------------

    def ensure_bootstrap_admin(self, *, handle: str = "admin") -> Optional[UserRecord]:
        """Create an initial admin user from env if the users table is empty.

        Returns the newly-created record, or ``None`` if no bootstrap
        happened (table non-empty, or env var unset). Logs (at INFO) the
        handle, never the password.
        """
        if self.count_users() > 0:
            return None
        password = bootstrap_admin_password_from_env()
        if not password:
            logger.info(
                "users table empty and HERMES_BOOTSTRAP_ADMIN_PASSWORD unset — "
                "skipping bootstrap admin. Set the env var to enable login."
            )
            return None
        record = self.create_user(handle=handle, password=password, role="admin")
        logger.info(
            "Bootstrap admin user %r created (role=admin). "
            "Unset HERMES_BOOTSTRAP_ADMIN_PASSWORD on next restart.",
            handle,
        )
        return record

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


def _row_to_user(row: Any) -> UserRecord:
    user_id, handle, role, password_hash, created_at, last_seen, disabled = row
    return UserRecord(
        user_id=str(user_id),
        handle=str(handle),
        role=str(role),
        password_hash=str(password_hash),
        created_at=float(created_at),
        last_seen=float(last_seen) if last_seen is not None else None,
        disabled=bool(disabled),
    )


__all__ = [
    "JWT_TTL_SECONDS",
    "JWT_ALGORITHM",
    "DEFAULT_ROLE",
    "USERS_TABLE_DDL",
    "USER_SKILL_OVERRIDES_DDL",
    "MCP_SERVERS_DDL",
    "ALL_DDL",
    "TokenPayload",
    "UserRecord",
    "AuthStore",
    "hash_password",
    "verify_password",
    "encode_jwt",
    "decode_jwt",
    "check_bearer",
    "make_user_id",
    "bootstrap_admin_password_from_env",
]
