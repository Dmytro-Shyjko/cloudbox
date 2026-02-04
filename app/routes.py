from functools import wraps
from pathlib import Path
import mimetypes

from flask import (
    Blueprint, render_template, session, redirect, url_for,
    request, current_app, send_from_directory, abort
)
from werkzeug.utils import secure_filename

from .i18n import get_lang, t as _t

from datetime import datetime

import shutil

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

def all_folders_under(base: Path):
    """Return list of relative folder paths under user base, sorted."""
    base = base.resolve()
    out = []
    for p in base.rglob("*"):
        if p.is_dir():
            rel = p.relative_to(base).as_posix()
            out.append(rel)
    out.sort(key=lambda s: s.lower())
    return out

def human_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for u in units:
        if size < 1024 or u == units[-1]:
            if u == "B":
                return f"{int(size)} {u}"
            return f"{size:.1f} {u}"
        size /= 1024.0

def fmt_mtime(ts: float) -> str:
    # локальний час сервера
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

def dir_size_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total

def user_limits():
    """Return (max_upload_bytes, quota_bytes) for current user."""
    is_premium = int(session.get("is_premium", 0)) == 1

    if is_premium:
        max_mb = int(current_app.config.get("PREMIUM_MAX_UPLOAD_MB", 200))
        quota_mb = int(current_app.config.get("PREMIUM_QUOTA_MB", 10_000))
    else:
        max_mb = int(current_app.config.get("FREE_MAX_UPLOAD_MB", 10))
        quota_mb = int(current_app.config.get("FREE_QUOTA_MB", 200))

    return max_mb * 1024 * 1024, quota_mb * 1024 * 1024

def get_ext(filename: str) -> str:
    filename = (filename or "").strip().lower()
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1]

def looks_like_double_extension(filename: str) -> bool:
    # типу: file.jpg.exe або invoice.pdf.js
    parts = (filename or "").lower().split(".")
    if len(parts) < 3:
        return False
    # якщо останній extension нормальний, але передостанній теж "виконуваний/скриптовий" — підозріло
    dangerous = {"exe","bat","cmd","com","msi","sh","bash","zsh","js","jar","php","phtml","phar","py","pl","rb"}
    return parts[-1] in dangerous or parts[-2] in dangerous

def is_allowed_upload(filename: str) -> tuple[bool, str]:
    """
    Returns (ok, reason_key)
    reason_key: i18n key for error
    """
    filename = (filename or "").strip()

    if not filename:
        return False, "err.no_file"

    # forbid hidden files like ".env" or ".bashrc"
    if filename.startswith("."):
        return False, "err.upload_hidden"

    # prevent weird long names
    max_len = int(current_app.config.get("UPLOAD_MAX_FILENAME_LEN", 80))
    if len(filename) > max_len:
        return False, "err.upload_name_too_long"

    if looks_like_double_extension(filename):
        return False, "err.upload_double_ext"

    ext = get_ext(filename)
    if not ext:
        return False, "err.upload_no_ext"

    blocked = set(current_app.config.get("UPLOAD_BLOCKED_EXTENSIONS", set()))
    if ext in blocked:
        return False, "err.upload_ext_blocked"

    allowed = set(current_app.config.get("UPLOAD_ALLOWED_EXTENSIONS", set()))
    if ext not in allowed:
        return False, "err.upload_ext_not_allowed"

    return True, ""


@main_bp.get("/")
def index():
    return redirect(url_for("main.files")) if session.get("user_id") else redirect(url_for("auth.login_get"))

