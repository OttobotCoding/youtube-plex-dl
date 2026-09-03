"""Background download queue.

A small pool of worker threads pulls jobs off a queue and runs yt-dlp.
Nothing here blocks the web server: routes only touch SQLite and the queue.
"""
from __future__ import annotations

import logging
import os
import queue
import shutil
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

from yt_dlp import YoutubeDL

from . import db, naming, notify, ytdlp_service
from .config import config

log = logging.getLogger("ypd.downloader")

_jobs: "queue.Queue[int]" = queue.Queue()
_workers: list[threading.Thread] = []
_started = False
_state_lock = threading.Lock()
_running: set[int] = set()
_stop = threading.Event()


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def start() -> None:
    global _started
    if _started:
        return
    _started = True
    _stop.clear()
    for i in range(config.CONCURRENCY):
        t = threading.Thread(target=_worker, name=f"ypd-worker-{i+1}", daemon=True)
        t.start()
        _workers.append(t)
    threading.Thread(target=_notifier, name="ypd-notifier", daemon=True).start()
    log.info("Download workers started (concurrency=%s)", config.CONCURRENCY)


def shutdown() -> None:
    _stop.set()


def submit(metas: list[dict[str, Any]]) -> dict[str, int]:
    """Queue a list of normalised video dicts. Returns a small summary."""
    queued = skipped = 0
    for meta in metas:
        if not meta.get("video_id"):
            continue
        existing = db.get_download(meta["video_id"])
        if existing and existing["status"] in ("completed", "downloading", "queued"):
            skipped += 1
            continue
        row_id, created = db.enqueue(meta)
        if created:
            if meta.get("channel_id"):
                db.upsert_channel(
                    meta["channel_id"], meta.get("channel_name") or "Unknown Channel",
                    naming.sanitize(meta.get("channel_name") or "", 80),
                    meta.get("channel_url") or "",
                )
            _jobs.put(row_id)
            queued += 1
        else:
            skipped += 1
    return {"queued": queued, "skipped": skipped}


def is_running(row_id: int) -> bool:
    with _state_lock:
        return row_id in _running


