from flask import Blueprint, render_template, request, redirect, url_for, session, current_app

from .models import create_user, verify_user
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
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return redirect(url_for("main.files"))

@auth_bp.get("/login")
def login_get():
    return render_template("login.html", mode="login", error=None)

@auth_bp.post("/login")
def login_post():
    lang = get_lang(session)

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    user = verify_user(current_app, username, password)
    if not user:
        return render_template("login.html", mode="login", error=_t(lang, "err.bad_credentials"))

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return redirect(url_for("main.files"))

@auth_bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login_get"))

@auth_bp.get("/lang/<lang>")
def set_lang(lang):
    # Зберігаємо мову в сесії
    if lang in SUPPORTED_LANGS:
        session["lang"] = lang
    return redirect(request.referrer or url_for("main.index"))
