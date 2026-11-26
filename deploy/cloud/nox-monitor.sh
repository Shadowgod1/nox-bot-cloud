#!/usr/bin/env bash
# Nox cloud monitor: checks the client bots, the Cloudflare tunnel and the VM, and sends Telegram alerts
# when a check changes state (down / back up), plus a daily summary. Runs as root from a systemd timer.
#
#   sudo bash deploy/cloud/nox-monitor.sh setup     # ask for the Telegram bot token, find the chat, install timers
#   sudo bash deploy/cloud/nox-monitor.sh check     # one round of checks (the timer runs this every minute)
#   sudo bash deploy/cloud/nox-monitor.sh summary   # send the daily summary now
#   sudo bash deploy/cloud/nox-monitor.sh test      # send a test message
#   sudo bash deploy/cloud/nox-monitor.sh status    # print the current state of every check (no Telegram)
#
# Config: /etc/nox-monitor/monitor.env (mode 600): NOX_TG_TOKEN, NOX_TG_CHAT_ID, optional NOX_MONITOR_URLS
# (space-separated public URLs that must answer 200, e.g. https://bot.noxsw.kz/health), NOX_DISK_PCT (85),
# NOX_MEM_PCT (90), NOX_FAILS_BEFORE_ALERT (2). Env: ILER_CLIENTS_DIR (default /opt/iler/clients).
# The token is never printed or put on a command line: curl reads it from stdin.
set -euo pipefail

CONF_DIR="${NOX_MONITOR_CONF_DIR:-/etc/nox-monitor}"
CONF="$CONF_DIR/monitor.env"
STATE_DIR="${NOX_MONITOR_STATE_DIR:-/var/lib/nox-monitor}"
CLIENTS_DIR="${ILER_CLIENTS_DIR:-/opt/iler/clients}"
HOST="$(hostname -s 2>/dev/null || hostname)"
SELF="$(readlink -f "${BASH_SOURCE[0]}")"

die() { printf 'nox-monitor: %s\n' "$*" >&2; exit 1; }

load_conf() {
  [[ -f "$CONF" ]] || die "no $CONF; run: sudo bash $SELF setup"
  # shellcheck disable=SC1090
  . "$CONF"
  NOX_DISK_PCT="${NOX_DISK_PCT:-85}"
  NOX_MEM_PCT="${NOX_MEM_PCT:-90}"
  NOX_FAILS_BEFORE_ALERT="${NOX_FAILS_BEFORE_ALERT:-2}"
  NOX_MONITOR_URLS="${NOX_MONITOR_URLS:-}"
}

# tg_api <method> [curl -d args...]: Telegram Bot API call; the URL with the token goes through stdin
tg_api() {
  local method="$1"; shift
  printf 'url = "https://api.telegram.org/bot%s/%s"\n' "$NOX_TG_TOKEN" "$method" |
    curl -sS --max-time 15 -K - "$@"
}

tg_send() {
  [[ -n "${NOX_TG_TOKEN:-}" && -n "${NOX_TG_CHAT_ID:-}" ]] || { printf '%s\n' "$1"; return 0; }
  tg_api sendMessage --data-urlencode "chat_id=$NOX_TG_CHAT_ID" --data-urlencode "text=$1" \
    -d disable_web_page_preview=true >/dev/null || printf 'nox-monitor: telegram send failed\n' >&2
}

env_value() { # env_value <file> <KEY>
  sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -1 | tr -d '"'"'"
}

# ---- checks: each prints "key|ok|detail" or "key|fail|detail" or "key|info|detail" ----

