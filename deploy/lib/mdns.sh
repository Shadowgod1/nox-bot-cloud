#!/usr/bin/env bash
# Publishes iler.local via avahi when avahi-daemon is present (sourced).

MDNS_UNIT=/etc/systemd/system/iler-mdns.service
MDNS_HELPER=/usr/local/lib/iler/mdns-publish.sh

mdns_available() {
  have systemctl && systemctl is-active --quiet avahi-daemon 2>/dev/null
}

configure_mdns() {
  local offline="$1"
  if ! mdns_available; then
    log "avahi-daemon not running, skipping iler.local"
    return 1
  fi
  if ! have avahi-publish; then
    if [[ "$offline" == "1" ]] || ! have apt-get; then
      warn "avahi-publish missing (package avahi-utils), skipping iler.local"
      return 1
    fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq avahi-utils >/dev/null || {
      warn "could not install avahi-utils, skipping iler.local"
      return 1
    }
  fi
  mkdir -p "$(dirname "$MDNS_HELPER")"
  cat >"$MDNS_HELPER" <<'EOF'
#!/bin/sh
# Re-resolves the LAN address on every (re)start so DHCP changes are picked up.
ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}')"
[ -n "$ip" ] || { echo "no LAN address yet" >&2; exit 1; }
exec avahi-publish -a -R iler.local "$ip"
EOF
  chmod 755 "$MDNS_HELPER"
  cat >"$MDNS_UNIT" <<EOF
[Unit]
Description=ILER Bot mDNS name iler.local
After=avahi-daemon.service network-online.target
Wants=network-online.target
Requires=avahi-daemon.service

[Service]
ExecStart=$MDNS_HELPER
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now iler-mdns.service >/dev/null 2>&1 || {
    warn "could not start iler-mdns.service"
    return 1
  }
  systemctl restart iler-mdns.service >/dev/null 2>&1 || true
  ok "mDNS: iler.local published"
  return 0
}

remove_mdns() {
  if [[ -f "$MDNS_UNIT" ]]; then
    systemctl disable --now iler-mdns.service >/dev/null 2>&1 || true
    rm -f "$MDNS_UNIT" "$MDNS_HELPER"
    systemctl daemon-reload || true
  fi
}
