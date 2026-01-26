from functools import wraps
from pathlib import Path

from flask import (
    Blueprint, render_template, session, redirect, url_for,
    request, current_app, send_from_directory, abort
)
from werkzeug.utils import secure_filename

from .i18n import get_lang, t as _t

main_bp = Blueprint("main", __name__)

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login_get"))
        return view(*args, **kwargs)
    return wrapped

def user_base_dir() -> Path:
    """storage/<user_id>/"""
    base = Path(current_app.root_path).parent / "storage"
    uid = str(session["user_id"])
    p = base / uid
    p.mkdir(parents=True, exist_ok=True)
    return p

def safe_rel_path(rel: str) -> str:
    """
    Normalize path from querystring:
    - remove leading '/'
    - forbid '..'
    """
    rel = (rel or "").strip()
    rel = rel.lstrip("/").strip()
    if not rel:
        return ""
    parts = [p for p in rel.split("/") if p]
    if any(p in ("..", ".") for p in parts):
        raise ValueError("bad path")
    return "/".join(parts)

def resolve_user_path(rel: str) -> Path:
    """Return absolute path inside user's base dir, prevent traversal."""
    base = user_base_dir().resolve()
    rel_clean = safe_rel_path(rel)
    target = (base / rel_clean).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError("bad path")
    return target

def safe_filename(name: str) -> str:
    return secure_filename(name) or "file"

def safe_folder_name(name: str) -> str:
    # дозволимо прості імена папок (без слешів)
    name = (name or "").strip()
    name = name.replace("\\", "/")
    if "/" in name or name in ("", ".", ".."):
        return ""
    # ще трохи чистимо
    return secure_filename(name)

def breadcrumbs(rel: str):
    """
    rel: 'a/b/c'
    returns list of {label, path}
    """
    rel_clean = safe_rel_path(rel)
    if not rel_clean:
        return []
    parts = rel_clean.split("/")
    acc = []
    out = []
    for p in parts:
        acc.append(p)
        out.append({"label": p, "path": "/".join(acc)})
    return out

@main_bp.get("/")
def index():
    return redirect(url_for("main.files")) if session.get("user_id") else redirect(url_for("auth.login_get"))

@main_bp.route("/files", methods=["GET", "POST"])
@login_required
def files():
    lang = get_lang(session)

    # current folder path
    rel = request.args.get("path", "")
    try:
        rel = safe_rel_path(rel)
        current_dir = resolve_user_path(rel)
    except ValueError:
        return render_template("files.html",
                               username=session.get("username"),
                               path="",
                               breadcrumbs=[],
                               folders=[],
                               files=[],
                               error=_t(lang, "err.bad_path"),
                               message=None)

    current_dir.mkdir(parents=True, exist_ok=True)

    error = None
    message = request.args.get("msg") or None

    # POST actions: upload OR create_folder
    action = request.form.get("action", "")

    if request.method == "POST":
        # CREATE FOLDER
        if action == "mkdir":
            folder = safe_folder_name(request.form.get("folder") or "")
            if not folder:
                error = _t(lang, "err.folder_name")
            else:
                new_dir = (current_dir / folder)
                if new_dir.exists():
                    error = _t(lang, "err.folder_exists")
                else:
                    new_dir.mkdir(parents=False, exist_ok=False)
                    message = _t(lang, "msg.uploaded", filename=folder)  # reuse, або зробимо окремий key пізніше

        # UPLOAD FILE
        elif action == "upload":
            if "file" not in request.files:
                error = _t(lang, "err.no_file")
            else:
                f = request.files["file"]
                if not f.filename:
                    error = _t(lang, "err.no_file")
                else:
                    filename = safe_filename(f.filename)
                    dest = current_dir / filename
                    f.save(dest)
                    message = _t(lang, "msg.uploaded", filename=filename)

    # LIST folders/files
    folders_list = []
    files_list = []

    for item in sorted(current_dir.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if item.is_dir():
            folders_list.append(item.name)
        elif item.is_file():
            files_list.append(item.name)

    return render_template(
        "files.html",
        username=session.get("username"),
        path=rel,
        breadcrumbs=breadcrumbs(rel),
        folders=folders_list,
        files=files_list,
        error=error,
        message=message,
    )

@main_bp.get("/download/<path:filepath>")
@login_required
def download(filepath):
    # filepath includes folders, e.g. "a/b/file.txt"
    try:
        filepath = safe_rel_path(filepath)
        abs_path = resolve_user_path(filepath)
    except ValueError:
        abort(404)

    if not abs_path.exists() or not abs_path.is_file():
        abort(404)

    directory = abs_path.parent
    filename = abs_path.name
    return send_from_directory(directory, filename, as_attachment=True)

@main_bp.post("/delete/<path:filepath>")
@login_required
def delete_file(filepath):
    lang = get_lang(session)
    # after delete, redirect back to folder
    back_path = request.args.get("path", "")
    try:
        back_rel = safe_rel_path(back_path)
    except ValueError:
        back_rel = ""

    try:
        filepath = safe_rel_path(filepath)
        abs_path = resolve_user_path(filepath)
    except ValueError:
        return redirect(url_for("main.files", path=back_rel))

    if abs_path.exists() and abs_path.is_file():
        abs_path.unlink()
        msg = _t(lang, "msg.deleted", filename=abs_path.name)
        return redirect(url_for("main.files", path=back_rel, msg=msg))

    return redirect(url_for("main.files", path=back_rel))
