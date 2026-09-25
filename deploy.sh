#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"

bash setup.sh || { echo "Setup failed. See the messages above."; exit 1; }

envval() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- || true; }
PORT="$(envval PORT)"; PORT="${PORT:-47823}"
HOST="$(envval HOST)"; HOST="${HOST:-127.0.0.1}"
URL="http://localhost:${PORT}"
mkdir -p data

if [ -f data/server.pid ] && kill -0 "$(cat data/server.pid)" 2>/dev/null; then
  kill "$(cat data/server.pid)" 2>/dev/null
fi
for pid in $(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null); do
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  if [ "$cwd" = "$DIR" ]; then kill "$pid" 2>/dev/null; fi
done
for i in 1 2 3 4 5 6 7 8 9 10; do
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 0.5
done
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is used by another app:"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN | tail -n +2
  echo "Change PORT in $DIR/.env and run this again."
  exit 1
fi

echo "Starting BestTake $(cat VERSION) on port $PORT..."
nohup "$DIR/.venv/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT" > data/server.log 2>&1 &
echo $! > data/server.pid
disown 2>/dev/null || true

UP=0
for i in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then UP=1; break; fi
  if ! kill -0 "$(cat data/server.pid)" 2>/dev/null; then break; fi
  sleep 0.5
done
if [ "$UP" != "1" ]; then
  echo "The server did not start. Last lines of $DIR/data/server.log:"
  tail -n 40 data/server.log
  exit 1
fi
echo "BestTake is running at $URL (log: $DIR/data/server.log)"
command -v open >/dev/null 2>&1 && open "$URL"

echo "Pushing to GitHub..."
if bash publish.sh; then :; else echo "GitHub push skipped. The app is still running at $URL"; fi

if ! grep -qE '^ANTHROPIC_API_KEY=.+' .env; then
  echo
  echo "To build courses, add ANTHROPIC_API_KEY=sk-ant-... to $DIR/.env and run the same one-liner again."
fi