# --------------------------------------------------------------------------
# worker loop
# --------------------------------------------------------------------------
def _worker() -> None:
    while not _stop.is_set():
        try:
            row_id = _jobs.get(timeout=1.0)
        except queue.Empty:
            continue
        with _state_lock:
            _running.add(row_id)
        try:
            _process(row_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("Job %s crashed", row_id)
            db.update(row_id, status="failed", error=str(exc)[:500], progress=0)
        finally:
            with _state_lock:
                _running.discard(row_id)
            _jobs.task_done()


def _notifier() -> None:
    """Flush email notifications once the queue has been idle for a moment."""
    idle_since: Optional[float] = None
    while not _stop.is_set():
        time.sleep(2.0)
        if not config.email_ready():
            continue
        busy = bool(db.active()) or not _jobs.empty()
        if config.NOTIFY_MODE == "each":
            notify.flush_pending()
            continue
        if busy:
            idle_since = None
            continue
        if idle_since is None:
            idle_since = time.time()
        elif time.time() - idle_since > 5:
            notify.flush_pending()
            idle_since = None


# --------------------------------------------------------------------------
# a single download
# --------------------------------------------------------------------------
def _process(row_id: int) -> None:
    row = db.one("SELECT * FROM downloads WHERE id=?", (row_id,))
    if row is None:
        return
    if row["status"] not in ("queued",):
        return

    db.update(row_id, status="downloading", progress=0, error=None,
              speed=None, eta="preparing")

    # 1. refresh metadata — flat playlist entries lack upload_date/description
    try:
        meta = ytdlp_service.probe_video(row["url"])
    except ytdlp_service.ProbeError as exc:
        db.update(row_id, status="failed", error=f"Metadata fetch failed: {exc}")
        return

    channel_id = meta["channel_id"]
    channel_name = meta["channel_name"]
    db.upsert_channel(channel_id, channel_name,
                      naming.sanitize(channel_name, 80), meta.get("channel_url", ""))

    # 2. work out where it goes
    season, episode, code = naming.allocate_episode(
        channel_id, meta["upload_date"], row_id=row_id
    )
    season_dir, stem = naming.build_paths(
        channel_name, meta["title"], meta["upload_date"], season, code
    )
    season_dir.mkdir(parents=True, exist_ok=True)

    db.update(
        row_id,
        title=meta["title"], channel_id=channel_id, channel_name=channel_name,
        upload_date=meta["upload_date"], duration=meta["duration"],
        thumbnail=meta["thumbnail"], season=season, episode=episode,
        episode_code=code, eta="starting",
    )

    # 3. download
    outtmpl = str(season_dir / f"{stem}.%(ext)s")
    opts = _download_opts(outtmpl, row_id)
    final_path: Optional[str] = None
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(meta["url"], download=True)
        final_path = _resolve_path(info, season_dir, stem)
    except Exception as exc:  # noqa: BLE001
        db.update(row_id, status="failed", progress=0, speed=None, eta=None,
                  error=ytdlp_service._clean(str(exc)))
        _cleanup_partials(season_dir, stem)
        return

    if not final_path or not Path(final_path).exists():
        db.update(row_id, status="failed",
                  error="Download finished but the output file was not found.")
        return

    size = Path(final_path).stat().st_size

    # 4. Plex-friendly sidecars
    if config.WRITE_NFO:
        try:
            (season_dir / f"{stem}.nfo").write_text(
                naming.episode_nfo(meta | {"channel_name": channel_name},
                                   season, episode),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Could not write NFO: %s", exc)

    if config.WRITE_CHANNEL_POSTER:
        _maybe_channel_poster(channel_id, channel_name, meta.get("channel_url", ""))

    _fix_ownership(season_dir)

    db.update(row_id, status="completed", progress=100.0, speed=None, eta=None,
              filepath=final_path, filesize=size, error=None)
    log.info("Completed %s -> %s", meta["video_id"], final_path)


# --------------------------------------------------------------------------
# yt-dlp download options
# --------------------------------------------------------------------------
def _format_selector() -> str:
    ext = config.MERGE_FORMAT
    height = f"[height<=?{config.MAX_HEIGHT}]" if config.MAX_HEIGHT > 0 else ""
    if ext == "mp4":
        # Prefer a directly-muxable mp4 pair, fall back to anything best.
        return (
            f"bestvideo{height}[ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo{height}+bestaudio/best{height}/best"
        )
    return f"bestvideo{height}+bestaudio/best{height}/best"


def _download_opts(outtmpl: str, row_id: int) -> dict[str, Any]:
    postprocessors: list[dict[str, Any]] = []
    if config.SPONSORBLOCK:
        postprocessors.append({"key": "SponsorBlock", "when": "after_filter"})
        postprocessors.append({
            "key": "ModifyChapters",
            "remove_sponsor_segments": ["sponsor", "selfpromo", "interaction"],
        })
    if config.EMBED_METADATA:
        postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True,
                               "add_chapters": True})
    if config.SUBTITLES and config.EMBED_SUBTITLES:
        postprocessors.append({"key": "FFmpegEmbedSubtitle",
                               "already_have_subtitle": config.WRITE_SUBTITLE_FILES})
    if config.EMBED_THUMBNAIL:
        postprocessors.append({"key": "EmbedThumbnail",
                               "already_have_thumbnail": config.WRITE_THUMBNAIL})

    opts: dict[str, Any] = {
        "outtmpl": {"default": outtmpl},
        "format": _format_selector(),
        "merge_output_format": config.MERGE_FORMAT,
        "final_ext": config.MERGE_FORMAT,
        "writethumbnail": config.WRITE_THUMBNAIL or config.EMBED_THUMBNAIL,
        "writesubtitles": config.SUBTITLES,
        "writeautomaticsub": config.SUBTITLES and config.SUBTITLE_AUTO,
        "subtitleslangs": [s.strip() for s in config.SUBTITLE_LANGS.split(",") if s.strip()],
        "subtitlesformat": "srt/best",
        "postprocessors": postprocessors,
        "progress_hooks": [_make_progress_hook(row_id)],
        "postprocessor_hooks": [_make_pp_hook(row_id)],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "retries": 5,
        "fragment_retries": 10,
        "continuedl": True,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 30,
        "windowsfilenames": True,
        "trim_file_name": 200,
        "overwrites": False,
        "postprocessor_args": {"thumbnailsconvertor+ffmpeg_o": ["-c:v", "mjpeg", "-vf",
                                                                "crop=ih*16/9:ih"]},
    }
    if config.SUBTITLES and config.WRITE_SUBTITLE_FILES:
        # Convert whatever YouTube hands us into .srt, which Plex reads happily.
        opts["postprocessors"].insert(0, {"key": "FFmpegSubtitlesConvertor",
                                          "format": "srt"})
    if config.WRITE_THUMBNAIL:
        opts["postprocessors"].insert(0, {"key": "FFmpegThumbnailsConvertor",
                                          "format": "jpg", "when": "before_dl"})
    if config.RATE_LIMIT:
        opts["ratelimit"] = _parse_rate(config.RATE_LIMIT)
    if config.COOKIES_FILE:
        opts["cookiefile"] = config.COOKIES_FILE
    return opts


