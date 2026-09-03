"""FastAPI app: routes + htmx partials."""
from __future__ import annotations

import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, downloader, naming, notify, ytdlp_service
from .config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("ypd")

BASE_DIR = Path(__file__).resolve().parent

# token -> {"created": ts, "kind": str, "title": str, "videos": {video_id: meta}}
_analyses: dict[str, dict[str, Any]] = {}
_ANALYSIS_TTL = 60 * 60


def _prune_analyses() -> None:
    cutoff = time.time() - _ANALYSIS_TTL
    for token in [t for t, a in _analyses.items() if a["created"] < cutoff]:
        _analyses.pop(token, None)


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    db.init()
    downloader.start()
    log.info("Output dir: %s | Config dir: %s | Season mode: %s",
             config.OUTPUT_DIR, config.CONFIG_DIR, config.SEASON_MODE)
    log.info("Email notifications: %s",
             "on" if config.email_ready() else "off (SMTP not configured)")
    yield
    downloader.shutdown()


app = FastAPI(title=config.APP_TITLE, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# --------------------------------------------------------------------------
# template helpers
# --------------------------------------------------------------------------
def fmt_duration(seconds) -> str:
    s = int(seconds or 0)
    if s <= 0:
        return "—"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_size(num) -> str:
    n = float(num or 0)
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def fmt_count(num) -> str:
    n = int(num or 0)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


templates.env.filters["duration"] = fmt_duration
templates.env.filters["filesize"] = fmt_size
templates.env.filters["compact"] = fmt_count
templates.env.filters["pdate"] = naming.pretty_date
templates.env.globals["config"] = config


def render(request: Request, name: str, ctx: dict[str, Any] | None = None,
           status: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx or {}, status_code=status)


def toast(request: Request, message: str, level: str = "ok",
          status: int = 200) -> HTMLResponse:
    return render(request, "partials/toast.html",
                  {"message": message, "level": level}, status)


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return render(request, "index.html", {
        "active": db.active(),
        "recent": db.recent(15),
        "counts": db.counts(),
    })


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request, q: str = Query("")):
    return render(request, "history.html", {
        "rows": db.history(500, q.strip()),
        "q": q.strip(),
        "counts": db.counts(),
    })


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"


# --------------------------------------------------------------------------
# analyse
# --------------------------------------------------------------------------
@app.post("/analyze", response_class=HTMLResponse)
async def analyze(
    request: Request,
    url: str = Form(""),
    limit: str = Form("50"),
    auto_download: str = Form(""),
):
    url = url.strip()
    if not url:
        return toast(request, "Paste a YouTube link first.", "warn")

    try:
        target = ytdlp_service.classify(url)
    except ytdlp_service.ProbeError as exc:
        return toast(request, str(exc), "error")

    limit_n = _parse_limit(limit)

    try:
        if target["kind"] == ytdlp_service.KIND_VIDEO:
            payload = await _analyze_video(target, bool(auto_download))
        else:
            payload = await _analyze_collection(target, limit_n)
    except ytdlp_service.ProbeError as exc:
        return toast(request, f"yt-dlp could not read that URL: {exc}", "error")

    return render(request, "partials/results.html", payload)


def _parse_limit(raw: str) -> int:
    raw = (raw or "").strip().lower()
    if raw in ("all", "0", "*", ""):
        return 5000
    try:
        return max(1, min(int(raw), 5000))
    except ValueError:
        return config.PROBE_LIMIT


async def _analyze_video(target: dict, auto_download: bool) -> dict[str, Any]:
    import anyio
    meta = await anyio.to_thread.run_sync(ytdlp_service.probe_video, target["url"])

    token = _store([meta], kind="video", title=meta["title"])
    summary = None
    if auto_download:
        summary = downloader.submit([meta])

    return {
        "kind": "video",
        "token": token,
        "heading": meta["title"],
        "subheading": meta["channel_name"],
        "videos": [meta],
        "states": db.existing_states([meta["video_id"]]),
        "channel_name": meta["channel_name"],
        "channel_url": meta.get("channel_url", ""),
        "channel_exclude": meta["video_id"],
        "playlist_url": target.get("playlist_url"),
        "auto_summary": summary,
        "total_available": 1,
    }


