import json
import mimetypes
import os
import re
import secrets
import sys
from datetime import datetime, timezone, timedelta, date
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, send_file, abort, session, request, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

from settings import DEFAULT_ADMIN_USER, DEFAULT_ADMIN_PASS

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

_here = Path(__file__).parent
_default = _here / "videos" if (_here / "videos").is_dir() else _here
VIDEO_ROOT = (
    Path(sys.argv[1]).resolve() if len(sys.argv) > 1
    else Path(os.environ["VIDEO_ROOT"]).resolve() if os.environ.get("VIDEO_ROOT")
    else _default
)
_APP_DIR = _here
_CONFIG_DIR = (
    Path(os.environ["CONFIG_ROOT"]).resolve() if os.environ.get("CONFIG_ROOT")
    else _APP_DIR
)

USERS_FILE = _CONFIG_DIR / "users.json"
MEMES_FILE = _CONFIG_DIR / "memes.json"
DEFAULT_MEMES = ["dMTy6C4UiQ4", "dQw4w9WgXcQ"]
AVATARS_DIR = _CONFIG_DIR / "avatars"
AVATARS_DIR.mkdir(exist_ok=True)
ALLOWED_IMG = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_AVATAR_BYTES = 2 * 1024 * 1024

EP_RE = re.compile(r"EP(\d+)", re.IGNORECASE)
_EP_TITLE_RE = re.compile(r"^EP\d+\s*\|\s*(.+)$", re.IGNORECASE)
IGNORED = {".venv", "__pycache__", ".git", ".claude"}


def _ep_title(stem: str) -> str:
    m = _EP_TITLE_RE.match(stem)
    return m.group(1).strip() if m else stem


def _is_path_allowed(allowed_paths, video_path: str) -> bool:
    """None = unrestricted. Each entry is a folder or file path relative to VIDEO_ROOT;
    a folder entry covers all files beneath it. VTT files inherit their MP4's access."""
    if allowed_paths is None:
        return True
    checks = [video_path]
    if video_path.endswith(".vtt"):
        checks.append(video_path[:-4] + ".mp4")
    for rule in allowed_paths:
        for p in checks:
            if p == rule or p.startswith(rule + "/"):
                return True
    return False


def _avatar_basename(username: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", username)


# ── User store ────────────────────────────────────────────────────────────────

def load_users() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text())
    return {}


def save_users(users: dict) -> None:
    USERS_FILE.write_text(json.dumps(users, indent=2))


def load_memes() -> list:
    if MEMES_FILE.exists():
        return json.loads(MEMES_FILE.read_text())
    return list(DEFAULT_MEMES)


def save_memes(memes: list) -> None:
    MEMES_FILE.write_text(json.dumps(memes, indent=2))


_YT_ID_RE = re.compile(r'[A-Za-z0-9_-]{11}')


def _extract_yt_id(raw: str) -> str | None:
    raw = raw.strip()
    if re.fullmatch(r'[A-Za-z0-9_-]{11}', raw):
        return raw
    m = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', raw)
    return m.group(1) if m else None


def bootstrap():
    """Create default admin from env vars if no users exist yet."""
    users = load_users()
    if not users:
        username = DEFAULT_ADMIN_USER
        password = DEFAULT_ADMIN_PASS
        users[username] = {"hash": generate_password_hash(password), "admin": True}
        save_users(users)
        print(f"  Created default admin: {username} / {password}")


# ── Online tracking ───────────────────────────────────────────────────────────

_last_seen: dict = {}  # username -> datetime (utc)
ONLINE_THRESHOLD = timedelta(seconds=90)


def _touch(username: str) -> None:
    _last_seen[username] = datetime.now(timezone.utc)


def _is_online(username: str) -> bool:
    ts = _last_seen.get(username)
    return ts is not None and datetime.now(timezone.utc) - ts < ONLINE_THRESHOLD


# ── Auth decorators ───────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login", next=request.path))
        _touch(session["username"])
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login", next=request.path))
        if not session.get("is_admin"):
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ── Login rate limiting ───────────────────────────────────────────────────────

ATTEMPT_LIMIT = 3
BAN_DURATION  = timedelta(minutes=15)

_attempts: dict = {}  # ip -> {"count": int, "until": datetime | None}


def _client_ip() -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    return ip.split(",")[0].strip()


