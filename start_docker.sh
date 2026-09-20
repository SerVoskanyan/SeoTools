#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

IMAGE="seo-tools-backend"
CONTAINER="seo-tools-container"
BACKEND_PORT=8000
FRONTEND_PORT=5500
PID_DIR="$ROOT/.run"
LOG_DIR="$ROOT/.logs"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"

mkdir -p "$PID_DIR" "$LOG_DIR"

stop_frontend() {
  if [[ -f "$FRONTEND_PID_FILE" ]]; then
    local pid
    pid="$(cat "$FRONTEND_PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$FRONTEND_PID_FILE"
  fi
  local pids
  pids="$(lsof -ti tcp:"$FRONTEND_PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    kill $pids 2>/dev/null || true
  fi
}

free_backend_port() {
  local pids
  pids="$(lsof -ti tcp:"$BACKEND_PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "Освобождаю порт $BACKEND_PORT для Docker…"
    kill $pids 2>/dev/null || true
    sleep 0.5
  fi
}

echo "Сборка Docker-образа…"
docker build -t "$IMAGE" .

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "Перезапуск контейнера $CONTAINER…"
  docker rm -f "$CONTAINER" >/dev/null
fi

free_backend_port

echo "Запуск контейнера…"
docker run -d -p "$BACKEND_PORT:$BACKEND_PORT" --name "$CONTAINER" "$IMAGE"

stop_frontend
echo "Запускаю статику на http://localhost:$FRONTEND_PORT …"
nohup python3 -m http.server "$FRONTEND_PORT" --bind 127.0.0.1 --directory frontend \
  >>"$LOG_DIR/frontend.log" 2>&1 &
echo $! >"$FRONTEND_PID_FILE"

for _ in {1..40}; do
  if curl -sf "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null; then
    break
  fi
  sleep 0.25
done

docker ps --filter "name=$CONTAINER"

open "http://localhost:$FRONTEND_PORT/index.html"
echo "Готово: Docker API http://localhost:$BACKEND_PORT · UI http://localhost:$FRONTEND_PORT/index.html"
