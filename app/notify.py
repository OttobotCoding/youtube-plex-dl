"""Email notifications for finished downloads (stdlib smtplib, no extra deps)."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate

from . import db
from .config import config
from .naming import pretty_date

log = logging.getLogger("ypd.notify")


def _fmt_duration(seconds: int | None) -> str:
    s = int(seconds or 0)
    if s <= 0:
        return "—"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _fmt_size(num: int | None) -> str:
    n = float(num or 0)
    if n <= 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _send(subject: str, text: str, html: str) -> bool:
    if not config.email_ready():
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM or config.SMTP_USER
    msg["To"] = ", ".join(config.email_recipients())
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    try:
        if config.SMTP_SSL:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT,
                                  context=ctx, timeout=30) as srv:
                if config.SMTP_USER:
                    srv.login(config.SMTP_USER, config.SMTP_PASS)
                srv.send_message(msg)
        else:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as srv:
                srv.ehlo()
                if config.SMTP_TLS:
                    srv.starttls(context=ssl.create_default_context())
                    srv.ehlo()
                if config.SMTP_USER:
                    srv.login(config.SMTP_USER, config.SMTP_PASS)
                srv.send_message(msg)
        log.info("Sent notification email: %s", subject)
        return True
    except Exception as exc:  # noqa: BLE001 - never let email break downloads
        log.warning("Email notification failed: %s", exc)
        return False


def _rows_html(rows) -> str:
    cells = []
    for r in rows:
        ok = r["status"] == "completed"
        badge = ("<span style='color:#1a7f37;font-weight:600'>Downloaded</span>" if ok
                 else "<span style='color:#b42318;font-weight:600'>Failed</span>")
        detail = (r["filepath"] or "") if ok else (r["error"] or "Unknown error")
        cells.append(
            "<tr>"
            f"<td style='padding:8px 10px;border-bottom:1px solid #eee'>{badge}</td>"
            f"<td style='padding:8px 10px;border-bottom:1px solid #eee'>"
            f"<strong>{_esc(r['title'])}</strong><br>"
            f"<span style='color:#667085;font-size:12px'>{_esc(r['channel_name'] or '')}"
            f" &middot; {pretty_date(r['upload_date']) or '—'}"
            f" &middot; {_fmt_duration(r['duration'])}"
            f" &middot; {_fmt_size(r['filesize'])}</span><br>"
            f"<span style='color:#98a2b3;font-size:11px;font-family:monospace'>"
            f"{_esc(detail)}</span></td>"
            "</tr>"
        )
    return "".join(cells)


def _esc(v) -> str:
    return (str(v or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def notify_finished(rows) -> None:
    """Send one email covering the supplied finished rows."""
    rows = list(rows)
    if not rows or not config.email_ready():
        return
    done = [r for r in rows if r["status"] == "completed"]
    failed = [r for r in rows if r["status"] == "failed"]
    if not done and not config.NOTIFY_ON_FAILURE:
        return

    if len(rows) == 1:
        r = rows[0]
        verb = "downloaded" if r["status"] == "completed" else "FAILED"
        subject = f"[{config.APP_TITLE}] {verb}: {r['title'][:90]}"
    else:
        subject = (f"[{config.APP_TITLE}] {len(done)} downloaded"
                   + (f", {len(failed)} failed" if failed else ""))

    text_lines = [subject, ""]
    for r in rows:
        mark = "OK  " if r["status"] == "completed" else "FAIL"
        text_lines.append(f"{mark} {r['channel_name']} - {r['title']}")
        text_lines.append(f"     {r['filepath'] or r['error'] or ''}")
    text_lines += ["", "Plex tip: run a library scan if you don't see them yet."]

    html = (
        "<div style=\"font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
        "max-width:640px\">"
        f"<h2 style='margin:0 0 4px;font-size:18px'>{_esc(config.APP_TITLE)}</h2>"
        f"<p style='margin:0 0 16px;color:#667085;font-size:13px'>"
        f"{len(done)} completed{', ' + str(len(failed)) + ' failed' if failed else ''}"
        f" &middot; saved under <code>{_esc(str(config.OUTPUT_DIR))}</code></p>"
        "<table style='border-collapse:collapse;width:100%;font-size:14px'>"
        f"{_rows_html(rows)}</table>"
        "<p style='margin-top:16px;color:#98a2b3;font-size:12px'>"
        "If Plex hasn't picked these up, trigger a library scan.</p></div>"
    )
    _send(subject, "\n".join(text_lines), html)


def flush_pending() -> None:
    """Send whatever finished since the last flush, then mark it notified."""
    if not config.email_ready():
        return
    rows = db.unnotified_finished()
    if not rows:
        return
    notify_finished(rows)
    db.mark_notified([r["id"] for r in rows])


def send_test() -> bool:
    if not config.email_ready():
        return False
    return _send(
        f"[{config.APP_TITLE}] Test email",
        "SMTP settings look good — notifications will arrive here.",
        "<p style=\"font-family:sans-serif\">SMTP settings look good — "
        "notifications will arrive here.</p>",
    )
