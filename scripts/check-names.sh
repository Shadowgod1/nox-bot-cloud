#!/usr/bin/env bash
# Fails if any forbidden name appears in the repository.
# The list itself is never committed: read from $ILER_FORBIDDEN_NAMES (newline/comma separated)
# or from the untracked file .forbidden-names in the repo root.
set -euo pipefail
cd "$(dirname "$0")/.."

list="$(mktemp)"
trap 'rm -f "$list"' EXIT
if [[ -n "${ILER_FORBIDDEN_NAMES:-}" ]]; then
  printf '%s\n' "$ILER_FORBIDDEN_NAMES" | tr ',' '\n' | sed '/^\s*$/d' >"$list"
elif [[ -f .forbidden-names ]]; then
  sed '/^\s*$/d' .forbidden-names >"$list"
else
  echo "check-names: no list (set ILER_FORBIDDEN_NAMES or create .forbidden-names)" >&2
  exit 2
fi

if grep -rniF -f "$list" . \
    --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=dist \
    --exclude-dir=__pycache__ --exclude=.forbidden-names --exclude='*.lock' --exclude=package-lock.json; then
  echo "check-names: forbidden names found" >&2
  exit 1
fi
echo "check-names: clean"
