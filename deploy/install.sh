#!/usr/bin/env bash
# ILER Bot installer for Linux (Ubuntu 22.04/24.04, Debian 12) and WSL2 (spec section 8).
#
#   sudo bash deploy/install.sh [--port 8080] [--dir /opt/iler] [--offline images.tar]
#   sudo bash deploy/install.sh --restore /path/iler-backup-YYYYMMDD-HHMMSS.tar.gz
#   sudo bash deploy/install.sh --uninstall [--purge]
#   curl -fsSL <release>/install.sh | sudo bash          # downloads the bundle first
#
# Idempotent: re-running keeps existing secrets and data, applies a newer bundle if present.
# shellcheck source-path=SCRIPTDIR
set -euo pipefail

BUNDLE_URL="${ILER_BUNDLE_URL:-https://github.com/shadowgod1/iler-bot/releases/latest/download/iler-bot.tar.gz}"

script_path="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
if [[ -n "$script_path" && -f "$script_path" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "$script_path")" && pwd)"
fi

# Piped from curl (no bundle next to us): fetch the bundle and re-run its installer.
if [[ -z "$SCRIPT_DIR" || ! -f "$SCRIPT_DIR/lib/common.sh" ]]; then
  [[ "$(id -u)" -eq 0 ]] || { echo "error: run as root (curl ... | sudo bash)" >&2; exit 1; }
  command -v curl >/dev/null || { echo "error: curl is required" >&2; exit 1; }
  workdir="$(mktemp -d /tmp/iler-bundle.XXXXXX)"
  echo "==> downloading $BUNDLE_URL"
  curl -fsSL "$BUNDLE_URL" -o "$workdir/bundle.tar.gz"
  tar -xzf "$workdir/bundle.tar.gz" -C "$workdir"
  installer="$(find "$workdir" -maxdepth 3 -path '*/deploy/install.sh' | head -n1)"
  [[ -n "$installer" ]] || { echo "error: bundle has no deploy/install.sh" >&2; exit 1; }
  exec bash "$installer" "$@"
fi

# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=lib/env.sh
. "$SCRIPT_DIR/lib/env.sh"
# shellcheck source=lib/docker.sh
. "$SCRIPT_DIR/lib/docker.sh"
# shellcheck source=lib/firewall.sh
. "$SCRIPT_DIR/lib/firewall.sh"
# shellcheck source=lib/mdns.sh
. "$SCRIPT_DIR/lib/mdns.sh"

SRC_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CORE_UID=10001

usage() {
  cat <<EOF
Usage: sudo bash install.sh [options]
  --port N            admin UI port on the LAN (default 8080 or the value in .env)
  --dir PATH          install directory (default /opt/iler)
  --offline FILE      load images from FILE (docker save archive) instead of the registry
  --restore FILE      restore a backup archive after installing
  --uninstall         stop and remove the stack (volumes and backups are kept)
  --purge             with --uninstall: also delete volumes, backups and the install directory
  --yes               do not ask for confirmation
  -h, --help          this help
EOF
}

PORT=""
ILER_DIR_PATH="/opt/iler"
OFFLINE_TAR=""
RESTORE_FILE=""
UNINSTALL=0
PURGE=0
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:-}"; shift 2 ;;
    --port=*) PORT="${1#*=}"; shift ;;
    --dir) ILER_DIR_PATH="${2:-}"; shift 2 ;;
    --dir=*) ILER_DIR_PATH="${1#*=}"; shift ;;
    --offline) OFFLINE_TAR="${2:-}"; shift 2 ;;
    --offline=*) OFFLINE_TAR="${1#*=}"; shift ;;
    --restore) RESTORE_FILE="${2:-}"; shift 2 ;;
    --restore=*) RESTORE_FILE="${1#*=}"; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done
export ASSUME_YES

