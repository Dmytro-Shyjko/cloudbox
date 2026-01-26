from functools import wraps
from pathlib import Path

from flask import (
    Blueprint, render_template, session, redirect, url_for,
    request, current_app, send_from_directory, abort
)
from werkzeug.utils import secure_filename

main_bp = Blueprint("main", __name__)

ALLOWED_EXTENSIONS = set()  # порожньо = дозволити все (для MVP)

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login_get"))
        return view(*args, **kwargs)
    return wrapped

def user_storage_dir() -> Path:
    # storage/<user_id>/
    base = Path(current_app.root_path).parent / "storage"
    uid = str(session["user_id"])
    p = base / uid
    p.mkdir(parents=True, exist_ok=True)
    return p

def safe_filename(name: str) -> str:
    # захист від "../" і дивних символів
    return secure_filename(name) or "file"

@main_bp.get("/")
def index():
    return redirect(url_for("main.files")) if session.get("user_id") else redirect(url_for("auth.login_get"))

@main_bp.route("/files", methods=["GET", "POST"])
@login_required
def files():
    error = None
    message = None

    # UPLOAD
    if request.method == "POST":
        if "file" not in request.files:
            error = "Файл не обрано"
        else:
            f = request.files["file"]
            if not f.filename:
                error = "Файл не обрано"
            else:
                filename = safe_filename(f.filename)
                dest = user_storage_dir() / filename
                f.save(dest)
                message = f"Файл завантажено: {filename}"

    # LIST
    files_list = []
    for item in sorted(user_storage_dir().iterdir()):
        if item.is_file():
            files_list.append(item.name)

    return render_template(
        "files.html",
        username=session.get("username"),
        files=files_list,
        error=error,
        message=message,
    )

@main_bp.get("/download/<path:filename>")
@login_required
def download(filename):
    filename = safe_filename(filename)
    p = user_storage_dir() / filename
    if not p.exists() or not p.is_file():
        abort(404)
    # send_from_directory безпечніше, ніж ручний open()
    return send_from_directory(user_storage_dir(), filename, as_attachment=True)