def _parse_rate(value: str) -> Optional[int]:
    value = value.strip().upper().rstrip("B")
    mult = 1
    if value.endswith("K"):
        mult, value = 1024, value[:-1]
    elif value.endswith("M"):
        mult, value = 1024 ** 2, value[:-1]
    elif value.endswith("G"):
        mult, value = 1024 ** 3, value[:-1]
    try:
        return int(float(value) * mult)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# progress
# --------------------------------------------------------------------------
def _make_progress_hook(row_id: int):
    last = {"t": 0.0}

    def hook(d: dict) -> None:
        status = d.get("status")
        if status == "downloading":
            now = time.time()
            if now - last["t"] < 0.7:
                return
            last["t"] = now
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            pct = (got / total * 100.0) if total else 0.0
            db.update(
                row_id,
                progress=round(min(pct, 99.5), 1),
                speed=_human_speed(d.get("speed")),
                eta=_human_eta(d.get("eta")),
            )
        elif status == "finished":
            db.update(row_id, progress=99.5, speed=None, eta="processing")

    return hook


def _make_pp_hook(row_id: int):
    def hook(d: dict) -> None:
        if d.get("status") == "started":
            name = (d.get("postprocessor") or "").replace("FFmpeg", "")
            db.update(row_id, eta=(name or "processing").lower()[:24])
    return hook


def _human_speed(speed) -> Optional[str]:
    if not speed:
        return None
    n = float(speed)
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB/s"


def _human_eta(eta) -> Optional[str]:
    if eta in (None, ""):
        return None
    try:
        s = int(eta)
    except (TypeError, ValueError):
        return None
    m, sec = divmod(max(s, 0), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{sec:02d}s"


# --------------------------------------------------------------------------
# filesystem helpers
# --------------------------------------------------------------------------
def _resolve_path(info: dict | None, season_dir: Path, stem: str) -> Optional[str]:
    if info:
        req = info.get("requested_downloads") or []
        for entry in req:
            for key in ("filepath", "_filename", "filename"):
                p = entry.get(key)
                if p and Path(p).exists():
                    return str(Path(p))
        for key in ("filepath", "_filename"):
            p = info.get(key)
            if p and Path(p).exists():
                return str(Path(p))
    video_exts = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi")
    candidates = [p for p in season_dir.glob(f"{glob_escape(stem)}.*")
                  if p.suffix.lower() in video_exts]
    if candidates:
        return str(max(candidates, key=lambda p: p.stat().st_mtime))
    return None


def glob_escape(value: str) -> str:
    return value.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")


def _cleanup_partials(season_dir: Path, stem: str) -> None:
    for p in season_dir.glob(f"{glob_escape(stem)}*"):
        if p.suffix in (".part", ".ytdl", ".temp") or p.name.endswith(".part"):
            try:
                p.unlink()
            except OSError:
                pass


def _maybe_channel_poster(channel_id: str, channel_name: str, channel_url: str) -> None:
    row = db.get_channel(channel_id)
    if row is not None and row["poster_saved"]:
        return
    target_dir = naming.channel_dir(channel_name)
    poster = target_dir / "poster.jpg"
    if poster.exists():
        db.mark_poster_saved(channel_id)
        return
    if not channel_url:
        return
    try:
        opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                "extract_flat": "in_playlist", "playlistend": 1}
        if config.COOKIES_FILE:
            opts["cookiefile"] = config.COOKIES_FILE
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(ytdlp_service.channel_tab_url(channel_url),
                                    download=False) or {}
        thumbs = sorted(
            [t for t in (info.get("thumbnails") or []) if t.get("url")],
            key=lambda t: (t.get("width") or 0), reverse=True,
        )
        avatar = next((t["url"] for t in thumbs if (t.get("width") or 0) >= 200), None)
        if not avatar and thumbs:
            avatar = thumbs[0]["url"]
        if not avatar:
            return
        target_dir.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(avatar, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp, poster.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
        db.mark_poster_saved(channel_id)
        log.info("Saved channel poster for %s", channel_name)
    except Exception as exc:  # noqa: BLE001
        log.debug("Channel poster skipped for %s: %s", channel_name, exc)


def _fix_ownership(path: Path) -> None:
    """Best-effort chown so Unraid shares stay tidy when running as root.

    No-op on Windows, where geteuid/chown don't exist, and no-op when we
    already dropped to PUID/PGID (the files are created correctly owned).
    """
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or not hasattr(os, "chown"):
        return
    if geteuid() != 0:
        return
    try:
        for target in (path, path.parent):
            os.chown(target, config.PUID, config.PGID)
        for child in path.iterdir():
            try:
                os.chown(child, config.PUID, config.PGID)
            except OSError:
                pass
    except OSError:
        pass
