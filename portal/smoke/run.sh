#!/usr/bin/env bash
# LLM gateway smoke against the local stack (project nox-smoke, mock upstream); see deploy/cloud/PORTAL.md.
# Never against a server: it creates a staff account and a test client.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
DC="docker compose -p nox-smoke -f compose.yml -f smoke/compose.smoke.yml --env-file ${NOX_SMOKE_ENV:-.env}"
BASE=http://127.0.0.1:18100
JAR=$(mktemp)

echo "== staff + local account (5 coins)"
printf 'smoke-staff-pass-1\n' | $DC exec -T api python manage.py create-staff --email ops@example.kz --password-stdin
curl -s -c "$JAR" -b "$JAR" -H 'Content-Type: application/json' -d '{"email":"ops@example.kz","password":"smoke-staff-pass-1"}' $BASE/api/staff/auth/login >/dev/null
CSRF=$(awk '$6=="nox_csrf"{print $7}' "$JAR")
RESP=$(curl -s -c "$JAR" -b "$JAR" -H 'Content-Type: application/json' -H "X-Nox-CSRF: $CSRF" \
  -d '{"company":"Smoke LLP","contact_name":"Test","email":"smoke@example.kz","phone":"+77010000000","slug":"smoke","tariff":"start","coins":5}' \
  $BASE/api/staff/local-accounts)
KEY=$(printf '%s' "$RESP" | python -c 'import json,sys; print(json.load(sys.stdin)["key"])')
echo "key prefix: ${KEY:0:13}..."

echo "== anthropic non-stream via nginx"
curl -s -w '\nHTTP %{http_code}\n' -H "x-api-key: $KEY" -H "Authorization: Bearer $KEY" -H 'anthropic-version: 2023-06-01' \
  -H 'Content-Type: application/json' -d '{"model":"claude-haiku-5-5","max_tokens":500,"messages":[{"role":"user","content":"hi"}]}' \
  $BASE/llm/anthropic/v1/messages

echo "== anthropic stream via nginx (timestamps per line show unbuffered delivery)"
curl -sN -H "x-api-key: $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"claude-haiku-5-5","max_tokens":500,"stream":true,"messages":[{"role":"user","content":"hi"}]}' \
  $BASE/llm/anthropic/v1/messages | while IFS= read -r line; do [[ -n "$line" ]] && printf '%s %s\n' "$(date +%S.%N | cut -c1-6)" "${line:0:90}"; done

echo "== openai stream (usage injected and hidden)"
curl -sN -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.4-mini","stream":true,"messages":[{"role":"user","content":"hi"}]}' $BASE/llm/openai/v1/chat/completions

echo "== gemini generateContent"
curl -s -w '\nHTTP %{http_code}\n' -H "x-goog-api-key: $KEY" -H 'Content-Type: application/json' -d '{"contents":[]}' \
  $BASE/llm/gemini/v1beta/models/gemini-3.8-flash:generateContent

echo "== ?key= refused"
curl -s -w '\nHTTP %{http_code}\n' -d '{}' "$BASE/llm/gemini/v1beta/models/gemini-3.8-flash:generateContent?key=$KEY"

sleep 1
echo "== ledger"
$DC exec -T db psql -U portal -d portal -c "SELECT provider, model, input_tokens, output_tokens, cache_read_tokens, coins, priced FROM llm_usage ORDER BY id"
echo "== balance"
curl -s -H "x-api-key: $KEY" $BASE/llm/v1/balance; echo

echo "== drain to 402"
for i in $(seq 1 10); do
  code=$(curl -s -o /tmp/nox-smoke-last.json -w '%{http_code}' -H "x-api-key: $KEY" -H 'Content-Type: application/json' \
    -d '{"model":"claude-sonnet-5-5","max_tokens":500,"messages":[]}' $BASE/llm/anthropic/v1/messages)
  echo "request $i: HTTP $code"
  if [[ "$code" == "402" ]]; then cat /tmp/nox-smoke-last.json; echo; break; fi
  sleep 0.3
done
curl -s -H "x-api-key: $KEY" $BASE/llm/v1/balance; echo
$DC exec -T db psql -U portal -d portal -tc "SELECT 'spent=' || sum(coins) || ' rows=' || count(*) FROM llm_usage"
echo "== nginx access log has no keys"
$DC logs web 2>/dev/null | grep -c "nox_vk_" || true
rm -f "$JAR"
