#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
envval() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- || true; }
PORT="${PORT:-$(envval PORT)}"; PORT="${PORT:-47823}"
HOST="${HOST:-$(envval HOST)}"; HOST="${HOST:-127.0.0.1}"
if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use by:"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN | tail -n +2
  echo "Set a different PORT in .env, or stop that process, then run ./run.sh again."
  exit 1
fi
echo "BestTake $(cat VERSION) is running at http://localhost:${PORT}"
echo "The first account you create becomes the owner account (Pro plan)."
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