check_clients() {
  local envf dir name port running total body
  shopt -s nullglob
  for envf in "$CLIENTS_DIR"/*/.env; do
    dir="$(dirname "$envf")"; name="$(basename "$dir")"
    port="$(env_value "$envf" ILER_HTTP_PORT)"; port="${port:-8080}"
    total="$(docker ps -a --filter "label=com.docker.compose.project=iler-$name" -q | wc -l)"
    running="$(docker ps --filter "label=com.docker.compose.project=iler-$name" -q | wc -l)"
    if (( total > 0 && running == 0 )); then
      printf 'client:%s|info|остановлен (iler-client stop)\n' "$name"; continue
    fi
    body="$(curl -s --max-time 8 "http://127.0.0.1:$port/health" 2>/dev/null || true)"
    if [[ "$body" == *'"status":"ok"'* ]]; then
      if (( running < total )); then
        printf 'client:%s|fail|отвечает, но запущено %s из %s контейнеров\n' "$name" "$running" "$total"
      else
        printf 'client:%s|ok|работает (порт %s)\n' "$name" "$port"
      fi
    else
      printf 'client:%s|fail|не отвечает на /health (порт %s, контейнеров %s из %s)\n' "$name" "$port" "$running" "$total"
    fi
  done
  shopt -u nullglob
}

check_tunnel() {
  if systemctl is-active --quiet cloudflared 2>/dev/null; then
    printf 'tunnel|ok|cloudflared работает\n'
  else
    printf 'tunnel|fail|служба cloudflared не запущена\n'
  fi
}

check_urls() {
  local url code
  for url in $NOX_MONITOR_URLS; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$url" 2>/dev/null || true)"
    if [[ "$code" == 200 ]]; then
      printf 'url:%s|ok|200\n' "$url"
    else
      printf 'url:%s|fail|ответ %s\n' "$url" "${code:-нет}"
    fi
  done
}

check_resources() {
  local disk mem_pct
  disk="$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')"
  if (( disk >= NOX_DISK_PCT )); then printf 'disk|fail|диск занят на %s%%\n' "$disk"; else printf 'disk|ok|%s%%\n' "$disk"; fi
  mem_pct="$(awk '/MemTotal/ {t=$2} /MemAvailable/ {a=$2} END {printf "%d", (t-a)*100/t}' /proc/meminfo)"
  if (( mem_pct >= NOX_MEM_PCT )); then printf 'mem|fail|ОЗУ занята на %s%%\n' "$mem_pct"; else printf 'mem|ok|%s%%\n' "$mem_pct"; fi
  if docker info >/dev/null 2>&1; then printf 'docker|ok|работает\n'; else printf 'docker|fail|Docker не отвечает\n'; fi
}

run_checks() { check_clients; check_tunnel; check_urls; check_resources; }

state_file() { printf '%s/%s' "$STATE_DIR" "$(printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_')"; }

cmd_check() {
  load_conf
  mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR"
  local line key status detail sf prev fails seen=""
  while IFS='|' read -r key status detail; do
    [[ -n "$key" ]] || continue
    seen+="$(state_file "$key")"$'\n'
    sf="$(state_file "$key")"
    prev="ok"; fails=0
    [[ -f "$sf" ]] && read -r prev fails <"$sf"
    case "$status" in
      ok|info)
        if [[ "$prev" == alerted ]]; then tg_send "✅ $HOST: $key — восстановлено. $detail"; fi
        printf '%s 0\n' "$status" >"$sf" ;;
      fail)
        fails=$((fails + 1))
        if [[ "$prev" != alerted && $fails -ge $NOX_FAILS_BEFORE_ALERT ]]; then
          tg_send "🔴 $HOST: $key — $detail"
          printf 'alerted %s\n' "$fails" >"$sf"
        elif [[ "$prev" == alerted ]]; then
          printf 'alerted %s\n' "$fails" >"$sf"
        else
          printf 'fail %s\n' "$fails" >"$sf"
        fi ;;
    esac
  done < <(run_checks)
  # a removed client or URL must not stay "down" forever
  for line in "$STATE_DIR"/*; do
    [[ -e "$line" ]] || continue
    grep -qxF "$line" <<<"$seen" || rm -f "$line"
  done
}

cmd_status() {
  load_conf
  run_checks | awk -F'|' '{printf "%-6s %-40s %s\n", $2, $1, $3}'
}

cmd_summary() {
  load_conf
  local ok=0 fail=0 info=0 lines="" key status detail
  while IFS='|' read -r key status detail; do
    case "$status" in ok) ok=$((ok+1)) ;; fail) fail=$((fail+1)); lines+="🔴 $key — $detail"$'\n' ;; info) info=$((info+1)); lines+="⏸ $key — $detail"$'\n' ;; esac
  done < <(run_checks)
  local clients mem disk up
  clients="$(find "$CLIENTS_DIR" -mindepth 2 -maxdepth 2 -name .env 2>/dev/null | wc -l)"
  mem="$(free -m | awk 'NR==2 {printf "%d/%d МБ", $3, $2}')"
  disk="$(df -h / | awk 'NR==2 {print $3 "/" $2}')"
  up="$(uptime -p 2>/dev/null || true)"
  local head="📊 $HOST: сводка. Клиентов: $clients. Проверок ок: $ok, проблем: $fail, остановлено: $info."
  tg_send "$head
ОЗУ: $mem, диск: $disk, $up
${lines:-Всё работает.}"
}

cmd_test() { load_conf; tg_send "🔔 $HOST: тест мониторинга Nox. Если вы это видите — алерты работают."; }

cmd_setup() {
  [[ $EUID -eq 0 ]] || die "run as root (sudo)"
  command -v curl >/dev/null || die "curl is required"
  mkdir -p "$CONF_DIR"; chmod 700 "$CONF_DIR"
  local token chat urls updates
  if [[ -f "$CONF" ]]; then
    # shellcheck disable=SC1090
    . "$CONF"
  fi
  if [[ -n "${NOX_TG_TOKEN:-}" ]]; then
    read -r -p "Токен бота уже сохранён. Ввести новый? [y/N] " ans </dev/tty || ans=""
    [[ "$ans" =~ ^[Yy] ]] && NOX_TG_TOKEN=""
  fi
  if [[ -z "${NOX_TG_TOKEN:-}" ]]; then
    read -r -s -p "Токен Telegram-бота от @BotFather (ввод скрыт): " token </dev/tty; echo
    [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] || die "это не похоже на токен бота"
    NOX_TG_TOKEN="$token"
  fi
  updates="$(tg_api getUpdates || true)"
  [[ "$updates" == *'"ok":true'* ]] || die "Telegram не принял токен (проверьте токен и что у VM есть интернет)"
  chat="$(printf '%s' "$updates" | grep -o '"chat":{"id":-\?[0-9]*' | tail -1 | grep -o -- '-\?[0-9]*$' || true)"
  if [[ -z "$chat" ]]; then
    echo "Чат не найден: добавьте бота в группу алертов и напишите там любое сообщение, затем повторите setup."
    read -r -p "Или введите chat_id вручную (Enter — выйти): " chat </dev/tty || chat=""
    [[ -n "$chat" ]] || exit 1
  fi
  echo "Чат для алертов: $chat"
  urls="${NOX_MONITOR_URLS:-}"
  read -r -p "Публичные адреса для проверки через Cloudflare [${urls:-нет}] (Enter — оставить): " ans </dev/tty || ans=""
  [[ -n "$ans" ]] && urls="$ans"
  umask 077
  {
    printf 'NOX_TG_TOKEN=%s\n' "$NOX_TG_TOKEN"
    printf 'NOX_TG_CHAT_ID=%s\n' "$chat"
    printf "NOX_MONITOR_URLS='%s'\n" "$urls"
    printf 'NOX_DISK_PCT=%s\nNOX_MEM_PCT=%s\nNOX_FAILS_BEFORE_ALERT=%s\n' \
      "${NOX_DISK_PCT:-85}" "${NOX_MEM_PCT:-90}" "${NOX_FAILS_BEFORE_ALERT:-2}"
  } >"$CONF"
  chmod 600 "$CONF"
  install -m 755 "$SELF" /usr/local/sbin/nox-monitor
  cat >/etc/systemd/system/nox-monitor.service <<'EOF'
[Unit]
Description=Nox cloud monitor: one round of checks
After=network-online.target docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/nox-monitor check
EOF
  cat >/etc/systemd/system/nox-monitor.timer <<'EOF'
[Unit]
Description=Nox cloud monitor every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=60s
AccuracySec=10s

[Install]
WantedBy=timers.target
EOF
  cat >/etc/systemd/system/nox-monitor-summary.service <<'EOF'
[Unit]
Description=Nox cloud monitor: daily summary

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/nox-monitor summary
EOF
  cat >/etc/systemd/system/nox-monitor-summary.timer <<'EOF'
[Unit]
Description=Nox cloud monitor daily summary at 09:00 Almaty

[Timer]
OnCalendar=*-*-* 09:00:00 Asia/Almaty
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now nox-monitor.timer nox-monitor-summary.timer
  load_conf
  cmd_test
  echo "Готово: проверки раз в минуту, сводка в 09:00 (Алматы). Состояние: sudo nox-monitor status"
}

case "${1:-}" in
  setup) cmd_setup ;;
  check) cmd_check ;;
  summary) cmd_summary ;;
  test) cmd_test ;;
  status) cmd_status ;;
  *) sed -n '2,15p' "$SELF" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
