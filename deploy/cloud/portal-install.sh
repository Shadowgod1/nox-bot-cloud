#!/usr/bin/env bash
# Nox client portal (bot.noxsw.kz): install or update on the cloud server (run as root, idempotent).
#
#   sudo bash deploy/cloud/portal-install.sh [--dir /opt/nox-portal] [--no-systemd] [--no-start]
#
# From an iler-bot checkout or release bundle (needs portal/ and deploy/cloud/). It:
#   1. copies portal/ (compose.yml, backend/, web/) to --dir (default /opt/nox-portal);
#   2. creates /etc/nox-portal (root-only): provisioner key pair (RSA-3072) and provisioner.env with a
#      fresh shared token; the portal gets only the public key and the token's sha256;
#   3. creates <dir>/.env from .env.example on the first run, generating NOX_SECRET_KEY and the DB password
#      (existing values are never replaced);
#   4. builds the images locally and starts docker compose project "nox-portal" (web on 127.0.0.1:8100);
#   5. installs nox-provisioner.service + .timer; the timer is enabled once CF_API_TOKEN is filled in.
# Runbook (SMTP, Cloudflare, staff accounts, backups): deploy/cloud/PORTAL.md.
# shellcheck source-path=SCRIPTDIR
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"
# shellcheck source=../lib/common.sh
. "$DEPLOY_DIR/lib/common.sh"
# shellcheck source=../lib/env.sh
. "$DEPLOY_DIR/lib/env.sh"

PORTAL_DIR="/opt/nox-portal"
ETC_DIR="${NOX_ETC_DIR:-/etc/nox-portal}"
SYSTEMD_DIR="${NOX_SYSTEMD_DIR:-/etc/systemd/system}"
PROJECT="nox-portal"
INSTALL_SYSTEMD=1
START=1

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) PORTAL_DIR="${2:-}"; shift 2 ;;
    --dir=*) PORTAL_DIR="${1#*=}"; shift ;;
    --no-systemd) INSTALL_SYSTEMD=0; shift ;;
    --no-start) START=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done
PORTAL_DIR="${PORTAL_DIR%/}"
[[ "$PORTAL_DIR" == /?* ]] || die "--dir must be an absolute path other than /"

require_root
for tool in docker openssl python3 tar sha256sum; do
  have "$tool" || die "$tool is required"
done
docker compose version >/dev/null 2>&1 || die "docker compose plugin is required"
[[ -f "$SRC_ROOT/portal/compose.yml" && -f "$SCRIPT_DIR/nox-provisioner.py" ]] \
  || die "run from an iler-bot checkout or bundle (portal/compose.yml not found in $SRC_ROOT)"

# ---- 1. files -------------------------------------------------------------------------------

log "copying the portal to $PORTAL_DIR"
mkdir -p "$PORTAL_DIR" "$PORTAL_DIR/bin"
# build contexts are replaced as a whole so removed files do not linger
rm -rf "$PORTAL_DIR/backend" "$PORTAL_DIR/web"
tar -C "$SRC_ROOT/portal" --exclude=node_modules --exclude=dist --exclude='*.egg-info' --exclude=__pycache__ \
    --exclude=.pytest_cache --exclude=tests -cf - compose.yml .env.example backend web | tar -C "$PORTAL_DIR" -xf -
install -m 0755 -o root -g root "$SCRIPT_DIR/nox-provisioner.py" "$PORTAL_DIR/bin/nox-provisioner.py"
install -m 0644 -o root -g root "$SCRIPT_DIR/PORTAL.md" "$PORTAL_DIR/PORTAL.md"

# ---- 2. provisioner secrets (root-only) -----------------------------------------------------

