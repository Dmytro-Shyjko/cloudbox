from functools import wraps
from flask import Blueprint, render_template, session, redirect, url_for, request

main_bp = Blueprint("main", __name__)

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login_get", next=request.path))
        return view(*args, **kwargs)
    return wrapped

@main_bp.get("/")
def index():
    # редірект на приватну сторінку або логін
    if session.get("user_id"):
        return redirect(url_for("main.files"))
    return redirect(url_for("auth.login_get"))

@main_bp.get("/files")
@login_required
def files():
    return render_template("files.html", username=session.get("username"))
