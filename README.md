# youtube-plex-dl

A small self-hosted web app that pulls YouTube videos, playlists and channels
into a directory layout Plex reads as **TV Shows**, with a GUI for reviewing and
cherry-picking what actually gets downloaded.

FastAPI + Jinja2 + htmx on the front, `yt-dlp` + ffmpeg on the back, SQLite for
history. One container, no host dependencies beyond Docker.

---

## What it does

**Paste any YouTube URL.** The app classifies it without guessing:

| You paste | It does |
|---|---|
| `watch?v=…`, `youtu.be/…`, `/shorts/…`, or a bare 11-char video ID | Starts the download immediately (toggleable), then lazily loads a **"More from this channel"** panel with that channel's recent uploads so you can tick more |
| `/playlist?list=…` | Lists the playlist with thumbnails, titles, durations, upload dates and checkboxes |
| `/@handle`, `/channel/UC…`, `/c/…`, `/user/…` | Same grid, pulled from the channel's Videos tab |
| `watch?v=…&list=…` | Treats it as a single video but offers a one-click "analyze the whole playlist instead" |

**How many to list** accepts `10`, `25`, `all`, or any number. That controls how
many are *fetched and shown* — you then tick exactly what you want. Select
all / none / invert are there for the lazy path.

Anything already in your library shows an **In library** badge and a disabled
checkbox, so you can't queue a duplicate. Failed items stay selectable and get
a Retry button.

**Downloads run in a background thread pool** (`CONCURRENCY`, default 2). The
queue panel polls every 1.5s while anything is active and shows per-video
percentage, speed, ETA, and which ffmpeg post-processor is currently running.
The UI never blocks.

**When the queue drains you get an email** summarising what landed and what
failed (`NOTIFY_MODE=batch`), or one per video (`each`).

---

## Plex layout

This is the layout you asked for — creators as shows:

```
/downloads/                                  ← your Plex TV Shows library root
└── Veritasium/
    ├── poster.jpg                           ← channel avatar, becomes the show poster
    └── Season 01/
        ├── Veritasium - S01E01 - How Big Is Infinity (2026-08-29).mp4
        ├── Veritasium - S01E01 - How Big Is Infinity (2026-08-29).jpg   ← episode thumb
        ├── Veritasium - S01E01 - How Big Is Infinity (2026-08-29).en.srt
        └── Veritasium - S01E01 - How Big Is Infinity (2026-08-29).nfo
```

Two season strategies, set with `SEASON_MODE`:

- **`single`** (default, matches your spec) — everything lands in `Season 01`
  and episode numbers increment per channel: `S01E01`, `S01E02`, … Numbers are
  handed out **in download order, not upload order**. If you download a
  channel's back catalogue newest-first, episode 1 is the newest video. If
  chronological order matters to you, either select oldest-first, or use:
- **`year`** — `Season 2026/` and `S2026E0829` (year + MMDD). Uploads sort
  correctly forever, no matter what order you grab them in, and new videos slot
  into the right place automatically. This is what I'd run long-term; `single`
  is the default only because it's what you specified.

**In Plex:** add `/mnt/user/media/YouTube` as a **TV Shows** library, and in
Advanced set the Scanner to **Plex Series Scanner** and the Agent to
**Personal Media Shows**. Turn off "Use local assets" only if you *don't* want
the `poster.jpg` / episode `.jpg` / `.nfo` sidecars picked up — you probably do
want them, so leave local assets enabled.

`.nfo` files are written for Kodi/Jellyfin compatibility. Plex's Personal Media
agent ignores them; they cost nothing. Set `WRITE_NFO=false` if you'd rather
keep the folders clean.

---

## Test locally, then move to Unraid

`docker-compose.yml` is **identical on both machines**. Every host-specific
value — where files land, which port, which uid — comes from a `.env` file
sitting next to it. Moving to the array is a one-file swap, not an edit.

The paths *inside* the container are always `/downloads` and `/config`. Only
the host side of the mapping changes.

### 1. On your laptop

```bash
cd youtube-plex-dl
cp .env.local.example .env
# set PUID/PGID to your own — check with:  id -u ; id -g
docker compose up --build
```

Open `http://127.0.0.1:8080`, paste a short video, and watch `./data/downloads`
fill in. You'll get the real folder tree:

