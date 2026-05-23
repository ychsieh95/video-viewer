# Video Viewer

A self-hosted Flask video streaming app with user authentication, profile management, per-user video permissions, meme video control, and an admin panel.

## Directory Structure

```text
video-viewer/
├── app/                         # Flask application (copied into Docker image)
│   ├── main.py                  # Flask server — routes, auth, video serving
│   ├── settings.example.py      # Template for settings.py
│   ├── uwsgi.ini                # uWSGI worker config
│   ├── admin.html               # Admin page — users, IP bans, permissions, memes
│   ├── changelog.html           # Changelog page (/changelog)
│   ├── favicon.svg              # Site favicon
│   ├── index.html               # Video viewer UI
│   ├── login.html               # Login page
│   ├── meme.html                # Meme page shown after login
│   └── profile.html             # User profile — password change & avatar upload
├── Dockerfile
└── requirements.txt             # Python dependencies (flask, werkzeug)
```

## Running

### Docker (production)

```bash
# 1. Copy and edit credentials
cp app/settings.example.py app/settings.py

# 2. Build the image
docker build -t video-viewer .
```

**Option A — docker run:**

```bash
docker run -d \
  -p 8080:8080 \
  -e CONFIG_ROOT=/configs \
  -e VIDEO_ROOT=/videos \
  -v /path/to/configs:/configs \
  -v /path/to/videos:/videos:ro \
  --name video-viewer \
  video-viewer
```

**Option B — docker compose:**

```yaml
services:
  video-viewer:
    image: video-viewer
    container_name: video-viewer
    ports:
      - 8080:8080
    environment:
      - CONFIG_ROOT=/configs
      - VIDEO_ROOT=/videos
    volumes:
      - /path/to/configs:/configs
      - /path/to/videos:/videos:ro
```

```bash
docker compose up -d
```

App runs at `http://localhost:8080`.

### Local dev

```bash
cp app/settings.example.py app/settings.py   # set DEFAULT_ADMIN_USER / DEFAULT_ADMIN_PASS
pip install -r requirements.txt
cd app
VIDEO_ROOT=../videos python main.py           # http://localhost:5000
```

Pass a custom port as a second argument: `python main.py ../videos 8080`.

The default admin account is created from `settings.py` on first start if `users.json` is empty.

## Video Library Layout

Place videos under `videos/`. Each top-level folder is a show; episodes are `.mp4` files named `EP##` or `EP## | Episode Title`:

```text
videos/
└── My Show/
    ├── EP01.mp4                   # title shown as "EP01"
    ├── EP01.vtt                   # optional WebVTT subtitles
    ├── EP02 | The Pilot.mp4       # title shown as "The Pilot"
    └── Season 2/                  # optional sub-sections
        └── EP01.mp4
```

Use the scripts in `scripts/` to prepare files:

```bash
# Convert MKV → web-ready MP4 + VTT
bash scripts/convert_for_web.sh /path/to/source /path/to/output

# Normalize audio loudness to EBU R128 (-16 LUFS) — in-place, resumable
bash scripts/normalize_audio.sh [search_dir]
# Options: --log <file>  (custom log path)
#          --clean       (remove .loudnorm_done markers to force re-run)
```

## Features

- **Login with rate limiting** — 3 failed attempts triggers a 15-minute IP ban
- **User profiles** — change password and upload an avatar (JPG/PNG/GIF/WEBP, max 2 MB)
- **Admin panel** — add/delete users, view last login info, manage IP bans, manage meme videos
- **Meme page** — plays a random YouTube video after login; users can skip for now, today, this week, or always
- **Video permissions** — restrict each user to specific shows, sub-folders, or individual files; admins always have full access
- **Episode titles** — name files `EP## | Title.mp4` to display a human-readable title in the UI
- **Subtitle support** — place a `.vtt` sidecar next to each `.mp4` for in-browser subtitles
- **Range requests** — supports seeking via HTTP `Range` header
- **Changelog** — in-app changelog at `/changelog`

## Video Permissions

In the admin panel, click **Perms** on any non-admin user to open the permissions modal.

- **Full access (unrestricted)** — user sees the entire library (default for new accounts)
- **Show-level** — check a show folder to grant access to all its episodes
- **Section-level** — check a sub-folder (e.g. Season 2) within a show
- **File-level** — check individual episode files for fine-grained control

Permissions are stored as a list of paths in `users.json` under `allowed_paths`. `null` means unrestricted; a folder path covers all files beneath it.

## Meme Videos

After login, users are shown a random YouTube video from the meme list before being redirected to the viewer. The list is managed in the admin panel under **Meme Videos** — paste a YouTube URL or bare video ID to add, click Delete to remove. The list is persisted in `memes.json` under `CONFIG_ROOT`.

## Environment Variables

| Variable      | Default              | Description                                           |
|---------------|----------------------|-------------------------------------------------------|
| `CONFIG_ROOT` | *(app directory)*    | Directory for users.json, memes.json, and avatars/    |
| `VIDEO_ROOT`  | `app/videos`         | Path to the video directory                           |
| `VIDEO_USER`  | *(from settings.py)* | Default admin username (used on first start)          |
| `VIDEO_PASS`  | *(from settings.py)* | Default admin password (used on first start)          |
| `SECRET_KEY`  | random               | Flask session secret key                              |