[[ "$ILER_DIR_PATH" == /* ]] || die "--dir must be an absolute path"
ILER_DIR_PATH="${ILER_DIR_PATH%/}"
if [[ -n "$PORT" ]] && ! [[ "$PORT" =~ ^[0-9]+$ && "$PORT" -ge 1 && "$PORT" -le 65535 ]]; then
  die "invalid --port: $PORT"
fi
if [[ "$PURGE" == "1" && "$UNINSTALL" != "1" ]]; then
  die "--purge is only valid together with --uninstall"
fi
OFFLINE=0
if [[ -n "$OFFLINE_TAR" ]]; then
  [[ -f "$OFFLINE_TAR" ]] || die "offline archive not found: $OFFLINE_TAR"
  OFFLINE_TAR="$(cd "$(dirname "$OFFLINE_TAR")" && pwd)/$(basename "$OFFLINE_TAR")"
  OFFLINE=1
fi
if [[ -n "$RESTORE_FILE" ]]; then
  [[ -f "$RESTORE_FILE" ]] || die "backup file not found: $RESTORE_FILE"
  [[ "$(basename "$RESTORE_FILE")" =~ ^[A-Za-z0-9._-]+\.tar\.gz$ ]] || die "unexpected backup file name: $RESTORE_FILE"
fi

require_root

# ---- uninstall ----------------------------------------------------------------------------

do_uninstall() {
  local what="stop and remove the ILER Bot containers"
  [[ "$PURGE" == "1" ]] && what="DELETE ILER Bot completely: containers, database, WhatsApp session, backups and $ILER_DIR_PATH"
  confirm "This will $what. Continue?" || die "aborted"
  if [[ -f "$ILER_DIR_PATH/compose.yml" ]] && have docker; then
    if [[ "$PURGE" == "1" ]]; then
      dc down --remove-orphans --volumes || warn "compose down failed"
    else
      dc down --remove-orphans || warn "compose down failed"
    fi
  else
    warn "no installation found in $ILER_DIR_PATH"
  fi
  ufw_remove_iler
  have ufw && ufw_active && ufw reload >/dev/null || true
  remove_mdns
  if [[ "$PURGE" == "1" ]]; then
    rm -rf "$ILER_DIR_PATH"
    ok "removed $ILER_DIR_PATH and all volumes"
  else
    ok "stack removed; data volumes and $ILER_DIR_PATH/backups are kept (use --uninstall --purge to delete)"
  fi
}

if [[ "$UNINSTALL" == "1" ]]; then
  do_uninstall
  exit 0
fi

# ---- checks -------------------------------------------------------------------------------

check_system() {
  local id="" version="" mem_kb free_kb parent
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    id="${ID:-}"
    version="${VERSION_ID:-}"
  fi
  case "$id:$version" in
    ubuntu:22.04|ubuntu:24.04|debian:12) ok "OS $id $version" ;;
    *) warn "untested OS '$id $version' (supported: Ubuntu 22.04/24.04, Debian 12); continuing" ;;
  esac
  mem_kb="$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)"
  if ((mem_kb < 3500000)); then
    warn "only $((mem_kb / 1024)) MB RAM; minimum is 4 GB (2 vCPU / 4 GB / 40 GB SSD)"
  else
    ok "RAM $((mem_kb / 1024)) MB"
  fi
  parent="$ILER_DIR_PATH"
  while [[ ! -d "$parent" ]]; do parent="$(dirname "$parent")"; done
  free_kb="$(df -Pk "$parent" | awk 'NR == 2 { print $4 }')"
  if ((free_kb < 20 * 1024 * 1024)); then
    warn "only $((free_kb / 1024 / 1024)) GB free on $parent; 40 GB recommended"
  else
    ok "disk $((free_kb / 1024 / 1024)) GB free"
  fi
  [[ -f "$SRC_ROOT/compose.yml" ]] || die "bundle is incomplete: $SRC_ROOT/compose.yml not found"
}

port_in_use() {
  if have ss; then
    ss -ltnH "sport = :$1" 2>/dev/null | grep -q .
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
  fi
}

check_port() {
  local running=""
  if [[ -f "$ILER_DIR_PATH/compose.yml" && -f "$ILER_DIR_PATH/.env" ]]; then
    running="$(dc ps -q iler-web 2>/dev/null || true)"
  fi
  if [[ -z "$running" ]] && port_in_use "$1"; then
    die "port $1 is already in use; choose another with --port"
  fi
}

# ---- install steps ------------------------------------------------------------------------

copy_bundle() {
  mkdir -p "$ILER_DIR_PATH" "$ILER_DIR_PATH/backups"
  chown "$CORE_UID:$CORE_UID" "$ILER_DIR_PATH/backups"
  chmod 750 "$ILER_DIR_PATH/backups"
  if [[ "$SRC_ROOT" != "$ILER_DIR_PATH" ]]; then
    install -m 644 "$SRC_ROOT/compose.yml" "$ILER_DIR_PATH/compose.yml"
    rm -rf "$ILER_DIR_PATH/deploy.new"
    cp -a "$SRC_ROOT/deploy" "$ILER_DIR_PATH/deploy.new"
    rm -rf "$ILER_DIR_PATH/deploy"
    mv "$ILER_DIR_PATH/deploy.new" "$ILER_DIR_PATH/deploy"
  fi
  # bundles built on Windows may carry CRLF; postgres runs the init script with bash
  find "$ILER_DIR_PATH/deploy" -name '*.sh' -exec sed -i 's/\r$//' {} +
  chmod 755 "$ILER_DIR_PATH/deploy/install.sh" "$ILER_DIR_PATH"/deploy/postgres-init/*.sh
  ok "files in $ILER_DIR_PATH"
}

configure_env() {
  local envfile="$ILER_DIR_PATH/.env" version
  render_env "$ILER_DIR_PATH/deploy/.env.example" "$envfile"
  version="$(env_get "$ILER_DIR_PATH/deploy/channel/stable.env" ILER_VERSION)"
  [[ -n "$version" ]] && env_set "$envfile" ILER_VERSION "$version"
  env_set "$envfile" ILER_DIR "$ILER_DIR_PATH"
  if [[ -n "$PORT" ]]; then
    env_set "$envfile" ILER_HTTP_PORT "$PORT"
  else
    PORT="$(env_get "$envfile" ILER_HTTP_PORT)"
    PORT="${PORT:-8080}"
  fi
  chmod 600 "$envfile"
  printf '%s\n' "$(env_get "$envfile" ILER_VERSION)" >"$ILER_DIR_PATH/VERSION"
  ok ".env ready (version $(env_get "$envfile" ILER_VERSION), port $PORT)"
}

registry_login() {
  local token_file="$ILER_DIR_PATH/deploy/registry-token" owner
  [[ -f "$token_file" ]] || return 0
  owner="$(env_get "$ILER_DIR_PATH/.env" ILER_IMAGE_OWNER)"
  if docker login ghcr.io -u "${owner:-shadowgod1}" --password-stdin <"$token_file" >/dev/null; then
    ok "logged in to ghcr.io (read-only token)"
  else
    warn "ghcr.io login failed; pulling anonymously"
  fi
}

fetch_images() {
  if [[ "$OFFLINE" == "1" ]]; then
    log "loading images from $OFFLINE_TAR"
    docker load -i "$OFFLINE_TAR"
    local img missing=""
    while IFS= read -r img; do
      docker image inspect "$img" >/dev/null 2>&1 || missing="$missing $img"
    done < <(dc config --images | sort -u)
    # digest-pinned refs resolve after `docker load` only with the containerd image store
    [[ -z "$missing" ]] || die "images missing after load:$missing (Docker Engine 29+ with the containerd image store is required for offline install)"
  else
    registry_login
    log "pulling images"
    dc pull --quiet
  fi
}

start_stack() {
  log "starting services"
  dc up -d --no-build --remove-orphans
  log "waiting for services to become healthy (up to 5 min)"
  if ! wait_healthy 300 postgres redis iler-core iler-web; then
    dc ps || true
    dc logs --tail 50 iler-core || true
    die "ILER Bot did not start; see the logs above (docker compose -f $ILER_DIR_PATH/compose.yml logs)"
  fi
  wait_healthy 180 evolution-api || warn "evolution-api is not healthy yet; WhatsApp may need a minute"
  ok "all services are up"
}

restore_backup() {
  local name
  name="$(basename "$RESTORE_FILE")"
  log "restoring backup $name"
  install -o "$CORE_UID" -g "$CORE_UID" -m 640 "$RESTORE_FILE" "$ILER_DIR_PATH/backups/$name"
  dc exec -T iler-core python -m iler.backup restore "$name" || die "restore failed"
  dc restart iler-core evolution-api
  wait_healthy 300 iler-core || die "core is not healthy after restore"
  ok "backup restored"
}

print_summary() {
  local ip mdns="$1"
  ip="$(lan_ip)"
  echo
  printf '%sILER Bot is running.%s Open the admin UI:\n' "$C_BOLD" "$C_RESET"
  echo "  http://localhost:$PORT"
  [[ -n "$ip" ]] && echo "  http://$ip:$PORT    (from other computers in the local network)"
  [[ "$mdns" == "1" ]] && echo "  http://iler.local:$PORT"
  echo
  echo "Data: $ILER_DIR_PATH (secrets in .env, backups in backups/)."
  echo "Uninstall: sudo bash $ILER_DIR_PATH/deploy/install.sh --uninstall"
}

main() {
  local mdns=0
  log "ILER Bot installer ($SRC_ROOT -> $ILER_DIR_PATH)"
  check_system
  ensure_docker "$OFFLINE"
  copy_bundle
  configure_env
  check_port "$PORT"
  fetch_images
  start_stack
  [[ -n "$RESTORE_FILE" ]] && restore_backup
  configure_firewall "$PORT"
  configure_mdns "$OFFLINE" && mdns=1
  print_summary "$mdns"
}

main
