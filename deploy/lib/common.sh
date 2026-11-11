#!/usr/bin/env bash
# Shared helpers for deploy/install.sh (sourced, not executed).

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_RED=$'\033[31m'; C_YELLOW=$'\033[33m'; C_GREEN=$'\033[32m'
else
  C_RESET=""; C_BOLD=""; C_RED=""; C_YELLOW=""; C_GREEN=""
fi

log()  { printf '%s==>%s %s\n' "$C_BOLD" "$C_RESET" "$*"; }
ok()   { printf '%s ok%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%swarning:%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

require_root() {
  [[ "$(id -u)" -eq 0 ]] || die "run as root (sudo bash $0 ...)"
}

# confirm "question" -> 0 on yes. Honors ASSUME_YES=1. Reads from the terminal even when piped.
confirm() {
  local answer
  if [[ "${ASSUME_YES:-0}" == "1" ]]; then
    return 0
  fi
  if [[ ! -r /dev/tty ]]; then
    die "confirmation required but no terminal available (use --yes)"
  fi
  read -r -p "$1 [y/N] " answer </dev/tty
  [[ "$answer" =~ ^[Yy] ]]
}

# Primary LAN IPv4 of this host (empty if unknown).
lan_ip() {
  local ip=""
  if have ip; then
    ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}')"
  fi
  if [[ -z "$ip" ]] && have hostname; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi
  printf '%s' "$ip"
}
