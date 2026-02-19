from flask import Flask, session, render_template
from flask_wtf.csrf import CSRFProtect
from jinja2 import TemplateNotFound
from pathlib import Path
import os

from .i18n import get_lang, t, SUPPORTED_LANGS
from .models import init_db


csrf = CSRFProtect()


def create_app(test_config=None):
	app = Flask(__name__, instance_relative_config=True)

	def _positive_int_env(name: str, default: int) -> int:
		raw_value = os.environ.get(name, str(default))
		try:
			value = int(raw_value)
		except (TypeError, ValueError) as exc:
			raise RuntimeError(f"{name} must be an integer greater than 0") from exc
		if value <= 0:
			raise RuntimeError(f"{name} must be greater than 0")
		return value

	def _resolve_upload_tmp_dir() -> str:
		raw_dir = (os.environ.get("UPLOAD_TMP_DIR") or "instance/uploads_tmp").strip()
		if not raw_dir:
			raw_dir = "instance/uploads_tmp"

		candidate = Path(raw_dir)
		if ".." in candidate.parts:
			raise RuntimeError("UPLOAD_TMP_DIR must not contain path traversal segments ('..')")

		if candidate.is_absolute():
			return str(candidate.resolve())

		return str((Path(app.instance_path) / candidate).resolve())

	def _upload_max_mb() -> int:
		try:
			value = int(os.environ.get("UPLOAD_MAX_MB", "1024"))
		except (TypeError, ValueError):
			return 1024
		return value if value > 0 else 1024

	secret_key = os.environ.get("SECRET_KEY")
	if not secret_key or len(secret_key) < 32:
		raise RuntimeError("SECRET_KEY must be set in the environment and contain at least 32 characters")

	# --- base config ---
	app.config["SECRET_KEY"] = secret_key
	app.config["SESSION_COOKIE_HTTPONLY"] = True
	app.config["SESSION_COOKIE_SECURE"] = True
	app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
	app.config["DATABASE"] = str(Path(app.instance_path) / "cloudbox.sqlite3")
	app.config["MAX_CONTENT_LENGTH"] = _upload_max_mb() * 1024 * 1024
	app.config["CHUNK_SIZE_DEFAULT_MB"] = _positive_int_env("CHUNK_SIZE_DEFAULT_MB", 5)
	app.config["CHUNK_SIZE_MAX_MB"] = _positive_int_env("CHUNK_SIZE_MAX_MB", 10)
	if app.config["CHUNK_SIZE_DEFAULT_MB"] > app.config["CHUNK_SIZE_MAX_MB"]:
		raise RuntimeError("CHUNK_SIZE_DEFAULT_MB must be less than or equal to CHUNK_SIZE_MAX_MB")
	app.config["UPLOAD_TTL_HOURS"] = _positive_int_env("UPLOAD_TTL_HOURS", 24)
	app.config["MAX_ACTIVE_UPLOADS_PER_USER"] = _positive_int_env("MAX_ACTIVE_UPLOADS_PER_USER", 3)
	app.config["UPLOAD_TMP_DIR_ABS"] = _resolve_upload_tmp_dir()

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

	try:
		Path(app.config["UPLOAD_TMP_DIR_ABS"]).mkdir(parents=True, exist_ok=True)
	except OSError as exc:
		env_name = (os.environ.get("ENV") or os.environ.get("FLASK_ENV") or "").strip().lower()
		if env_name == "production":
			raise RuntimeError(
				f"Failed to create upload temp directory at {app.config['UPLOAD_TMP_DIR_ABS']}"
			) from exc
		app.logger.warning(
			"Failed to create upload temp directory at %s: %s",
			app.config["UPLOAD_TMP_DIR_ABS"],
			exc,
		)

	# CSRF protection for all POST forms/endpoints.
	csrf.init_app(app)

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

	@app.errorhandler(413)
	def request_entity_too_large(_err):
		try:
			return render_template("413.html"), 413
		except TemplateNotFound:
			return (
				"<h1>Upload too large</h1><p>Your file exceeds the allowed upload limit.</p>",
				413,
			)


	# --- blueprints (import AFTER app exists) ---
	from .auth import auth_bp
	from .routes import main_bp
	from .admin import admin_bp

	app.register_blueprint(auth_bp)
	app.register_blueprint(main_bp)
	app.register_blueprint(admin_bp)

	return app
