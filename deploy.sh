#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BRANCH="${1:-master}"

cd "$APP_DIR"

echo "[deploy] app dir: $APP_DIR"
echo "[deploy] branch: $BRANCH"

if [[ -d .git ]]; then
  echo "[deploy] fetching latest code..."
  git fetch --all --prune
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
fi

echo "[deploy] starting docker compose..."
docker compose up -d --build

echo "[deploy] service status:"
docker compose ps

echo "[deploy] backend logs (tail 80):"
docker compose logs --tail=80 backend

echo "[deploy] done"

