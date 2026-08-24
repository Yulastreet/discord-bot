#!/usr/bin/env bash
set -euo pipefail

NAME="${BGUTIL_CONTAINER_NAME:-bgutil-provider}"
IMAGE="${BGUTIL_IMAGE:-brainicism/bgutil-ytdlp-pot-provider}"
PORT="${BGUTIL_PORT:-4416}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[bgutil] docker introuvable. Installe Docker ou lance le provider bgutil en mode natif."
  exit 1
fi

if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "[bgutil] container deja en cours: $NAME"
elif docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "[bgutil] redemarrage du container existant: $NAME"
  docker start "$NAME" >/dev/null
else
  echo "[bgutil] creation du container: $NAME"
  docker run \
    --name "$NAME" \
    -d \
    --restart unless-stopped \
    --init \
    -p "127.0.0.1:${PORT}:4416" \
    "$IMAGE" >/dev/null
fi

if curl -fsS "http://127.0.0.1:${PORT}/ping" >/dev/null; then
  echo "[bgutil] OK http://127.0.0.1:${PORT}/ping"
else
  echo "[bgutil] container lance, mais /ping ne repond pas encore."
  echo "[bgutil] Verifie avec: docker logs $NAME --tail 80"
  exit 1
fi
