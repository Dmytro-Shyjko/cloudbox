import re
from flask import Blueprint, render_template, request, redirect, url_for, session, current_app

from .models import (
    create_user,
    verify_user,
    is_login_blocked,
    record_login_attempt,
    cleanup_old_attempts,              # alias до cleanup_old_login_attempts в models.py
    is_register_blocked,
    record_register_attempt,
    cleanup_old_register_attempts,
)

from .i18n import SUPPORTED_LANGS, get_lang, t as _t


def _client_ip() -> str:
    # для тестів достатньо remote_addr; X-Forwarded-For теж підтримуємо
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "0.0.0.0"
    return ip.split(",")[0].strip()


def is_email_ok(email: str) -> bool:
    email = (email or "").strip().lower()
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


auth_bp = Blueprint("auth", __name__)


@auth_bp.get("/register")
def register_get():
    return render_template("login.html", mode="register", error=None)


@auth_bp.post("/register")
def register_post():
    lang = get_lang(session)
    ip = _client_ip()

    # cleanup + rate limit (до будь-якої логіки)
    cleanup_old_register_attempts(current_app)

    blocked, retry_after = is_register_blocked(current_app, ip)
    if blocked:
        return render_template(
            "login.html",
            mode="register",
            error=_t(lang, "err.too_many_registrations", seconds=retry_after),
        )

    # honeypot
    hp = (request.form.get("company") or "").strip()
    if hp:
        record_register_attempt(current_app, ip, success=False)
        return render_template("login.html", mode="register", error=_t(lang, "err.bad_request"))

    # поля
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    email = (request.form.get("email") or "").strip().lower()
    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    country = (request.form.get("country") or "").strip()
    phone = (request.form.get("phone") or "").strip() or None

    accepted_terms = (request.form.get("accept_terms") == "on")

    # валідaція (кожен fail -> record)
    if len(username) < 3:
        record_register_attempt(current_app, ip, success=False)
        return render_template("login.html", mode="register", error=_t(lang, "err.username_short"))

    if len(password) < 6:
        record_register_attempt(current_app, ip, success=False)
        return render_template("login.html", mode="register", error=_t(lang, "err.password_short"))

    if not is_email_ok(email):
        record_register_attempt(current_app, ip, success=False)
        return render_template("login.html", mode="register", error=_t(lang, "err.email_invalid"))

    if not first_name or not last_name or not country:
        record_register_attempt(current_app, ip, success=False)
        return render_template("login.html", mode="register", error=_t(lang, "err.profile_required"))

    if not accepted_terms:
        record_register_attempt(current_app, ip, success=False)
        return render_template("login.html", mode="register", error=_t(lang, "err.terms_required"))

    # Перевірка ліміту користувачів
    from .models import can_create_more_users

    if not can_create_more_users(current_app):
        record_register_attempt(current_app, ip, success=False)
        return render_template("login.html", mode="register", error=_t(lang, "err.beta_user_limit"))

    # create user
    ok = create_user(
        current_app,
        username,
        password,
        email,
        first_name,
        last_name,
        country,
        phone,
        accepted_terms=True,
    )
    if not ok:
        record_register_attempt(current_app, ip, success=False)
        return render_template("login.html", mode="register", error=_t(lang, "err.user_exists"))

    record_register_attempt(current_app, ip, success=True)

    # auto-login
    user = verify_user(current_app, username, password)
    if not user or user.get("blocked"):
        return redirect(url_for("auth.login_get"))

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user.get("role", "user")
    session["is_premium"] = int(user.get("is_premium", 0))

    return redirect(url_for("main.files"))


@auth_bp.get("/login")
def login_get():
    return render_template("login.html", mode="login", error=None)


@auth_bp.post("/login")
def login_post():
    lang = get_lang(session)
    ip = _client_ip()

    # honeypot (ВАЖЛИВО для тестів)
    hp = (request.form.get("company") or "").strip()
    if hp:
        record_login_attempt(current_app, ip, "", success=False)
        return render_template("login.html", mode="login", error=_t(lang, "err.bad_request"))

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    # cleanup старих логін-спроб
    cleanup_old_attempts(current_app)

    # rate limit
    blocked, retry_after = is_login_blocked(
        current_app,
        ip=ip,
        username=username,
        window_seconds=15 * 60,
        max_fail_ip=20,
        max_fail_user=10,
        block_seconds=15 * 60,
    )
    if blocked:
        return render_template(
            "login.html",
            mode="login",
            error=_t(lang, "err.too_many_attempts", seconds=retry_after),
        )

    # verify
    user = verify_user(current_app, username, password)

    if not user or user.get("blocked"):
        record_login_attempt(current_app, ip, username, success=False)
        return render_template("login.html", mode="login", error=_t(lang, "err.bad_credentials"))

    record_login_attempt(current_app, ip, username, success=True)

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user.get("role", "user")
    session["is_premium"] = int(user.get("is_premium", 0))

    return redirect(url_for("main.files"))


@auth_bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login_get"))


@auth_bp.get("/lang/<lang>")
def set_lang(lang):
    if lang in SUPPORTED_LANGS:
        session["lang"] = lang
    return redirect(request.referrer or url_for("main.index"))
