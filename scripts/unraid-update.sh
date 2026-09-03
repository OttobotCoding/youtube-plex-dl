#!/bin/bash
# ─── Unraid auto-update for youtube-plex-dl ───────────────────────────────
#
# Pulls the newest image from GHCR and recreates the container only if the
# image actually changed AND nothing is mid-download. Safe to run on a cron.
#
# Install: Unraid → Settings → User Scripts → Add New Script, paste this in,
# then set a schedule (daily at 4am is a sensible default).
#
#   Flags:
#     --force   update even if downloads are in progress
#     --dry-run report what would happen, change nothing
#
# Why not just `docker compose pull && up -d`? Recreating the container
# mid-download kills the transfer. The app marks interrupted items failed on
# boot so nothing is lost, but you'd have to re-queue them by hand.

set -euo pipefail

# ── configure ────────────────────────────────────────────────────────────
# Where docker-compose.yml and .env live on the array.
# Docker Compose Manager puts projects here:
#   /boot/config/plugins/compose.manager/projects/youtube-plex-dl
STACK_DIR="${STACK_DIR:-/boot/config/plugins/compose.manager/projects/youtube-plex-dl}"
APP_URL="${APP_URL:-http://127.0.0.1:8080}"
SERVICE="youtube-plex-dl"
# ─────────────────────────────────────────────────────────────────────────

FORCE=0
DRY=0
for arg in "$@"; do
  case "$arg" in
    --force)   FORCE=1 ;;
    --dry-run) DRY=1 ;;
    *) echo "unknown flag: $arg"; exit 2 ;;
  esac
done

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

if [ ! -f "$STACK_DIR/docker-compose.yml" ]; then
  log "ERROR: no docker-compose.yml in $STACK_DIR"
  log "       Set STACK_DIR at the top of this script to the right folder."
  exit 1
fi
cd "$STACK_DIR"

# Compose v2 is `docker compose`; older installs have `docker-compose`.
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  log "ERROR: neither 'docker compose' nor 'docker-compose' is available."
  exit 1
fi

# ── 1. don't interrupt an active download ────────────────────────────────
if [ "$FORCE" -eq 0 ]; then
  # /api/status returns "active":[] when the queue is idle. No jq needed.
  if status="$(curl -fsS --max-time 10 "$APP_URL/api/status" 2>/dev/null)"; then
    if ! printf '%s' "$status" | grep -q '"active":\[\]'; then
      log "Downloads in progress — skipping this run. (--force to override)"
      exit 0
    fi
    log "Queue is idle."
  else
    log "Could not reach $APP_URL/api/status — container may be down; continuing."
  fi
fi

# ── 2. pull ──────────────────────────────────────────────────────────────
before="$(docker inspect --format '{{.Image}}' "$SERVICE" 2>/dev/null || echo none)"

log "Pulling newest image…"
if [ "$DRY" -eq 1 ]; then
  log "DRY RUN: would run '$DC pull $SERVICE'"
else
  $DC pull "$SERVICE"
fi

# ── 3. recreate only if the image changed ────────────────────────────────
image_ref="$($DC config --images "$SERVICE" 2>/dev/null | head -n1 || true)"
after="$(docker image inspect --format '{{.Id}}' "$image_ref" 2>/dev/null || echo none)"

if [ "$before" = "$after" ] && [ "$before" != "none" ]; then
  log "Already on the newest image ($image_ref) — nothing to do."
  exit 0
fi

log "New image available: $image_ref"
if [ "$DRY" -eq 1 ]; then
  log "DRY RUN: would run '$DC up -d' and prune the old image"
  exit 0
fi

$DC up -d
log "Container recreated."

# ── 4. tidy up ───────────────────────────────────────────────────────────
# Dangling images only — this will not touch images other containers use.
docker image prune -f >/dev/null 2>&1 || true

# ── 5. confirm it came back ──────────────────────────────────────────────
for _ in $(seq 1 30); do
  if curl -fsS --max-time 5 "$APP_URL/healthz" >/dev/null 2>&1; then
    log "Healthy. Update complete."
    exit 0
  fi
  sleep 2
done

log "WARNING: container did not report healthy within 60s."
log "         Check:  docker logs --tail 50 $SERVICE"
exit 1
