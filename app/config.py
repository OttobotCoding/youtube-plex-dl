"""Central configuration, all driven by environment variables.

Every value here can be overridden from docker-compose / the Unraid template.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except (TypeError, ValueError):
        return default


def _str(name: str, default: str = "") -> str:
    val = os.getenv(name)
    return default if val is None or val.strip() == "" else val.strip()


class Config:
    # ---- paths -------------------------------------------------------
    # OUTPUT_DIR is the *root of the Plex TV Shows library* for YouTube.
    # Inside the container this is always /downloads; map it on the host.
    OUTPUT_DIR = Path(_str("OUTPUT_DIR", "/downloads"))
    # DB + logs. Map to appdata on Unraid.
    CONFIG_DIR = Path(_str("CONFIG_DIR", "/config"))
    # Scratch space for small temp files. Defaults to a folder under CONFIG_DIR
    # because that one is guaranteed writable by the app's uid — see temp_dir().
    TEMP_DIR = _str("TEMP_DIR", "")

    # ---- server ------------------------------------------------------
    PORT = _int("PORT", 8080)
    HOST = _str("HOST", "0.0.0.0")
    APP_TITLE = _str("APP_TITLE", "YouTube → Plex")

    # ---- plex naming -------------------------------------------------
    # "single"  -> <Channel>/Season 01/<Channel> - S01E07 - <Title>.mp4
    # "year"    -> <Channel>/Season 2026/<Channel> - S2026E0829 - <Title>.mp4
    SEASON_MODE = _str("SEASON_MODE", "single").lower()
    SEASON_NUMBER = _int("SEASON_NUMBER", 1)  # only used by SEASON_MODE=single
    # Append the upload date to the episode title, e.g. "Title (2026-08-29)"
    TITLE_INCLUDE_DATE = _bool("TITLE_INCLUDE_DATE", True)
    FILENAME_MAX_LEN = _int("FILENAME_MAX_LEN", 150)

    # ---- download behaviour -----------------------------------------
    MERGE_FORMAT = _str("MERGE_FORMAT", "mp4").lower()  # mp4 | mkv
    MAX_HEIGHT = _int("MAX_HEIGHT", 0)  # 0 = unlimited
    CONCURRENCY = max(1, _int("CONCURRENCY", 2))
    EMBED_METADATA = _bool("EMBED_METADATA", True)
    EMBED_THUMBNAIL = _bool("EMBED_THUMBNAIL", True)
    WRITE_THUMBNAIL = _bool("WRITE_THUMBNAIL", True)   # sidecar .jpg for Plex
    WRITE_CHANNEL_POSTER = _bool("WRITE_CHANNEL_POSTER", True)
    WRITE_NFO = _bool("WRITE_NFO", True)               # episode .nfo sidecar
    SUBTITLES = _bool("SUBTITLES", True)
    SUBTITLE_LANGS = _str("SUBTITLE_LANGS", "en.*")
    SUBTITLE_AUTO = _bool("SUBTITLE_AUTO", True)       # include auto-generated
    EMBED_SUBTITLES = _bool("EMBED_SUBTITLES", True)
    WRITE_SUBTITLE_FILES = _bool("WRITE_SUBTITLE_FILES", True)
    SPONSORBLOCK = _bool("SPONSORBLOCK", False)
    RATE_LIMIT = _str("RATE_LIMIT", "")                # e.g. "5M"
    COOKIES_FILE = _str("COOKIES_FILE", "")            # e.g. /config/cookies.txt
    YTDLP_EXTRA_ARGS = _str("YTDLP_EXTRA_ARGS", "")    # rarely needed

    # ---- discovery ---------------------------------------------------
    PROBE_LIMIT = _int("PROBE_LIMIT", 60)        # max entries pulled per probe
    CHANNEL_PANEL_LIMIT = _int("CHANNEL_PANEL_LIMIT", 24)
    # Seconds before /analyze gives up and tells you, instead of spinning
    # forever. Large channels page through YouTube 30 videos at a time, so
    # "all" on a big channel legitimately takes minutes — raise this if you
    # routinely list very large channels.
    PROBE_TIMEOUT = _int("PROBE_TIMEOUT", 120)

    # ---- ownership (Unraid) -----------------------------------------
    PUID = _int("PUID", 99)
    PGID = _int("PGID", 100)
    UMASK = _str("UMASK", "002")

    # ---- email notifications ----------------------------------------
    NOTIFY_EMAIL = _bool("NOTIFY_EMAIL", False)
    # "batch" = one summary when the queue drains, "each" = one per video
    NOTIFY_MODE = _str("NOTIFY_MODE", "batch").lower()
    NOTIFY_ON_FAILURE = _bool("NOTIFY_ON_FAILURE", True)
    SMTP_HOST = _str("SMTP_HOST", "")
    SMTP_PORT = _int("SMTP_PORT", 587)
    SMTP_USER = _str("SMTP_USER", "")
    SMTP_PASS = _str("SMTP_PASS", "")
    SMTP_TLS = _bool("SMTP_TLS", True)      # STARTTLS
    SMTP_SSL = _bool("SMTP_SSL", False)     # implicit TLS (port 465)
    SMTP_FROM = _str("SMTP_FROM", "")
    SMTP_TO = _str("SMTP_TO", "")           # comma separated

    # NOTE: these are deliberately instance methods, not classmethods, so that
    # overriding an attribute on the shared `config` object (tests, or a future
    # settings page) is respected everywhere.
    def db_path(self) -> Path:
        return self.CONFIG_DIR / "youtube-plex-dl.sqlite3"

    def email_recipients(self) -> list[str]:
        return [a.strip() for a in self.SMTP_TO.split(",") if a.strip()]

    def email_ready(self) -> bool:
        return bool(
            self.NOTIFY_EMAIL
            and self.SMTP_HOST
            and self.email_recipients()
            and (self.SMTP_FROM or self.SMTP_USER)
        )

    def temp_dir(self) -> Path:
        """Where small scratch files go.

        Defaults under CONFIG_DIR rather than /tmp because CONFIG_DIR is a
        volume the entrypoint chowns to PUID:PGID, so it is writable whatever
        uid we end up as. Only tiny files land here (yt-dlp's format probes and
        its player cache) — actual downloads still go straight to OUTPUT_DIR.
        """
        return Path(self.TEMP_DIR) if self.TEMP_DIR else self.CONFIG_DIR / "tmp"

    def ensure_dirs(self) -> None:
        # Resolve to absolute before anything chdirs (see use_writable_cwd).
        self.OUTPUT_DIR = self.OUTPUT_DIR.expanduser().resolve()
        self.CONFIG_DIR = self.CONFIG_DIR.expanduser().resolve()
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        tmp = self.temp_dir()
        try:
            tmp.mkdir(parents=True, exist_ok=True)
            probe = tmp / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            tempfile.tempdir = str(tmp)   # for callers that pass dir=None
        except OSError:
            tempfile.tempdir = None

    def use_writable_cwd(self) -> Path | None:
        """Move the process into a directory it can write to.

        yt-dlp's _check_formats() creates a probe file with

            dir = self.get_output_path('temp')          # '' with no `paths`
            tempfile.NamedTemporaryFile(..., dir=dir or None)

        and `get_output_path` runs that empty string through
        `sanitize_path(path, force=windowsfilenames)`. We set
        `windowsfilenames` so titles stay safe on SMB shares — and with
        force=True, sanitize_path('') returns '.' rather than ''. '.' is
        truthy, so yt-dlp passes an explicit dir of '.', i.e. THE CURRENT
        WORKING DIRECTORY. In the container that is /app, root-owned from the
        image build while the app runs as PUID:

            [Errno 13] Permission denied: '/app/tmpXXXXXXXX.tmp'

        Because the directory is passed explicitly, pinning tempfile.tempdir
        does not help. The cwd itself has to be writable.
        """
        target = self.temp_dir()
        try:
            target.mkdir(parents=True, exist_ok=True)
            os.chdir(target)
            return target
        except OSError:
            return None


config = Config()
