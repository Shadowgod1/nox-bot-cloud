#!/usr/bin/env bash
# Builds the release bundles for a version:
#   dist/iler-bot.tar.gz                    compose.yml + deploy/ (online install, used by curl | bash)
#   dist/iler-bot-<ver>-offline.tar.gz      the same + images.tar (docker save of every image,
#                                           including the pinned third-party ones)
#   dist/stable.env                         channel file for the updater
#
# Usage: scripts/build-offline-bundle.sh <version> [--no-pull]
#   --no-pull   use our own images already present locally (built by `docker compose build` in CI);
#               pinned third-party images are still pulled
# Env: ILER_IMAGE_OWNER (default shadowgod1)
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-}"
PULL=1
[[ "${2:-}" == "--no-pull" ]] && PULL=0
[[ "$VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]] || { echo "usage: $0 <version> [--no-pull]" >&2; exit 2; }

export ILER_VERSION="$VERSION"
export ILER_IMAGE_OWNER="${ILER_IMAGE_OWNER:-shadowgod1}"
ILER_IMAGE_OWNER="$(printf '%s' "$ILER_IMAGE_OWNER" | tr '[:upper:]' '[:lower:]')"

mapfile -t IMAGES < <(docker compose -f compose.yml --env-file deploy/.env.example config --images | sort -u)
[[ ${#IMAGES[@]} -gt 0 ]] || { echo "no images found in compose.yml" >&2; exit 1; }

# Digest-pinned refs (repo:tag@sha256:...) are saved under repo:tag: `docker save` of a digest ref
# drops the name, while a saved repo:tag keeps its digest on load (containerd image store), so
# compose can still resolve repo:tag@sha256:... offline.
SAVE_REFS=()
for img in "${IMAGES[@]}"; do
  # --no-pull skips only our own images (built locally); third-party ones are always pulled
  if [[ "$PULL" == "1" || "$img" != ghcr.io/"$ILER_IMAGE_OWNER"/* ]]; then
    echo "==> pull $img"
    docker pull --quiet "$img" >/dev/null
  fi
  if [[ "$img" == *@sha256:* ]]; then
    docker tag "$img" "${img%@*}"
    SAVE_REFS+=("${img%@*}")
  else
    SAVE_REFS+=("$img")
  fi
done

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
root="$stage/iler-bot-$VERSION"
mkdir -p "$root" dist

cp compose.yml "$root/"
cp -a deploy "$root/"
printf 'ILER_VERSION=%s\n' "$VERSION" >"$root/deploy/channel/stable.env"
sed -i "s/^ILER_VERSION=.*/ILER_VERSION=$VERSION/; s/^ILER_IMAGE_OWNER=.*/ILER_IMAGE_OWNER=$ILER_IMAGE_OWNER/" "$root/deploy/.env.example"
printf '%s\n' "$VERSION" >"$root/VERSION"
cp "$root/deploy/channel/stable.env" dist/stable.env

echo "==> online bundle"
tar -C "$stage" -czf dist/iler-bot.tar.gz "iler-bot-$VERSION"

echo "==> docker save (${#IMAGES[@]} images)"
printf '  %s\n' "${IMAGES[@]}"
docker save -o "$root/images.tar" "${SAVE_REFS[@]}"
printf '%s\n' "${IMAGES[@]}" >"$root/images.txt"

echo "==> offline bundle"
tar -C "$stage" -czf "dist/iler-bot-$VERSION-offline.tar.gz" "iler-bot-$VERSION"
( cd dist && sha256sum iler-bot.tar.gz "iler-bot-$VERSION-offline.tar.gz" stable.env >SHA256SUMS )
ls -lh dist/
