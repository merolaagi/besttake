#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "BestTake needs Python 3.10 or newer. Install it with: brew install python@3.12"
  exit 1
fi

if ! command -v deno >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "Installing Deno (yt-dlp uses it to read YouTube)..."
    brew install deno || echo "Deno install failed. YouTube extraction may be limited."
  else
    echo "Tip: install Deno (https://deno.com) for full YouTube support."
  fi
fi

echo "Setting up Python environment with $PY..."
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q
.venv/bin/pip install -U "yt-dlp[default]" -q
mkdir -p data

if [ ! -f .env ]; then
  cp .env.example .env
  if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    sed -i '' "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}|" .env 2>/dev/null \
      || sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}|" .env
  fi
fi

if grep -qE '^PORT=8420$' .env; then
  sed -i '' 's/^PORT=8420$/PORT=47823/' .env 2>/dev/null || sed -i 's/^PORT=8420$/PORT=47823/' .env
  echo "Moved BestTake to port 47823."
fi

if ! grep -qE '^ANTHROPIC_API_KEY=.+' .env; then
  echo "Note: no ANTHROPIC_API_KEY in $(pwd)/.env yet. The app will run, but building courses needs the key."
fi
echo "Setup complete."
