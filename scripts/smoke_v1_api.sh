#!/usr/bin/env bash
#
# scripts/smoke_v1_api.sh — End-to-end smoke test for the data-plane API.
#
# Hits every /v1/* endpoint introduced by the web-demo work and confirms
# the legacy `Authorization: Bearer $API_SERVER_KEY` path still works.
# Exits 0 on full success, non-zero on the first failure. Designed to
# run from a host that can reach the gateway (e.g. inside docker compose
# via ``docker exec hermes-dashboard scripts/smoke_v1_api.sh``).
#
# Required env:
#   GATEWAY_URL=http://gateway:8642      (default for the dashboard container)
#   API_SERVER_KEY=<the gateway key>     (for the legacy Bearer path)
#
# Optional:
#   ADMIN_HANDLE=admin
#   ADMIN_PASSWORD=darling
#
# Exit codes:
#   0 — all checks passed
#   1 — a check failed; stderr names the failing step

set -euo pipefail

: "${GATEWAY_URL:=http://gateway:8642}"
: "${API_SERVER_KEY:?API_SERVER_KEY must be set}"
: "${ADMIN_HANDLE:=admin}"
: "${ADMIN_PASSWORD:=darling}"

# Curl is shared but we disable proxy env so we don't accidentally route
# through a host-side http proxy (a common WSL footgun).
NO_PROXY_ENV=(no_proxy='*' http_proxy='' https_proxy='' NO_PROXY='*' HTTP_PROXY='' HTTPS_PROXY='')
CURL=(env "${NO_PROXY_ENV[@]}" curl -fsS --max-time 30)

step() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
fail() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------- health

step "/health"
"${CURL[@]}" "$GATEWAY_URL/health" | tee /tmp/.smoke.health >/dev/null
grep -q '"platform": "hermes-agent"' /tmp/.smoke.health \
  || fail "/health did not include platform marker"

step "/v1/models"
"${CURL[@]}" -H "Authorization: Bearer $API_SERVER_KEY" "$GATEWAY_URL/v1/models" >/tmp/.smoke.models
grep -q '"hermes-agent"' /tmp/.smoke.models \
  || fail "/v1/models missing hermes-agent entry"

# --------------------------------------------------------------------- auth

step "POST /v1/auth/login"
"${CURL[@]}" -X POST "$GATEWAY_URL/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"handle\":\"$ADMIN_HANDLE\",\"password\":\"$ADMIN_PASSWORD\"}" >/tmp/.smoke.login
TOKEN=$(python3 -c 'import json; print(json.load(open("/tmp/.smoke.login"))["access_token"])')
[[ -n "$TOKEN" ]] || fail "login returned no access_token"
echo "  jwt: ${#TOKEN} chars"

step "GET /v1/auth/me (jwt)"
"${CURL[@]}" -H "Authorization: Bearer $TOKEN" "$GATEWAY_URL/v1/auth/me" >/tmp/.smoke.me
grep -q '"auth_source": "jwt"' /tmp/.smoke.me \
  || fail "/v1/auth/me did not report jwt auth_source"

step "GET /v1/auth/me (no token)"
# Use a non-failing curl variant here since 401 is the expected outcome
# (the main CURL array sets -f which exits non-zero on HTTP errors).
status=$(env "${NO_PROXY_ENV[@]}" curl -sS -o /dev/null -w '%{http_code}' \
  --max-time 10 "$GATEWAY_URL/v1/auth/me")
[[ "$status" == "401" ]] || fail "/v1/auth/me without token returned $status (want 401)"
echo "  401 confirmed"

step "GET /v1/auth/me (legacy api key)"
"${CURL[@]}" -H "Authorization: Bearer $API_SERVER_KEY" "$GATEWAY_URL/v1/auth/me" \
  | grep -q '"auth_source": "legacy_api_key"' \
  || fail "legacy API_SERVER_KEY did not authenticate"

# ------------------------------------------------------------------- skills

step "GET /v1/skills"
"${CURL[@]}" -H "Authorization: Bearer $TOKEN" "$GATEWAY_URL/v1/skills" >/tmp/.smoke.skills
SKILL_COUNT=$(python3 -c 'import json; print(len(json.load(open("/tmp/.smoke.skills"))["skills"]))')
[[ "$SKILL_COUNT" -gt 0 ]] || fail "/v1/skills returned zero skills"
echo "  $SKILL_COUNT skills"

step "POST /v1/skills/hermes-agent/toggle"
"${CURL[@]}" -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"enabled": false}' "$GATEWAY_URL/v1/skills/hermes-agent/toggle" >/tmp/.smoke.toggle
grep -q '"enabled": false' /tmp/.smoke.toggle \
  || fail "toggle did not echo back the new value"

# ---------------------------------------------------------------------- mcp

step "GET /v1/mcp/servers (admin)"
"${CURL[@]}" -H "Authorization: Bearer $TOKEN" "$GATEWAY_URL/v1/mcp/servers" >/tmp/.smoke.mcp_list

step "POST /v1/mcp/servers (admin upsert)"
"${CURL[@]}" -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"_smoke_test","command":"echo","args":["hi"]}' "$GATEWAY_URL/v1/mcp/servers" >/dev/null

step "DELETE /v1/mcp/servers/_smoke_test"
"${CURL[@]}" -X DELETE -H "Authorization: Bearer $TOKEN" \
  "$GATEWAY_URL/v1/mcp/servers/_smoke_test" >/tmp/.smoke.mcp_del
grep -q '"deleted": true' /tmp/.smoke.mcp_del \
  || fail "delete did not confirm deletion"

# -------------------------------------------------------------------- chat

step "POST /v1/chat/completions (stream=false, legacy key)"
"${CURL[@]}" -X POST -H "Authorization: Bearer $API_SERVER_KEY" -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"Reply only: OK"}],"stream":false}' \
  "$GATEWAY_URL/v1/chat/completions" >/tmp/.smoke.chat
python3 -c '
import json
d = json.load(open("/tmp/.smoke.chat"))
content = d["choices"][0]["message"]["content"]
print("  reply:", content[:60])
assert content, "empty content"
'

step "POST /v1/chat/completions (stream=true, jwt)"
TIMEOUT_BIN=$(command -v timeout || command -v gtimeout || true)
if [[ -n "$TIMEOUT_BIN" ]]; then
  "$TIMEOUT_BIN" 30 env "${NO_PROXY_ENV[@]}" curl -sS --no-buffer -X POST \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"model":"hermes-agent","messages":[{"role":"user","content":"Reply only: PONG"}],"stream":true}' \
    "$GATEWAY_URL/v1/chat/completions" >/tmp/.smoke.stream
else
  "${CURL[@]}" -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"model":"hermes-agent","messages":[{"role":"user","content":"Reply only: PONG"}],"stream":true}' \
    "$GATEWAY_URL/v1/chat/completions" >/tmp/.smoke.stream
fi
grep -q '\[DONE\]' /tmp/.smoke.stream \
  || fail "stream did not terminate with [DONE]"
grep -q '"delta": {"content"' /tmp/.smoke.stream \
  || fail "stream emitted no content delta"

# ---------------------------------------------------------------------- done

echo
echo '✅ All smoke checks passed.'
