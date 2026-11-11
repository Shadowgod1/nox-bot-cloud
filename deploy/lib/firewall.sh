#!/usr/bin/env bash
# ufw rules: the admin port is reachable only from private subnets (sourced).
#
# Docker publishes ports through its own iptables chains and bypasses the ufw INPUT rules, so
# besides the plain "ufw allow" rules we add a DOCKER-USER block to /etc/ufw/after.rules that
# drops non-private traffic to the published port. The block is delimited by markers so a
# re-run (or a different --port) replaces it.

PRIVATE_NETS=(10.0.0.0/8 172.16.0.0/12 192.168.0.0/16)
UFW_AFTER_RULES=/etc/ufw/after.rules
UFW_BEGIN="# BEGIN ILER admin port"
UFW_END="# END ILER admin port"

ufw_active() {
  have ufw && ufw status 2>/dev/null | grep -q "^Status: active"
}

ufw_remove_iler() {
  local nums n
  have ufw || return 0
  # delete numbered rules carrying our comment, highest number first
  nums="$(ufw status numbered 2>/dev/null | awk -F'[][]' '/iler-admin/ { gsub(/ /, "", $2); print $2 }' | sort -rn)"
  for n in $nums; do
    ufw --force delete "$n" >/dev/null || true
  done
  if [[ -f "$UFW_AFTER_RULES" ]] && grep -qF "$UFW_BEGIN" "$UFW_AFTER_RULES"; then
    sed -i "/^${UFW_BEGIN}\$/,/^${UFW_END}\$/d" "$UFW_AFTER_RULES"
  fi
}

ufw_apply_iler() {
  local port="$1" net
  ufw_remove_iler
  for net in "${PRIVATE_NETS[@]}"; do
    ufw allow from "$net" to any port "$port" proto tcp comment iler-admin >/dev/null
  done
  {
    echo "$UFW_BEGIN"
    echo "*filter"
    echo ":DOCKER-USER - [0:0]"
    for net in "${PRIVATE_NETS[@]}"; do
      echo "-A DOCKER-USER -p tcp -m conntrack --ctorigdstport ${port} --ctdir ORIGINAL -s ${net} -j RETURN"
    done
    echo "-A DOCKER-USER -p tcp -m conntrack --ctorigdstport ${port} --ctdir ORIGINAL -j DROP"
    echo "-A DOCKER-USER -j RETURN"
    echo "COMMIT"
    echo "$UFW_END"
  } >>"$UFW_AFTER_RULES"
  ufw reload >/dev/null
  ok "ufw: port $port allowed only from ${PRIVATE_NETS[*]}"
}

configure_firewall() {
  local port="$1"
  if ufw_active; then
    ufw_apply_iler "$port"
  else
    log "ufw is not active, skipping firewall rules (make sure port $port is not exposed to the internet)"
  fi
}
