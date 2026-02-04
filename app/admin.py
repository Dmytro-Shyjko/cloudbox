from functools import wraps
from flask import Blueprint, render_template, session, redirect, url_for, request, abort, current_app

from .models import list_users, toggle_user_premium, toggle_user_active, set_user_role

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

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

@admin_bp.get("/users")
@login_required
@require_role("moderator")  # moderator + admin
def users_page():
    rows = list_users(current_app)
    users = []
    for r in rows:
        users.append({
            "id": r["id"],
            "username": r["username"],
            "role": r["role"],
            "is_premium": int(r["is_premium"]),
            "is_active": int(r["is_active"]),
            "created_at": r["created_at"],
        })

    return render_template(
        "admin_users.html",
        users=users,
        me_role=session.get("role", "user"),
        me_id=session.get("user_id"),
    )

@admin_bp.post("/users/<int:user_id>/premium")
@login_required
@require_role("moderator")  # moderator + admin
def users_toggle_premium(user_id: int):
    toggle_user_premium(current_app, user_id)
    return redirect(url_for("admin.users_page"))

@admin_bp.post("/users/<int:user_id>/block")
@login_required
@require_role("moderator")  # moderator + admin
def users_toggle_block(user_id: int):
    # не даємо заблокувати самого себе
    if user_id == session.get("user_id"):
        return redirect(url_for("admin.users_page"))
    toggle_user_active(current_app, user_id)
    return redirect(url_for("admin.users_page"))

@admin_bp.post("/users/<int:user_id>/role")
@login_required
@require_role("admin")  # тільки admin
def users_set_role(user_id: int):
    role = (request.form.get("role") or "").strip().lower()
    if role not in ("user", "moderator", "admin"):
        abort(400)

    # не дозволяємо самому собі зняти admin
    if user_id == session.get("user_id") and role != "admin":
        return redirect(url_for("admin.users_page"))

    set_user_role(current_app, user_id, role)
    return redirect(url_for("admin.users_page"))
