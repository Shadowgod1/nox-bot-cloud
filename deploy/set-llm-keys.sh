#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Set the Nox Bot LLM API keys and the monthly coin limit on a client server (run by the Nox service
# technician, never by the client).
#
#   sudo bash /opt/iler/deploy/set-llm-keys.sh            # asks for each key (input hidden) and the limit
#   sudo ILER_ANTHROPIC_KEY=... ILER_MONTHLY_COINS=30000 bash set-llm-keys.sh --yes   # non-interactive
#
# Values are written to <dir>/.env (mode 600) and iler-core is restarted. An empty answer keeps the
# current value; "-" removes a key. The admin UI never shows or edits these values.
# Each new key is trimmed, checked for a double paste and tested with one request to the provider's
# model list (--no-check or ILER_SKIP_KEY_CHECK=1 skips the request). A rejected key is saved only
# after a "y"; with --yes it is saved with a warning, as before.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=lib/env.sh
source "$SCRIPT_DIR/lib/env.sh"
# shellcheck source=lib/llmkey.sh
source "$SCRIPT_DIR/lib/llmkey.sh"

ILER_DIR_PATH="${ILER_DIR:-/opt/iler}"
YES=0
CHECK=1
[[ "${ILER_SKIP_KEY_CHECK:-0}" == "1" ]] && CHECK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) ILER_DIR_PATH="${2:-}"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --no-check) CHECK=0; shift ;;
    -h|--help) sed -n '3,13p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

require_root
ENV_FILE="$ILER_DIR_PATH/.env"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE not found (is Nox Bot installed in $ILER_DIR_PATH?)"

mask() { local v="$1"; if [[ -z "$v" ]]; then echo "не задан"; else echo "••••${v: -4}"; fi; }

changed=0
for pair in "ANTHROPIC:Anthropic (Claude)" "OPENAI:OpenAI" "GEMINI:Google Gemini"; do
  name="${pair%%:*}"; label="${pair#*:}"; var="ILER_${name}_KEY"
  current="$(env_get "$ENV_FILE" "$var")"
  if [[ "$YES" == "1" ]]; then
    value="${!var-}"
    [[ -z "$value" ]] && continue
  else
    printf '%s — сейчас: %s. Новый ключ (Enter — оставить, "-" — удалить): ' "$label" "$(mask "$current")"
    IFS= read -rs value </dev/tty || value=""
    echo
    [[ -z "$value" ]] && continue
  fi
  value="$(key_clean "$value")"
  [[ -z "$value" ]] && continue
  if [[ "$value" == "-" ]]; then
    value=""
  else
    if key_doubled "$value"; then
      half=$(( ${#value} / 2 ))
      if [[ "$YES" == "1" ]]; then
        warn "$label: ключ похож на вставленный дважды (${#value} символов) — сохраняю первую половину (${half})"
        value="${value:0:half}"
      else
        printf '%s: ключ похож на вставленный дважды (%s символов). Оставить первую половину (%s)? [Y/n] ' \
          "$label" "${#value}" "$half"
        IFS= read -r answer </dev/tty || answer=""
        [[ "$answer" =~ ^[Nn] ]] || value="${value:0:half}"
      fi
    fi
    [[ "$value" =~ ^[A-Za-z0-9._-]+$ ]] || die "$label: недопустимые символы в ключе"
    if [[ "$CHECK" == "1" ]]; then
      result=0
      key_check "$name" "$value" >/dev/null || result=$?
      case "$result" in
        0) ok "$label: проверка ключа — ok" ;;
        1) warn "$label: проверка ключа — rejected (HTTP $KEY_CHECK_HTTP)"
           if [[ "$YES" == "1" ]]; then
             warn "$label: --yes — ключ всё равно сохраняется; проверьте его"
           else
             printf '%s: провайдер отклонил ключ. Всё равно сохранить? [y/N] ' "$label"
             IFS= read -r answer </dev/tty || answer=""
             if [[ ! "$answer" =~ ^[Yy] ]]; then
               warn "$label: ключ не сохранён, остаётся прежний ($(mask "$current"))"
               continue
             fi
           fi ;;
        *) if [[ "$KEY_CHECK_HTTP" =~ ^(000)?$ ]]; then
             warn "$label: проверка ключа — could not check (network)"
           else
             warn "$label: проверка ключа — could not check (HTTP $KEY_CHECK_HTTP)"
           fi ;;
      esac
    fi
  fi
  env_set "$ENV_FILE" "$var" "$value"
  chmod 600 "$ENV_FILE"
  ok "$label: $(mask "$value")"
  changed=1
done

current="$(env_get "$ENV_FILE" ILER_MONTHLY_COINS)"
if [[ "$YES" == "1" ]]; then
  value="${ILER_MONTHLY_COINS-}"
else
  printf 'Месячный лимит коинов — сейчас: %s. Новый (Enter — оставить, 0 — без лимита): ' "${current:-0}"
  IFS= read -r value </dev/tty || value=""
fi
if [[ -n "$value" ]]; then
  [[ "$value" =~ ^[0-9]{1,9}$ ]] || die "лимит коинов должен быть целым числом"
  env_set "$ENV_FILE" ILER_MONTHLY_COINS "$value"
  chmod 600 "$ENV_FILE"
  ok "лимит коинов: $value"
  changed=1
fi

if [[ "$changed" == "1" ]]; then
  log "restarting the bot core (iler-core)"
  docker compose --project-directory "$ILER_DIR_PATH" -f "$ILER_DIR_PATH/compose.yml" --env-file "$ENV_FILE" \
    up -d --no-build iler-core >/dev/null
  ok "done: providers with a key appear in the admin UI (Система → Языковая модель)"
else
  ok "nothing changed"
fi

# opened from the Start menu shortcut: keep the window open until the technician reads the result
if [[ "$YES" != "1" && -t 0 ]]; then
  read -rp "Нажмите Enter, чтобы закрыть окно" _ </dev/tty || true
fi
