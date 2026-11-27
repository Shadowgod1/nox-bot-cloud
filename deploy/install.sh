#!/usr/bin/env bash
# ILER Bot installer for Linux (Ubuntu 22.04/24.04, Debian 12) and WSL2 (spec section 8).
#
#   sudo bash deploy/install.sh [--port 8080] [--dir /opt/iler] [--offline images.tar]
#   sudo bash deploy/install.sh --dir /opt/iler/clients/acme --name iler-acme --port 8101 \
#        --public-url https://acme.example.kz --brand nox  # cloud instance (see deploy/cloud/README.md)
#   sudo bash deploy/install.sh --restore /path/iler-backup-YYYYMMDD-HHMMSS.tar.gz [--env /path/old.env]
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
  # Release assets ship SHA256SUMS next to the bundle: verify when it is there.
  sums_url="${ILER_SHA256SUMS_URL:-${BUNDLE_URL%/*}/SHA256SUMS}"
  if curl -fsSL "$sums_url" -o "$workdir/SHA256SUMS" 2>/dev/null; then
    bundle_name="${BUNDLE_URL##*/}"
    expected="$(awk -v f="$bundle_name" '{ n = $2; sub(/^\*/, "", n) } n == f { print $1; exit }' "$workdir/SHA256SUMS")"
    [[ -n "$expected" ]] || { echo "error: SHA256SUMS has no entry for $bundle_name" >&2; exit 1; }
    actual="$(sha256sum "$workdir/bundle.tar.gz" | awk '{ print $1 }')"
    [[ "$actual" == "$expected" ]] || { echo "error: checksum mismatch for $bundle_name (download corrupted or tampered)" >&2; exit 1; }
    echo "==> checksum ok ($bundle_name)"
  else
    echo "warning: no SHA256SUMS at $sums_url; bundle checksum not verified" >&2
  fi
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
  --name NAME         docker compose project name (default iler); one per instance on this machine
  --bind ADDR         address the admin port is published on (default: all interfaces; 127.0.0.1 = cloud)
  --public-url URL    public address behind the tunnel, e.g. https://acme.example.kz (cloud; implies
                      --bind 127.0.0.1). Cloud mode (--public-url or --bind 127.x): no firewall rules,
                      no iler.local, Secure cookies for https, setup link printed with this address
  --brand NAME        admin UI brand: iler (default) or nox (cloud edition by Nox service);
                      a re-install without --brand keeps the value in .env
  --restore FILE      restore a backup archive after installing (databases + WhatsApp session)
  --env FILE          with --restore: take ILER_SECRET_KEY from the ORIGINAL server's .env, so
                      stored secrets (SMTP password, Telegram bot token) can be decrypted;
                      LLM keys live only in .env: run set-llm-keys.sh again after a restore
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
ENV_IMPORT=""
UNINSTALL=0
PURGE=0
ASSUME_YES=0
PROJECT_NAME=""
HTTP_BIND=""
PUBLIC_URL=""
PUBLIC_URL_SET=0
BRAND=""
CLOUD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:-}"; shift 2 ;;
    --port=*) PORT="${1#*=}"; shift ;;
    --name) PROJECT_NAME="${2:-}"; shift 2 ;;
    --name=*) PROJECT_NAME="${1#*=}"; shift ;;
    --bind) HTTP_BIND="${2:-}"; shift 2 ;;
    --bind=*) HTTP_BIND="${1#*=}"; shift ;;
    --public-url) PUBLIC_URL="${2:-}"; PUBLIC_URL_SET=1; shift 2 ;;
    --public-url=*) PUBLIC_URL="${1#*=}"; PUBLIC_URL_SET=1; shift ;;
    --brand) BRAND="${2:-}"; shift 2 ;;
    --brand=*) BRAND="${1#*=}"; shift ;;
    --dir) ILER_DIR_PATH="${2:-}"; shift 2 ;;
    --dir=*) ILER_DIR_PATH="${1#*=}"; shift ;;
    --offline) OFFLINE_TAR="${2:-}"; shift 2 ;;
    --offline=*) OFFLINE_TAR="${1#*=}"; shift ;;
    --restore) RESTORE_FILE="${2:-}"; shift 2 ;;
    --restore=*) RESTORE_FILE="${1#*=}"; shift ;;
    --env) ENV_IMPORT="${2:-}"; shift 2 ;;
    --env=*) ENV_IMPORT="${1#*=}"; shift ;;
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
if [[ -n "$PROJECT_NAME" ]] && ! [[ "$PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]]; then
  die "invalid --name: $PROJECT_NAME (lowercase letters, digits, '-' and '_'; starts with a letter or digit)"
fi
if [[ -n "$HTTP_BIND" ]] && ! [[ "$HTTP_BIND" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  die "invalid --bind: $HTTP_BIND (an IPv4 address such as 127.0.0.1 or 0.0.0.0)"
fi
PUBLIC_URL="${PUBLIC_URL%/}"
if [[ -n "$PUBLIC_URL" ]]; then
  [[ "$PUBLIC_URL" =~ ^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$ ]] \
    || die "invalid --public-url: $PUBLIC_URL (expected https://host[:port], no path)"
  [[ "$PUBLIC_URL" == https://* ]] || warn "--public-url is not https: session cookies will not be Secure"
fi
if [[ -n "$BRAND" && "$BRAND" != "iler" && "$BRAND" != "nox" ]]; then
  die "invalid --brand: $BRAND (iler or nox)"
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
if [[ -n "$ENV_IMPORT" ]]; then
  [[ -n "$RESTORE_FILE" ]] || die "--env is only valid together with --restore"
  [[ -f "$ENV_IMPORT" ]] || die "env file not found: $ENV_IMPORT"
fi

require_root

# ---- instance (project name, bind address, public URL) ------------------------------------

# Combines the options with the existing <dir>/.env: a re-run without options keeps what the
# installation already has; an installation from before these settings is project "iler" on all interfaces.
# Sets PROJECT_NAME, HTTP_BIND, PUBLIC_URL, PORT and CLOUD (1 = behind a tunnel on this machine).
resolve_instance() {
  local envfile="$ILER_DIR_PATH/.env" cur_name cur_bind cur_url cur_port
  cur_name="$(env_get "$envfile" ILER_PROJECT)"
  cur_bind="$(env_get "$envfile" ILER_HTTP_BIND)"
  cur_url="$(env_get "$envfile" ILER_PUBLIC_URL)"
  cur_port="$(env_get "$envfile" ILER_HTTP_PORT)"
  if [[ -f "$envfile" ]]; then
    cur_name="${cur_name:-iler}"
    # renaming would orphan the running containers and the data volumes of the old name
    [[ -z "$PROJECT_NAME" || "$PROJECT_NAME" == "$cur_name" ]] \
      || die "$ILER_DIR_PATH is installed as project '$cur_name'; the project name cannot be changed (--name $PROJECT_NAME)"
  fi
  PROJECT_NAME="${PROJECT_NAME:-${cur_name:-iler}}"
  [[ "$PUBLIC_URL_SET" == "1" ]] || PUBLIC_URL="$cur_url"
  if [[ -z "$HTTP_BIND" ]]; then
    if [[ "$PUBLIC_URL_SET" == "1" && -n "$PUBLIC_URL" ]]; then
      HTTP_BIND=127.0.0.1
    else
      HTTP_BIND="$cur_bind"
    fi
  fi
  CLOUD=0
  if [[ -n "$PUBLIC_URL" || "$HTTP_BIND" == 127.* ]]; then
    CLOUD=1
  fi
  # cloud mode has no firewall rules: the port must not be reachable from outside this machine
  if [[ "$CLOUD" == "1" && "$HTTP_BIND" != 127.* ]]; then
    die "a cloud instance (--public-url) publishes the admin port on 127.0.0.1 only, not on ${HTTP_BIND:-all interfaces}"
  fi
  PORT="${PORT:-${cur_port:-8080}}"
}

# ---- uninstall ----------------------------------------------------------------------------

# Deletes the install directory, but never other instances kept below it (<dir>/clients/*).
purge_dir() {
  if [[ -d "$ILER_DIR_PATH/clients" ]] && [[ -n "$(ls -A "$ILER_DIR_PATH/clients" 2>/dev/null)" ]]; then
    find "$ILER_DIR_PATH" -mindepth 1 -maxdepth 1 ! -name clients -exec rm -rf {} +
    warn "kept $ILER_DIR_PATH/clients (other ILER instances); everything else in $ILER_DIR_PATH is deleted"
  else
    rm -rf "$ILER_DIR_PATH"
  fi
}

do_uninstall() {
  local what="stop and remove the ILER Bot containers"
  [[ "$PURGE" == "1" ]] && what="DELETE ILER Bot completely: containers, database, WhatsApp session, backups and $ILER_DIR_PATH"
  resolve_instance
  confirm "This will $what (project $PROJECT_NAME). Continue?" || die "aborted"
  if [[ -f "$ILER_DIR_PATH/compose.yml" ]] && have docker; then
    if [[ "$PURGE" == "1" ]]; then
      dc down --remove-orphans --volumes || warn "compose down failed"
    else
      dc down --remove-orphans || warn "compose down failed"
    fi
  else
    warn "no installation found in $ILER_DIR_PATH"
  fi
  # firewall rules and iler.local belong to the LAN installation of this machine; a cloud instance
  # has none and must not remove them
  if [[ "$CLOUD" != "1" ]]; then
    remove_firewall
    remove_mdns
  fi
  if [[ "$PURGE" == "1" ]]; then
    purge_dir
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
    ubuntu:24.04) ok "OS $id $version (primary platform)" ;;
    ubuntu:22.04|debian:12) ok "OS $id $version (supported; Ubuntu Server 24.04 LTS is the primary platform)" ;;
    *) warn "untested OS '$id $version' (primary: Ubuntu Server 24.04 LTS; also Ubuntu 22.04, Debian 12); continuing" ;;
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

# One compose project = one installation: refuse a name another directory's containers already use.
check_project() {
  local other
  other="$(docker ps -a --filter "label=com.docker.compose.project=$PROJECT_NAME" \
    --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null \
    | sort -u | grep -vxF "$ILER_DIR_PATH" | head -n1 || true)"
  [[ -z "$other" ]] || die "project '$PROJECT_NAME' already belongs to the installation in $other; choose another --name"
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
  [[ -f "$ILER_DIR_PATH/deploy/cloud/iler-client.sh" ]] && chmod 755 "$ILER_DIR_PATH/deploy/cloud/iler-client.sh"
  ok "files in $ILER_DIR_PATH"
}

configure_env() {
  local envfile="$ILER_DIR_PATH/.env" version current="" fresh=1 use_channel=1
  if [[ -f "$envfile" ]]; then
    fresh=0
    current="$(env_get "$envfile" ILER_VERSION)"
  fi
  render_env "$ILER_DIR_PATH/deploy/.env.example" "$envfile"
  version="$(env_get "$ILER_DIR_PATH/deploy/channel/stable.env" ILER_VERSION)"
  # Re-running an older bundle must not downgrade an installation that the updater already moved
  # forward: take the channel's version/digests only on a fresh .env or when the bundle is newer.
  if [[ "$fresh" == "0" && -n "$current" && -n "$version" && "$current" != "$version" ]]; then
    if [[ "$(printf '%s\n%s\n' "$current" "$version" | sort -V | tail -n1)" == "$current" ]]; then
      use_channel=0
      log "keeping installed version $current (bundle has older $version)"
    fi
  fi
  if [[ "$use_channel" == "1" ]]; then
    [[ -n "$version" ]] && env_set "$envfile" ILER_VERSION "$version"
    # optional image digests from the channel: pull by tag@digest (empty = by tag)
    local key digest
    for key in ILER_CORE_DIGEST ILER_WEB_DIGEST ILER_UPDATER_DIGEST; do
      digest="$(env_get "$ILER_DIR_PATH/deploy/channel/stable.env" "$key")"
      [[ -z "$digest" || "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || die "invalid $key in channel file"
      env_set "$envfile" "$key" "$digest"
    done
  fi
  [[ -n "$ENV_IMPORT" ]] && import_secret_key "$envfile"
  env_set "$envfile" ILER_DIR "$ILER_DIR_PATH"
  # resolved by resolve_instance from the options and the previous .env
  env_set "$envfile" ILER_HTTP_PORT "$PORT"
  env_set "$envfile" ILER_PROJECT "$PROJECT_NAME"
  env_set "$envfile" ILER_HTTP_BIND "$HTTP_BIND"
  env_set "$envfile" ILER_PUBLIC_URL "$PUBLIC_URL"
  # --brand wins; otherwise keep the installed brand (render_env added ILER_BRAND=iler to an older .env)
  [[ -n "$BRAND" ]] && env_set "$envfile" ILER_BRAND "$BRAND"
  BRAND="$(env_get "$envfile" ILER_BRAND)"
  chmod 600 "$envfile"
  printf '%s\n' "$(env_get "$envfile" ILER_VERSION)" >"$ILER_DIR_PATH/VERSION"
  ok ".env ready (version $(env_get "$envfile" ILER_VERSION), project $PROJECT_NAME, port ${HTTP_BIND:-*}:$PORT, brand ${BRAND:-iler})"
}

# Take ILER_SECRET_KEY from the original server's .env (--env). Other values (DB passwords etc.)
# stay local: they belong to this machine's volumes.
import_secret_key() {
  local envfile="$1" key
  key="$(env_get "$ENV_IMPORT" ILER_SECRET_KEY)"
  [[ -n "$key" && "$key" != "__GENERATE__" ]] || die "$ENV_IMPORT has no ILER_SECRET_KEY"
  if [[ "$(env_get "$envfile" ILER_SECRET_KEY)" != "$key" ]]; then
    cp -p "$envfile" "$envfile.bak-$(date +%Y%m%d-%H%M%S)"
    env_set "$envfile" ILER_SECRET_KEY "$key"
    ok "ILER_SECRET_KEY imported from $ENV_IMPORT (previous .env backed up)"
  else
    ok "ILER_SECRET_KEY already matches $ENV_IMPORT"
  fi
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

# Puts redis.rdb (WhatsApp session cache) from the archive into the redis volume. Runs after core
# has verified the archive checksums. Redis is stopped; a temporary redis loads the RDB with AOF off
# and then rewrites the AOF from it, so the real redis (appendonly yes) starts with that data.
restore_redis() {
  local tmp cid vol image i info
  cid="$(dc ps -a -q redis | head -n1)"
  [[ -n "$cid" ]] || { warn "redis container not found; WhatsApp session not restored"; return 1; }
  vol="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$cid")"
  image="$(docker inspect -f '{{.Config.Image}}' "$cid")"
  [[ -n "$vol" && -n "$image" ]] || { warn "redis volume not found; WhatsApp session not restored"; return 1; }
  tmp="$(mktemp -d)"
  if ! tar -xzf "$RESTORE_FILE" -C "$tmp" redis.rdb; then
    rm -rf "$tmp"
    warn "archive has no redis.rdb; WhatsApp session not restored"
    return 1
  fi
  chmod 644 "$tmp/redis.rdb"
  dc stop evolution-api redis
  docker run --rm -v "$vol:/data" -v "$tmp:/src:ro" --entrypoint sh "$image" -c \
    'rm -rf /data/appendonlydir /data/appendonly.aof* && cp /src/redis.rdb /data/dump.rdb && chown redis:redis /data/dump.rdb' \
    || { rm -rf "$tmp"; dc start redis evolution-api; warn "could not copy redis.rdb"; return 1; }
  rm -rf "$tmp"
  cid="$(docker run -d -v "$vol:/data" "$image" redis-server --dir /data --appendonly no --save '')"
  for ((i = 0; i < 60; i++)); do
    info="$(docker exec "$cid" redis-cli info persistence 2>/dev/null || true)"
    [[ "$info" == *"loading:0"* ]] && break
    sleep 1
  done
  docker exec "$cid" redis-cli config set appendonly yes >/dev/null
  for ((i = 0; i < 120; i++)); do
    info="$(docker exec "$cid" redis-cli info persistence 2>/dev/null || true)"
    [[ "$info" == *"aof_rewrite_in_progress:0"* && "$info" == *"aof_rewrite_scheduled:0"* \
      && "$info" == *"aof_last_bgrewrite_status:ok"* ]] && break
    sleep 1
  done
  docker exec "$cid" redis-cli shutdown nosave >/dev/null 2>&1 || true
  docker rm -f "$cid" >/dev/null 2>&1 || true
  dc start redis
  wait_healthy 120 redis || { warn "redis did not come back after restoring the snapshot"; return 1; }
  dc start evolution-api
  ok "WhatsApp session (redis snapshot) restored"
}

restore_backup() {
  local name result
  name="$(basename "$RESTORE_FILE")"
  log "restoring backup $name"
  if [[ -z "$ENV_IMPORT" ]]; then
    printf '%s\n' \
      "${C_RED}${C_BOLD}!!! IMPORTANT !!!${C_RESET}" \
      "  Stored secrets (LLM API keys, SMTP password, Telegram bot token) are encrypted with ILER_SECRET_KEY" \
      "  from the ORIGINAL server's $ILER_DIR_PATH/.env. Without it they cannot be decrypted and must be" \
      "  re-entered in the admin UI. To keep them, re-run with: --restore $RESTORE_FILE --env /path/to/original/.env" >&2
    sleep 5
  fi
  install -o "$CORE_UID" -g "$CORE_UID" -m 640 "$RESTORE_FILE" "$ILER_DIR_PATH/backups/$name"
  result="$(dc exec -T iler-core python -m iler.backup restore "$name")" || die "restore failed: $result"
  printf '%s\n' "$result"
  if ! restore_redis; then
    warn "databases restored, but the WhatsApp session was not; reconnect WhatsApp in the admin UI if needed"
    dc start redis evolution-api >/dev/null 2>&1 || true
  fi
  dc restart iler-core evolution-api
  wait_healthy 300 iler-core || die "core is not healthy after restore"
  ok "backup restored"
}

# address this machine reaches the admin port on
local_host() {
  if [[ -z "$HTTP_BIND" || "$HTTP_BIND" == "0.0.0.0" ]]; then echo 127.0.0.1; else echo "$HTTP_BIND"; fi
}

needs_setup() {
  have curl || return 0
  curl -fsS --max-time 10 "http://$(local_host):$PORT/api/setup/state" 2>/dev/null | grep -q '"needs_setup":[[:space:]]*true'
}

print_summary() {
  local ip mdns="$1" token setup="" dir_opt=""
  ip="$(lan_ip)"
  token="$(env_get "$ILER_DIR_PATH/.env" ILER_SETUP_TOKEN)"
  if [[ -n "$token" ]] && needs_setup; then
    setup="/setup?token=$token"
  fi
  [[ "$ILER_DIR_PATH" == "/opt/iler" ]] || dir_opt=" --dir $ILER_DIR_PATH"
  echo
  printf '%sILER Bot is running.%s Open the admin UI:\n' "$C_BOLD" "$C_RESET"
  if [[ "$CLOUD" == "1" ]]; then
    [[ -n "$setup" ]] && printf '%sFirst run:%s send the owner this one-time setup link privately (it creates the owner account):\n' "$C_YELLOW" "$C_RESET"
    if [[ -n "$PUBLIC_URL" ]]; then
      echo "  $PUBLIC_URL$setup"
      echo "  (the tunnel must route ${PUBLIC_URL#*://} to http://$(local_host):$PORT)"
    else
      warn "no --public-url: the admin UI is reachable only on this machine until a tunnel routes to it"
      echo "  http://$(local_host):$PORT$setup"
    fi
    echo
    echo "Data: $ILER_DIR_PATH (secrets in .env, backups in backups/), compose project $PROJECT_NAME."
    echo "Uninstall: sudo bash $ILER_DIR_PATH/deploy/install.sh --uninstall$dir_opt"
    return 0
  fi
  if [[ -n "$setup" ]]; then
    printf '%sFirst run:%s create the owner account with this one-time setup link (keep it private):\n' "$C_YELLOW" "$C_RESET"
    echo "  http://localhost:$PORT$setup"
    [[ -n "$ip" ]] && echo "  http://$ip:$PORT$setup    (from another computer in the local network)"
  else
    echo "  http://localhost:$PORT"
    [[ -n "$ip" ]] && echo "  http://$ip:$PORT    (from other computers in the local network)"
  fi
  [[ "$mdns" == "1" ]] && echo "  http://iler.local:$PORT"
  echo
  echo "Data: $ILER_DIR_PATH (secrets in .env, backups in backups/)."
  echo "Uninstall: sudo bash $ILER_DIR_PATH/deploy/install.sh --uninstall$dir_opt"
}

main() {
  local mdns=0
  log "ILER Bot installer ($SRC_ROOT -> $ILER_DIR_PATH)"
  resolve_instance
  [[ "$CLOUD" == "1" ]] && log "cloud instance: project $PROJECT_NAME, admin port $HTTP_BIND:$PORT${PUBLIC_URL:+, public $PUBLIC_URL}"
  check_system
  ensure_docker "$OFFLINE"
  # before anything is written: a busy port or a taken project name stops the install here
  check_project
  check_port "$PORT"
  copy_bundle
  configure_env
  fetch_images
  if [[ "$CLOUD" == "1" ]]; then
    log "cloud instance: port published on $HTTP_BIND only; firewall rules and iler.local are skipped"
  else
    # the admin port must be restricted before docker publishes it
    configure_firewall "$PORT"
  fi
  start_stack
  [[ -n "$RESTORE_FILE" ]] && restore_backup
  if [[ "$CLOUD" != "1" ]]; then
    configure_mdns "$OFFLINE" && mdns=1
  fi
  print_summary "$mdns"
}

main
