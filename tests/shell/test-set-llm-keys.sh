#!/usr/bin/env bash
# Offline tests for deploy/lib/llmkey.sh and deploy/set-llm-keys.sh --yes. curl, docker and id are stubs:
# no network request is made and no real key is used.
#   bash tests/shell/test-set-llm-keys.sh
# check() evals its condition later, so single-quoted expressions and "unused" vars are intended.
# shellcheck disable=SC2016,SC2034,SC1091
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
STUBS="$WORK/bin"
mkdir -p "$STUBS"

fails=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; fails=$((fails + 1)); }
check() { if eval "$2"; then pass "$1"; else fail "$1"; fi; }

# curl stub: answers STUB_CODE (or 000 + exit 7 for "network"), records argv and the stdin config.
cat >"$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STUB_LOG.argv"
cat >>"$STUB_LOG.stdin"
if [[ "${STUB_CODE:-200}" == "net" ]]; then printf '000'; exit 7; fi
printf '%s' "${STUB_CODE:-200}"
EOF
printf '#!/usr/bin/env bash\nexit 0\n' >"$STUBS/docker"
printf '#!/usr/bin/env bash\necho 0\n' >"$STUBS/id"
chmod +x "$STUBS"/*
export STUB_LOG="$WORK/curl"

# Fake keys only (never real): A is a 106-char "sk-ant-usr-" style string.
A="sk-ant-usr-$(printf 'x%.0s' $(seq 1 95))"
O="sk-test-openai-placeholder-0123456789"

# --- library ------------------------------------------------------------------------------------
# shellcheck source=../../deploy/lib/llmkey.sh
source "$ROOT/deploy/lib/llmkey.sh"
export KEY_CHECK_CURL="$STUBS/curl"

raw=$' \tab c\r\n'
check "key_clean drops spaces, tabs and CR" '[[ "$(key_clean "$raw")" == "abc" ]]'
check "key_doubled detects X+X" 'key_doubled "$A$A"'
check "key_doubled ignores a single key" '! key_doubled "$A"'
check "key_doubled ignores short repeats" '! key_doubled "abcabc"'

for p in ANTHROPIC OPENAI GEMINI; do
  : >"$STUB_LOG.argv"; : >"$STUB_LOG.stdin"
  out="$(STUB_CODE=200 key_check "$p" "$A")" && rc=0 || rc=$?
  check "$p: 200 -> ok" '[[ $rc == 0 && $out == ok ]]'
  check "$p: key not in curl argv" '! grep -q "xxxxxxxx" "$STUB_LOG.argv"'
  check "$p: key passed on stdin" 'grep -q "xxxxxxxx" "$STUB_LOG.stdin"'
done
check "GEMINI: key goes in x-goog-api-key, not the URL" 'grep -q "x-goog-api-key" "$STUB_LOG.stdin" && ! grep -q "key=" "$STUB_LOG.argv"'
: >"$STUB_LOG.stdin"; STUB_CODE=200 key_check ANTHROPIC "$A" >/dev/null
check "ANTHROPIC: sends anthropic-version" 'grep -q "anthropic-version: 2023-06-01" "$STUB_LOG.stdin"'

rc=0; out="$(STUB_CODE=401 key_check ANTHROPIC "$A")" || rc=$?
check "401 -> rejected" '[[ $rc == 1 && $out == rejected ]]'
rc=0; out="$(STUB_CODE=400 key_check GEMINI "$A")" || rc=$?
check "gemini 400 -> rejected" '[[ $rc == 1 ]]'
rc=0; out="$(STUB_CODE=400 key_check OPENAI "$A")" || rc=$?
check "openai 400 -> unknown" '[[ $rc == 2 ]]'
rc=0; out="$(STUB_CODE=net key_check OPENAI "$A")" || rc=$?
check "network failure -> unknown" '[[ $rc == 2 && $out == unknown ]]'
rc=0; out="$(KEY_CHECK_CURL=/nonexistent/curl key_check OPENAI "$A")" || rc=$?
check "no curl -> unknown" '[[ $rc == 2 ]]'
unset KEY_CHECK_CURL

# --- set-llm-keys.sh --yes -------------------------------------------------------------------------
run_script() {  # run_script <logfile> [env...]: run --yes against a temp install dir
  local log="$1"; shift
  mkdir -p "$WORK/iler"
  printf 'ILER_ANTHROPIC_KEY=old-anthropic-0000\nILER_MONTHLY_COINS=0\n' >"$WORK/iler/.env"
  env PATH="$STUBS:$PATH" "$@" bash "$ROOT/deploy/set-llm-keys.sh" --dir "$WORK/iler" --yes >"$log" 2>&1
}
saved() { awk -F= -v k="$1" '$1 == k { print substr($0, length(k) + 2) }' "$WORK/iler/.env"; }

run_script "$WORK/out1" STUB_CODE=200 "ILER_ANTHROPIC_KEY=$A$A"
check "--yes: doubled paste saves the first half" '[[ "$(saved ILER_ANTHROPIC_KEY)" == "$A" ]]'
check "--yes: prints ok for an accepted key" 'grep -q "проверка ключа — ok" "$WORK/out1"'
check "--yes: never echoes the key" '! grep -q "xxxxxxxx" "$WORK/out1"'

run_script "$WORK/out2" STUB_CODE=200 "ILER_ANTHROPIC_KEY= $A"$'\r'
check "--yes: whitespace and CR are trimmed" '[[ "$(saved ILER_ANTHROPIC_KEY)" == "$A" ]]'

run_script "$WORK/out3" STUB_CODE=401 "ILER_OPENAI_KEY=$O"
check "--yes: rejected key is still saved (as before) with a warning" \
  '[[ "$(saved ILER_OPENAI_KEY)" == "$O" ]] && grep -q "rejected (HTTP 401)" "$WORK/out3"'

run_script "$WORK/out4" STUB_CODE=net "ILER_OPENAI_KEY=$O"
check "--yes: network failure is reported and the key saved" \
  '[[ "$(saved ILER_OPENAI_KEY)" == "$O" ]] && grep -q "could not check (network)" "$WORK/out4"'

: >"$STUB_LOG.argv"
run_script "$WORK/out5" STUB_CODE=200 ILER_SKIP_KEY_CHECK=1 "ILER_OPENAI_KEY=$O"
check "ILER_SKIP_KEY_CHECK=1 makes no request" '[[ ! -s "$STUB_LOG.argv" ]] && [[ "$(saved ILER_OPENAI_KEY)" == "$O" ]]'

rc=0; run_script "$WORK/out6" STUB_CODE=200 "ILER_OPENAI_KEY=bad\$key" || rc=$?
check "invalid characters still fail" '[[ $rc != 0 ]] && grep -q "недопустимые символы" "$WORK/out6"'

if (( fails > 0 )); then
  printf '%d failed\n' "$fails"
  exit 1
fi
echo "all passed"
