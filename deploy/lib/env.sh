#!/usr/bin/env bash
# .env generation helpers (sourced). Never overwrites an existing secret.

# random_b64 <bytes>: base64 of N random bytes, single line
random_b64() {
  if have openssl; then
    openssl rand -base64 "$1" | tr -d '\n'
  else
    head -c "$1" /dev/urandom | base64 | tr -d '\n'
  fi
}

# random_hex <bytes>: URL-safe secret (used inside connection URIs)
random_hex() {
  if have openssl; then
    openssl rand -hex "$1"
  else
    head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

# env_get <file> <key>
env_get() {
  [[ -f "$1" ]] || return 0
  awk -v k="$2" 'index($0, k "=") == 1 { print substr($0, length(k) + 2); exit }' "$1"
}

# env_set <file> <key> <value>: replace or append, value taken literally
env_set() {
  local file="$1" key="$2" tmp
  tmp="$(mktemp "${file}.XXXXXX")"
  K="$key" V="$3" awk '
    BEGIN { k = ENVIRON["K"]; v = ENVIRON["V"]; done = 0 }
    index($0, k "=") == 1 { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$file" >"$tmp"
  cat "$tmp" >"$file"
  rm -f "$tmp"
}

generate_secret_for() {
  case "$1" in
    ILER_SECRET_KEY) random_b64 32 ;;
    # everything else (DB passwords, ILER_REDIS_PASSWORD, ILER_SETUP_TOKEN, ...): hex, URL-safe
    *) random_hex 24 ;;
  esac
}

# render_env <example> <target>: create target from example on first run; on later runs add
# keys introduced by a newer release. Replaces __GENERATE__ placeholders with fresh secrets.
render_env() {
  local example="$1" target="$2" line key value
  if [[ ! -f "$target" ]]; then
    (umask 077 && cp "$example" "$target")
    log "created $target"
  else
    while IFS= read -r line; do
      [[ "$line" =~ ^([A-Z0-9_]+)= ]] || continue
      key="${BASH_REMATCH[1]}"
      if ! grep -q "^${key}=" "$target"; then
        printf '%s\n' "$line" >>"$target"
        log "added new setting $key"
      fi
    done <"$example"
  fi
  while IFS= read -r key; do
    value="$(generate_secret_for "$key")"
    env_set "$target" "$key" "$value"
  done < <(awk -F= '$2 == "__GENERATE__" { print $1 }' "$target")
  chmod 600 "$target"
  chown root:root "$target" 2>/dev/null || true
}
