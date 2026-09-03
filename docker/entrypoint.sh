#!/usr/bin/env bash
set -euo pipefail

PUID="${PUID:-99}"
PGID="${PGID:-100}"
UMASK="${UMASK:-002}"
PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"
OUTPUT_DIR="${OUTPUT_DIR:-/downloads}"
CONFIG_DIR="${CONFIG_DIR:-/config}"

umask "${UMASK}"

mkdir -p "${OUTPUT_DIR}" "${CONFIG_DIR}"

echo "─────────────────────────────────────────────"
echo " youtube-plex-dl"
echo "  user       : ${PUID}:${PGID}   umask ${UMASK}"
echo "  library    : ${OUTPUT_DIR}"
echo "  config     : ${CONFIG_DIR}"
echo "  listening  : http://${HOST}:${PORT}"
echo "  yt-dlp     : $(python -c 'import yt_dlp; print(yt_dlp.version.__version__)' 2>/dev/null || echo unknown)"
echo "─────────────────────────────────────────────"

run_app() {
  exec python -m uvicorn app.main:app \
      --host "${HOST}" --port "${PORT}" \
      --proxy-headers --forwarded-allow-ips='*' \
      --log-level "${LOG_LEVEL:-info}"
}

# Running as root: create/reuse a matching user and drop privileges.
if [ "$(id -u)" = "0" ] && [ "${PUID}" != "0" ]; then
  if ! getent group "${PGID}" >/dev/null 2>&1; then
    groupadd -g "${PGID}" appgroup
  fi
  GROUP_NAME="$(getent group "${PGID}" | cut -d: -f1)"

  if ! getent passwd "${PUID}" >/dev/null 2>&1; then
    useradd -u "${PUID}" -g "${PGID}" -M -s /usr/sbin/nologin appuser
  fi
  USER_NAME="$(getent passwd "${PUID}" | cut -d: -f1)"

  # Only the config dir is chowned recursively (it is small). The library gets
  # a top-level chown so new channel folders inherit the right owner; existing
  # media is left untouched on purpose.
  chown -R "${PUID}:${PGID}" "${CONFIG_DIR}" 2>/dev/null || true
  chown "${PUID}:${PGID}" "${OUTPUT_DIR}" 2>/dev/null || true

  case "${1:-serve}" in
    serve) exec gosu "${USER_NAME}:${GROUP_NAME}" /entrypoint.sh serve-as-user ;;
    *)     exec gosu "${USER_NAME}:${GROUP_NAME}" "$@" ;;
  esac
fi

case "${1:-serve}" in
  serve|serve-as-user) run_app ;;
  *) exec "$@" ;;
esac