```
data/downloads/Some Channel/poster.jpg
data/downloads/Some Channel/Season 01/Some Channel - S01E01 - Title (2026-08-29).mp4
```

Two things the local `.env` does that the Unraid one doesn't:

- **`MAX_HEIGHT=360`** so test downloads finish in seconds. Delete it when
  you want real quality.
- **`PUID`/`PGID` set to you** instead of `99`/`100`, so `./data` doesn't end
  up owned by a uid that doesn't exist on your laptop.

Keep `SEASON_MODE`, `SEASON_NUMBER` and `TITLE_INCLUDE_DATE` the same in both
files — that's the whole point of testing locally, so the tree you approve is
the tree Plex gets.

To point Plex-on-your-laptop at it (optional but the most honest test), add
`./data/downloads` as a TV Shows library with the Personal Media Shows agent
and confirm the episodes group the way you want *before* touching the array.

### 2. On Unraid

**See [DEPLOY.md](DEPLOY.md) for the full walkthrough** — GitHub repo, automatic
image builds, install, and self-updating. The short version, once the image is
published to GHCR:

```bash
cp .env.unraid.example .env
# set IMAGE to your ghcr.io path; check MEDIA_PATH matches the share Plex scans
docker compose pull && docker compose up -d
```

Only `docker-compose.yml` and `.env` need to be on the array — Unraid pulls a
prebuilt image rather than compiling anything. Your local `.env` and `./data`
folder are both gitignored, so they don't follow you over.

If you'd rather use Unraid's template UI than compose, copy
`unraid-template.xml` to
`/boot/config/plugins/dockerMan/templates-user/my-youtube-plex-dl.xml` and it
appears under **Docker → Add Container → Template**. The same variables are
exposed there as fields.

### Without Docker at all

Handy while Docker Desktop is being uncooperative, or for poking at the code
with auto-reload. Both scripts use the same `./data` layout, so the folder
tree you get is still exactly what Plex will see on Unraid.

```bash
./scripts/run-local.sh                                        # macOS / Linux
```
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-local.ps1   # Windows
```

Needs Python 3.10+ and **ffmpeg on PATH** — yt-dlp cannot merge video+audio
without it. On Windows: `winget install Gyan.FFmpeg`, then open a *new*
terminal so PATH refreshes. Both scripts check for it and tell you if it's
missing.

This is a development convenience; the Docker path is what ships to Unraid.

### Docker Desktop won't connect (Windows)

```
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
```

That's the Docker *daemon* being down, not a problem with this project — the
named pipe only exists while Docker Desktop's Linux engine is actually running.
In rough order of likelihood:

1. **Docker Desktop isn't running,** or is still starting. Launch it and wait
   for the whale icon to read **Engine running** — it takes 30–60s from cold,
   and the CLI fails with exactly this error until then.
2. **Check what the CLI sees:** `docker version`. If the *Client* block prints
   but the *Server* block errors, the daemon is down. `docker context ls`
   should show `desktop-linux` as current; if not,
   `docker context use desktop-linux`.
3. **It's in Windows-containers mode.** Right-click the tray icon →
   *Switch to Linux containers*. The `dockerDesktopLinuxEngine` pipe only
   exists in Linux mode.
4. **WSL2 is stale or broken.** `wsl --update`, then `wsl --shutdown`, then
   restart Docker Desktop. Settings → General → *Use the WSL 2 based engine*
   should be ticked.
5. **Still stuck:** quit Docker Desktop fully (tray → Quit, not just close the
   window), restart the **Docker Desktop Service** in `services.msc`, and
   relaunch. Failing that, Settings → Troubleshoot → *Clean / Purge data*
   resets the engine — it removes local images and containers, but nothing in
   this project.

Nothing above touches the project. Once `docker version` prints a Server block,
`docker compose up --build` works as written. In the meantime `run-local.ps1`
gets you the same app without Docker.

---

## Things you need to change for your setup

All of these live in `.env` now:

1. **`MEDIA_PATH`.** On Unraid I guessed `/mnt/user/media/YouTube`. It **must
   be the same host share Plex scans** — if Plex maps that share to `/tv/YouTube`
   inside its own container, that's fine, they just both need to point at the
   same place on the array.
2. **`APPDATA_PATH`.** `/mnt/user/appdata/youtube-plex-dl`. Holds the SQLite DB
   and an optional `cookies.txt`. Back this up if you care about the history.
3. **`WEBUI_PORT`.** `8080`. Unraid's own UI is 80/443 so that's usually free,
   but binhex/linuxserver containers like 8080 too.
4. **`PUID`/`PGID`.** `99:100` (`nobody:users`) on Unraid. Match whatever your
   Plex container writes as, or Plex may not be able to read new files.
5. **`TZ`.** `America/Denver`. Only affects log timestamps and email dates.
6. **`SEASON_MODE`.** `single` as you specified; `year` if you want
   chronological ordering (see above). **Changing it later does not rename
   existing files** — new downloads use the new scheme and you'd have a mixed
   library. Decide during local testing, before you bulk-download.
7. **SMTP block.** `NOTIFY_EMAIL=false` while testing. For Gmail you need a
   16-character **App Password** — regular account passwords are rejected.
   Verify with `curl -X POST http://<ip>:8080/email/test` before relying on it.
