# Hermes Web Demo

A browser-based chat front end for Hermes that runs alongside the
existing admin dashboard. The two surfaces share the same gateway
process but serve different audiences:

| Surface | Port | Audience | Auth |
|---------|------|----------|------|
| Admin dashboard (`web/`) | 9119 | Operators — sessions, models, plugins, cron | Ephemeral session token injected on page load |
| **User chat (`web-chat/`)** | 9120 | End users — chat + uploads + skill toggles | Username/password → JWT |
| Data plane (`gateway/platforms/api_server.py`) | 8642 (internal) | Both UIs, plus Feishu / OpenAI-compatible clients | Bearer JWT **or** legacy `API_SERVER_KEY` |

## Architecture

```
                ┌────────────────────────┐    ┌────────────────────────┐
                │  Admin dashboard SPA   │    │  User chat SPA         │
                │  http://127.0.0.1:9119 │    │  http://127.0.0.1:9120 │
                └───────────┬────────────┘    └───────────┬────────────┘
                            │ /api/*                      │ /v1/* (+ SSE)
                            ▼                             ▼
                ┌────────────────────────┐    ┌────────────────────────┐
                │ hermes_cli/web_server  │    │ nginx 1.27-alpine      │
                │ (FastAPI, 9119)        │    │ reverse proxy          │
                └───────────┬────────────┘    └───────────┬────────────┘
                            │                             │ /v1/*
                            │                             ▼
                            │                 ┌────────────────────────┐
                            └─────────────────┤ gateway/platforms/     │
                                              │ api_server.py (8642)   │
                                              │ aiohttp, OpenAI-compat │
                                              └───────────┬────────────┘
                                                          ▼
                                                   Hermes core
                                              (agent · tools · memory)
```

Why two front ends? The admin dashboard exposes operational knobs
(rotate API keys, edit cron, inspect session DBs) that should never
be in a user-facing chat. The user app exposes nothing the user
doesn't own — their messages, their skill overrides, their uploads.
Keeping the two services on different ports makes the boundary
auditable instead of relying on hidden middleware.

## Quick start (Docker)

```bash
# From the repo root
HERMES_UID=$(id -u) HERMES_GID=$(id -g) docker compose up -d --build
```

The compose file ships three services plus an init container:

| Service | Container name | Host port | Role |
|---------|----------------|-----------|------|
| `gateway` | `hermes` | (none — internal) | Runs the agent + data-plane API |
| `dashboard` | `hermes-dashboard` | `127.0.0.1:9119` | Admin SPA |
| `web-chat` | `hermes-web-chat` | `127.0.0.1:9120` | User SPA via nginx |
| `web-chat-build-copy` | `hermes-web-chat-build-copy` | — | One-shot: stages the SPA bundle into the volume nginx mounts |

When the build settles, open `http://127.0.0.1:9120` and sign in with:

- **Handle:** `admin`
- **Password:** the value of `HERMES_BOOTSTRAP_ADMIN_PASSWORD` (defaults to `darling` in `docker-compose.yml`)

The first request creates the `admin` row in `~/.hermes/response_store.db`.
Override the password by exporting `HERMES_BOOTSTRAP_ADMIN_PASSWORD` before
the first compose-up. Once the row exists, the env var is ignored on
subsequent boots.

## What the user app can do

- **Streaming chat** — `POST /v1/chat/completions stream=true` consumed as
  SSE; tokens render as they arrive.
- **File upload** — paperclip button sends to `POST /v1/uploads`, which
  drops the file under `/opt/uploads/<user_id>/<uuid>.<ext>` (50 MB cap,
  whitelisted extensions). The returned path is appended to the textarea
  so the user can describe what to do with it in the next turn. The
  agent's `describe_dataset` and `plot_chart` tools accept paths under
  both `/opt/data` and `/opt/uploads`.
- **Skill drawer** — lists scanned skills, lets the user toggle
  individual skills on or off for their own scope. Overrides are stored
  in `user_skill_overrides`; the global default in `~/.hermes/config.yaml`
  stays untouched.
- **Sign out** — clears the JWT from `localStorage` and the access
  token's TTL is 12 h regardless.

## Adding more users

There is no signup endpoint yet — admin users create accounts via the
API. From inside the gateway container:

```bash
docker exec hermes /opt/hermes/.venv/bin/python3 -c '
from hermes_cli.config import get_hermes_home
from gateway.platforms.api_auth import AuthStore
store = AuthStore(str(get_hermes_home() / "response_store.db"))
print(store.create_user(handle="alice", password="changeme", role="user"))
'
```

Roles: `user` (default) for chat-only access; `admin` to also reach
`/v1/mcp/servers`. The role string is free-form — only `"admin"` has
elevated checks today.

## Admin dashboard RBAC (opt-in)

