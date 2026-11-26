#!/usr/bin/env bash
# Docker Engine / compose helpers (sourced). Expects ILER_DIR_PATH to be set by install.sh.

# Offline mode only means "images come from the archive": Docker Engine itself is still installed from
# the internet when it is missing (the Windows installer always runs in offline mode).
# Usage: ensure_docker <offline 0|1> - the flag is kept for callers; the Engine install does not depend on it.
ensure_docker() {
  local tmp docker_bin
  # Docker Desktop's WSL integration puts a "docker" CLI into every distro (/mnt/wsl/docker-desktop/...);
  # ILER needs its own Docker Engine inside the distro, so that CLI does not count.
  docker_bin=""
  if have docker; then
    docker_bin="$(readlink -f "$(command -v docker)" 2>/dev/null || command -v docker)"
    if [[ "$docker_bin" == /mnt/wsl/docker-desktop* ]]; then
      log "docker CLI comes from Docker Desktop integration ($docker_bin); installing Docker Engine in this distro"
      docker_bin=""
    fi
  fi
  if [[ -z "$docker_bin" ]]; then
    log "installing Docker Engine (get.docker.com)"
    have curl || { apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates >/dev/null; }       || die "Docker is not installed and apt is unreachable; install docker-ce + docker-compose-plugin first."
    tmp="$(mktemp)"
    curl -fsSL https://get.docker.com -o "$tmp"       || die "Docker is not installed and get.docker.com is unreachable; install docker-ce + docker-compose-plugin first."
    sh "$tmp"
    rm -f "$tmp"
  fi
  if have systemctl && [[ -d /run/systemd/system ]]; then
    systemctl enable --now docker >/dev/null 2>&1 || warn "could not enable docker.service"
  fi
  if ! docker compose version >/dev/null 2>&1; then
    log "installing docker compose plugin"
    { apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-plugin >/dev/null; }       || die "docker compose plugin is missing and could not be installed"
  fi
  docker info >/dev/null 2>&1 || die "Docker daemon is not running"
  ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null), $(docker compose version --short 2>/dev/null)"
}

# Explicit -p (ILER_PROJECT from .env, default iler, like the updater): an exported
# COMPOSE_PROJECT_NAME must never point compose at another instance's project.
dc() {
  local project
  project="$(env_get "$ILER_DIR_PATH/.env" ILER_PROJECT)"
  docker compose -p "${project:-iler}" --project-directory "$ILER_DIR_PATH" -f "$ILER_DIR_PATH/compose.yml" \
    --env-file "$ILER_DIR_PATH/.env" "$@"
}

# container_health <service> -> healthy|unhealthy|starting|missing
container_health() {
  local id
  id="$(dc ps -q "$1" 2>/dev/null | head -n1)"
  if [[ -z "$id" ]]; then
    echo missing
    return
  fi
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || echo missing
}

# wait_healthy <timeout-seconds> <service...>
wait_healthy() {
  local timeout="$1" deadline svc state pending
  shift
  deadline=$((SECONDS + timeout))
  while :; do
    pending=""
    for svc in "$@"; do
      state="$(container_health "$svc")"
      [[ "$state" == "healthy" ]] || pending="$pending $svc($state)"
    done
    [[ -z "$pending" ]] && return 0
    if ((SECONDS >= deadline)); then
      warn "not healthy after ${timeout}s:$pending"
      return 1
    fi
    sleep 3
  done
}