8. **`MAX_HEIGHT`.** `0` grabs the best available, including 4K/8K where
   YouTube offers it. `1080` if you'd rather not fill the array.

---

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `OUTPUT_DIR` | `/downloads` | Container-side library root. Leave it; change the volume mapping instead. |
| `CONFIG_DIR` | `/config` | SQLite DB + optional `cookies.txt`. |
| `PORT` | `8080` | Container-side listen port. |
| `APP_TITLE` | `YouTube → Plex` | Shown in the header and email subjects. |
| `SEASON_MODE` | `single` | `single` or `year`. |
| `SEASON_NUMBER` | `1` | Season folder number for `single` mode. |
| `TITLE_INCLUDE_DATE` | `true` | Appends ` (YYYY-MM-DD)` to the episode title. |
| `FILENAME_MAX_LEN` | `150` | Title truncation before the channel/episode prefix. |
| `MERGE_FORMAT` | `mp4` | `mp4` (best Plex direct-play odds) or `mkv`. |
| `MAX_HEIGHT` | `0` | `0` = unlimited, else `1080` / `1440` / `2160`. |
| `CONCURRENCY` | `2` | Parallel downloads. |
| `EMBED_METADATA` | `true` | Title/description/chapters into the container. |
| `EMBED_THUMBNAIL` | `true` | Cover art inside the file. |
| `WRITE_THUMBNAIL` | `true` | Sidecar `.jpg` next to the video (Plex episode thumb). |
| `WRITE_CHANNEL_POSTER` | `true` | Downloads the channel avatar to `poster.jpg` once per channel. |
| `WRITE_NFO` | `true` | Episode `.nfo` sidecar. |
| `SUBTITLES` | `true` | Master switch. |
| `SUBTITLE_LANGS` | `en.*` | yt-dlp language spec, comma separated. |
| `SUBTITLE_AUTO` | `true` | Include YouTube's auto-generated captions. |
| `EMBED_SUBTITLES` | `true` | Mux subs into the video. |
| `WRITE_SUBTITLE_FILES` | `true` | Also keep `.srt` sidecars. |
| `SPONSORBLOCK` | `false` | Strips sponsor/self-promo segments when enabled. |
| `RATE_LIMIT` | *(unset)* | e.g. `10M` to throttle. |
| `COOKIES_FILE` | *(unset)* | Path to a Netscape `cookies.txt` for age-restricted or bot-checked videos. |
| `PROBE_LIMIT` | `60` | Default entries fetched per analyze. |
| `CHANNEL_PANEL_LIMIT` | `24` | Videos in the "more from this channel" panel. |
| `NOTIFY_EMAIL` | `false` | Master switch for email. |
| `NOTIFY_MODE` | `batch` | `batch` (one summary per drained queue) or `each`. |
| `NOTIFY_ON_FAILURE` | `true` | Include failures in the summary. |
| `SMTP_HOST` / `SMTP_PORT` | — / `587` | |
| `SMTP_TLS` / `SMTP_SSL` | `true` / `false` | STARTTLS (587) vs implicit TLS (465). |
| `SMTP_USER` / `SMTP_PASS` | — | Gmail needs an App Password. |
| `SMTP_FROM` / `SMTP_TO` | — | `SMTP_TO` is comma-separated. |
| `PUID` / `PGID` / `UMASK` | `99` / `100` / `002` | Unraid ownership. |

