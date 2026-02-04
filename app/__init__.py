from flask import Flask, session
from pathlib import Path
from .admin import admin_bp

from .i18n import get_lang, t, SUPPORTED_LANGS
from .models import init_db

def create_app():
    app = Flask(__name__, instance_relative_config=True)

    # Для session (потім замінимо на env)
    app.config["SECRET_KEY"] = "change-me-in-production"
    app.config["DATABASE"] = str(Path(app.instance_path) / "cloudbox.sqlite3")
    
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
        "zip"
    }

    # Optional: forbid some common dangerous extensions explicitly (defense in depth)
    app.config["UPLOAD_BLOCKED_EXTENSIONS"] = {
        "exe", "bat", "cmd", "com", "msi",
        "sh", "bash", "zsh",
        "js", "jar",
        "php", "phtml", "phar",
        "py", "pl", "rb"
    }

    # Optional: limit filename length
    app.config["UPLOAD_MAX_FILENAME_LEN"] = 80

    
    app.register_blueprint(admin_bp)
    



    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    # init DB
    init_db(app)

    # i18n в шаблони
    @app.context_processor
    def inject_i18n():
        lang = get_lang(session)
        return {
            "t": lambda key, **kw: t(lang, key, **kw),
            "current_lang": lang,
            "supported_langs": SUPPORTED_LANGS,
        }

    # blueprints
    from .auth import auth_bp
    from .routes import main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    return app

