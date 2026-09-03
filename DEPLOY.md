# Deploying to Unraid

End-to-end: push the code to GitHub, have GitHub build the container image for
you, pull that image on Unraid, and keep it updating itself.

**The shape of it:** GitHub Actions builds the image and pushes it to GitHub
Container Registry (GHCR). Unraid never compiles anything — it just pulls a
finished image, the same way it pulls linuxserver.io containers. Your laptop is
the only machine that needs git.

Substitute your own GitHub username for `msmigiel22` throughout.

---

## Part 1 — Put the code on GitHub

### 1.1 Install git and sign in

On Windows, if you don't already have them:

```powershell
winget install Git.Git
winget install GitHub.cli
```

Open a **new** terminal so PATH refreshes, then:

```powershell
gh auth login
```

Choose **GitHub.com → HTTPS → Login with a web browser**, and paste the
one-time code it shows you.

### 1.2 Create the repo and push

From inside the project folder:

```powershell
cd path\to\youtube-plex-dl

git init -b main
git add .
git commit -m "Initial commit: YouTube to Plex downloader"

gh repo create youtube-plex-dl --private --source=. --remote=origin --push
```

`--private` is the right call here — your `.env` is gitignored, but a private
repo means a leaked SMTP password is a much smaller problem. The container
image can still be public independently (see 2.2).

> **Check before you push:** `git status --short` should not list `.env`,
> `data/`, or `.venv/`. If it does, `.gitignore` isn't being applied — stop and
> fix that first. A committed `.env` means your Gmail app password is in the
> repo history.

Prefer the web UI? Create an empty repo at github.com/new (no README, no
.gitignore — you already have both), then:

```powershell
git remote add origin https://github.com/msmigiel22/youtube-plex-dl.git
git push -u origin main
```

---

## Part 2 — Let GitHub build the image

### 2.1 The workflow is already there

`.github/workflows/docker-publish.yml` ships with the project. It builds and
pushes on:

| Trigger | What you get |
|---|---|
| Push to `main` | `:latest` and `:sha-abc1234` |
| Push a tag like `v1.0.0` | `:1.0.0`, `:1.0`, and `:latest` |
| **Every Monday, automatically** | a fresh `:latest` with the newest yt-dlp |
| The "Run workflow" button | same as the weekly build, on demand |

The weekly rebuild is the important one. `requirements.txt` deliberately leaves
`yt-dlp` unpinned, and YouTube breaks extractors every few weeks — this is what
keeps downloads working without you doing anything. Scheduled and manual runs
build with `no-cache` on purpose, because a cached pip layer would happily
reinstall the *same* yt-dlp and quietly defeat the point.

Watch the first run under the repo's **Actions** tab. Three or four minutes.

### 2.2 Make the image public (recommended)

By default a GHCR package inherits the repo's visibility, so a private repo
gives you a private image — and Unraid would then need to `docker login` with a
personal access token before every pull.

Far simpler to make just the *image* public while the *code* stays private:

1. github.com/msmigiel22 → **Packages** tab → `youtube-plex-dl`
2. **Package settings** (right-hand side)
3. **Danger Zone → Change visibility → Public**

Nothing sensitive is in the image — it's the app source, which you wrote, plus
Python and ffmpeg. No `.env`, no database, no credentials (`.dockerignore`
excludes all of them).

**If you'd rather keep it private,** run this once on Unraid instead, using a
classic PAT with `read:packages` scope from
github.com/settings/tokens:

```bash
echo "YOUR_PAT" | docker login ghcr.io -u msmigiel22 --password-stdin
```

The credential persists in `/root/.docker/config.json`, which survives reboots
on Unraid.

### 2.3 Confirm the image exists

```powershell
docker pull ghcr.io/msmigiel22/youtube-plex-dl:latest
```

If that works from your laptop, Unraid will manage it too.

---

## Part 3 — Install on Unraid

### 3.1 Prerequisites

From **Apps** (Community Applications), install:

- **Docker Compose Manager** — gives Unraid `docker compose`
- **User Scripts** — for the scheduled auto-update in Part 4

### 3.2 Create the folders

Unraid terminal (top-right **>_** icon):

```bash
mkdir -p /mnt/user/media/YouTube
mkdir -p /mnt/user/appdata/youtube-plex-dl
chown -R nobody:users /mnt/user/media/YouTube /mnt/user/appdata/youtube-plex-dl
```

`/mnt/user/media/YouTube` is the folder Plex will scan. If your media share is
named something else, use that path here and in `.env` — it has to match.

### 3.3 Create the stack

**Docker → Compose → Add New Stack**, name it `youtube-plex-dl`, then click the
gear → **Edit Stack → Compose File** and paste in the `docker-compose.yml` from
the project.

Gear → **Edit Stack → Env File**, and paste your `.env.unraid.example` contents
with these lines checked:

```ini
IMAGE=ghcr.io/msmigiel22/youtube-plex-dl:latest
MEDIA_PATH=/mnt/user/media/YouTube
APPDATA_PATH=/mnt/user/appdata/youtube-plex-dl
WEBUI_PORT=8080
PUID=99
PGID=100
TZ=America/Denver
SEASON_MODE=single
NOTIFY_EMAIL=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=msmigiel22@gmail.com
SMTP_PASS=your-16-char-app-password
SMTP_FROM=msmigiel22@gmail.com
SMTP_TO=msmigiel22@gmail.com
```

Only those two files go on the array. No source code, no build step.

Then **Compose Up**.

Prefer the command line? Same thing:

```bash
mkdir -p /boot/config/plugins/compose.manager/projects/youtube-plex-dl
cd /boot/config/plugins/compose.manager/projects/youtube-plex-dl
# put docker-compose.yml and .env here
docker compose pull
docker compose up -d
docker compose logs -f
```

