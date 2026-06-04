# Video Viewer

![banner](assets/images/banner.png)

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
│   └── profile.html             # User profile — password, avatar, language
├── assets/                      # Static assets (banner image, etc.)
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

Videos are organised as `TYPE/NAME/...` under `VIDEO_ROOT`. The top-level folder is the **type** (e.g. Drama, Movie, Anime), the second level is the **show name**, and episodes are `.mp4` files named `EP##` or `EP## | Episode Title`:

```text
videos/
└── Drama/                         # TYPE — top-level category
    └── My Show/                   # NAME — the show
        ├── EP01.mp4               # title shown as "EP01"
        ├── EP01.vtt               # optional WebVTT subtitles
        ├── EP02 | The Pilot.mp4   # title shown as "The Pilot"
        └── Season 2/              # optional sub-section
            └── EP01.mp4
```

Files without an `EP##` prefix are assigned index `01` and sorted by filename.

## Features

- **Login with rate limiting** — 3 failed attempts triggers a 15-minute IP ban
- **User profiles** — change password, upload an avatar (JPG/PNG/GIF/WEBP, max 2 MB), and set display language
- **Language preference** — switch between English (`en`) and Traditional Chinese (`zh-TW`) per account
- **Admin panel** — add/delete/disable users, view online status and last login, manage IP bans and meme videos
- **User enable/disable** — admins can disable accounts to block login without deleting them
- **Online presence** — admin panel shows which users are currently active
- **Meme page** — plays a random YouTube video after login; users can skip for now, today, this week, or always
- **Video permissions** — restrict each user to specific shows, sub-folders, or individual files; admins always have full access
- **Watch history** — playback position is saved per file so users can resume where they left off
- **Episode titles** — name files `EP## | Title.mp4` to display a human-readable title in the UI
- **Subtitle support** — place a `.vtt` sidecar next to each `.mp4` for in-browser subtitles
- **Range requests** — supports seeking via HTTP `Range` header
- **Changelog** — in-app changelog at `/changelog`

## Watch History

The app automatically saves each user's playback position for every file. When a video is reopened, it resumes from the last saved position. Watch history can be cleared from the user's profile page.

## Video Permissions

In the admin panel, click **Perms** on any non-admin user to open the permissions modal.

- **Full access (unrestricted)** — user sees the entire library (default for new accounts)
- **Type-level** — check a type folder (e.g. Drama) to grant access to all shows inside it
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
