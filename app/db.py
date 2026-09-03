"""SQLite persistence: download history, queue state, per-channel episode counters."""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any, Iterable, Optional

from .config import config

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id     TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    folder         TEXT,
    url            TEXT,
    poster_saved   INTEGER NOT NULL DEFAULT 0,
    last_episode   INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS downloads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id       TEXT NOT NULL UNIQUE,
    url            TEXT NOT NULL,
    title          TEXT NOT NULL,
    channel_id     TEXT,
    channel_name   TEXT,
    upload_date    TEXT,          -- YYYYMMDD
    duration       INTEGER,
    thumbnail      TEXT,
    season         INTEGER,
    episode        INTEGER,
    episode_code   TEXT,          -- S01E07 / S2026E0829
    filepath       TEXT,
    filesize       INTEGER,
    status         TEXT NOT NULL, -- queued|downloading|completed|failed|skipped
    progress       REAL NOT NULL DEFAULT 0,
    speed          TEXT,
    eta            TEXT,
    error          TEXT,
    notified       INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_downloads_status  ON downloads(status);
CREATE INDEX IF NOT EXISTS idx_downloads_channel ON downloads(channel_id);
CREATE INDEX IF NOT EXISTS idx_downloads_updated ON downloads(updated_at DESC);
"""


def init() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            return
        config.ensure_dirs()
        _conn = sqlite3.connect(config.db_path(), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.executescript(SCHEMA)
        _conn.commit()
        # Anything left mid-flight from a previous container run is stale.
        _conn.execute(
            "UPDATE downloads SET status='failed', error='Interrupted by restart',"
            " updated_at=? WHERE status IN ('downloading','queued')",
            (time.time(),),
        )
        _conn.commit()


def _c() -> sqlite3.Connection:
    if _conn is None:
        init()
    assert _conn is not None
    return _conn


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    with _lock:
        return list(_c().execute(sql, tuple(params)).fetchall())


def one(sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with _lock:
        cur = _c().execute(sql, tuple(params))
        _c().commit()
        return cur.lastrowid or 0


# --------------------------------------------------------------------------
# channels
# --------------------------------------------------------------------------
def upsert_channel(channel_id: str, name: str, folder: str = "", url: str = "") -> None:
    with _lock:
        _c().execute(
            """INSERT INTO channels (channel_id, name, folder, url, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(channel_id) DO UPDATE SET
                 name=excluded.name,
                 folder=COALESCE(NULLIF(excluded.folder,''), channels.folder),
                 url=COALESCE(NULLIF(excluded.url,''), channels.url)""",
            (channel_id, name, folder, url, time.time()),
        )
        _c().commit()


def get_channel(channel_id: str) -> Optional[sqlite3.Row]:
    return one("SELECT * FROM channels WHERE channel_id=?", (channel_id,))


def next_episode_number(channel_id: str) -> int:
    """Atomically hand out the next sequential episode number for a channel."""
    with _lock:
        row = _c().execute(
            "SELECT last_episode FROM channels WHERE channel_id=?", (channel_id,)
        ).fetchone()
        if row is None:
            # Never seen this channel: create the row so the counter persists.
            _c().execute(
                "INSERT OR IGNORE INTO channels (channel_id, name, created_at)"
                " VALUES (?,?,?)",
                (channel_id, channel_id, time.time()),
            )
            current = 0
        else:
            current = row["last_episode"]
        # Never hand out a number already used by a file on disk.
        highest = _c().execute(
            "SELECT MAX(episode) AS m FROM downloads WHERE channel_id=? AND season=?"
            " AND status IN ('completed','downloading','queued')",
            (channel_id, config.SEASON_NUMBER),
        ).fetchone()
        current = max(int(current), int((highest["m"] if highest else 0) or 0))
        nxt = current + 1
        _c().execute(
            "UPDATE channels SET last_episode=? WHERE channel_id=?", (nxt, channel_id)
        )
        _c().commit()
        return nxt


def mark_poster_saved(channel_id: str) -> None:
    execute("UPDATE channels SET poster_saved=1 WHERE channel_id=?", (channel_id,))


def episode_code_taken(channel_id: str, code: str) -> bool:
    return (
        one(
            "SELECT 1 FROM downloads WHERE channel_id=? AND episode_code=?"
            " AND status IN ('completed','downloading','queued')",
            (channel_id, code),
        )
        is not None
    )


# --------------------------------------------------------------------------
# downloads
# --------------------------------------------------------------------------
def get_download(video_id: str) -> Optional[sqlite3.Row]:
    return one("SELECT * FROM downloads WHERE video_id=?", (video_id,))


def existing_states(video_ids: Iterable[str]) -> dict[str, str]:
    ids = list(video_ids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = query(f"SELECT video_id, status FROM downloads WHERE video_id IN ({marks})", ids)
    return {r["video_id"]: r["status"] for r in rows}


def enqueue(meta: dict) -> tuple[int, bool]:
    """Insert (or revive) a download row. Returns (row_id, was_created)."""
    now = time.time()
    with _lock:
        row = _c().execute(
            "SELECT id, status FROM downloads WHERE video_id=?", (meta["video_id"],)
        ).fetchone()
        if row is not None:
            if row["status"] in ("completed", "downloading", "queued"):
                return row["id"], False
            # retry a previously failed/skipped item
            _c().execute(
                "UPDATE downloads SET status='queued', progress=0, error=NULL,"
                " notified=0, updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            _c().commit()
            return row["id"], True

        cur = _c().execute(
            """INSERT INTO downloads
               (video_id, url, title, channel_id, channel_name, upload_date,
                duration, thumbnail, status, progress, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,'queued',0,?,?)""",
            (
                meta["video_id"], meta["url"], meta["title"], meta.get("channel_id"),
                meta.get("channel_name"), meta.get("upload_date"), meta.get("duration"),
                meta.get("thumbnail"), now, now,
            ),
        )
        _c().commit()
        return cur.lastrowid, True


def update(row_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    sets = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE downloads SET {sets} WHERE id=?", (*fields.values(), row_id))


def active() -> list[sqlite3.Row]:
    return query(
        "SELECT * FROM downloads WHERE status IN ('queued','downloading')"
        " ORDER BY CASE status WHEN 'downloading' THEN 0 ELSE 1 END, id ASC"
    )


def recent(limit: int = 40) -> list[sqlite3.Row]:
    return query(
        "SELECT * FROM downloads WHERE status IN ('completed','failed','skipped')"
        " ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    )


def history(limit: int = 500, search: str = "") -> list[sqlite3.Row]:
    if search:
        like = f"%{search}%"
        return query(
            "SELECT * FROM downloads WHERE title LIKE ? OR channel_name LIKE ?"
            " ORDER BY updated_at DESC LIMIT ?",
            (like, like, limit),
        )
    return query("SELECT * FROM downloads ORDER BY updated_at DESC LIMIT ?", (limit,))


def counts() -> dict[str, int]:
    rows = query("SELECT status, COUNT(*) AS n FROM downloads GROUP BY status")
    out = {r["status"]: r["n"] for r in rows}
    out["total"] = sum(out.values())
    return out


def unnotified_finished() -> list[sqlite3.Row]:
    return query(
        "SELECT * FROM downloads WHERE notified=0 AND status IN ('completed','failed')"
        " ORDER BY updated_at ASC"
    )


def mark_notified(ids: Iterable[int]) -> None:
    ids = list(ids)
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    execute(f"UPDATE downloads SET notified=1 WHERE id IN ({marks})", ids)


def clear_finished() -> int:
    with _lock:
        cur = _c().execute("DELETE FROM downloads WHERE status IN ('failed','skipped')")
        _c().commit()
        return cur.rowcount


def forget(video_id: str) -> None:
    """Remove a video from history so it can be downloaded again."""
    execute("DELETE FROM downloads WHERE video_id=? AND status!='downloading'", (video_id,))
