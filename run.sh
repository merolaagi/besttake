#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
envval() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- || true; }
PORT="${PORT:-$(envval PORT)}"; PORT="${PORT:-8420}"
HOST="${HOST:-$(envval HOST)}"; HOST="${HOST:-127.0.0.1}"
echo "BestTake $(cat VERSION) is running at http://localhost:${PORT}"
echo "The first account you create becomes the owner account (Pro plan)."
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT"
