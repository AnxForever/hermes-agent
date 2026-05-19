# Hermes Web Demo — Playwright E2E

Smoke tests for the two browser-facing surfaces:

- **user chat** (default `http://127.0.0.1:9120`) — `web-chat.spec.ts`
- **admin dashboard** (default `http://127.0.0.1:9119`) — `dashboard.spec.ts`

## Run

The tests are an opt-in workspace (not pulled in by the root `npm
install`) so they don't slow down the main repo bootstrap. Install
their own deps once:

```bash
cd tests/e2e-web
npm install
npx playwright install chromium  # downloads the headless browser
```

Then with both Hermes containers (`hermes`, `hermes-dashboard`) and
the user chat nginx running locally:

```bash
npx playwright test           # headless, full suite
npx playwright test --headed  # watch the browser drive itself
npx playwright test web-chat  # one file only
```

After a failure:

```bash
npx playwright show-report    # opens the HTML report with traces
```

## Env

| Variable | Default | Purpose |
|----------|---------|---------|
| `HERMES_WEB_CHAT_URL` | `http://127.0.0.1:9120` | User chat base URL |
| `HERMES_DASHBOARD_URL` | `http://127.0.0.1:9119` | Admin dashboard base URL |
| `ADMIN_HANDLE` | `admin` | Login handle used by every test |
| `ADMIN_PASSWORD` | `darling` | Login password — match what was set via `HERMES_BOOTSTRAP_ADMIN_PASSWORD` |

## What's covered

`web-chat.spec.ts` — five flows:
1. Wrong password → inline 401 error, stays on /login
2. Right password → /chat with user chip + sign-out button visible
3. Send a message → user bubble appears → assistant bubble streams the answer
4. Open skill drawer → toggle a switch → per-user override badge appears
5. Sign out → JWT removed from localStorage, redirected to /login

`dashboard.spec.ts` — five flows:
1. /login.html serves 200 with the expected form
2. Wrong password → inline error, stays on /login.html
3. Right password → JWT stored, redirected to /
4. JSON login → /api/admin/me with bearer → returns admin user dict
5. Missing auth on /api/sessions → 401

## CI hook (future)

When the WSL-side npm proxy isn't a problem, plumb this into CI as:

```bash
docker compose up -d --build
cd tests/e2e-web && npm install && npx playwright install chromium
HERMES_WEB_CHAT_URL=http://127.0.0.1:9120 \
HERMES_DASHBOARD_URL=http://127.0.0.1:9119 \
ADMIN_PASSWORD=$HERMES_BOOTSTRAP_ADMIN_PASSWORD \
  npx playwright test
```