By default the admin dashboard (`web/`, port 9119) trusts an ephemeral
session token injected into its HTML — fine for a single operator on
localhost, brittle once you have teammates. Flip the gate by exporting
`HERMES_DASHBOARD_REQUIRE_LOGIN=1` before `docker compose up`:

```bash
HERMES_DASHBOARD_REQUIRE_LOGIN=1 \
HERMES_BOOTSTRAP_ADMIN_PASSWORD=<your-pwd> \
HERMES_UID=$(id -u) HERMES_GID=$(id -g) \
  docker compose up -d --build
```

The dashboard server now:

- **Refuses** the legacy session token on `/api/*`. A 401 there bounces
  the SPA to `/login.html`.
- **Accepts** `Authorization: Bearer <JWT>` issued by either
  `POST /api/admin/login` (same origin) or `POST /v1/auth/login` (gateway).
  Both servers verify against the shared `API_SERVER_KEY`, so a JWT minted
  on one works on the other.
- Logs every admin write to `audit_log` (see Users/Audit pages below).

### First-time admin

The compose file passes `HERMES_BOOTSTRAP_ADMIN_PASSWORD` (default
`darling`) into the gateway. On first start, if the `users` table is
empty, that env value creates `admin` with role `admin`. Sign in at
`http://127.0.0.1:9119/login.html` and rotate the password from
`/users` (see below) — the env var is only consumed when the table is
empty and is ignored afterwards.

### Adding teammates

`/users` (Shield icon in the sidebar) is the SPA front for
`/api/admin/users`:

- **New user** → handle + password + role (`user` or `admin`)
- **Role** select on each row — gated against self-demotion
- **Reset password** prompts inline (sends `PATCH {password: ...}`)
- **Disable / Enable** flips the `disabled` flag — disabled accounts get
  a 401 from `/v1/auth/login` *and* `/api/admin/login`, so they're locked
  out of both surfaces at once
- **Delete** cascades the user's row in `user_skill_overrides` so their
  toggles don't strand. MCP servers are global scope and survive.

Self-protection: you can't change your own role, disable yourself, or
delete yourself. If every admin somehow gets locked out, recovery is to
drop the row from `users` directly:

```bash
docker exec hermes /opt/hermes/.venv/bin/python3 -c '
from hermes_cli.config import get_hermes_home
import sqlite3
db = str(get_hermes_home() / "response_store.db")
sqlite3.connect(db).execute("DELETE FROM users WHERE handle=?", ("admin",)).connection.commit()
'
# Then restart with HERMES_BOOTSTRAP_ADMIN_PASSWORD set.
docker compose restart gateway
```

### Audit log

`/audit` (Eye icon) is the SPA front for `/api/admin/audit`. Every
admin write surface logs a row:

| Action | Captured payload |
|--------|------------------|
| `user.create` | `{handle, role}` |
| `user.patch` | `{handle, changes: {role?, disabled?, password: "***"}}` |
| `user.delete` | `{handle, deleted}` |
| `mcp.upsert` | `{command, args, enabled}` |
| `mcp.delete` | `{deleted}` |
| `skill.toggle` | `{enabled}` |
| `upload.create` | `{size, original_name, extension}` |

Filters: action prefix (e.g. `user.delete` or `mcp.`) + actor handle.
Pagination is 50 rows per page; the table sticks the header so you can
scroll without losing the column names. Each row's payload is rendered
inline as pretty-printed JSON.

### JWT lifecycle

- Tokens live for 12 h (see `JWT_TTL_SECONDS` in `api_auth.py`).
- The dashboard's SPA stores the JWT under `localStorage['hermes.admin_jwt']`
  and sends it as `Authorization: Bearer …` on every `/api/*` request.
- If any `/api/*` call returns 401 (token expired, server restarted with a
  rotated `API_SERVER_KEY`, account disabled), the SPA clears the stored
  JWT and redirects to `/login.html`.
- `POST /api/admin/logout` is a courtesy — the server holds no blocklist;
  it just lets the SPA confirm a no-op succeeded before clearing storage.

### Legacy session-token mode is still available

If `HERMES_DASHBOARD_REQUIRE_LOGIN` is unset, the dashboard injects the
session token into HTML as before and treats unauthenticated browsers as
the implicit `_session_token_` admin. JWT login still works in this mode;
the two paths coexist. Flip the env var when you're ready to move
production to RBAC — no further code changes needed.

## Auth model

Two bearers are accepted on every `/v1/*` endpoint:

1. **JWT** (`Authorization: Bearer eyJ…`) — signed with HS256 using the
   same secret as `API_SERVER_KEY`. Issued by `POST /v1/auth/login`,
   12 h TTL, claims `{sub, handle, role, iat, exp}`.
