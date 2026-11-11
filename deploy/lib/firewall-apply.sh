#!/bin/sh
# ILER Bot: restrict the admin port to private/loopback sources (iptables + ip6tables).
#   firewall-apply.sh <port>     apply (idempotent)
#   firewall-apply.sh --remove   remove every ILER rule
set -u
TAG="iler-admin"
CHAIN="ILER-ADMIN"
NETS4="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 127.0.0.0/8"
NETS6="fc00::/7 fe80::/10 ::1/128"

remove_jumps() { # <cmd> <chain>: delete every rule in <chain> tagged with our comment
  "$1" -w -S "$2" 2>/dev/null | grep -F -- "--comment $TAG" | sed 's/^-A /-D /' |
    while read -r rule; do
      # shellcheck disable=SC2086 # rule is iptables -S output: words without spaces inside
      "$1" -w $rule 2>/dev/null || true
    done
}

remove_family() { # <cmd>
  remove_jumps "$1" DOCKER-USER
  remove_jumps "$1" INPUT
  "$1" -w -F "$CHAIN" 2>/dev/null || true
  "$1" -w -X "$CHAIN" 2>/dev/null || true
}

apply_family() { # <cmd> <port> <nets>
  cmd="$1" port="$2" nets="$3"
  "$cmd" -w -N "$CHAIN" 2>/dev/null || true
  "$cmd" -w -F "$CHAIN" || return 1
  for net in $nets; do
    "$cmd" -w -A "$CHAIN" -s "$net" -m comment --comment "$TAG" -j RETURN || return 1
  done
  "$cmd" -w -A "$CHAIN" -m comment --comment "$TAG" -j DROP || return 1
  remove_jumps "$cmd" DOCKER-USER
  remove_jumps "$cmd" INPUT
  # DOCKER-USER is created by dockerd; creating it first is harmless (dockerd reuses it)
  "$cmd" -w -N DOCKER-USER 2>/dev/null || true
  "$cmd" -w -I DOCKER-USER 1 -p tcp -m conntrack --ctorigdstport "$port" --ctdir ORIGINAL \
    -m comment --comment "$TAG" -j "$CHAIN" || return 1
  "$cmd" -w -I INPUT 1 -p tcp --dport "$port" -m comment --comment "$TAG" -j "$CHAIN" || return 1
}

if [ "${1:-}" = "--remove" ]; then
  for cmd in iptables ip6tables; do
    command -v "$cmd" >/dev/null 2>&1 && remove_family "$cmd"
  done
  exit 0
fi

port="${1:-}"
case "$port" in
  ''|*[!0-9]*) echo "usage: $0 <port>|--remove" >&2; exit 2 ;;
esac
command -v iptables >/dev/null 2>&1 || { echo "iptables not found" >&2; exit 1; }
apply_family iptables "$port" "$NETS4" || { echo "iptables rules failed" >&2; exit 1; }
if command -v ip6tables >/dev/null 2>&1; then
  apply_family ip6tables "$port" "$NETS6" || echo "ip6tables rules failed (IPv6 disabled?)" >&2
fi
exit 0
