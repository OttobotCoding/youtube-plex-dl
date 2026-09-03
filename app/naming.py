"""Turn YouTube metadata into a Plex-friendly TV-show path.

Target layout (Plex "TV Shows" library, Personal Media Shows agent):

    <OUTPUT_DIR>/
        Veritasium/
            poster.jpg
            Season 01/
                Veritasium - S01E07 - Some Title (2026-08-29).mp4
                Veritasium - S01E07 - Some Title (2026-08-29).jpg
                Veritasium - S01E07 - Some Title (2026-08-29).en.srt
                Veritasium - S01E07 - Some Title (2026-08-29).nfo

With SEASON_MODE=year the season folder is `Season 2026` and the episode code
is `S2026E0829` (MMDD), which keeps uploads chronologically ordered forever.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from datetime import date
from pathlib import Path

from . import db
from .config import config

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DOTS = re.compile(r"\.+$")
_SPACES = re.compile(r"\s+")


def sanitize(name: str, max_len: int = 120) -> str:
    """Make a string safe on Linux, SMB/Windows shares and macOS alike."""
    name = unicodedata.normalize("NFC", name or "")
    name = name.replace("/", "-").replace("\\", "-")
    name = _ILLEGAL.sub("", name)
    name = _SPACES.sub(" ", name).strip()
    name = _DOTS.sub("", name).strip()
    if len(name) > max_len:
        name = name[:max_len].rstrip(" -_.")
    return name or "Untitled"


def pretty_date(upload_date: str | None) -> str:
    """'20260829' -> '2026-08-29'. Returns '' when unknown."""
    if not upload_date or len(upload_date) < 8 or not upload_date[:8].isdigit():
        return ""
    return f"{upload_date[0:4]}-{upload_date[4:6]}-{upload_date[6:8]}"


def _year_of(upload_date: str | None) -> int:
    d = pretty_date(upload_date)
    return int(d[:4]) if d else date.today().year


_alloc_lock = threading.Lock()


def allocate_episode(channel_id: str, upload_date: str | None,
                     row_id: int | None = None) -> tuple[int, int, str]:
    """Return (season, episode, episode_code) for a new download.

    Serialised, and the result is written back to the download row inside the
    same lock, so two workers finishing at the same moment can never be handed
    the same episode number.
    """
    with _alloc_lock:
        season, episode, code = _allocate_episode(channel_id, upload_date)
        if row_id is not None:
            db.update(row_id, season=season, episode=episode, episode_code=code)
        return season, episode, code


def _allocate_episode(channel_id: str, upload_date: str | None) -> tuple[int, int, str]:
    if config.SEASON_MODE == "year":
        season = _year_of(upload_date)
        d = pretty_date(upload_date)
        # MMDD as the episode number keeps the natural upload order.
        episode = int(d[5:7] + d[8:10]) if d else int(date.today().strftime("%m%d"))
        code = f"S{season}E{episode:04d}"
        # Two uploads on the same day would collide; walk forward until free.
        guard = 0
        while db.episode_code_taken(channel_id, code) and guard < 40:
            episode += 1
            code = f"S{season}E{episode:04d}"
            guard += 1
        return season, episode, code

    season = config.SEASON_NUMBER
    episode = db.next_episode_number(channel_id)
    guard = 0
    code = f"S{season:02d}E{episode:02d}"
    while db.episode_code_taken(channel_id, code) and guard < 200:
        episode = db.next_episode_number(channel_id)
        code = f"S{season:02d}E{episode:02d}"
        guard += 1
    return season, episode, code


def season_folder(season: int) -> str:
    if config.SEASON_MODE == "year":
        return f"Season {season}"
    return f"Season {season:02d}"


def channel_dir(channel_name: str) -> Path:
    return config.OUTPUT_DIR / sanitize(channel_name, 80)


def build_paths(channel_name: str, title: str, upload_date: str | None,
                season: int, episode_code: str) -> tuple[Path, str]:
    """Return (season directory, filename stem without extension)."""
    safe_channel = sanitize(channel_name, 80)
    safe_title = sanitize(title, config.FILENAME_MAX_LEN)
    if config.TITLE_INCLUDE_DATE:
        d = pretty_date(upload_date)
        if d:
            safe_title = f"{safe_title} ({d})"
    stem = f"{safe_channel} - {episode_code} - {safe_title}"
    stem = sanitize(stem, config.FILENAME_MAX_LEN + 60)
    return channel_dir(channel_name) / season_folder(season), stem


def episode_nfo(meta: dict, season: int, episode: int) -> str:
    """Minimal Kodi/Plex-compatible episode NFO (harmless if Plex ignores it)."""
    def esc(v: str) -> str:
        return (
            str(v or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    aired = pretty_date(meta.get("upload_date"))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n'
        "<episodedetails>\n"
        f"  <title>{esc(meta.get('title'))}</title>\n"
        f"  <showtitle>{esc(meta.get('channel_name'))}</showtitle>\n"
        f"  <season>{season}</season>\n"
        f"  <episode>{episode}</episode>\n"
        f"  <aired>{esc(aired)}</aired>\n"
        f"  <premiered>{esc(aired)}</premiered>\n"
        f"  <studio>{esc(meta.get('channel_name'))}</studio>\n"
        f"  <runtime>{int((meta.get('duration') or 0) // 60)}</runtime>\n"
        f"  <plot>{esc((meta.get('description') or '')[:4000])}</plot>\n"
        f"  <uniqueid type=\"youtube\" default=\"true\">{esc(meta.get('video_id'))}</uniqueid>\n"
        "</episodedetails>\n"
    )
