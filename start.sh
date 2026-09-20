#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ ! -d "venv" ]; then
  echo "Creating venv..."
  python3 -m venv venv
fi

# shellcheck source=/dev/null
source venv/bin/activate

echo "Installing dependencies..."
pip install -q -r requirements.txt || pip install -q fastapi uvicorn httpx beautifulsoup4 lxml pydantic

echo "Starting backend + frontend (static from /frontend)..."
nohup venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 > backend.log 2>&1 &

sleep 2

echo "✅ Приложение: http://localhost:8000/"
echo "✅ API docs: http://localhost:8000/docs"

open http://localhost:8000/