def _is_banned(ip: str) -> datetime | None:
    """Returns the ban-expiry datetime if still banned, else None."""
    entry = _attempts.get(ip)
    if entry and entry["until"] and datetime.now(timezone.utc) < entry["until"]:
        return entry["until"]
    return None


def _record_failure(ip: str) -> int:
    """Records a failure and returns remaining attempts (0 = just banned)."""
    entry = _attempts.setdefault(ip, {"count": 0, "until": None})
    entry["count"] += 1
    remaining = max(0, ATTEMPT_LIMIT - entry["count"])
    if entry["count"] >= ATTEMPT_LIMIT:
        entry["until"] = datetime.now(timezone.utc) + BAN_DURATION
    return remaining


def _reset(ip: str) -> None:
    _attempts.pop(ip, None)


# ── Auth routes ───────────────────────────────────────────────────────────────

LOGIN_HTML = (_APP_DIR / "login.html").read_text

@app.route("/favicon.svg")
def favicon():
    return send_file(_APP_DIR / "favicon.svg", mimetype="image/svg+xml")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        ip = _client_ip()
        ban_until = _is_banned(ip)
        if ban_until:
            remaining = int((ban_until - datetime.now(timezone.utc)).total_seconds() / 60) + 1
            error = f"Too many failed attempts. Try again in {remaining} minute(s)."
        else:
            u = request.form.get("username", "")
            p = request.form.get("password", "")
            users = load_users()
            user = users.get(u)
            if user and check_password_hash(user["hash"], p):
                _reset(ip)
                user["last_login_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                user["last_login_ip"] = ip
                users[u] = user
                save_users(users)
                session["username"] = u
                session["is_admin"] = user.get("admin", False)
                dest = request.args.get("next") or url_for("index")
                sep = "&" if "?" in dest else "?"
                dest = f"{dest}{sep}no_autoplay=1"
                return redirect(url_for("meme", next=dest))
            remaining = _record_failure(ip)
            if remaining:
                error = f"Invalid username or password. {remaining} attempt(s) remaining."
            else:
                error = f"Too many failed attempts. Try again in {int(BAN_DURATION.total_seconds() / 60)} minutes."
    return (_APP_DIR / "login.html").read_text().replace("{error}", error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Admin routes ──────────────────────────────────────────────────────────────

@app.route("/api/heartbeat", methods=["POST"])
@login_required
def heartbeat():
    return jsonify({"ok": True})


@app.route("/api/me")
@login_required
def me():
    users = load_users()
    user = users.get(session["username"], {})
    return jsonify({
        "username": session["username"],
        "admin": session.get("is_admin", False),
        "lang": user.get("lang", "en"),
    })


# ── Profile routes ────────────────────────────────────────────────────────────

@app.route("/profile")
@login_required
def profile():
    return (_APP_DIR / "profile.html").read_text()


@app.route("/api/profile/lang", methods=["PUT"])
@login_required
def set_lang():
    body = request.get_json(silent=True) or {}
    lang = body.get("lang") or "en"
    if lang not in ("en", "zh-TW"):
        return jsonify({"error": "Invalid language."}), 400
    users = load_users()
    users.setdefault(session["username"], {})["lang"] = lang
    save_users(users)
    return jsonify({"ok": True})


@app.route("/api/profile/password", methods=["POST"])
@login_required
def change_password():
    body = request.get_json(silent=True) or {}
    current = body.get("current") or ""
    new_pw = body.get("new") or ""

    if not current or not new_pw:
        return jsonify({"error": "Current and new password required."}), 400
    if len(new_pw) < 6:
        return jsonify({"error": "New password must be at least 6 characters."}), 400

    username = session["username"]
    users = load_users()
    user = users.get(username)
    if not user or not check_password_hash(user["hash"], current):
        return jsonify({"error": "Current password is incorrect."}), 400

    user["hash"] = generate_password_hash(new_pw)
    users[username] = user
    save_users(users)
    return jsonify({"ok": True})


@app.route("/api/profile/avatar", methods=["POST"])
@login_required
def upload_avatar():
    if "avatar" not in request.files:
        return jsonify({"error": "No file provided."}), 400
    f = request.files["avatar"]
    if not f.filename:
        return jsonify({"error": "No file selected."}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED_IMG:
        return jsonify({"error": "Unsupported file type. Use JPG, PNG, GIF, or WEBP."}), 400

    data = f.read(MAX_AVATAR_BYTES + 1)
    if len(data) > MAX_AVATAR_BYTES:
        return jsonify({"error": "File too large (max 2 MB)."}), 400

    username = session["username"]
    base = _avatar_basename(username)
    for old in AVATARS_DIR.glob(f"{base}.*"):
        old.unlink(missing_ok=True)

    (AVATARS_DIR / f"{base}{ext}").write_bytes(data)
    return jsonify({"ok": True})


@app.route("/api/profile/avatar/<username>")
@login_required
def serve_avatar(username: str):
    base = _avatar_basename(username)
    for ext in ALLOWED_IMG:
        path = AVATARS_DIR / f"{base}{ext}"
        if path.exists():
            mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            return send_file(path, mimetype=mime)
    abort(404)


@app.route("/admin")
@admin_required
def admin():
    return (_APP_DIR / "admin.html").read_text()


@app.route("/changelog")
@login_required
def changelog():
    return (_APP_DIR / "changelog.html").read_text()


@app.route("/admin/api/users", methods=["GET"])
@admin_required
def admin_list_users():
    users = load_users()
    return jsonify([
        {
            "username": u,
            "admin": data.get("admin", False),
            "last_login_at": data.get("last_login_at"),
            "last_login_ip": data.get("last_login_ip"),
            "online": _is_online(u),
        }
        for u, data in users.items()
    ])


@app.route("/admin/api/users", methods=["POST"])
@admin_required
def admin_add_user():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    is_admin = bool(body.get("admin", False))

    if not username or not password:
        return jsonify({"error": "Username and password required."}), 400

    users = load_users()
    if username in users:
        return jsonify({"error": f"User '{username}' already exists."}), 409

    users[username] = {"hash": generate_password_hash(password), "admin": is_admin}
    save_users(users)
    return jsonify({"ok": True}), 201


@app.route("/admin/api/users/<username>", methods=["DELETE"])
@admin_required
def admin_delete_user(username: str):
    users = load_users()
    if username not in users:
        return jsonify({"error": "User not found."}), 404
    if username == session["username"]:
        return jsonify({"error": "Cannot delete your own account."}), 400

    remaining_admins = [
        u for u, d in users.items()
        if d.get("admin") and u != username
    ]
    if users[username].get("admin") and not remaining_admins:
        return jsonify({"error": "Cannot delete the last admin."}), 400

    del users[username]
    save_users(users)
    return jsonify({"ok": True})


@app.route("/admin/api/bans", methods=["GET"])
@admin_required
def admin_list_bans():
    now = datetime.now(timezone.utc)
    return jsonify([
        {"ip": ip, "count": e["count"], "until": e["until"].isoformat(timespec="seconds")}
        for ip, e in _attempts.items()
        if e.get("until") and now < e["until"]
    ])


@app.route("/admin/api/bans", methods=["POST"])
@admin_required
def admin_ban_ip():
    body = request.get_json(silent=True) or {}
    ip = (body.get("ip") or "").strip()
    if not ip:
        return jsonify({"error": "IP required."}), 400
    _attempts[ip] = {"count": ATTEMPT_LIMIT, "until": datetime.now(timezone.utc) + BAN_DURATION}
    return jsonify({"ok": True}), 201


@app.route("/admin/api/bans/<ip>", methods=["DELETE"])
@admin_required
def admin_unban_ip(ip: str):
    _reset(ip)
    return jsonify({"ok": True})


@app.route("/admin/api/library")
@admin_required
def admin_library():
    result = []
    for show_dir in sorted(VIDEO_ROOT.iterdir()):
        if not show_dir.is_dir() or show_dir.name in IGNORED or show_dir.name.startswith("."):
            continue
        show: dict = {"name": show_dir.name, "path": show_dir.name, "children": []}
        for f in sorted(
            (f for f in show_dir.iterdir() if f.is_file() and f.suffix == ".mp4"),
            key=lambda f: (ep_num(f.name), f.name),
        ):
            show["children"].append({"name": f.name, "path": f"{show_dir.name}/{f.name}", "type": "file"})
        for sec_dir in sorted(d for d in show_dir.iterdir() if d.is_dir()):
            files = sorted(
                (f for f in sec_dir.iterdir() if f.is_file() and f.suffix == ".mp4"),
                key=lambda f: (ep_num(f.name), f.name),
            )
            if not files:
                continue
            show["children"].append({
                "name": sec_dir.name,
                "path": f"{show_dir.name}/{sec_dir.name}",
                "type": "folder",
                "children": [
                    {"name": f.name, "path": f"{show_dir.name}/{sec_dir.name}/{f.name}", "type": "file"}
                    for f in files
                ],
            })
        if show["children"]:
            result.append(show)
    return jsonify(result)


@app.route("/admin/api/users/<username>/permissions", methods=["GET"])
@admin_required
def admin_get_permissions(username: str):
    users = load_users()
    if username not in users:
        return jsonify({"error": "User not found."}), 404
    return jsonify({"allowed_paths": users[username].get("allowed_paths")})


@app.route("/admin/api/users/<username>/permissions", methods=["PUT"])
@admin_required
def admin_set_permissions(username: str):
    users = load_users()
    if username not in users:
        return jsonify({"error": "User not found."}), 404
    body = request.get_json(silent=True) or {}
    allowed = body.get("allowed_paths")
    if allowed is not None and not isinstance(allowed, list):
        return jsonify({"error": "allowed_paths must be null or a list."}), 400
    if isinstance(allowed, list):
        allowed = [p for p in allowed if isinstance(p, str)]
    users[username]["allowed_paths"] = allowed
    save_users(users)
    return jsonify({"ok": True})


# ── Video routes ──────────────────────────────────────────────────────────────

def ep_num(name: str) -> int:
    m = EP_RE.search(name)
    return int(m.group(1)) if m else 9999


def scan_videos(allowed_paths=None) -> list[dict]:
    result = []
    for show_dir in sorted(VIDEO_ROOT.iterdir()):
        if not show_dir.is_dir() or show_dir.name in IGNORED or show_dir.name.startswith("."):
            continue
        direct = sorted(
            [f for f in show_dir.iterdir() if f.is_file() and f.suffix == ".mp4"],
            key=lambda f: (ep_num(f.name), f.name),
        )
        for f in direct:
            path = f"{show_dir.name}/{f.name}"
            if not _is_path_allowed(allowed_paths, path):
                continue
            vtt = f.with_suffix(".vtt")
            ep = ep_num(f.name)
            result.append({
                "show": show_dir.name, "section": None, "ep": ep,
                "title": _ep_title(f.stem),
                "path": path,
                "vtt_path": f"{show_dir.name}/{vtt.name}" if vtt.exists() else None,
            })
        for sec_dir in sorted(d for d in show_dir.iterdir() if d.is_dir()):
            files = sorted(
                [f for f in sec_dir.iterdir() if f.is_file() and f.suffix == ".mp4"],
                key=lambda f: (ep_num(f.name), f.name),
            )
            for f in files:
                path = f"{show_dir.name}/{sec_dir.name}/{f.name}"
                if not _is_path_allowed(allowed_paths, path):
                    continue
                vtt = sec_dir / f.with_suffix(".vtt").name
                ep = ep_num(f.name)
                result.append({
                    "show": show_dir.name, "section": sec_dir.name, "ep": ep,
                    "title": _ep_title(f.stem),
                    "path": path,
                    "vtt_path": (
                        f"{show_dir.name}/{sec_dir.name}/{vtt.name}"
                        if vtt.exists() else None
                    ),
                })
    return result


@app.route("/meme")
@login_required
def meme():
    next_url = request.args.get("next") or url_for("index")
    users = load_users()
    skip = users.get(session["username"], {}).get("meme_skip")
    if skip == "always":
        return redirect(next_url)
    if isinstance(skip, dict):
        until = skip.get("until")
        if until and date.fromisoformat(until) >= date.today():
            return redirect(next_url)
    return (_APP_DIR / "meme.html").read_text().replace("{next}", next_url)


@app.route("/api/profile/meme-skip", methods=["GET"])
@login_required
def get_meme_skip():
    users = load_users()
    skip = users.get(session["username"], {}).get("meme_skip")
    return jsonify({"meme_skip": skip})


@app.route("/api/profile/meme-skip", methods=["PUT"])
@login_required
def set_meme_skip():
    body = request.get_json(silent=True) or {}
    preset = body.get("preset")
    users = load_users()
    user = users.setdefault(session["username"], {})
    if preset is None:
        user["meme_skip"] = None
    elif preset == "today":
        user["meme_skip"] = {"until": date.today().isoformat()}
    elif preset == "week":
        days_left = 6 - date.today().weekday()
        user["meme_skip"] = {"until": (date.today() + timedelta(days=days_left)).isoformat()}
    elif preset == "always":
        user["meme_skip"] = "always"
    else:
        return jsonify({"error": "Invalid preset."}), 400
    save_users(users)
    return jsonify({"ok": True})


@app.route("/api/memes")
@login_required
def list_memes():
    return jsonify(load_memes())


@app.route("/admin/api/memes", methods=["GET"])
@admin_required
def admin_list_memes():
    return jsonify(load_memes())


@app.route("/admin/api/memes", methods=["POST"])
@admin_required
def admin_add_meme():
    body = request.get_json(silent=True) or {}
    raw = (body.get("url") or "").strip()
    vid_id = _extract_yt_id(raw)
    if not vid_id:
        return jsonify({"error": "Invalid YouTube URL or video ID."}), 400
    memes = load_memes()
    if vid_id in memes:
        return jsonify({"error": "This video is already in the list."}), 409
    memes.append(vid_id)
    save_memes(memes)
    return jsonify({"ok": True, "id": vid_id}), 201


@app.route("/admin/api/memes/<int:index>", methods=["DELETE"])
@admin_required
def admin_delete_meme(index: int):
    memes = load_memes()
    if index < 0 or index >= len(memes):
        return jsonify({"error": "Index out of range."}), 404
    memes.pop(index)
    save_memes(memes)
    return jsonify({"ok": True})


@app.route("/api/watch", methods=["DELETE"])
@login_required
def clear_watch():
    users = load_users()
    username = session["username"]
    user = users.setdefault(username, {})
    user.pop("watch", None)
    user.pop("last_watch", None)
    save_users(users)
    return jsonify({"ok": True})


@app.route("/api/watch", methods=["GET"])
@login_required
def get_watch():
    users = load_users()
    user = users.get(session["username"], {})
    return jsonify({
        "records": user.get("watch", {}),
        "last_watch": user.get("last_watch"),
    })


@app.route("/api/watch", methods=["POST"])
@login_required
def set_watch():
    body = request.get_json(silent=True) or {}
    path = (body.get("path") or "").strip()
    position = body.get("position")
    duration = body.get("duration")

    if not path or position is None or not duration:
        return jsonify({"error": "path, position, and duration required."}), 400
    if ".." in Path(path).parts:
        abort(400)
    if not isinstance(position, (int, float)) or not isinstance(duration, (int, float)):
        return jsonify({"error": "position and duration must be numbers."}), 400

    users = load_users()
    username = session["username"]
    user = users.get(username, {})
    if not user.get("admin") and not _is_path_allowed(user.get("allowed_paths"), path):
        abort(403)

    user_rec = users.setdefault(username, {})
    user_rec.setdefault("watch", {})[path] = {
        "position": round(float(position), 2),
        "duration": round(float(duration), 2),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    user_rec["last_watch"] = path
    save_users(users)
    return jsonify({"ok": True})


@app.route("/")
@login_required
def index():
    return (_APP_DIR / "index.html").read_text()


@app.route("/api/episodes")
@login_required
def list_episodes():
    users = load_users()
    user = users.get(session["username"], {})
    allowed = None if user.get("admin") else user.get("allowed_paths")
    return jsonify(scan_videos(allowed))


@app.route("/video/<path:relpath>")
@login_required
def serve_video(relpath: str):
    if ".." in Path(relpath).parts:
        abort(404)
    path = VIDEO_ROOT / relpath
    if not path.is_file():
        abort(404)
    users = load_users()
    user = users.get(session["username"], {})
    if not user.get("admin") and not _is_path_allowed(user.get("allowed_paths"), relpath):
        abort(403)
    return send_file(path.resolve(), conditional=True)


bootstrap()

if __name__ == "__main__":
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    print(f"  Video root: {VIDEO_ROOT}")
    app.run(host="0.0.0.0", port=port, debug=False)
