# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="youtube-plex-dl" \
      org.opencontainers.image.description="Download YouTube videos, playlists and channels into a Plex-friendly TV Shows layout." \
      org.opencontainers.image.source="https://github.com/YOURNAME/youtube-plex-dl"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    OUTPUT_DIR=/downloads \
    CONFIG_DIR=/config \
    PORT=8080

# ffmpeg is the only heavy runtime dependency (merging video+audio, embedding
# metadata/thumbnails/subtitles). gosu lets us drop to PUID/PGID at start.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        gosu \
        tini \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Vendor htmx so the UI works on a LAN-only / offline Unraid box.
# Tries three mirrors; if your build host has no internet the page falls back
# to loading htmx from a CDN at runtime (see templates/base.html).
ARG HTMX_VERSION=2.0.4
RUN set -eux; \
    for u in \
      "https://cdn.jsdelivr.net/npm/htmx.org@${HTMX_VERSION}/dist/htmx.min.js" \
      "https://unpkg.com/htmx.org@${HTMX_VERSION}/dist/htmx.min.js" \
      "https://cdnjs.cloudflare.com/ajax/libs/htmx/${HTMX_VERSION}/htmx.min.js" ; do \
        if curl -fsSL "$u" -o /app/app/static/htmx.min.js && \
           [ -s /app/app/static/htmx.min.js ]; then \
            echo "vendored htmx from $u"; break; \
        fi; \
    done; \
    [ -s /app/app/static/htmx.min.js ] || echo "WARNING: htmx not vendored, runtime CDN fallback will be used"

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN mkdir -p /config /downloads

VOLUME ["/config", "/downloads"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8080}/healthz" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["serve"]
