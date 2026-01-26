from flask import Flask, session
from pathlib import Path

from .i18n import get_lang, t, SUPPORTED_LANGS
from .models import init_db

def create_app():
    app = Flask(__name__, instance_relative_config=True)

    # Для session (потім замінимо на env)
    app.config["SECRET_KEY"] = "change-me-in-production"
    app.config["DATABASE"] = str(Path(app.instance_path) / "cloudbox.sqlite3")

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
