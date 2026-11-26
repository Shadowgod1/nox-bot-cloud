#!/usr/bin/env bash
# LLM key hygiene for deploy/set-llm-keys.sh (sourced, not executed). Never prints a key.

# key_clean <value>: drop all whitespace and CR (keys never contain any; a paste often adds them).
key_clean() {
  local v="$1"
  printf '%s' "${v//[[:space:]]/}"
}

# key_doubled <value>: 0 when the value is some X pasted twice (X+X), e.g. a 212-char Anthropic key
# that is really one 106-char key. Keys shorter than 2x20 chars are not considered.
key_doubled() {
  local v="$1" n half
  n=${#v}
  (( n >= 40 && n % 2 == 0 )) || return 1
  half=$(( n / 2 ))
  [[ "${v:0:half}" == "${v:half}" ]]
}

# key_check <provider> <key>: one cheap authenticated GET to the provider's model list.
# Prints exactly one status word and returns:
#   0 "ok"          the provider accepted the key
#   1 "rejected"    401/403 (Gemini also 400: API_KEY_INVALID); KEY_CHECK_HTTP holds the code
#   2 "unknown"     no curl, no network or any other answer (429, 5xx); KEY_CHECK_HTTP holds the code
# The key goes to curl on stdin as a config file (never in argv or a URL, so it is not visible in ps
# or any log); the response body is discarded. Test seam: KEY_CHECK_CURL overrides the curl binary.
KEY_CHECK_HTTP=""
key_check() {
  local provider="$1" key="$2" url header code curl_bin="${KEY_CHECK_CURL:-curl}"
  KEY_CHECK_HTTP=""
  case "$provider" in
    ANTHROPIC) url="https://api.anthropic.com/v1/models"
               header="header = \"x-api-key: $key\""$'\n'"header = \"anthropic-version: 2023-06-01\"" ;;
    OPENAI)    url="https://api.openai.com/v1/models"
               header="header = \"Authorization: Bearer $key\"" ;;
    GEMINI)    url="https://generativelanguage.googleapis.com/v1beta/models"
               header="header = \"x-goog-api-key: $key\"" ;;
    *) echo "unknown"; return 2 ;;
  esac
  command -v "$curl_bin" >/dev/null 2>&1 || { echo "unknown"; return 2; }
  code="$(printf '%s\n' "$header" | "$curl_bin" -sS -o /dev/null -w '%{http_code}' \
            --connect-timeout 5 --max-time 15 -K - "$url" 2>/dev/null)" || code="000"
  [[ "$code" =~ ^[0-9]{3}$ ]] || code="000"
  # shellcheck disable=SC2034  # read by the caller (set-llm-keys.sh) after key_check returns
  KEY_CHECK_HTTP="$code"
  case "$code" in
    2??) echo "ok"; return 0 ;;
    401|403) echo "rejected"; return 1 ;;
    400) if [[ "$provider" == "GEMINI" ]]; then echo "rejected"; return 1; fi ;;
  esac
  echo "unknown"
  return 2
}