install -d -m 0700 -o root -g root "$ETC_DIR"
KEY_FILE="$ETC_DIR/provisioner-key.pem"
if [[ ! -s "$KEY_FILE" ]]; then
  log "generating the provisioner key pair (RSA-3072)"
  (umask 077 && openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$KEY_FILE" 2>/dev/null)
fi
chmod 600 "$KEY_FILE"
openssl pkey -in "$KEY_FILE" -pubout -out "$PORTAL_DIR/provisioner-pub.pem" 2>/dev/null
chmod 644 "$PORTAL_DIR/provisioner-pub.pem"

PROV_ENV="$ETC_DIR/provisioner.env"
if [[ ! -f "$PROV_ENV" ]]; then
  (umask 077 && cp "$SCRIPT_DIR/provisioner.env.example" "$PROV_ENV")
  log "created $PROV_ENV"
else
  # keys introduced by a newer release (NOX_GATEWAY_URL, ...) are appended, existing values stay
  while IFS= read -r line; do
    [[ "$line" =~ ^([A-Z0-9_]+)= ]] || continue
    grep -q "^${BASH_REMATCH[1]}=" "$PROV_ENV" || { printf '%s\n' "$line" >>"$PROV_ENV"; log "added provisioner setting ${BASH_REMATCH[1]}"; }
  done <"$SCRIPT_DIR/provisioner.env.example"
fi
token="$(env_get "$PROV_ENV" NOX_PROVISIONER_TOKEN)"
if [[ -z "$token" || "$token" == "__GENERATE__" ]]; then
  token="$(openssl rand -hex 32)"
  env_set "$PROV_ENV" NOX_PROVISIONER_TOKEN "$token"
  log "generated the provisioner token"
fi
env_set "$PROV_ENV" NOX_PROVISIONER_KEY_FILE "$KEY_FILE"
chmod 600 "$PROV_ENV"
chown root:root "$PROV_ENV"
token_sha="$(printf '%s' "$token" | sha256sum | awk '{ print $1 }')"
unset token

# ---- 3. portal .env -------------------------------------------------------------------------

ENV_FILE="$PORTAL_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  (umask 077 && cp "$PORTAL_DIR/.env.example" "$ENV_FILE")
  log "created $ENV_FILE"
else
  # keys introduced by a newer release are appended, existing values stay
  while IFS= read -r line; do
    [[ "$line" =~ ^([A-Z0-9_]+)= ]] || continue
    grep -q "^${BASH_REMATCH[1]}=" "$ENV_FILE" || { printf '%s\n' "$line" >>"$ENV_FILE"; log "added setting ${BASH_REMATCH[1]}"; }
  done <"$PORTAL_DIR/.env.example"
fi
if [[ "$(env_get "$ENV_FILE" NOX_SECRET_KEY)" == "__GENERATE__" ]]; then
  env_set "$ENV_FILE" NOX_SECRET_KEY "$(openssl rand -base64 32)"
fi
if [[ "$(env_get "$ENV_FILE" NOX_DB_PASSWORD)" == "__GENERATE__" ]]; then
  env_set "$ENV_FILE" NOX_DB_PASSWORD "$(openssl rand -hex 24)"
fi
env_set "$ENV_FILE" NOX_PROVISIONER_TOKEN_SHA256 "$token_sha"
chmod 600 "$ENV_FILE"
chown root:root "$ENV_FILE"

if [[ "$(env_get "$ENV_FILE" NOX_EMAIL_MODE)" == "smtp" ]]; then
  smtp_host="$(env_get "$ENV_FILE" NOX_SMTP_HOST)"
  if [[ -z "$smtp_host" || "$smtp_host" == *example.kz || -z "$(env_get "$ENV_FILE" NOX_SMTP_PASSWORD)" ]]; then
    warn "SMTP is not configured yet: edit NOX_SMTP_* in $ENV_FILE (see PORTAL.md), then re-run this script"
  fi
elif [[ "$(env_get "$ENV_FILE" NOX_EMAIL_MODE)" == "log" ]]; then
  warn "NOX_EMAIL_MODE=log: codes and setup links are printed to the log. Development only!"
fi
if [[ -z "$(env_get "$ENV_FILE" NOX_UPSTREAM_ANTHROPIC_KEY)$(env_get "$ENV_FILE" NOX_UPSTREAM_OPENAI_KEY)$(env_get "$ENV_FILE" NOX_UPSTREAM_GEMINI_KEY)" ]]; then
  warn "LLM gateway has no provider key: fill in NOX_UPSTREAM_*_KEY in $ENV_FILE (see PORTAL.md), then re-run this script"
fi

# ---- 4. containers --------------------------------------------------------------------------

dcp() {
  docker compose -p "$PROJECT" --project-directory "$PORTAL_DIR" -f "$PORTAL_DIR/compose.yml" --env-file "$ENV_FILE" "$@"
}

if [[ "$START" == "1" ]]; then
  log "building images"
  dcp build --pull
  log "starting $PROJECT"
  dcp up -d --remove-orphans
  deadline=$((SECONDS + 180))
  while :; do
    pending=""
    for svc in db api web; do
      id="$(dcp ps -q "$svc" 2>/dev/null | head -n1)"
      state="$( [[ -n "$id" ]] && docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || echo missing)"
      [[ "$state" == "healthy" ]] || pending="$pending $svc($state)"
    done
    [[ -z "$pending" ]] && break
    ((SECONDS < deadline)) || die "not healthy after 180s:$pending; see: docker compose -p $PROJECT logs"
    sleep 3
  done
  ok "portal is running on 127.0.0.1:$(env_get "$ENV_FILE" NOX_HTTP_PORT) (internal API 127.0.0.1:$(env_get "$ENV_FILE" NOX_INTERNAL_PORT))"
fi

# ---- 5. provisioner service -----------------------------------------------------------------

if [[ "$INSTALL_SYSTEMD" == "1" ]]; then
  install -m 0644 -o root -g root "$SCRIPT_DIR/systemd/nox-provisioner.service" "$SYSTEMD_DIR/nox-provisioner.service"
  install -m 0644 -o root -g root "$SCRIPT_DIR/systemd/nox-provisioner.timer" "$SYSTEMD_DIR/nox-provisioner.timer"
  if [[ "$PORTAL_DIR" != "/opt/nox-portal" ]]; then
    sed -i "s#/opt/nox-portal/#$PORTAL_DIR/#g" "$SYSTEMD_DIR/nox-provisioner.service"
  fi
  if have systemctl && [[ -d /run/systemd/system ]]; then
    systemctl daemon-reload
    if [[ -n "$(env_get "$PROV_ENV" CF_API_TOKEN)" ]]; then
      systemctl enable --now nox-provisioner.timer >/dev/null
      ok "nox-provisioner.timer enabled (every 30 s)"
    else
      warn "fill in CF_* in $PROV_ENV, then: systemctl enable --now nox-provisioner.timer"
    fi
  else
    warn "systemd is not running: units copied to $SYSTEMD_DIR but not enabled"
  fi
fi

echo
ok "Nox portal installed in $PORTAL_DIR"
echo "Next (details in $PORTAL_DIR/PORTAL.md):"
echo "  1. SMTP: NOX_SMTP_* in $ENV_FILE, then re-run this script"
echo "  2. Provisioner: CF_* (and NOX_GATEWAY_URL if not https://bot.noxsw.kz) in $PROV_ENV; check with"
echo "     sudo bash -c 'set -a; . $PROV_ENV; python3 $PORTAL_DIR/bin/nox-provisioner.py check'"
echo "  3. Tunnel route: bot.noxsw.kz -> HTTP 127.0.0.1:$(env_get "$ENV_FILE" NOX_HTTP_PORT)"
echo "  4. Staff account: cd $PORTAL_DIR && docker compose exec api python manage.py create-staff --email <e-mail>"
