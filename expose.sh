#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
HOST_NAME="${1:-besttake.fueldeskpro.com}"
CFG="${CLOUDFLARED_CONFIG:-/etc/cloudflared/config.yml}"
SUDO="sudo"; [ "$(id -u)" = "0" ] && SUDO=""
envval() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- || true; }
PORT="$(envval PORT)"; PORT="${PORT:-47823}"

[ -f "$CFG" ] || { echo "Cloudflare config not found at $CFG. Set CLOUDFLARED_CONFIG=/path/to/config.yml and run again."; exit 1; }

if grep -qE "hostname:[[:space:]]*${HOST_NAME}[[:space:]]*$" "$CFG"; then
  echo "$HOST_NAME is already in $CFG"
else
  BACKUP="$CFG.bak.$(date +%Y%m%d%H%M%S)"
  TMP="$(mktemp)"
  python3 - "$CFG" "$HOST_NAME" "$PORT" "$TMP" <<'PY'
import re, sys
cfg, host, port, out = sys.argv[1:5]
lines = open(cfg).read().splitlines(keepends=True)
catch = next((i for i, l in enumerate(lines) if re.match(r"^\s*-\s*service:\s*http_status:404", l)), None)
if catch is None:
    sys.exit("No catch-all rule (- service: http_status:404) found in the ingress list; add the entry by hand.")
indent = re.match(r"^(\s*)-", lines[catch]).group(1)
entry = [f"{indent}- hostname: {host}\n", f"{indent}  service: http://localhost:{port}\n"]
open(out, "w").write("".join(lines[:catch] + entry + lines[catch:]))
PY
  $SUDO cp "$CFG" "$BACKUP"
  $SUDO cp "$TMP" "$CFG"
  rm -f "$TMP"
  echo "Added $HOST_NAME -> http://localhost:$PORT to $CFG (backup: $BACKUP)"
  if command -v cloudflared >/dev/null 2>&1; then
    if ! $SUDO cloudflared tunnel --config "$CFG" ingress validate >/dev/null 2>&1; then
      $SUDO cp "$BACKUP" "$CFG"
      echo "The new config didn't validate, so the backup was restored. Nothing changed."
      exit 1
    fi
  fi
  if $SUDO launchctl kickstart -k system/com.cloudflare.cloudflared 2>/dev/null; then
    echo "Restarted the cloudflared daemon."
  else
    echo "Restart cloudflared yourself so it picks up the new hostname."
  fi
fi

if grep -qE '^COOKIE_SECURE=' .env; then
  sed -i '' 's/^COOKIE_SECURE=.*/COOKIE_SECURE=1/' .env 2>/dev/null || sed -i 's/^COOKIE_SECURE=.*/COOKIE_SECURE=1/' .env
else
  echo "COOKIE_SECURE=1" >> .env
fi

if [ "$(uname)" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.besttake.app.plist"
  mkdir -p "$HOME/Library/LaunchAgents" data
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.besttake.app</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DIR/run.sh</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/data/server.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/server.log</string>
</dict></plist>
PL
  if [ -f data/server.pid ] && kill -0 "$(cat data/server.pid)" 2>/dev/null; then kill "$(cat data/server.pid)"; rm -f data/server.pid; sleep 1; fi
  launchctl bootout "gui/$(id -u)/com.besttake.app" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "BestTake now runs as a background service that restarts on crash and at login."
fi

for i in $(seq 1 40); do curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1 && echo "BestTake is up on port $PORT." || echo "BestTake isn't answering yet. Check $DIR/data/server.log"

TUNNEL="$(grep -E '^tunnel:' "$CFG" | awk '{print $2}' | tr -d '"' || true)"
echo
echo "Now add this DNS record in the Cloudflare dashboard for fueldeskpro.com:"
echo "  Type: CNAME   Name: ${HOST_NAME%%.*}   Target: ${TUNNEL:-<tunnel-id>}.cfargotunnel.com   Proxy: on"
echo "Then open https://$HOST_NAME"
