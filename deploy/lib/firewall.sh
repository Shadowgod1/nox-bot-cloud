#!/usr/bin/env bash
# Firewall: the admin port is reachable only from private / loopback sources (sourced).
#
# Docker publishes ports through its own iptables chains (DNAT + FORWARD) and bypasses the ufw INPUT
# rules, so the restriction lives in Docker's DOCKER-USER chain, which Docker never flushes:
#
#   DOCKER-USER -p tcp --ctorigdstport <port> --ctdir ORIGINAL -j ILER-ADMIN   (published port)
#   INPUT       -p tcp --dport <port>                          -j ILER-ADMIN   (docker-proxy path)
#   ILER-ADMIN  -s <private net> -j RETURN ... -j DROP
#
# for iptables and ip6tables. This is applied always (ufw or not), before the stack starts, and
# re-applied at boot by iler-firewall.service (after docker.service). Every rule carries the comment
# "iler-admin", so a re-run (or a different --port) replaces it. When ufw is active we additionally
# keep a DOCKER-USER block in /etc/ufw/after.rules and after6.rules, because `ufw reload` re-creates
# that chain from its own files.

PRIVATE_NETS=(10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 127.0.0.0/8)
PRIVATE_NETS6=(fc00::/7 fe80::/10 ::1/128)
FW_HELPER=/usr/local/lib/iler/firewall-apply.sh
FW_UNIT=/etc/systemd/system/iler-firewall.service
UFW_AFTER_RULES=/etc/ufw/after.rules
UFW_AFTER6_RULES=/etc/ufw/after6.rules
UFW_BEGIN="# BEGIN ILER admin port"
UFW_END="# END ILER admin port"

ufw_active() {
  have ufw && ufw status 2>/dev/null | grep -q "^Status: active"
}

# ---- iptables / ip6tables (always) --------------------------------------------------------

# The rules live in deploy/lib/firewall-apply.sh, installed as $FW_HELPER so the boot unit can run it.
write_fw_helper() {
  install -D -m 755 "$ILER_DIR_PATH/deploy/lib/firewall-apply.sh" "$FW_HELPER"
}

install_fw_unit() {
  local port="$1"
  if ! have systemctl || [[ ! -d /run/systemd/system ]]; then
    warn "no systemd: firewall rules for port $port are NOT persisted across reboots; re-run the installer after a reboot"
    return 0
  fi
  cat >"$FW_UNIT" <<EOF
[Unit]
Description=ILER Bot: admin port $port only from private networks (iptables DOCKER-USER)
After=docker.service network-pre.target
Wants=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$FW_HELPER $port

[Install]
WantedBy=multi-user.target docker.service
EOF
  systemctl daemon-reload
  systemctl enable iler-firewall.service >/dev/null 2>&1 || warn "could not enable iler-firewall.service"
}

iptables_apply_iler() {
  local port="$1"
  if ! have iptables; then
    warn "iptables not found: port $port is NOT restricted to the local network"
    return 0
  fi
  write_fw_helper
  if "$FW_HELPER" "$port"; then
    ok "iptables: port $port reachable only from ${PRIVATE_NETS[*]} ${PRIVATE_NETS6[*]}"
  else
    warn "could not install iptables rules for port $port"
  fi
  install_fw_unit "$port"
}

iptables_remove_iler() {
  if [[ -f "$FW_UNIT" ]]; then
    systemctl disable iler-firewall.service >/dev/null 2>&1 || true
    rm -f "$FW_UNIT"
    systemctl daemon-reload || true
  fi
  if [[ -x "$FW_HELPER" ]]; then
    "$FW_HELPER" --remove || true
    rm -f "$FW_HELPER"
  fi
}

# ---- ufw (only when active) ---------------------------------------------------------------

ufw_block_remove() { # <file>
  if [[ -f "$1" ]] && grep -qF "$UFW_BEGIN" "$1"; then
    sed -i "/^${UFW_BEGIN}\$/,/^${UFW_END}\$/d" "$1"
  fi
}

ufw_block_append() { # <file> <port> <nets...>
  local file="$1" port="$2" net
  shift 2
  [[ -f "$file" ]] || return 0
  {
    echo "$UFW_BEGIN"
    echo "*filter"
    echo ":DOCKER-USER - [0:0]"
    for net in "$@"; do
      echo "-A DOCKER-USER -p tcp -m conntrack --ctorigdstport ${port} --ctdir ORIGINAL -s ${net} -j RETURN"
    done
    echo "-A DOCKER-USER -p tcp -m conntrack --ctorigdstport ${port} --ctdir ORIGINAL -j DROP"
    echo "-A DOCKER-USER -j RETURN"
    echo "COMMIT"
    echo "$UFW_END"
  } >>"$file"
}

ufw_remove_iler() {
  local nums n
  have ufw || return 0
  # delete numbered rules carrying our comment, highest number first
  nums="$(ufw status numbered 2>/dev/null | awk -F'[][]' '/iler-admin/ { gsub(/ /, "", $2); print $2 }' | sort -rn)"
  for n in $nums; do
    ufw --force delete "$n" >/dev/null || true
  done
  ufw_block_remove "$UFW_AFTER_RULES"
  ufw_block_remove "$UFW_AFTER6_RULES"
}

ufw_apply_iler() {
  local port="$1" net
  ufw_remove_iler
  for net in "${PRIVATE_NETS[@]}" "${PRIVATE_NETS6[@]}"; do
    ufw allow from "$net" to any port "$port" proto tcp comment iler-admin >/dev/null || true
  done
  ufw_block_append "$UFW_AFTER_RULES" "$port" "${PRIVATE_NETS[@]}"
  ufw_block_append "$UFW_AFTER6_RULES" "$port" "${PRIVATE_NETS6[@]}"
  ufw reload >/dev/null
  ok "ufw: port $port allowed only from private networks"
}

# configure_firewall <port>: call BEFORE the stack publishes the port.
configure_firewall() {
  local port="$1"
  iptables_apply_iler "$port"
  if ufw_active; then
    ufw_apply_iler "$port"
    # ufw reload re-creates DOCKER-USER from its files; put our chain jump back on top
    if [[ -x "$FW_HELPER" ]]; then
      "$FW_HELPER" "$port" >/dev/null 2>&1 || true
    fi
  fi
}

remove_firewall() {
  ufw_remove_iler
  if have ufw && ufw_active; then
    ufw reload >/dev/null || true
  fi
  iptables_remove_iler
}
