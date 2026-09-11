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

# ── scratch space ────────────────────────────────────────────────────────
# yt-dlp writes a probe file into the CURRENT WORKING DIRECTORY when checking
# formats (it passes dir='.' once windowsfilenames is on). That directory is
# /app, root-owned from the image build, so as PUID you get:
#     [Errno 13] Permission denied: '/app/tmpXXXXXXXX.tmp'
# Give the process a writable home to sit in, and somewhere for caches to go.
TEMP_DIR="${TEMP_DIR:-${CONFIG_DIR}/tmp}"
mkdir -p "${TEMP_DIR}/cache"
chmod 1777 /tmp 2>/dev/null || true
export TEMP_DIR
export TMPDIR="${TEMP_DIR}"
export HOME="${TEMP_DIR}"              # the app user is created with no home
export XDG_CACHE_HOME="${TEMP_DIR}/cache"

echo "─────────────────────────────────────────────"
echo " youtube-plex-dl"
echo "  user       : ${PUID}:${PGID}   umask ${UMASK}"
echo "  library    : ${OUTPUT_DIR}"
echo "  config     : ${CONFIG_DIR}"
echo "  listening  : http://${HOST}:${PORT}"
echo "  yt-dlp     : $(python -c 'import yt_dlp; print(yt_dlp.version.__version__)' 2>/dev/null || echo unknown)"
echo "─────────────────────────────────────────────"

run_app() {
  # Run from the scratch dir rather than /app, so that if anything still
  # resolves a temp path relative to the working directory it lands somewhere
  # writable. PYTHONPATH keeps the app importable from there.
  export PYTHONPATH="/app${PYTHONPATH:+:${PYTHONPATH}}"
  cd "${TEMP_DIR}" || cd /app
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
  # Covered by the line above when TEMP_DIR sits under CONFIG_DIR (the
  # default), but not if it has been pointed somewhere else.
  chown -R "${PUID}:${PGID}" "${TEMP_DIR}" 2>/dev/null || true

  case "${1:-serve}" in
    serve) exec gosu "${USER_NAME}:${GROUP_NAME}" /entrypoint.sh serve-as-user ;;
    *)     exec gosu "${USER_NAME}:${GROUP_NAME}" "$@" ;;
  esac
fi

case "${1:-serve}" in
  serve|serve-as-user) run_app ;;
  *) exec "$@" ;;
esac