@main_bp.route("/files", methods=["GET", "POST"])
@login_required
def files():
    lang = get_lang(session)

    # current folder path
    rel = request.args.get("path", "")
    sort_by = (request.args.get("sort") or "name").lower()
    order = (request.args.get("order") or "asc").lower()

    if sort_by not in ("name", "size", "date"):
        sort_by = "name"
    if order not in ("asc", "desc"):
        order = "asc"

    reverse = (order == "desc")

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
                               message=None,
                               )

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

        # RENAME file/folder
        elif action == "rename":
            old_name = (request.form.get("old") or "").strip()
            new_name = (request.form.get("new") or "").strip()

            # rename тільки в поточній папці, без слешів
            def clean_name(x: str) -> str:
                x = x.replace("\\", "/").strip()
                if "/" in x or x in ("", ".", ".."):
                    return ""
                return secure_filename(x)

            old_clean = clean_name(old_name)
            new_clean = clean_name(new_name)

            if not old_clean or not new_clean:
                error = _t(lang, "err.rename_invalid")
            else:
                src = current_dir / old_clean
                dst = current_dir / new_clean

                if not src.exists():
                    error = _t(lang, "err.not_found")
                elif dst.exists():
                    error = _t(lang, "err.rename_exists")
                else:
                    src.rename(dst)
                    message = _t(lang, "msg.renamed", old=old_clean, new=new_clean)

        # MOVE file to another folder (relative to user base)
        elif action == "move":
            old_name = (request.form.get("old") or "").strip()
            target_rel = (request.form.get("target") or "").strip()

            def clean_name(x: str) -> str:
                x = x.replace("\\", "/").strip()
                if "/" in x or x in ("", ".", ".."):
                    return ""
                return secure_filename(x)

            old_clean = clean_name(old_name)

            try:
                target_rel_clean = safe_rel_path(target_rel)
                target_dir = resolve_user_path(target_rel_clean)
            except ValueError:
                target_dir = None

            if not old_clean:
                error = _t(lang, "err.not_found")
            elif not target_rel.strip() and target_rel != "":
                error = _t(lang, "err.move_no_target")
            elif target_dir is None or not target_dir.exists() or not target_dir.is_dir():
                error = _t(lang, "err.move_target_missing")
            else:
                src = current_dir / old_clean
                dst = target_dir / old_clean

                if not src.exists() or not src.is_file():
                    error = _t(lang, "err.not_found")
                elif dst.exists():
                    error = _t(lang, "err.rename_exists")  # вже є такий файл
                else:
                    src.rename(dst)
                    message = _t(lang, "msg.moved", filename=old_clean, target=target_rel_clean)

        # MOVE FOLDER to another folder (relative to user base)
        elif action == "move_folder":
            folder_name = (request.form.get("old") or "").strip()
            target_rel = (request.form.get("target") or "").strip()

            def clean_name(x: str) -> str:
                x = x.replace("\\", "/").strip()
                if "/" in x or x in ("", ".", ".."):
                    return ""
                return secure_filename(x)

            folder_clean = clean_name(folder_name)

            try:
                target_rel_clean = safe_rel_path(target_rel)  # "" = root
                target_dir = resolve_user_path(target_rel_clean)
            except ValueError:
                target_dir = None
                target_rel_clean = ""

            if not folder_clean:
                error = _t(lang, "err.folder_name")
            elif target_dir is None or not target_dir.exists() or not target_dir.is_dir():
                error = _t(lang, "err.move_target_missing")
            else:
                src_dir = current_dir / folder_clean
                if not src_dir.exists() or not src_dir.is_dir():
                    error = _t(lang, "err.not_found")
                else:
                    # абсолютні, щоб перевірити "всередину себе"
                    src_abs = src_dir.resolve()
                    target_abs = target_dir.resolve()

                    # 1) target = сама папка
                    if src_abs == target_abs:
                        error = _t(lang, "err.move_into_itself")

                    # 2) target всередині src (підпапка)
                    elif str(target_abs).startswith(str(src_abs) + "/") or str(target_abs).startswith(str(src_abs) + "\\"):
                        error = _t(lang, "err.move_into_child")

                    else:
                        # Куди саме переміщаємо: target/<folder_name>
                        dst_dir = target_dir / folder_clean
                        if dst_dir.exists():
                            error = _t(lang, "err.rename_exists")
                        else:
                            # shutil.move works across filesystems too
                            shutil.move(str(src_dir), str(dst_dir))
                            message = _t(lang, "msg.folder_moved", old=folder_clean, target=(target_rel_clean or ""))

        # UPLOAD FILE
        elif action == "upload":
            max_upload_bytes, quota_bytes = user_limits()

            if "file" not in request.files:
                error = _t(lang, "err.no_file")
            else:
                f = request.files["file"]
                if not f.filename:
                    error = _t(lang, "err.no_file")
                else:
                    raw_name = f.filename

                    # 0) filename policy (hidden/double ext/allowed ext)
                    ok_name, reason_key = is_allowed_upload(raw_name)
                    if not ok_name:
                        error = _t(lang, reason_key)
                    else:
                        filename = safe_filename(raw_name)

                        # extra safety: ensure extension still allowed after secure_filename()
                        ext = get_ext(filename)
                        allowed = set(current_app.config.get("UPLOAD_ALLOWED_EXTENSIONS", set()))
                        blocked = set(current_app.config.get("UPLOAD_BLOCKED_EXTENSIONS", set()))

                        if not ext:
                            error = _t(lang, "err.upload_no_ext")
                        elif ext in blocked:
                            error = _t(lang, "err.upload_ext_blocked")
                        elif ext not in allowed:
                            error = _t(lang, "err.upload_ext_not_allowed")
                        else:
                            # 1) check file size (per-upload)
                            # werkzeug FileStorage stream supports seek/tell
                            try:
                                f.stream.seek(0, 2)  # end
                                upload_size = f.stream.tell()
                                f.stream.seek(0)
                            except Exception:
                                upload_size = 0

                            if upload_size <= 0:
                                error = _t(lang, "err.no_file")
                            elif upload_size > max_upload_bytes:
                                error = _t(
                                    lang,
                                    "err.upload_too_large",
                                    mb=int(max_upload_bytes / (1024 * 1024)),
                                )
                            else:
                                # 2) check quota (total storage)
                                base_dir = user_base_dir()
                                used = dir_size_bytes(base_dir)

                                if used + upload_size > quota_bytes:
                                    error = _t(
                                        lang,
                                        "err.quota_exceeded",
                                        mb=int(quota_bytes / (1024 * 1024)),
                                    )
                                else:
                                    # 3) best-effort MIME sanity check for images/pdf
                                    client_mime = (f.mimetype or "").lower()

                                    if ext in {"png", "jpg", "jpeg", "gif", "webp"}:
                                        if client_mime and not client_mime.startswith("image/"):
                                            error = _t(lang, "err.upload_mime_mismatch")
                                    elif ext == "pdf":
                                        if client_mime and client_mime not in {"application/pdf", "application/x-pdf"}:
                                            error = _t(lang, "err.upload_mime_mismatch")

                                    if not error:
                                        # 4) final save (no overwrite)
                                        dest = current_dir / filename
                                        if dest.exists():
                                            error = _t(lang, "err.rename_exists")
                                        else:
                                            f.save(dest)
                                            message = _t(lang, "msg.uploaded", filename=filename)


    # LIST folders/files (with size/date)
    folders_list = []
    files_list = []

    for item in current_dir.iterdir():
        try:
            st = item.stat()
            mtime_ts = float(st.st_mtime)
            mtime = fmt_mtime(mtime_ts)
        except OSError:
            mtime_ts = 0.0
            mtime = ""

        if item.is_dir():
            folders_list.append({
                "name": item.name,
                "mtime": mtime,
                "mtime_ts": mtime_ts,
            })
        elif item.is_file():
            try:
                size_bytes = int(st.st_size)
                size = human_size(size_bytes)
            except Exception:
                size_bytes = 0
                size = ""

            files_list.append({
                "name": item.name,
                "size": size,
                "size_bytes": size_bytes,
                "mtime": mtime,
                "mtime_ts": mtime_ts,
            })

    # folders: name/date (size not meaningful)
    if sort_by == "date":
        folders_list.sort(key=lambda x: x.get("mtime_ts", 0.0), reverse=reverse)
    else:
        folders_list.sort(key=lambda x: x.get("name", "").lower(), reverse=reverse)

    # files: name/size/date
    if sort_by == "size":
        files_list.sort(key=lambda x: x.get("size_bytes", 0), reverse=reverse)
    elif sort_by == "date":
        files_list.sort(key=lambda x: x.get("mtime_ts", 0.0), reverse=reverse)
    else:
        files_list.sort(key=lambda x: x.get("name", "").lower(), reverse=reverse)

    all_folders = all_folders_under(user_base_dir())

    # ===== Storage stats (used/quota/max upload) =====
    max_upload_bytes, quota_bytes = user_limits()

    base_dir = user_base_dir()
    used_bytes = dir_size_bytes(base_dir)

    used_mb = used_bytes // (1024 * 1024)
    quota_mb = quota_bytes // (1024 * 1024)
    max_up_mb = max_upload_bytes // (1024 * 1024)

    return render_template(
        "files.html",
        username=session.get("username"),
        path=rel,
        breadcrumbs=breadcrumbs(rel),
        folders=folders_list,
        files=files_list,
        error=error,
        message=message,
        all_folders=all_folders,
        sort_by=sort_by,
        order=order,
        used_mb=used_mb,
        quota_mb=quota_mb,
        max_up_mb=max_up_mb,
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

@main_bp.post("/delete-folder/<path:folder>")
@login_required
def delete_folder(folder):
    """
    Delete EMPTY folder only
    """
    lang = get_lang(session)
    back_path = request.args.get("path", "")

    try:
        folder = safe_rel_path(folder)
        abs_dir = resolve_user_path(folder)
    except ValueError:
        return redirect(url_for("main.files", path=back_path))

    if not abs_dir.exists() or not abs_dir.is_dir():
        return redirect(url_for("main.files", path=back_path))

    # allow delete only empty folder
    if any(abs_dir.iterdir()):
        return redirect(
            url_for(
                "main.files",
                path=back_path,
                msg=_t(lang, "err.folder_not_empty"),
            )
        )

    abs_dir.rmdir()
    msg = _t(lang, "msg.deleted", filename=abs_dir.name)
    return redirect(url_for("main.files", path=back_path, msg=msg))


@main_bp.post("/delete-folder-recursive/<path:folder>")
@login_required
def delete_folder_recursive(folder):
    """
    Delete folder WITH ALL CONTENTS
    """
    lang = get_lang(session)
    back_path = request.args.get("path", "")

    try:
        folder = safe_rel_path(folder)
        abs_dir = resolve_user_path(folder)
    except ValueError:
        return redirect(url_for("main.files", path=back_path))

    if not abs_dir.exists() or not abs_dir.is_dir():
        return redirect(url_for("main.files", path=back_path))

    shutil.rmtree(abs_dir)
    msg = _t(lang, "msg.deleted", filename=abs_dir.name)
    return redirect(url_for("main.files", path=back_path, msg=msg))