The startup banner prints the resolved library path, uid/gid, and yt-dlp
version. Worth reading once — it's the fastest way to catch a wrong path.

### 3.4 First run

Open `http://<unraid-ip>:8080` and download one short video. Then check that
the file landed where you expect and is owned correctly:

```bash
find /mnt/user/media/YouTube -type f -printf '%u:%g  %p\n' | head
```

Owner should be `nobody:users`. If it's `root:root`, your `PUID`/`PGID` didn't
apply — check the `.env` file is actually being read (`docker compose config`
prints the resolved values).

### 3.5 Point Plex at it

**Plex → Settings → Libraries → Add Library → TV Shows**, folder
`/mnt/user/media/YouTube` (as Plex's container sees it — if Plex maps that
share to `/tv`, browse to `/tv/YouTube`).

Then **Advanced**, before you save:

- **Scanner:** Plex Series Scanner
- **Agent:** Personal Media Shows
- **Use local assets:** on — this is what picks up `poster.jpg`, the episode
  thumbnails and the `.nfo` files

Get this right on an empty library. Changing the agent later means removing and
re-adding the library.

---

## Part 4 — Keep it updated

Two ways. Both do the same job; pick one, don't run both.

### Option A — Scheduled script (recommended)

`scripts/unraid-update.sh` pulls, and recreates the container **only if the
image actually changed and nothing is mid-download**. That second part matters:
recreating a container during a download kills the transfer. Nothing is lost —
the app marks interrupted items failed on restart — but you'd have to re-queue
them by hand.

It reads `/api/status` and skips the run if the queue is busy, so a nightly
schedule never lands on top of a long download.

**Settings → User Scripts → Add New Script**, name it
`update-youtube-plex-dl`, click **Edit Script**, paste the file's contents,
save. Set the schedule dropdown to **Custom** and enter:

```
0 4 * * *
```

Daily at 4am. Click **Run Script** once to test it — it prints exactly what it
did. `--dry-run` reports without changing anything.

No extra container, no Docker socket exposed, and it tells you if the container
fails to come back healthy.

### Option B — Watchtower

Already defined in `docker-compose.yml` behind a profile, so it's opt-in:

```bash
docker compose --profile autoupdate up -d
```

It's scoped by label (`WATCHTOWER_LABEL_ENABLE=true`), so it will only ever
touch this container — nothing else on your array is at risk.

Two honest caveats. It mounts `/var/run/docker.sock`, which is effectively root
on the host — fine for a container you built, but it's a real consideration.
And upstream Watchtower has been quiet for a while; it works, but it isn't
actively developed. Option A avoids both issues, which is why it's the
recommendation.

### Updating the app itself

Change code on your laptop, then:

```powershell
git add .
git commit -m "Whatever changed"
git push
```

Actions rebuilds and pushes `:latest` in a few minutes. Unraid picks it up on
its next scheduled run, or immediately:

```bash
cd /boot/config/plugins/compose.manager/projects/youtube-plex-dl
docker compose pull && docker compose up -d
```

### Pinning instead

If you'd rather updates be deliberate, tag releases and pin to one:

```powershell
git tag v1.0.0 && git push --tags
```

```ini
IMAGE=ghcr.io/msmigiel22/youtube-plex-dl:1.0.0
```

You then update by editing that one line. **But** you also lose the automatic
yt-dlp refresh, which is the main thing keeping downloads working — so if you
pin, plan on bumping the tag every month or so.

---

## Rollback

Every build is tagged with its commit, so going back is one line. Find the tag
under the repo's **Packages** page, then:

```ini
IMAGE=ghcr.io/msmigiel22/youtube-plex-dl:sha-abc1234
```

```bash
docker compose up -d
```

Your database and downloads are untouched — they live in the volumes, not the
image. Rolling back the app never risks your library.

---

## Troubleshooting

**`denied` or `manifest unknown` when pulling on Unraid**
The package is still private. Either make it public (2.2) or `docker login
ghcr.io` with a `read:packages` PAT.

**Actions fails with `installation not allowed to Create organization package`**
The workflow needs write access to packages. Repo **Settings → Actions →
General → Workflow permissions → Read and write permissions**.

**`failed to solve: failed to read dockerfile` on Unraid**
The pull didn't succeed, so Compose fell back to building — and there's no
source on the array. Fix the pull (usually 2.2), or delete the `build: .` line
from the Unraid copy of `docker-compose.yml`; it's only there for local
development and is otherwise ignored once the image is present.

**Container starts, then restarts in a loop**
`docker compose logs --tail 50 youtube-plex-dl`. Usually `MEDIA_PATH` pointing
somewhere that doesn't exist, or a permission problem on `APPDATA_PATH`.

**Downloads fail with an extraction error, all of them at once**
YouTube changed something and yt-dlp needs updating. Trigger the workflow
manually (Actions → Build and publish image → Run workflow), then pull on
Unraid. This is what the weekly build exists to prevent.

**"Sign in to confirm you're not a bot"**
Export a `cookies.txt` from a logged-in browser, drop it in
`/mnt/user/appdata/youtube-plex-dl/`, and set `COOKIES_FILE=/config/cookies.txt`
in `.env`.

**Plex sees the files but shows them as one show / wrong episodes**
Wrong scanner or agent. Should be Plex Series Scanner + Personal Media Shows.
Fixing it means removing and re-adding the library.

**Nothing appears in Plex at all**
Check the file landed (`find /mnt/user/media/YouTube -type f`), then that Plex
can read it (owner `nobody:users`), then trigger a manual library scan.
