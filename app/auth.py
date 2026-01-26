from flask import Blueprint, render_template, request, redirect, url_for, session, current_app

from .models import create_user, verify_user

auth_bp = Blueprint("auth", __name__)

@auth_bp.get("/register")
def register_get():
    return render_template("login.html", mode="register", error=None)

@auth_bp.post("/register")
def register_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if len(username) < 3:
        return render_template("login.html", mode="register", error="Username мінімум 3 символи")
    if len(password) < 6:
        return render_template("login.html", mode="register", error="Password мінімум 6 символів")

    ok = create_user(current_app, username, password)
    if not ok:
        return render_template("login.html", mode="register", error="Такий username вже існує")

    # Авто-логін після реєстрації
    user = verify_user(current_app, username, password)
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return redirect(url_for("main.files"))

@auth_bp.get("/login")
def login_get():
    return render_template("login.html", mode="login", error=None)

@auth_bp.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    user = verify_user(current_app, username, password)
    if not user:
        return render_template("login.html", mode="login", error="Невірний username або password")

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return redirect(url_for("main.files"))

@auth_bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login_get"))
