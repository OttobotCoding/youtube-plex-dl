# Deploying to Unraid

End-to-end: push the code to GitHub, have GitHub build the container image for
you, pull that image on Unraid, and keep it updating itself.

**The shape of it:** GitHub Actions builds the image and pushes it to GitHub
Container Registry (GHCR). Unraid never compiles anything — it just pulls a
finished image, the same way it pulls linuxserver.io containers. Your laptop is
the only machine that needs git.

Substitute your own GitHub username for `ottobotcoding` throughout.

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
git remote add origin https://github.com/ottobotcoding/youtube-plex-dl.git
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

1. github.com/ottobotcoding → **Packages** tab → `youtube-plex-dl`
2. **Package settings** (right-hand side)
3. **Danger Zone → Change visibility → Public**

Nothing sensitive is in the image — it's the app source, which you wrote, plus
Python and ffmpeg. No `.env`, no database, no credentials (`.dockerignore`
excludes all of them).

**If you'd rather keep it private,** run this once on Unraid instead, using a
classic PAT with `read:packages` scope from
github.com/settings/tokens:

```bash
echo "YOUR_PAT" | docker login ghcr.io -u ottobotcoding --password-stdin
```

The credential persists in `/root/.docker/config.json`, which survives reboots
on Unraid.

### 2.3 Confirm the image exists

```powershell
docker pull ghcr.io/ottobotcoding/youtube-plex-dl:latest
```

If that works from your laptop, Unraid will manage it too.

---

## Part 3 — Install on Unraid

### 3.1 Prerequisites

From **Apps** (Community Applications), install:

- **Docker Compose Manager** — gives Unraid `docker compose`
- **User Scripts** — for the scheduled auto-update in Part 5

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
IMAGE=ghcr.io/ottobotcoding/youtube-plex-dl:latest
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

**Delete the `build: .` line** while you're in the Compose File editor. Near the
top of the `youtube-plex-dl` service you'll see:

```yaml
    image: ${IMAGE:-youtube-plex-dl:local}
    build: .
    container_name: youtube-plex-dl
```

Remove the middle line. This is not optional on Unraid. When a service has both
`image:` and `build:`, `docker compose up` does **not** pull a missing image —
it builds instead, goes looking for a Dockerfile that isn't on the array, and
fails with `failed to read dockerfile: open Dockerfile`. That message sounds
like a broken build; it actually means Compose never contacted the registry at
all. With no build section, pulling is the only path available.

Keep the line in your laptop's copy — that's what makes `docker compose up
--build` work for local development. The two copies differing by one line is
the intended state.

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

### 3.6 Give it an icon in the Docker tab

A Compose-managed container takes its icon from a **label**, not from
`unraid-template.xml` — that file is only read by the template installer. The
labels are already in `docker-compose.yml`:

```yaml
net.unraid.docker.icon: "${ICON_URL:-http://[IP]:[PORT:8080]/static/icon.png}"
net.unraid.docker.webui: "http://[IP]:[PORT:8080]/"
net.unraid.docker.managed: "composeman"
```

`[IP]` and `[PORT:8080]` are substituted by Unraid when it renders the page. The
`webui` one is worth having on its own — it's what makes the container's
**WebUI** menu entry work.

The default points at `/static/icon.png`, which the app serves itself. That
needs `icon.png` to be inside the image, so it only works **after** you commit
the new `app/static/icon.png` and let Actions rebuild:

```powershell
git add app/static/icon.png icon.png docker-compose.yml
git commit -m "Add container icon"
git push
```

Then on Unraid, `docker compose pull && docker compose up -d`.

Two alternatives if you want it working before that rebuild, or prefer not to
depend on the container being up:

- **Serve it off the flash.** Copy `icon.png` to
  `/boot/config/plugins/dockerMan/images/youtube-plex-dl-icon.png`. Unraid
  looks in that folder for a cached icon before fetching a remote one.
- **Point at GitHub.** Only works if you make the repo public — a `raw
  .githubusercontent.com` URL on a private repo returns 404 to Unraid:
  ```ini
  ICON_URL=https://raw.githubusercontent.com/ottobotcoding/youtube-plex-dl/main/icon.png
  ```

`ICON_URL` in `.env` overrides the default, so you can switch between these
without editing the compose file.

Worth knowing: I couldn't test Unraid's label handling directly, so if the icon
doesn't appear after a `docker compose up -d` and a browser refresh, the
flash-drive route above is the one that doesn't depend on it.

---

## Part 4 — Email notifications

When the download queue drains, you get one email listing everything that
completed and anything that failed, with file paths and sizes. Failures never
break a download — if SMTP is misconfigured the send is logged as a warning and
the app carries on.

### 4.1 Create a Gmail App Password

Gmail rejects normal account passwords over SMTP. You need an **App Password**,
which requires 2-Step Verification on the account first.

1. `myaccount.google.com/security` → turn on **2-Step Verification** if it isn't
   already. App Passwords don't exist as an option until this is on.
