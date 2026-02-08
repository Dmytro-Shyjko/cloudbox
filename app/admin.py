from __future__ import annotations

from functools import wraps
from datetime import datetime
from flask import Blueprint, render_template, session, redirect, url_for, request, abort, current_app

from .models import (
    list_users,
    get_user,
    toggle_user_premium,
    toggle_user_active,
    set_user_role,
    user_storage_used_bytes,
    delete_user,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# -------------------------
# Helpers
# -------------------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login_get"))
        return view(*args, **kwargs)
    return wrapped


def require_role(*roles):
    """Admin always allowed; otherwise allow only listed roles."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            role = session.get("role", "user")
            if role == "admin":
                return view(*args, **kwargs)
            if role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def _is_admin() -> bool:
    return session.get("role", "user") == "admin"


def _me_id() -> int | None:
    try:
        return int(session.get("user_id"))
    except Exception:
        return None


def safe_row_get(row, key: str, default=None):
    """
    sqlite3.Row підтримує keys(), але інколи row може бути dict або None.
    """
    if row is None:
        return default
    try:
        # sqlite3.Row
        if hasattr(row, "keys") and key in row.keys():
            return row[key]
        # dict-like
        if isinstance(row, dict) and key in row:
            return row[key]
    except Exception:
        return default
    return default


def human_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for u in units:
        if size < 1024 or u == units[-1]:
            if u == "B":
                return f"{int(size)} {u}"
            return f"{size:.1f} {u}"
        size /= 1024.0
    return f"{int(num_bytes)} B"


def fmt_last_active(ts: int | None) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


def _redirect_back_or_users(user_id: int | None = None):
    """
    Якщо прийшли з detail — повертаємось туди.
    Інакше — на /admin/users
    """
    ref = request.referrer or ""
    if user_id is not None and f"/admin/users/{int(user_id)}" in ref:
        return redirect(url_for("admin.user_detail", user_id=int(user_id)))
    return redirect(url_for("admin.users_page"))


# -------------------------
# Pages
# -------------------------
@admin_bp.get("/users")
@login_required
@require_role("moderator")  # moderator + admin
def users_page():
    rows = list_users(current_app)

    users = []
    for r in rows:
        uid = int(safe_row_get(r, "id", 0) or 0)

        used_bytes = 0
        try:
            used_bytes = int(user_storage_used_bytes(current_app, uid))
        except Exception:
            used_bytes = 0

        last_active_at = safe_row_get(r, "last_active_at", None)

        users.append({
            "id": uid,
            "username": safe_row_get(r, "username", ""),
            "role": safe_row_get(r, "role", "user") or "user",
            "is_premium": int(safe_row_get(r, "is_premium", 0) or 0),
            "is_active": int(safe_row_get(r, "is_active", 1) if safe_row_get(r, "is_active", None) is not None else 1),
            "created_at": safe_row_get(r, "created_at", ""),

            # NEW:
            "used_bytes": used_bytes,
            "used_human": human_size(used_bytes),
            "last_active_at": last_active_at,
            "last_active_str": fmt_last_active(int(last_active_at) if last_active_at else None),
        })

    return render_template(
        "admin_users.html",
        users=users,
        me_role=session.get("role", "user"),
        me_id=_me_id(),
        me_is_admin=_is_admin(),
    )


@admin_bp.get("/users/<int:user_id>")
@login_required
@require_role("moderator")  # moderator + admin
def user_detail(user_id: int):
    r = get_user(current_app, user_id)
    if not r:
        abort(404)

    used_bytes = 0
    try:
        used_bytes = int(user_storage_used_bytes(current_app, int(user_id)))
    except Exception:
        used_bytes = 0

    last_active_at = safe_row_get(r, "last_active_at", None)

    user = {
        "id": int(safe_row_get(r, "id", user_id)),
        "username": safe_row_get(r, "username", ""),
        "role": safe_row_get(r, "role", "user") or "user",
        "is_premium": int(safe_row_get(r, "is_premium", 0) or 0),
        "is_active": int(safe_row_get(r, "is_active", 1) if safe_row_get(r, "is_active", None) is not None else 1),
        "created_at": safe_row_get(r, "created_at", ""),

        # анкета:
        "email": safe_row_get(r, "email", None),
        "first_name": safe_row_get(r, "first_name", None),
        "last_name": safe_row_get(r, "last_name", None),
        "country": safe_row_get(r, "country", None),
        "phone": safe_row_get(r, "phone", None),

        # NEW:
        "used_bytes": used_bytes,
        "used_human": human_size(used_bytes),
        "last_active_at": last_active_at,
        "last_active_str": fmt_last_active(int(last_active_at) if last_active_at else None),
    }

    return render_template(
        "admin_user_detail.html",
        user=user,
        me_role=session.get("role", "user"),
        me_id=_me_id(),
        me_is_admin=_is_admin(),
    )


# -------------------------
# Actions
# -------------------------
@admin_bp.post("/users/<int:user_id>/premium")
@login_required
@require_role("moderator")  # moderator + admin
def users_toggle_premium(user_id: int):
    toggle_user_premium(current_app, user_id)
    return _redirect_back_or_users(user_id)


@admin_bp.post("/users/<int:user_id>/block")
@login_required
@require_role("moderator")  # moderator + admin
def users_toggle_block(user_id: int):
    # не даємо заблокувати самого себе
    if int(user_id) == int(_me_id() or -1):
        return _redirect_back_or_users(user_id)

    toggle_user_active(current_app, user_id)
    return _redirect_back_or_users(user_id)


@admin_bp.post("/users/<int:user_id>/role")
@login_required
@require_role("admin")  # тільки admin
def users_set_role(user_id: int):
    role = (request.form.get("role") or "").strip().lower()
    if role not in ("user", "moderator", "admin"):
        abort(400)

    # не дозволяємо самому собі зняти admin
    if int(user_id) == int(_me_id() or -1) and role != "admin":
        return _redirect_back_or_users(user_id)

    set_user_role(current_app, user_id, role)
    return _redirect_back_or_users(user_id)


@admin_bp.post("/users/<int:user_id>/delete")
@login_required
@require_role("admin")  # тільки admin
def users_delete(user_id: int):
    # BETA: без додаткових умов, але себе видаляти не даємо
    if int(user_id) == int(_me_id() or -1):
        return _redirect_back_or_users(user_id)

    delete_user(current_app, user_id)
    return redirect(url_for("admin.users_page"))
