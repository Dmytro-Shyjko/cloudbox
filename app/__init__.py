from flask import Flask, session
from pathlib import Path

from .i18n import get_lang, t, SUPPORTED_LANGS
from .models import init_db


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    # --- base config ---
    app.config["SECRET_KEY"] = "change-me-in-production"
    app.config["DATABASE"] = str(Path(app.instance_path) / "cloudbox.sqlite3")

    # BETA
    app.config["BETA_MAX_USERS"] = 50  # постав будь-яке число

    # FREE
    app.config["FREE_MAX_UPLOAD_MB"] = 100
    app.config["FREE_QUOTA_MB"] = 2_000

    # PREMIUM
    app.config["PREMIUM_MAX_UPLOAD_MB"] = 500
    app.config["PREMIUM_QUOTA_MB"] = 10_000  # 10 GB

    # Upload security
    app.config["UPLOAD_ALLOWED_EXTENSIONS"] = {
        "txt", "md", "pdf",
        "png", "jpg", "jpeg", "gif", "webp",
        "zip",
    }

    app.config["UPLOAD_BLOCKED_EXTENSIONS"] = {
        "exe", "bat", "cmd", "com", "msi",
        "sh", "bash", "zsh",
        "js", "jar",
        "php", "phtml", "phar",
        "py", "pl", "rb",
    }

    app.config["UPLOAD_MAX_FILENAME_LEN"] = 80



    # Allow tests to override config
    if test_config:
        app.config.update(test_config)

    # Ensure instance folder exists
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    # init DB
    init_db(app)

    # i18n into templates
    @app.context_processor
    def inject_i18n():
        lang = get_lang(session)
        return {
            "t": lambda key, **kw: t(lang, key, **kw),
            "current_lang": lang,
            "supported_langs": SUPPORTED_LANGS,
        }
    
    import time
    from flask import request
    from .models import touch_user_activity

    @app.before_request
    def _track_activity():
        uid = session.get("user_id")
        if not uid:
            return

        now = int(time.time())
        last = session.get("_last_touch", 0)

        if now - int(last) >= 60:
            touch_user_activity(app, int(uid))
            session["_last_touch"] = now


    # --- blueprints (import AFTER app exists) ---
    from .auth import auth_bp
    from .routes import main_bp
    from .admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)

    return app