---

## Project structure

```
youtube-plex-dl/
├── Dockerfile
├── docker-compose.yml
├── unraid-template.xml
├── requirements.txt
├── .env.example
├── .dockerignore / .gitignore
├── docker/
│   └── entrypoint.sh          drops to PUID/PGID via gosu, prints config banner
└── app/
    ├── main.py                FastAPI routes + htmx partials
    ├── config.py              every env var, one place
    ├── db.py                  SQLite schema, dedupe, episode counters
    ├── ytdlp_service.py       URL classification + metadata probing
    ├── naming.py              Plex path/filename builder, episode allocation, NFO
    ├── downloader.py          worker pool, yt-dlp options, progress hooks
    ├── notify.py              SMTP summaries
    ├── templates/
    │   ├── base.html  index.html  history.html
    │   └── partials/  results.html  channel_panel.html  queue.html
    │                  video_card.html  toast.html
    └── static/                app.css  app.js  favicon.svg
```

### How the pieces fit

`main.py` never downloads anything. `POST /analyze` classifies the URL, probes
it in a worker thread, caches the normalised results in memory under a token
(1h TTL), and returns an HTML fragment. `POST /enqueue` looks the selected IDs
up in that cache and pushes rows into SQLite + an in-process `queue.Queue`.

`downloader.py`'s threads pull from that queue. Each job re-probes the single
video (flat playlist entries don't carry `upload_date` or `description`),
allocates an episode number under a lock, builds the path, then runs yt-dlp with
progress hooks that write throttled updates back to SQLite every ~0.7s. The
queue panel just reads those rows. Nothing shared between the web layer and the
workers except the database and the queue, which keeps the whole thing easy to
reason about.

### Persistence and dedupe

`downloads.video_id` is `UNIQUE`. Queueing checks status first: `completed`,
`downloading` and `queued` are skipped and reported back as "already
downloaded". `failed` rows are revived on retry. **Forget** on the history page
deletes the row so a video can be pulled again (it does not delete the file).

State survives restarts: anything left `downloading`/`queued` when the container
stops is marked failed on boot with "Interrupted by restart", so it's obvious
and one click from a retry.

---

## API (for scripting / Unraid notifications)

```
GET  /healthz            → "ok"  (also the container healthcheck)
GET  /api/status         → {counts, active[], output_dir, season_mode}
POST /email/test         → sends a test email
POST /retry/{video_id}
POST /forget/{video_id}
```

---

## Notes and caveats

- **Not tested against live YouTube in my sandbox** — outbound network to
  YouTube and the CDNs is blocked here. URL classification, episode allocation
  and collision handling, path building, NFO generation, email rendering, every
  route, and the full analyze → queue → worker → history → retry loop were all
  exercised end to end with the network stubbed out; the one path that needs
  your box is the actual yt-dlp fetch. Watch `docker logs -f youtube-plex-dl`
  on the first download.
- **yt-dlp goes stale fast.** YouTube breaks extractors regularly. Rebuild the
  image (or add `pip install -U yt-dlp` to a cron) every few weeks. Symptom is
  always the same: everything suddenly fails with an extraction error.
- **Bot checks.** If you see "Sign in to confirm you're not a bot", export a
  `cookies.txt` from a logged-in browser, drop it in your appdata folder, and
  set `COOKIES_FILE=/config/cookies.txt`.
- **Members-only / age-restricted** videos need the same cookies file.
- **`single` mode episode numbers are download-ordered**, not upload-ordered —
  see the Plex layout section.
- **Two uploads on the same day in `year` mode** would collide on `SxxxxEMMDD`,
  so the second walks forward to the next free number. Its filename will read
  one day later than its actual upload date; the title still carries the real
  date. Rare, and only in `year` mode.
- Rename/move a file by hand and the DB still thinks it's downloaded (that's
  the point — it stops re-downloads). Use **Forget** if you want it back.
- Be sensible about what you pull down — this is aimed at your own personal
  library, not at republishing other people's work.
