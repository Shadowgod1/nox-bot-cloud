#!/usr/bin/env bash
# ILER cloud: client instances on one machine, one command each (run by the ILER technician as root).
#
#   sudo bash deploy/cloud/iler-client.sh create <name> --port N [--coins N] [--public-url URL]
#                                         [--offline images.tar] [--prices JSON] [--brand nox|iler]
#   sudo bash deploy/cloud/iler-client.sh stop <name>
#   sudo bash deploy/cloud/iler-client.sh start <name>
#   sudo bash deploy/cloud/iler-client.sh remove <name> [--yes]
#   sudo bash deploy/cloud/iler-client.sh list
#
# Every client is its own docker compose project "iler-<name>" in /opt/iler/clients/<name> (own .env,
# database, WhatsApp session, backups, coin limit), with the admin port published on 127.0.0.1 only.
# The public hostname <name>.<domain> -> http://127.0.0.1:<port> is added in the Cloudflare tunnel by
# the technician (this script never calls the Cloudflare API). Cloud clients get the Nox brand
# (admin UI of Nox service) unless --brand iler is given. Details: deploy/cloud/README.md.
# Env: ILER_CLIENTS_DIR (default /opt/iler/clients). create also takes the owner account from the Nox portal
# (never as options: the command line is visible in ps): ILER_OWNER_EMAIL, ILER_OWNER_NAME, ILER_COMPANY,
# ILER_OWNER_PASSWORD_HASH (argon2id). They go to the client .env; core creates the owner on the first start.
# shellcheck source-path=SCRIPTDIR
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUNDLE_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"
# shellcheck source=../lib/common.sh
. "$DEPLOY_DIR/lib/common.sh"
# shellcheck source=../lib/env.sh
. "$DEPLOY_DIR/lib/env.sh"
# shellcheck source=../lib/docker.sh
. "$DEPLOY_DIR/lib/docker.sh"

CLIENTS_DIR="${ILER_CLIENTS_DIR:-/opt/iler/clients}"
CLIENTS_DIR="${CLIENTS_DIR%/}"
# rm -rf runs below this directory: it must be absolute and never /
[[ "$CLIENTS_DIR" == /?* ]] || die "ILER_CLIENTS_DIR must be an absolute path other than /"
# $ per 1M tokens (input, output) for the GPT / Gemini models of the admin UI: without them coins are
# billed at the highest tariff. Override with --prices.
DEFAULT_PRICES='{"gpt-5.4-mini": [0.75, 4.5], "gemini-3.8-flash": [0.75, 3.75]}'

usage() {
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
}

# argon2id hash as argon2-cffi writes it (portal and core both hash with its PasswordHasher)
OWNER_HASH_RE='^\$argon2id\$v=[0-9]+\$m=[0-9]+,t=[0-9]+,p=[0-9]+\$[A-Za-z0-9+/]+={0,2}\$[A-Za-z0-9+/]+={0,2}$'
OWNER_EMAIL_RE='^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'

