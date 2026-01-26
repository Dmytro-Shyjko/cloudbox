from flask import Flask
from pathlib import Path

def create_app():
    app = Flask(__name__, instance_relative_config=True)

    # Секретний ключ потрібен для session
    app.config["SECRET_KEY"] = "change-me-in-production"
    app.config["DATABASE"] = str(Path(app.instance_path) / "cloudbox.sqlite3")

    # Гарантуємо, що instance/ існує
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    # Ініціалізація БД (створення таблиць)
    from .models import init_db
    init_db(app)

    # Роути
    from .auth import auth_bp
    from .routes import main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    return app