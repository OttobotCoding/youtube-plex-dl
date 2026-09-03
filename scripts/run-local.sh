#!/usr/bin/env bash
# Run the app directly on your machine, no Docker. Useful for poking at the
# code with fast reloads; the Docker path is what actually ships to Unraid.
#
#   ./scripts/run-local.sh            # http://127.0.0.1:8080
#   PORT=9000 ./scripts/run-local.sh  # different port
#
# Requires: python3.10+ and ffmpeg on PATH.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is not installed — yt-dlp cannot merge video+audio without it."
  echo "  Debian/Ubuntu : sudo apt install ffmpeg"
  echo "  Fedora        : sudo dnf install ffmpeg"
  echo "  macOS         : brew install ffmpeg"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "→ creating .venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "→ installing dependencies"
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Local test dirs. These mirror the container's /downloads and /config, so the
# folder tree you get here is exactly the tree Plex will see on Unraid.
export OUTPUT_DIR="${OUTPUT_DIR:-$ROOT/data/downloads}"
export CONFIG_DIR="${CONFIG_DIR:-$ROOT/data/config}"
mkdir -p "$OUTPUT_DIR" "$CONFIG_DIR"

# Keep test downloads small and fast. Override any of these inline.
export MAX_HEIGHT="${MAX_HEIGHT:-360}"
export SEASON_MODE="${SEASON_MODE:-single}"
export CONCURRENCY="${CONCURRENCY:-2}"
export PORT="${PORT:-8080}"

# Vendor htmx once so the UI works offline (harmless if it fails).
if [ ! -s app/static/htmx.min.js ] || [ "$(wc -c < app/static/htmx.min.js)" -lt 1000 ]; then
  curl -fsSL "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js" \
    -o app/static/htmx.min.js 2>/dev/null \
    || echo "  (couldn't vendor htmx — the page falls back to a CDN)"
fi

cat <<EOF

─────────────────────────────────────────────
  library : $OUTPUT_DIR
  config  : $CONFIG_DIR
  quality : max ${MAX_HEIGHT}p (test setting)
  open    : http://127.0.0.1:${PORT}
─────────────────────────────────────────────

EOF

exec python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --reload