# owner_text <value>: name / company for a single-quoted .env value (one line, no single quotes)
owner_text() {
  local v="${1//$'\r'/ }"
  v="${v//$'\n'/ }"
  v="${v//\'/’}"
  # no slicing: under a C locale it would cut a UTF-8 letter in half; the provisioner sends at most 200 letters
  ((${#v} <= 1000)) || die "owner name / company is too long"
  printf '%s' "$v"
}

# 2-32 characters, a valid DNS label (it becomes <name>.<domain>): no '-' at the start or end
valid_name() {
  [[ "$1" =~ ^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])$ ]]
}

require_name() {
  local name="${1:-}"
  [[ -n "$name" ]] || { usage >&2; die "client name is required"; }
  valid_name "$name" || die "invalid client name '$name': only a-z, 0-9 and '-' (not first or last), 2-32 characters"
}

client_dir() { printf '%s/%s' "$CLIENTS_DIR" "$1"; }
project_of() { printf 'iler-%s' "$1"; }

# require_client <name>: the client must exist; sets ILER_DIR_PATH for dc()
require_client() {
  require_name "$1"
  ILER_DIR_PATH="$(client_dir "$1")"
  [[ -f "$ILER_DIR_PATH/.env" ]] || die "client '$1' not found (no $ILER_DIR_PATH/.env); see: $0 list"
  local project
  project="$(env_get "$ILER_DIR_PATH/.env" ILER_PROJECT)"
  [[ "$project" == "$(project_of "$1")" ]] \
    || die "$ILER_DIR_PATH/.env has ILER_PROJECT='$project', expected $(project_of "$1"); refusing to touch it"
}

port_listening() {
  if have ss; then
    ss -ltnH "sport = :$1" 2>/dev/null | grep -q .
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
  fi
}

# client that already has this port in its .env (also when it is stopped), empty if none
port_owner() {
  local env
  for env in "$CLIENTS_DIR"/*/.env; do
    [[ -f "$env" ]] || continue
    if [[ "$(env_get "$env" ILER_HTTP_PORT)" == "$1" ]]; then
      basename "$(dirname "$env")"
      return 0
    fi
  done
}

# ---- create -------------------------------------------------------------------------------

cmd_create() {
  local name="" port="" coins="" public_url="" offline="" prices="$DEFAULT_PRICES" brand="nox" dir project owner
  # owner from the Nox portal: environment only, and not passed on to install.sh / docker (they read .env)
  local owner_email="${ILER_OWNER_EMAIL:-}" owner_name="${ILER_OWNER_NAME:-}" company="${ILER_COMPANY:-}"
  local owner_hash="${ILER_OWNER_PASSWORD_HASH:-}"
  unset ILER_OWNER_EMAIL ILER_OWNER_NAME ILER_COMPANY ILER_OWNER_PASSWORD_HASH
  name="${1:-}"
  [[ $# -gt 0 ]] && shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --port) port="${2:-}"; shift 2 ;;
      --port=*) port="${1#*=}"; shift ;;
      --coins) coins="${2:-}"; shift 2 ;;
      --coins=*) coins="${1#*=}"; shift ;;
      --public-url) public_url="${2:-}"; shift 2 ;;
      --public-url=*) public_url="${1#*=}"; shift ;;
      --offline) offline="${2:-}"; shift 2 ;;
      --offline=*) offline="${1#*=}"; shift ;;
      --prices) prices="${2:-}"; shift 2 ;;
      --prices=*) prices="${1#*=}"; shift ;;
      --brand) brand="${2:-}"; shift 2 ;;
      --brand=*) brand="${1#*=}"; shift ;;
      *) usage >&2; die "unknown option for create: $1" ;;
    esac
  done
  require_name "$name"
  [[ -n "$port" ]] || die "--port is required (one free port per client, e.g. 8101)"
  [[ "$port" =~ ^[1-9][0-9]{3,4}$ && "$port" -le 65535 ]] || die "invalid --port: $port (1000-65535, no leading zeros)"
  [[ -z "$coins" || "$coins" =~ ^[0-9]{1,9}$ ]] || die "invalid --coins: $coins (whole number, 0 = no limit)"
  [[ "$brand" == "nox" || "$brand" == "iler" ]] || die "invalid --brand: $brand (nox or iler)"
  [[ "$prices" != *"'"* && "$prices" != *$'\n'* ]] || die "--prices must be one line of JSON without single quotes"
  have python3 || die "python3 is required to check --prices"
  python3 -c 'import json, sys; json.loads(sys.argv[1])' "$prices" 2>/dev/null || die "--prices is not valid JSON: $prices"
  public_url="${public_url%/}"
  if [[ -n "$public_url" ]]; then
    [[ "$public_url" =~ ^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$ ]] \
      || die "invalid --public-url: $public_url (expected https://host, no path)"
  fi
  if [[ -z "$offline" && -f "$BUNDLE_ROOT/images.tar" ]]; then
    offline="$BUNDLE_ROOT/images.tar"
  fi
  [[ -z "$offline" || -f "$offline" ]] || die "offline archive not found: $offline"
  owner_email="$(printf '%s' "$owner_email" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
  if [[ -n "$owner_email" || -n "$owner_hash" ]]; then
    [[ -n "$owner_email" && -n "$owner_hash" ]] || die "ILER_OWNER_EMAIL and ILER_OWNER_PASSWORD_HASH go together"
    [[ ${#owner_email} -le 254 && "$owner_email" =~ $OWNER_EMAIL_RE ]] || die "ILER_OWNER_EMAIL is not a valid e-mail"
    # never echo the hash itself
    [[ "$owner_hash" =~ $OWNER_HASH_RE ]] || die "ILER_OWNER_PASSWORD_HASH is not an argon2id hash"
    owner_name="$(owner_text "$owner_name")"
    company="$(owner_text "$company")"
  fi

  dir="$(client_dir "$name")"
  project="$(project_of "$name")"
  # checks first: nothing is written when one of them fails
  [[ ! -e "$dir" ]] || die "client '$name' already exists ($dir); nothing changed"
  owner="$(port_owner "$port")"
  [[ -z "$owner" ]] || die "port $port is already assigned to client '$owner'"
  ! port_listening "$port" || die "port $port is already in use on this machine"
  if have docker; then
    [[ -z "$(docker ps -aq --filter "label=com.docker.compose.project=$project")" ]] \
      || die "containers of project $project already exist (left from an earlier install?); remove them first"
    [[ -z "$(docker volume ls -q --filter "label=com.docker.compose.project=$project")" ]] \
      || die "volumes of project $project already exist (data of an earlier client '$name'?); remove them first: docker volume ls --filter label=com.docker.compose.project=$project"
  fi
  [[ -f "$BUNDLE_ROOT/compose.yml" && -f "$DEPLOY_DIR/install.sh" ]] \
    || die "run this script from an ILER bundle (compose.yml and deploy/install.sh not found in $BUNDLE_ROOT)"
  [[ -n "$public_url" ]] || warn "no --public-url: cookies are not Secure and the setup link points to 127.0.0.1; re-run install.sh --public-url later"

  # Seed .env with the client's own values; install.sh adds every other key and generates the secrets.
  log "creating client '$name' (project $project, port 127.0.0.1:$port, brand $brand)"
  mkdir -p "$dir"
  (
    umask 077
    {
      echo "# ILER cloud client '$name' (created by deploy/cloud/iler-client.sh)"
      echo "ILER_PROJECT=$project"
      echo "ILER_HTTP_BIND=127.0.0.1"
      echo "ILER_HTTP_PORT=$port"
      echo "ILER_PUBLIC_URL=$public_url"
      echo "ILER_BRAND=$brand"
      echo "ILER_MONTHLY_COINS=${coins:-0}"
      echo "ILER_MODEL_PRICES='$prices'"
      if [[ -n "$owner_email" ]]; then
        # single quotes: docker compose takes the value literally (the hash is full of '$')
        echo "# owner from the Nox portal: created on the first start while no user exists, ignored afterwards"
        echo "ILER_OWNER_EMAIL=$owner_email"
        echo "ILER_OWNER_NAME='$owner_name'"
        echo "ILER_COMPANY='$company'"
        echo "ILER_OWNER_PASSWORD_HASH='$owner_hash'"
      fi
    } >"$dir/.env"
  )
  [[ -z "$owner_email" ]] || log "owner account: $owner_email (from the Nox portal, same password as the cabinet)"
  local args=(--dir "$dir" --name "$project" --bind 127.0.0.1 --port "$port" --brand "$brand" --yes)
  [[ -n "$public_url" ]] && args+=(--public-url "$public_url")
  [[ -n "$offline" ]] && args+=(--offline "$offline")
  if ! bash "$DEPLOY_DIR/install.sh" "${args[@]}"; then
    if [[ ! -f "$dir/compose.yml" ]]; then
      # install.sh stopped before writing anything but our seed .env: leave no trace
      rm -rf "$dir"
      die "install of client '$name' failed before anything was installed; nothing left behind"
    fi
    die "install of client '$name' failed; inspect $dir, then retry with: $0 remove $name && $0 create $name ..."
  fi
  echo
  ok "client '$name' created"
  echo "Next:"
  echo "  1. LLM keys:  sudo bash $dir/deploy/set-llm-keys.sh --dir $dir"
  echo "  2. Tunnel:    Cloudflare Zero Trust -> Tunnels -> Public Hostname: ${public_url:-<name>.<domain>} -> HTTP 127.0.0.1:$port"
  if [[ -n "$owner_email" ]]; then
    echo "  3. The owner logs in with the e-mail $owner_email and the password of the Nox portal cabinet."
  else
    echo "  3. Send the owner the one-time setup link printed above (privately)."
  fi
  [[ -n "$coins" ]] || warn "no --coins: monthly coin limit is 0 (no limit); set it with set-llm-keys.sh"
}

# ---- stop / start / remove ----------------------------------------------------------------

require_installed() {
  [[ -f "$ILER_DIR_PATH/compose.yml" ]] || die "client '$1' is not installed completely (no compose.yml); remove it and create again"
}

cmd_stop() {
  require_client "${1:-}"
  require_installed "$1"
  dc stop
  ok "client '$1' stopped (data kept); start again with: $0 start $1"
}

cmd_start() {
  require_client "${1:-}"
  require_installed "$1"
  dc up -d --no-build
  if wait_healthy 300 postgres redis iler-core iler-web; then
    ok "client '$1' is running on 127.0.0.1:$(env_get "$ILER_DIR_PATH/.env" ILER_HTTP_PORT)"
  else
    die "client '$1' did not become healthy; see: docker compose -p $(project_of "$1") logs"
  fi
}

cmd_remove() {
  local name="${1:-}" yes=0 project url
  [[ $# -gt 0 ]] && shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --yes|-y) yes=1; shift ;;
      *) die "unknown option for remove: $1" ;;
    esac
  done
  require_client "$name"
  project="$(project_of "$name")"
  url="$(env_get "$ILER_DIR_PATH/.env" ILER_PUBLIC_URL)"
  { have docker && docker info >/dev/null 2>&1; } || die "docker is not available; nothing deleted"
  ASSUME_YES="$yes" confirm "DELETE client '$name' completely: containers, database, WhatsApp session, backups and $ILER_DIR_PATH?" \
    || die "aborted"
  if [[ -f "$ILER_DIR_PATH/compose.yml" ]]; then
    dc down --volumes --remove-orphans || die "compose down failed for $project; nothing deleted"
  fi
  # containers of this project only (also when compose.yml is missing after a failed install)
  docker ps -aq --filter "label=com.docker.compose.project=$project" | xargs -r docker rm -f >/dev/null
  # volumes of this project only (left by an older compose file or a failed install)
  docker volume ls -q --filter "label=com.docker.compose.project=$project" | xargs -r docker volume rm >/dev/null
  docker network ls -q --filter "label=com.docker.compose.project=$project" | xargs -r docker network rm >/dev/null
  case "$ILER_DIR_PATH" in
    "$CLIENTS_DIR"/?*) rm -rf "$ILER_DIR_PATH" ;;
    *) die "refusing to delete unexpected path $ILER_DIR_PATH" ;;
  esac
  ok "client '$name' removed (project $project, $ILER_DIR_PATH)"
  echo "Now delete the client's public hostname (${url:-<name>.<domain>}) in the Cloudflare tunnel."
}

# ---- list ---------------------------------------------------------------------------------

cmd_list() {
  local env dir name project port url version total running healthy status found=0
  printf '%-20s %-6s %-22s %-10s %s\n' NAME PORT STATUS VERSION PUBLIC_URL
  for env in "$CLIENTS_DIR"/*/.env; do
    [[ -f "$env" ]] || continue
    found=1
    dir="$(dirname "$env")"
    name="$(basename "$dir")"
    project="$(env_get "$env" ILER_PROJECT)"
    port="$(env_get "$env" ILER_HTTP_PORT)"
    url="$(env_get "$env" ILER_PUBLIC_URL)"
    version="$(env_get "$env" ILER_VERSION)"
    total=0 running=0 healthy=0
    if [[ -n "$project" ]] && have docker; then
      total="$(docker ps -aq --filter "label=com.docker.compose.project=$project" | wc -l)"
      running="$(docker ps -q --filter "label=com.docker.compose.project=$project" | wc -l)"
      healthy="$(docker ps -q --filter "label=com.docker.compose.project=$project" --filter health=healthy | wc -l)"
    fi
    if ((total == 0)); then
      status="not installed"
    elif ((running == 0)); then
      status="stopped"
    else
      status="running $running/$total ($healthy ok)"
    fi
    printf '%-20s %-6s %-22s %-10s %s\n' "$name" "${port:--}" "$status" "${version:--}" "${url:--}"
  done
  [[ "$found" == "1" ]] || echo "(no clients in $CLIENTS_DIR)"
}

# ---- main ---------------------------------------------------------------------------------

command="${1:-}"
[[ $# -gt 0 ]] && shift
case "$command" in
  -h|--help|help) usage; exit 0 ;;
  "") usage >&2; exit 2 ;;
esac
require_root
case "$command" in
  create) cmd_create "$@" ;;
  stop) cmd_stop "$@" ;;
  start) cmd_start "$@" ;;
  remove) cmd_remove "$@" ;;
  list) cmd_list ;;
  *) usage >&2; die "unknown command: $command" ;;
esac