async def _analyze_collection(target: dict, limit_n: int) -> dict[str, Any]:
    import anyio
    result = await anyio.to_thread.run_sync(
        ytdlp_service.probe_collection, target["url"], limit_n
    )
    videos = result["videos"]
    token = _store(videos, kind=result["kind"], title=result["title"])
    return {
        "kind": result["kind"],
        "token": token,
        "heading": result["title"],
        "subheading": f"{result['channel_name']} · showing {len(videos)} of "
                      f"{result['count'] or len(videos)}",
        "videos": videos,
        "states": db.existing_states([v["video_id"] for v in videos]),
        "channel_name": result["channel_name"],
        "channel_url": "",  # collection view already lists the channel's videos
        "channel_exclude": None,
        "playlist_url": None,
        "auto_summary": None,
        "total_available": result["count"] or len(videos),
    }


def _store(videos: list[dict], kind: str, title: str) -> str:
    _prune_analyses()
    token = secrets.token_urlsafe(12)
    _analyses[token] = {
        "created": time.time(),
        "kind": kind,
        "title": title,
        "videos": {v["video_id"]: v for v in videos},
    }
    return token


# --------------------------------------------------------------------------
# channel side panel (lazy loaded)
# --------------------------------------------------------------------------
@app.get("/channel", response_class=HTMLResponse)
async def channel_panel(request: Request, url: str = Query(""),
                        exclude: str = Query(""), name: str = Query("")):
    if not url:
        return HTMLResponse("")
    import anyio
    videos = await anyio.to_thread.run_sync(
        ytdlp_service.channel_recent, url, config.CHANNEL_PANEL_LIMIT, exclude or None
    )
    token = _store(videos, kind="channel", title=name or "Channel")
    return render(request, "partials/channel_panel.html", {
        "videos": videos,
        "token": token,
        "channel_name": name or (videos[0]["channel_name"] if videos else "Channel"),
        "channel_url": url,
        "states": db.existing_states([v["video_id"] for v in videos]),
    })


# --------------------------------------------------------------------------
# queue
# --------------------------------------------------------------------------
@app.post("/enqueue", response_class=HTMLResponse)
async def enqueue(request: Request):
    form = await request.form()
    token = str(form.get("token") or "")
    ids = [str(v) for v in form.getlist("video_ids")]
    analysis = _analyses.get(token)
    if analysis is None:
        return toast(request, "That result set expired — re-analyze the link.", "warn")
    if not ids:
        return toast(request, "Nothing selected.", "warn")

    metas = [analysis["videos"][i] for i in ids if i in analysis["videos"]]
    summary = downloader.submit(metas)
    msg = f"Queued {summary['queued']} video(s)."
    if summary["skipped"]:
        msg += f" Skipped {summary['skipped']} already downloaded or in progress."
    return toast(request, msg, "ok" if summary["queued"] else "warn")


@app.get("/partials/queue", response_class=HTMLResponse)
async def queue_partial(request: Request):
    return render(request, "partials/queue.html", {
        "active": db.active(),
        "recent": db.recent(15),
        "counts": db.counts(),
    })


@app.post("/queue/clear", response_class=HTMLResponse)
async def queue_clear(request: Request):
    n = db.clear_finished()
    return toast(request, f"Cleared {n} failed/skipped entr{'y' if n == 1 else 'ies'}.")


@app.post("/forget/{video_id}", response_class=HTMLResponse)
async def forget(request: Request, video_id: str):
    db.forget(video_id)
    return toast(request, "Removed from history — it can be downloaded again.")


@app.post("/retry/{video_id}", response_class=HTMLResponse)
async def retry(request: Request, video_id: str):
    row = db.get_download(video_id)
    if row is None:
        return toast(request, "Unknown video.", "warn")
    meta = {
        "video_id": row["video_id"], "url": row["url"], "title": row["title"],
        "channel_id": row["channel_id"], "channel_name": row["channel_name"],
        "upload_date": row["upload_date"], "duration": row["duration"],
        "thumbnail": row["thumbnail"],
    }
    db.forget(video_id)
    summary = downloader.submit([meta])
    return toast(request, "Re-queued." if summary["queued"] else "Could not re-queue.",
                 "ok" if summary["queued"] else "warn")


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------
@app.post("/email/test", response_class=HTMLResponse)
async def email_test(request: Request):
    if not config.email_ready():
        return toast(request, "SMTP is not configured (set SMTP_HOST, SMTP_TO, "
                              "SMTP_FROM and NOTIFY_EMAIL=true).", "warn")
    import anyio
    ok = await anyio.to_thread.run_sync(notify.send_test)
    return toast(request, "Test email sent." if ok else
                 "Send failed — check the container log.", "ok" if ok else "error")


@app.get("/api/status")
async def api_status():
    return JSONResponse({
        "counts": db.counts(),
        "active": [dict(r) for r in db.active()],
        "output_dir": str(config.OUTPUT_DIR),
        "season_mode": config.SEASON_MODE,
    })