2. Go to `myaccount.google.com/apppasswords`
3. Name it something like `unraid youtube-plex-dl` → **Create**
4. Copy the 16 characters. Google displays them as four groups of four with
   spaces — **strip the spaces**. `abcd efgh ijkl mnop` becomes
   `abcdefghijklmnop`.

You only see it once. If you lose it, delete that entry and make a new one.

### 4.2 Put it in `.env`

Gear → **Edit Stack → Env File**:

```ini
NOTIFY_EMAIL=true
NOTIFY_MODE=batch
NOTIFY_ON_FAILURE=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_TLS=true
SMTP_SSL=false
SMTP_USER=msmigiel22@gmail.com
SMTP_PASS=abcdefghijklmnop
SMTP_FROM=msmigiel22@gmail.com
SMTP_TO=msmigiel22@gmail.com
```

`SMTP_TO` accepts a comma-separated list if you want it going to more than one
address. Then bring the stack back up so it picks up the new values:

```bash
docker compose up -d
```

Email stays off unless `NOTIFY_EMAIL=true` **and** `SMTP_HOST`, `SMTP_TO` and
one of `SMTP_FROM`/`SMTP_USER` are all set. The startup banner in
`docker compose logs` says `Email notifications: on` or
`off (SMTP not configured)` — check that line first if nothing arrives.

### 4.3 Test it before trusting it

```bash
curl -X POST http://<unraid-ip>:8080/email/test
```

You get a short HTML snippet back saying whether it sent. If it reports a
failure, the real SMTP error is in the container log:

```bash
docker compose logs --tail 30 youtube-plex-dl
```

Common ones:

| Log says | Cause |
|---|---|
| `Username and Password not accepted` | Using your account password, not an App Password — or spaces left in it |
| `Connection unexpectedly closed` | Port/TLS mismatch. 587 needs `SMTP_TLS=true`, `SMTP_SSL=false`; 465 needs the reverse |
| `Email notifications: off` at startup | One of the required vars is missing or `NOTIFY_EMAIL` isn't `true` |

### 4.4 Choose when mail arrives

- **`NOTIFY_MODE=batch`** (default) — one summary once the whole queue finishes
  and stays idle a few seconds. Queue 40 videos, get one email.
- **`NOTIFY_MODE=each`** — one email per video. Fine for occasional single
  downloads, noisy for a bulk grab.
- **`NOTIFY_ON_FAILURE=false`** — only tell me about successes.

Other providers work the same way. For implicit-TLS hosts on port 465, set
`SMTP_PORT=465`, `SMTP_SSL=true`, `SMTP_TLS=false`.

---

## Part 5 — Keep it updated

The scheduled script below is the whole mechanism — set it up once and updates
land on their own.

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

> Earlier versions of this project also shipped a Watchtower service behind a
> compose profile. It was removed: it only ran if you explicitly opted in, it
> needed the Docker socket mounted (effectively root on the host), and upstream
> has been quiet for a while. The script above does the same job better.

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
IMAGE=ghcr.io/ottobotcoding/youtube-plex-dl:1.0.0
```

You then update by editing that one line. **But** you also lose the automatic
yt-dlp refresh, which is the main thing keeping downloads working — so if you
pin, plan on bumping the tag every month or so.

---

## Rollback

Every build is tagged with its commit, so going back is one line. Find the tag
under the repo's **Packages** page, then:

```ini
IMAGE=ghcr.io/ottobotcoding/youtube-plex-dl:sha-abc1234
```

```bash
docker compose up -d
```

Your database and downloads are untouched — they live in the volumes, not the
image. Rolling back the app never risks your library.

---

## Troubleshooting

**`unauthorized` when pulling on Unraid, even though the package is public**
GHCR returns `unauthorized` for three different situations, which is why this
one is confusing. Work through them in order:

1. **A stale stored credential.** Anonymous pulls of a public package work
   fine — but if `/root/.docker/config.json` holds an old or wrong `ghcr.io`
   entry, Docker sends *those* instead of asking anonymously, and they get
   rejected. `docker logout ghcr.io`, then retry the pull. This is the most
   common cause.
2. **The tag doesn't exist.** A green Actions run doesn't guarantee `:latest` —
   that tag is only applied on the default branch. If yours is `master` rather
   than `main`, or the run came from a tag push, you may have `sha-abc1234` and
   nothing else. GHCR answers a missing manifest with the same `unauthorized`.
3. **The package really is private.** Repo visibility and package visibility are
   separate switches; making the repo public does not touch the package.

This settles all three at once, using no credentials at all — exactly what a
public pull does:

```bash
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:ottobotcoding/youtube-plex-dl:pull&service=ghcr.io" | grep -o '"token":"[^"]*' | cut -d'"' -f4)
curl -s -H "Authorization: Bearer $TOKEN" https://ghcr.io/v2/ottobotcoding/youtube-plex-dl/tags/list
```

Tags listed including `latest` → it was cause 1. Tags listed without `latest` →
cause 2; point `IMAGE` at a tag that exists. Empty token or a `DENIED` error →
cause 3; fix visibility at
`github.com/users/ottobotcoding/packages/container/youtube-plex-dl/settings`.

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
