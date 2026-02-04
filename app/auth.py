from flask import Blueprint, render_template, request, redirect, url_for, session, current_app
from .models import create_user, verify_user, is_login_blocked, record_login_attempt, cleanup_old_attempts
from .i18n import SUPPORTED_LANGS, get_lang, t as _t

auth_bp = Blueprint("auth", __name__)

@auth_bp.get("/register")
def register_get():
    return render_template("login.html", mode="register", error=None)

@auth_bp.post("/register")
def register_post():
    lang = get_lang(session)

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if len(username) < 3:
        return render_template("login.html", mode="register", error=_t(lang, "err.username_short"))
    if len(password) < 6:
        return render_template("login.html", mode="register", error=_t(lang, "err.password_short"))

    ok = create_user(current_app, username, password)
    if not ok:
        return render_template("login.html", mode="register", error=_t(lang, "err.user_exists"))

    # авто-логін
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

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    # IP (з проксі може бути інакше; для локального MVP достатньо remote_addr)
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or ""
    ip = ip.split(",")[0].strip()

    # чистимо старі записи інколи (можна раз на N запитів, але для MVP ок)
    cleanup_old_attempts(current_app)

    # 1) Check lockout (по IP і по username)
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
        # НЕ кажемо, що саме заблоковано (user чи ip)
        return render_template(
            "login.html",
            mode="login",
            error=_t(lang, "err.too_many_attempts", seconds=retry_after),
        )

    # 2) Verify (важливо: однакове повідомлення, щоб не палити існування user)
    user = verify_user(current_app, username, password)

    if not user:
        record_login_attempt(current_app, ip, username, success=False)
        return render_template("login.html", mode="login", error=_t(lang, "err.bad_credentials"))

    if user.get("blocked"):
        # теж не палимо зайве: можна показати загальне повідомлення або окреме
        record_login_attempt(current_app, ip, username, success=False)
        return render_template("login.html", mode="login", error=_t(lang, "err.bad_credentials"))

    # success
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
