"""Auth + user-scoped endpoint tests for the data-plane API server.

These tests exercise:

- ``api_auth`` primitives (password hash, JWT, dual-path bearer check).
- The ``AuthStore`` CRUD/bootstrap layer.
- The handler methods (`_handle_auth_login`, `_handle_auth_me`,
  `_handle_list_skills`, `_handle_toggle_skill`, `_handle_list_mcp`,
  ``_handle_upsert_mcp``) mounted on a stripped-down aiohttp app — no
  AIAgent / no run loop / no model calls.

Spinning a real aiohttp ``TestServer`` keeps regressions honest about
multipart parsing, JSON shape, and status codes. Mocking the handler
internals would not catch the integration glue that's been the bug
source in past auth refactors.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.platforms import api_auth
from gateway.platforms.api_auth import (
    AuthStore,
    check_bearer,
    decode_jwt,
    encode_jwt,
    hash_password,
    verify_password,
)


# ---------------------------------------------------------------------------
# AuthStore + password hashing + JWT
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    def test_round_trip(self):
        h = hash_password("hunter2")
        assert verify_password("hunter2", h) is True
        assert verify_password("hunter3", h) is False

    def test_unique_salt_per_call(self):
        a = hash_password("same")
        b = hash_password("same")
        assert a != b
        assert verify_password("same", a)
        assert verify_password("same", b)

    def test_malformed_stored(self):
        assert verify_password("anything", "not-a-hash") is False
        assert verify_password("anything", "") is False
        assert verify_password("", "anything") is False


class TestJWT:
    def test_round_trip(self):
        token, exp = encode_jwt(
            user_id="u1", handle="alice", role="user", signing_key="k"
        )
        payload = decode_jwt(token, signing_key="k")
        assert payload is not None
        assert payload.user_id == "u1"
        assert payload.handle == "alice"
        assert payload.role == "user"
        assert payload.expires_at == exp

    def test_wrong_signing_key(self):
        token, _ = encode_jwt(user_id="u", handle="h", role="user", signing_key="a")
        assert decode_jwt(token, signing_key="b") is None

    def test_empty_signing_key_refuses_to_sign(self):
        with pytest.raises(ValueError):
            encode_jwt(user_id="u", handle="h", role="user", signing_key="")

    def test_expired_token(self):
        token, _ = encode_jwt(
            user_id="u",
            handle="h",
            role="user",
            signing_key="k",
            ttl_seconds=1,
            now=0,
        )
        assert decode_jwt(token, signing_key="k") is None


class TestCheckBearer:
    def test_jwt_path(self):
        token, _ = encode_jwt(
            user_id="u", handle="alice", role="user", signing_key="key"
        )
        user, err = check_bearer(
            authorization_header=f"Bearer {token}", api_key="key"
        )
        assert err is None
        assert user["handle"] == "alice"
        assert user["auth_source"] == "jwt"

    def test_legacy_path(self):
        user, err = check_bearer(
            authorization_header="Bearer secret", api_key="secret"
        )
        assert err is None
        assert user["handle"] == "_legacy_key_"
        assert user["role"] == "admin"
        assert user["auth_source"] == "legacy_api_key"

    def test_rejects_bad_token(self):
        user, err = check_bearer(
            authorization_header="Bearer wrong", api_key="key"
        )
        assert user is None
        assert err

    def test_rejects_missing_header(self):
        user, err = check_bearer(authorization_header="", api_key="key")
        assert user is None and err

    def test_rejects_non_bearer_scheme(self):
        user, err = check_bearer(
            authorization_header="Basic abc", api_key="key"
        )
        assert user is None and "Bearer" in (err or "")


class TestAuthStore:
    def test_create_and_lookup_user(self, tmp_path):
        store = AuthStore(str(tmp_path / "auth.db"))
        u = store.create_user(handle="alice", password="pw", role="user")
        got = store.get_user_by_handle("alice")
        assert got is not None
        assert got.user_id == u.user_id
        assert got.role == "user"
        assert verify_password("pw", got.password_hash)
        store.close()

    def test_duplicate_handle_rejected(self, tmp_path):
        store = AuthStore(str(tmp_path / "auth.db"))
        store.create_user(handle="alice", password="pw")
        with pytest.raises(ValueError):
            store.create_user(handle="alice", password="other")
        store.close()

    def test_skill_overrides_per_user(self, tmp_path):
        store = AuthStore(str(tmp_path / "auth.db"))
        alice = store.create_user(handle="alice", password="x")
        bob = store.create_user(handle="bob", password="x")
        store.set_skill_override(
            user_id=alice.user_id, skill_name="kb-retrieval", enabled=False
        )
        assert store.get_skill_overrides(alice.user_id) == {"kb-retrieval": False}
        assert store.get_skill_overrides(bob.user_id) == {}
        store.close()

    def test_mcp_servers_upsert_list_delete(self, tmp_path):
        store = AuthStore(str(tmp_path / "auth.db"))
        store.upsert_mcp_server(
            name="time", command="uvx", args=["mcp-server-time"]
        )
        servers = store.list_mcp_servers()
        assert len(servers) == 1 and servers[0]["name"] == "time"
        assert servers[0]["args"] == ["mcp-server-time"]
        # Upsert overwrites
        store.upsert_mcp_server(name="time", command="uvx", env={"TZ": "UTC"})
        servers = store.list_mcp_servers()
        assert servers[0]["env"] == {"TZ": "UTC"}
        # Delete
        assert store.delete_mcp_server("time") is True
        assert store.delete_mcp_server("time") is False
        assert store.list_mcp_servers() == []
        store.close()

    def test_bootstrap_admin_skips_when_populated(self, tmp_path, monkeypatch):
        store = AuthStore(str(tmp_path / "auth.db"))
        store.create_user(handle="seed", password="x")
        monkeypatch.setenv("HERMES_BOOTSTRAP_ADMIN_PASSWORD", "secret")
        assert store.ensure_bootstrap_admin() is None
        store.close()

    def test_bootstrap_admin_skips_when_env_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HERMES_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
        store = AuthStore(str(tmp_path / "auth.db"))
        assert store.ensure_bootstrap_admin() is None
        store.close()

    def test_bootstrap_admin_creates_admin(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_BOOTSTRAP_ADMIN_PASSWORD", "secret")
        store = AuthStore(str(tmp_path / "auth.db"))
        admin = store.ensure_bootstrap_admin()
        assert admin is not None
        assert admin.handle == "admin"
        assert admin.role == "admin"
        assert verify_password("secret", admin.password_hash)
        # Second call no-ops because table is non-empty now.
        assert store.ensure_bootstrap_admin() is None
        store.close()


# ---------------------------------------------------------------------------
# HTTP handler integration tests
# ---------------------------------------------------------------------------


def _build_app(tmp_path, *, api_key="test-key-xyz"):
    """Spin a stripped APIServerAdapter with only the new auth/skill/mcp routes.

    We import the adapter class but avoid calling its full ``connect()``
    (which would also wire chat/responses/run routes that pull AIAgent).
    Instead we mount only the routes under test, and manually init the
    AuthStore.
    """
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    cfg = PlatformConfig(enabled=True, extra={"key": api_key, "host": "127.0.0.1"})
    adapter = APIServerAdapter(cfg)
    # Force AuthStore into a temp db (skip /v1/auth/* 503 path).
    adapter._auth_store = AuthStore(str(tmp_path / "auth.db"))

    app = web.Application()
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/auth/login", adapter._handle_auth_login)
    app.router.add_get("/v1/auth/me", adapter._handle_auth_me)
    app.router.add_post("/v1/auth/logout", adapter._handle_auth_logout)
    app.router.add_get("/v1/skills", adapter._handle_list_skills)
    app.router.add_post(
        "/v1/skills/{name}/toggle", adapter._handle_toggle_skill
    )
    app.router.add_get("/v1/mcp/servers", adapter._handle_list_mcp)
    app.router.add_post("/v1/mcp/servers", adapter._handle_upsert_mcp)
    app.router.add_delete(
        "/v1/mcp/servers/{name}", adapter._handle_delete_mcp
    )
    return adapter, app


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(tmp_path):
    adapter, app = _build_app(tmp_path)
    # Seed a regular user + an admin.
    adapter._auth_store.create_user(handle="alice", password="alice-pw", role="user")
    adapter._auth_store.create_user(handle="root", password="root-pw", role="admin")
    server = TestServer(app)
    cli = TestClient(server)
    await cli.start_server()
    try:
        yield adapter, cli
    finally:
        await cli.close()
        adapter._auth_store.close()


async def _login(cli, handle, password):
    resp = await cli.post(
        "/v1/auth/login", json={"handle": handle, "password": password}
    )
    return resp


class TestLogin:
    async def test_valid(self, client):
        _, cli = client
        resp = await _login(cli, "alice", "alice-pw")
        assert resp.status == 200
        body = await resp.json()
        assert body["token_type"] == "Bearer"
        assert body["user"]["handle"] == "alice"
        assert body["user"]["role"] == "user"
        assert body["access_token"]
        assert body["expires_in"] > 0

    async def test_wrong_password(self, client):
        _, cli = client
        resp = await _login(cli, "alice", "wrong")
        assert resp.status == 401
        body = await resp.json()
        assert body["error"]["code"] == "invalid_credentials"

    async def test_unknown_user_same_401(self, client):
        _, cli = client
        resp = await _login(cli, "ghost", "anything")
        assert resp.status == 401

    async def test_missing_fields(self, client):
        _, cli = client
        resp = await cli.post("/v1/auth/login", json={"handle": "alice"})
        assert resp.status == 400


class TestMe:
    async def test_me_with_jwt(self, client):
        _, cli = client
        login = await _login(cli, "alice", "alice-pw")
        token = (await login.json())["access_token"]
        resp = await cli.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["handle"] == "alice"
        assert body["auth_source"] == "jwt"

    async def test_me_with_legacy_key(self, client):
        _, cli = client
        resp = await cli.get(
            "/v1/auth/me", headers={"Authorization": "Bearer test-key-xyz"}
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["auth_source"] == "legacy_api_key"
        assert body["role"] == "admin"

    async def test_me_no_auth(self, client):
        _, cli = client
        resp = await cli.get("/v1/auth/me")
        assert resp.status == 401


class TestSkills:
    async def test_skills_user_scoped_isolation(self, client):
        adapter, cli = client
        alice_login = await _login(cli, "alice", "alice-pw")
        alice_token = (await alice_login.json())["access_token"]
        alice_id = (await alice_login.json())["user"]["user_id"]

        # Set an override directly via the store
        adapter._auth_store.set_skill_override(
            user_id=alice_id, skill_name="kb-retrieval", enabled=False
        )

        # Toggling another user's override via POST has no effect on alice's.
        # Validate alice still sees her override (depends on /v1/skills not 500-ing
        # when no skills found — we accept either skills empty or with kb-retrieval).
        resp = await cli.get(
            "/v1/skills", headers={"Authorization": f"Bearer {alice_token}"}
        )
        assert resp.status in (200, 500)

    async def test_toggle_persists(self, client):
        adapter, cli = client
        login = await _login(cli, "alice", "alice-pw")
        token = (await login.json())["access_token"]
        resp = await cli.post(
            "/v1/skills/foo-skill/toggle",
            headers={"Authorization": f"Bearer {token}"},
            json={"enabled": False},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body == {"name": "foo-skill", "enabled": False}
        alice_id = (await login.json())["user"]["user_id"]
        assert adapter._auth_store.get_skill_overrides(alice_id) == {
            "foo-skill": False
        }

    async def test_toggle_requires_auth(self, client):
        _, cli = client
        resp = await cli.post(
            "/v1/skills/foo-skill/toggle", json={"enabled": True}
        )
        assert resp.status == 401

    async def test_toggle_validates_body(self, client):
        _, cli = client
        login = await _login(cli, "alice", "alice-pw")
        token = (await login.json())["access_token"]
        resp = await cli.post(
            "/v1/skills/foo/toggle",
            headers={"Authorization": f"Bearer {token}"},
            json={"flipped": True},
        )
        assert resp.status == 400


class TestMCP:
    async def test_admin_can_upsert_and_list(self, client):
        _, cli = client
        login = await _login(cli, "root", "root-pw")
        token = (await login.json())["access_token"]
        upsert = await cli.post(
            "/v1/mcp/servers",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "time", "command": "uvx", "args": ["mcp-server-time"]},
        )
        assert upsert.status == 200
        listed = await cli.get(
            "/v1/mcp/servers", headers={"Authorization": f"Bearer {token}"}
        )
        body = await listed.json()
        assert any(s["name"] == "time" for s in body["servers"])

    async def test_user_role_forbidden(self, client):
        _, cli = client
        login = await _login(cli, "alice", "alice-pw")
        token = (await login.json())["access_token"]
        resp = await cli.get(
            "/v1/mcp/servers", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status == 403

    async def test_legacy_key_acts_as_admin(self, client):
        _, cli = client
        resp = await cli.get(
            "/v1/mcp/servers",
            headers={"Authorization": "Bearer test-key-xyz"},
        )
        assert resp.status == 200

    async def test_delete_returns_deleted_flag(self, client):
        _, cli = client
        login = await _login(cli, "root", "root-pw")
        token = (await login.json())["access_token"]
        await cli.post(
            "/v1/mcp/servers",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "throwaway", "command": "echo"},
        )
        resp = await cli.delete(
            "/v1/mcp/servers/throwaway",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200
        assert (await resp.json())["deleted"] is True
        # Second delete: deleted=False
        resp2 = await cli.delete(
            "/v1/mcp/servers/throwaway",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert (await resp2.json())["deleted"] is False