2. **Raw API key** (`Authorization: Bearer <API_SERVER_KEY>`) — the
   legacy single-shared-key path. Still used by the Feishu adapter and
   the admin dashboard's `/v1/chat/completions` calls. The auth middleware
   treats it as the synthetic admin user `_legacy_key_`.

The `_check_auth` middleware tries the JWT path first and falls back to
the raw key, so both clients coexist without a flag flip.

## Smoke test

`scripts/smoke_v1_api.sh` walks every new endpoint plus the legacy
chat completions paths:

```bash
docker exec -e API_SERVER_KEY=<your key> hermes-dashboard \
    bash /opt/hermes/scripts/smoke_v1_api.sh
```

Twelve steps run in order; the script exits non-zero on the first
failure. Useful before a release or after touching `api_server.py`.

## Building outside Docker

The bundled image baked into `docker-compose.yml` is the simplest path.
For local dev:

```bash
# At the monorepo root — workspaces handle the dep tree
npm install --workspaces

# Two terminals
npm run dev:web        # admin dashboard on :5173 (needs `hermes dashboard` running on :9119)
npm run dev:web-chat   # user chat on :5173 (the proxy points at :8642 by default)

# Override the gateway URL if it lives elsewhere
HERMES_GATEWAY_URL=http://192.168.1.50:8642 npm run dev:web-chat
```

A production build for the user app:

```bash
npm run build:web-chat   # outputs to web-chat/dist
```

The Dockerfile does this during image build and stages the result into
`/opt/hermes/web-chat/dist`. The `web-chat-build-copy` init container in
docker-compose then copies that into a named volume nginx serves
read-only.

## SSE behind nginx — why the long timeouts

The nginx config (`docker/nginx/web-chat.conf`) pins these for
`/v1/*` proxying:

```nginx
proxy_buffering off;
proxy_cache off;
proxy_request_buffering off;
proxy_read_timeout 3600s;
chunked_transfer_encoding on;
```

Without `proxy_buffering off`, nginx holds the entire response until
the upstream closes — which means the user sees a long blank pause and
then a wall of text, instead of streaming tokens. The 1 h read timeout
covers long-running tool calls that pause SSE without sending any
keepalive byte.

## Troubleshooting

**"login fails with 503 auth_unavailable"**
The `AuthStore` did not initialize. Confirm `API_SERVER_KEY` is set on
the gateway container — without it, JWT signing refuses to run.

**"login returns 401 invalid_credentials but I'm sure the password is right"**
The first-boot `HERMES_BOOTSTRAP_ADMIN_PASSWORD` is consumed only when
the `users` table is empty. To reset, drop the row directly:
```bash
docker exec hermes sqlite3 /opt/data/response_store.db \
    "DELETE FROM users WHERE handle='admin';"
```
Then restart the gateway with the env var set.

**"web-chat loads but `/v1/*` returns 502"**
nginx can't reach `gateway:8642`. Make sure both containers are on the
same compose network (`hermes-agent_default`) — `docker network inspect
hermes-agent_default` should list both.

**"SSE chat pauses, never streams"**
Either nginx is buffering (check the config landed at
`/etc/nginx/conf.d/default.conf`) or your host has an outbound proxy
inserting itself. Set `no_proxy=*` in the calling container to bypass.

**"file uploads return 413"**
The hard cap is 50 MB inside the API server. Bump it in
`gateway/platforms/api_server.py:_UPLOAD_MAX_BYTES` and rebuild — but
also raise `client_max_body_size` in the nginx config to match.

## What lives where

| Concern | File |
|---------|------|
| JWT + password hashing primitives | `gateway/platforms/api_auth.py` |
| `users` / `user_skill_overrides` / `mcp_servers` tables | `gateway/platforms/api_auth.py` (DDL + `AuthStore`) |
| `/v1/auth/*` `/v1/skills` `/v1/uploads` `/v1/mcp/servers` handlers | `gateway/platforms/api_server.py` |
| Path allowlist for `describe_dataset` / `plot_chart` | `tools/data_tools.py` (`_ALLOWED_ROOTS`) |
| Shared CSS/TS tokens | `packages/design-tokens/` |
| User chat SPA | `web-chat/src/` |
| Streaming SSE client | `web-chat/src/lib/api.ts` (`streamChatCompletion`) |
| nginx reverse-proxy config | `docker/nginx/web-chat.conf` |
| End-to-end regression script | `scripts/smoke_v1_api.sh` |
| Admin login page (no React) | `web/public/login.html` |
| Admin Users / Audit SPA pages | `web/src/pages/UsersAdminPage.tsx`, `web/src/pages/AuditAdminPage.tsx` |
| Dashboard admin endpoints | `hermes_cli/web_server.py:/api/admin/*` |
| Playwright E2E suite | `tests/e2e-web/` |
