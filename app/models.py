import sqlite3
import time
from typing import Optional, Tuple, Any
from werkzeug.security import generate_password_hash, check_password_hash
from pathlib import Path
import shutil


CHUNKED_UPLOADS_FOUNDATION_MIGRATION = "20260219_chunked_uploads_foundation"


# -----------------------------
# DB helpers
# -----------------------------
def _ensure_users_columns(conn: sqlite3.Connection) -> None:
	"""
	Додає відсутні колонки у users, щоб БД могла "самолікуватись"
	після змін схеми під час бети.
	"""
	wanted = {
		"role": "TEXT NOT NULL DEFAULT 'user'",
		"is_premium": "INTEGER NOT NULL DEFAULT 0",
		"is_active": "INTEGER NOT NULL DEFAULT 1",
		"email": "TEXT",
		"first_name": "TEXT",
		"last_name": "TEXT",
		"country": "TEXT",
		"phone": "TEXT",
		"accepted_terms_at": "INTEGER",
		"email_verified": "INTEGER NOT NULL DEFAULT 0",
		"email_verify_token": "TEXT",
		"reset_token": "TEXT",
		"reset_expires_at": "INTEGER",
		"last_active_at": "INTEGER",
	}

	cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
	for col, ddl in wanted.items():
		if col not in cols:
			conn.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")


def _ensure_user_files_columns(conn: sqlite3.Connection) -> None:
	cols = {row["name"] for row in conn.execute("PRAGMA table_info(user_files)").fetchall()}
	if "size" not in cols:
		conn.execute("ALTER TABLE user_files ADD COLUMN size INTEGER")
	if "sha256" not in cols:
		conn.execute("ALTER TABLE user_files ADD COLUMN sha256 TEXT")


def _apply_chunked_uploads_foundation_migration(conn: sqlite3.Connection) -> None:
	conn.execute(
		"""
		CREATE TABLE IF NOT EXISTS uploads (
			id TEXT PRIMARY KEY,
			user_id INTEGER NOT NULL,
			target_path TEXT,
			filename_original TEXT NOT NULL,
			filename_final TEXT,
			total_size INTEGER NOT NULL,
			chunk_size INTEGER NOT NULL,
			total_chunks INTEGER NOT NULL,
			status TEXT NOT NULL,
			sha256_client TEXT,
			sha256_final TEXT,
			created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
			updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
			expires_at DATETIME NOT NULL,
			last_activity_at DATETIME NOT NULL,
			FOREIGN KEY(user_id) REFERENCES users(id)
		);
		"""
	)
	conn.execute("CREATE INDEX IF NOT EXISTS idx_uploads_user_status ON uploads(user_id, status)")
	conn.execute("CREATE INDEX IF NOT EXISTS idx_uploads_expires_at ON uploads(expires_at)")
	conn.execute("CREATE INDEX IF NOT EXISTS idx_uploads_last_activity_at ON uploads(last_activity_at)")

	conn.execute(
		"""
		CREATE TABLE IF NOT EXISTS upload_chunks (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			upload_id TEXT NOT NULL,
			chunk_index INTEGER NOT NULL,
			size INTEGER NOT NULL,
			sha256 TEXT,
			created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
			FOREIGN KEY(upload_id) REFERENCES uploads(id) ON DELETE CASCADE,
			UNIQUE(upload_id, chunk_index)
		);
		"""
	)
	conn.execute("CREATE INDEX IF NOT EXISTS idx_upload_chunks_upload_id ON upload_chunks(upload_id)")

	conn.execute(
		"""
		CREATE TABLE IF NOT EXISTS user_files (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			user_id INTEGER NOT NULL,
			path TEXT NOT NULL,
			filename TEXT NOT NULL,
			size INTEGER,
			sha256 TEXT,
			created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
			updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
			FOREIGN KEY(user_id) REFERENCES users(id)
		);
		"""
	)
	_ensure_user_files_columns(conn)
	conn.execute("CREATE INDEX IF NOT EXISTS idx_user_files_user_sha256 ON user_files(user_id, sha256)")


def _ensure_schema_migrations(conn: sqlite3.Connection) -> None:
	conn.execute(
		"""
		CREATE TABLE IF NOT EXISTS schema_migrations (
			name TEXT PRIMARY KEY,
			applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
		);
		"""
	)


def _run_migrations(conn: sqlite3.Connection) -> None:
	_ensure_schema_migrations(conn)
	migration_names = {
		row["name"]
		for row in conn.execute("SELECT name FROM schema_migrations").fetchall()
	}
	if CHUNKED_UPLOADS_FOUNDATION_MIGRATION not in migration_names:
		_apply_chunked_uploads_foundation_migration(conn)
		conn.execute(
			"INSERT INTO schema_migrations(name) VALUES (?)",
			(CHUNKED_UPLOADS_FOUNDATION_MIGRATION,),
		)


def user_storage_used_bytes(app, user_id: int) -> int:
	base = Path(app.root_path).parent / "storage" / str(int(user_id))
	if not base.exists():
		return 0
	total = 0
	for p in base.rglob("*"):
		if p.is_file():
			try:
				total += p.stat().st_size
			except OSError:
				pass
	return total


def can_create_more_users(app) -> bool:
	limit = int(app.config.get("BETA_MAX_USERS", 0))
	if limit <= 0:
		return True  # 0 або менше = без ліміту

	with get_db(app) as conn:
		row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
		return int(row["c"]) < limit


def get_db(app):
	db_path = app.config["DATABASE"]
	conn = sqlite3.connect(db_path)
	conn.row_factory = sqlite3.Row
	conn.execute("PRAGMA foreign_keys = ON")
	return conn


def _now_ts() -> int:
	return int(time.time())


def init_db(app):
	"""
	Must be called on app startup (and in tests fixtures).
	Creates all required tables for register/login tests.
	"""
	with get_db(app) as conn:
		# users (schema aligned with what you posted)
		conn.execute(
			"""
			CREATE TABLE IF NOT EXISTS users (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				username TEXT UNIQUE NOT NULL,
				password_hash TEXT NOT NULL,
				created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
				role TEXT NOT NULL DEFAULT 'user',
				is_premium INTEGER NOT NULL DEFAULT 0,
				is_active INTEGER NOT NULL DEFAULT 1,

				email TEXT,
				first_name TEXT,
				last_name TEXT,
				country TEXT,
				phone TEXT,

				accepted_terms_at INTEGER,
				email_verified INTEGER NOT NULL DEFAULT 0,
				email_verify_token TEXT,

				reset_token TEXT,
				reset_expires_at INTEGER
			);
			"""
		)

		_ensure_users_columns(conn)

		# unique email when not null (sqlite partial unique index)
		conn.execute(
			"""
			CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique
			ON users(email)
			WHERE email IS NOT NULL;
			"""
		)

		# login_attempts (required by login tests)
		conn.execute(
			"""
			CREATE TABLE IF NOT EXISTS login_attempts (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				ip TEXT NOT NULL,
				username TEXT NOT NULL,
				success INTEGER NOT NULL DEFAULT 0,
				ts INTEGER NOT NULL
			);
			"""
		)
		conn.execute(
			"CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_ts ON login_attempts(ip, ts)"
		)
		conn.execute(
			"CREATE INDEX IF NOT EXISTS idx_login_attempts_user_ts ON login_attempts(username, ts)"
		)

		# register_attempts (required by register rate-limit tests)
		conn.execute(
			"""
			CREATE TABLE IF NOT EXISTS register_attempts (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				ip TEXT NOT NULL,
				success INTEGER NOT NULL DEFAULT 0,
				ts INTEGER NOT NULL
			);
			"""
		)
		conn.execute(
			"CREATE INDEX IF NOT EXISTS idx_register_attempts_ip_ts ON register_attempts(ip, ts)"
		)

		_run_migrations(conn)
		conn.commit()


def touch_user_activity(app, user_id: int) -> None:
    ts = _now_ts()
    with get_db(app) as conn:
        _ensure_users_columns(conn)
        conn.execute("UPDATE users SET last_active_at = ? WHERE id = ?", (ts, int(user_id)))
        conn.commit()


# -----------------------------
# Users
# -----------------------------
def create_user(
    app,
    username: str,
    password: str,
    email: str,
    first_name: str,
    last_name: str,
    country: str,
    phone: Optional[str],
    accepted_terms: bool,
) -> bool:
    """
    Returns True if created, False if username/email exists or invalid.
    DB schema has accepted_terms_at (timestamp), no accepted_terms flag.
    """
    if not can_create_more_users(app):  # Перевірка ліміту користувачів
        return False

    if not accepted_terms:
        return False

    username_clean = (username or "").strip()
    email_clean = (email or "").strip().lower()
    first_name_clean = (first_name or "").strip()
    last_name_clean = (last_name or "").strip()
    country_clean = (country or "").strip()
    phone_clean = (phone or "").strip() if phone else None

    password_hash = generate_password_hash(password)
    ts = _now_ts()

    try:
        with get_db(app) as conn:
            # якщо це перший користувач у системі — робимо його admin
            cnt = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            role = "admin" if int(cnt) == 0 else "user"
            
            conn.execute(
                """
                INSERT INTO users (
                    username, password_hash,
                    email, first_name, last_name, country, phone,
                    accepted_terms_at,
                    email_verified, email_verify_token,
                    role, is_premium, is_active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    username_clean,
                    password_hash,
                    email_clean,
                    first_name_clean,
                    last_name_clean,
                    country_clean,
                    phone_clean,
                    ts,
                    0,
                    None,
                    role,   # <-- тут
                    0,
                    1,
                ),
            )
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def verify_user(app, username: str, password: str):
    with get_db(app) as conn:
        row = conn.execute(
            """
            SELECT id, username, password_hash, role, is_premium, is_active
            FROM users
            WHERE username = ?
            """,
            ((username or "").strip(),),
        ).fetchone()

    if not row:
        return None

    if int(row["is_active"]) == 0:
        return {"blocked": True}

    if check_password_hash(row["password_hash"], password):
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "is_premium": int(row["is_premium"]),
            "is_active": int(row["is_active"]),
        }

    return None


def list_users(app):
    with get_db(app) as conn:
        rows = conn.execute(
            "SELECT id, username, role, is_premium, is_active, created_at FROM users ORDER BY id ASC"
        ).fetchall()
    return rows


def toggle_user_premium(app, user_id: int):
    with get_db(app) as conn:
        conn.execute(
            "UPDATE users SET is_premium = CASE WHEN is_premium=1 THEN 0 ELSE 1 END WHERE id=?",
            (user_id,),
        )
        conn.commit()


def toggle_user_active(app, user_id: int):
    with get_db(app) as conn:
        conn.execute(
            "UPDATE users SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?",
            (user_id,),
        )
        conn.commit()


def set_user_role(app, user_id: int, role: str):
    with get_db(app) as conn:
        conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
        conn.commit()


# -----------------------------
# Login attempts / rate-limit
# -----------------------------
def cleanup_old_login_attempts(app, older_than_seconds: int = 7 * 24 * 3600):
    cutoff = _now_ts() - older_than_seconds
    with get_db(app) as conn:
        conn.execute("DELETE FROM login_attempts WHERE ts < ?", (cutoff,))
        conn.commit()


# backward-compatible alias (if used in auth.py)
def cleanup_old_attempts(app, older_than_seconds: int = 7 * 24 * 3600):
    cleanup_old_login_attempts(app, older_than_seconds)


def record_login_attempt(app, ip: str, username: str, success: bool):
    ip = (ip or "").strip()[:80]
    username = (username or "").strip().lower()[:80]
    ts = _now_ts()
    with get_db(app) as conn:
        conn.execute(
            "INSERT INTO login_attempts (ip, username, success, ts) VALUES (?, ?, ?, ?)",
            (ip, username, 1 if success else 0, ts),
        )
        conn.commit()


def is_login_blocked(
    app,
    ip: str,
    username: str,
    window_seconds: int = 15 * 60,
    max_fail_ip: int = 20,
    max_fail_user: int = 10,
    block_seconds: int = 15 * 60,
) -> Tuple[bool, int]:
    """
    Returns (blocked: bool, retry_after_seconds: int)
    Blocks if too many FAILS in the last window.
    """
    ip = (ip or "").strip()[:80]
    username = (username or "").strip().lower()[:80]
    now = _now_ts()
    window_start = now - window_seconds

    with get_db(app) as conn:
        ip_fails = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM login_attempts
            WHERE ip = ? AND success = 0 AND ts >= ?
            """,
            (ip, window_start),
        ).fetchone()["c"]

        user_fails = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM login_attempts
            WHERE username = ? AND success = 0 AND ts >= ?
            """,
            (username, window_start),
        ).fetchone()["c"]

        if ip_fails >= max_fail_ip or user_fails >= max_fail_user:
            last_fail = conn.execute(
                """
                SELECT ts
                FROM login_attempts
                WHERE (ip = ? OR username = ?) AND success = 0
                ORDER BY ts DESC
                LIMIT 1
                """,
                (ip, username),
            ).fetchone()

            if last_fail:
                retry_after = max(0, block_seconds - (now - int(last_fail["ts"])))
                if retry_after > 0:
                    return True, retry_after

    return False, 0


# -----------------------------
# Register attempts / rate-limit
# -----------------------------
def cleanup_old_register_attempts(app, older_than_seconds: int = 7 * 24 * 3600):
    cutoff = _now_ts() - older_than_seconds
    with get_db(app) as conn:
        conn.execute("DELETE FROM register_attempts WHERE ts < ?", (cutoff,))
        conn.commit()


def record_register_attempt(app, ip: str, success: bool):
    ip = (ip or "").strip()[:80]
    ts = _now_ts()
    with get_db(app) as conn:
        conn.execute(
            "INSERT INTO register_attempts (ip, success, ts) VALUES (?, ?, ?)",
            (ip, 1 if success else 0, ts),
        )
        conn.commit()


def is_register_blocked(
    app,
    ip: str,
    window_seconds: int = 30 * 60,
    max_fail_ip: int = 10,
    block_seconds: int = 30 * 60,
) -> Tuple[bool, int]:
    """
    Returns (blocked: bool, retry_after_seconds: int)
    Blocks if too many FAILS from same IP in the last window.
    """
    ip = (ip or "").strip()[:80]
    now = _now_ts()
    window_start = now - window_seconds

    with get_db(app) as conn:
        fails = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM register_attempts
            WHERE ip = ? AND success = 0 AND ts >= ?
            """,
            (ip, window_start),
        ).fetchone()["c"]

        if fails >= max_fail_ip:
            last_fail = conn.execute(
                """
                SELECT ts
                FROM register_attempts
                WHERE ip = ? AND success = 0
                ORDER BY ts DESC
                LIMIT 1
                """,
                (ip,),
            ).fetchone()

            if last_fail:
                retry_after = max(0, block_seconds - (now - int(last_fail["ts"])))
                if retry_after > 0:
                    return True, retry_after

    return False, 0

# -------------------------
# Admin helpers
# -------------------------
def list_users(app):
    with get_db(app) as conn:
        rows = conn.execute(
            """
            SELECT id, username, email, first_name, last_name, country, phone,
                   role, is_premium, is_active, created_at
            FROM users
            ORDER BY id ASC
            """
        ).fetchall()
    return rows


def toggle_user_premium(app, user_id: int):
    with get_db(app) as conn:
        conn.execute(
            """
            UPDATE users
            SET is_premium = CASE WHEN is_premium = 1 THEN 0 ELSE 1 END
            WHERE id = ?
            """,
            (user_id,),
        )
        conn.commit()


def toggle_user_active(app, user_id: int):
    with get_db(app) as conn:
        conn.execute(
            """
            UPDATE users
            SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END
            WHERE id = ?
            """,
            (user_id,),
        )
        conn.commit()


def set_user_role(app, user_id: int, role: str):
    role = (role or "").strip()
    if role not in ("user", "admin"):
        role = "user"
    with get_db(app) as conn:
        conn.execute(
            "UPDATE users SET role = ? WHERE id = ?",
            (role, user_id),
        )
        conn.commit()

def list_users(app):
    with get_db(app) as conn:
        rows = conn.execute(
            """
            SELECT id, username, role, is_premium, is_active, created_at
            FROM users
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
    return rows


def toggle_user_premium(app, user_id: int) -> None:
    with get_db(app) as conn:
        conn.execute(
            """
            UPDATE users
            SET is_premium = CASE WHEN is_premium = 1 THEN 0 ELSE 1 END
            WHERE id = ?
            """,
            (int(user_id),),
        )
        conn.commit()


def toggle_user_active(app, user_id: int) -> None:
    with get_db(app) as conn:
        conn.execute(
            """
            UPDATE users
            SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END
            WHERE id = ?
            """,
            (int(user_id),),
        )
        conn.commit()


def set_user_role(app, user_id: int, role: str) -> None:
    role = (role or "").strip().lower()
    if role not in ("user", "moderator", "admin"):
        raise ValueError("Invalid role")

    with get_db(app) as conn:
        conn.execute(
            "UPDATE users SET role = ? WHERE id = ?",
            (role, int(user_id)),
        )
        conn.commit()

def list_users(app):
    with get_db(app) as conn:
        _ensure_users_columns(conn)
        rows = conn.execute(
            """
            SELECT id, username, role, is_premium, is_active, created_at,
                   email, first_name, last_name, country, phone,
                   last_active_at
            FROM users
            ORDER BY id ASC
            """
        ).fetchall()
        return rows

def get_user(app, user_id: int):
    with get_db(app) as conn:
        _ensure_users_columns(conn)
        row = conn.execute(
            """
            SELECT id, username, role, is_premium, is_active, created_at,
                   email, first_name, last_name, country, phone,
                   last_active_at
            FROM users
            WHERE id = ?
            """,
            (int(user_id),),
        ).fetchone()
        return row

def toggle_user_premium(app, user_id: int):
    with get_db(app) as conn:
        conn.execute(
            "UPDATE users SET is_premium = CASE WHEN is_premium=1 THEN 0 ELSE 1 END WHERE id = ?",
            (int(user_id),),
        )
        conn.commit()

def toggle_user_active(app, user_id: int):
    with get_db(app) as conn:
        conn.execute(
            "UPDATE users SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id = ?",
            (int(user_id),),
        )
        conn.commit()

def set_user_role(app, user_id: int, role: str):
    role = (role or "").strip().lower()
    if role not in ("user", "moderator", "admin"):
        raise ValueError("bad role")
    with get_db(app) as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, int(user_id)))
        conn.commit()

def delete_user(app, user_id: int) -> None:
    """
    BETA: видаляємо без умов.
    Також прибираємо папку storage/<user_id> і логін-спроби.
    """
    uid = int(user_id)

    # 1) delete storage folder
    from pathlib import Path
    import shutil
    storage_dir = Path(app.root_path).parent / "storage" / str(uid)
    if storage_dir.exists():
        shutil.rmtree(storage_dir)

    # 2) delete DB records
    with get_db(app) as conn:
        conn.execute("DELETE FROM login_attempts WHERE username IN (SELECT username FROM users WHERE id=?)", (uid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
        conn.commit()

def _storage_user_dir(app, user_id: int) -> Path:
    """
    storage/<user_id>/  (аналогічно до routes.py, але без current_app)
    """
    base = Path(app.root_path).parent / "storage"
    return (base / str(int(user_id)))


def _dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def used_mb(app, user_id: int) -> int:
    """
    Скільки MB використано у storage/<user_id>
    """
    p = _storage_user_dir(app, user_id)
    b = _dir_size_bytes(p)
    return int(b // (1024 * 1024))


def _user_quota_mb(app, is_premium: int) -> int:
    if int(is_premium) == 1:
        return int(app.config.get("PREMIUM_QUOTA_MB", 10_000))
    return int(app.config.get("FREE_QUOTA_MB", 2_000))


def last_active(app, user_id: int):
    """
    Повертає last_active_at як unix timestamp або None
    """
    with get_db(app) as conn:
        _ensure_users_columns(conn)
        row = conn.execute(
            "SELECT last_active_at FROM users WHERE id = ?",
            (int(user_id),),
        ).fetchone()
        if not row:
            return None
        v = row["last_active_at"]
        return int(v) if v is not None else None


def touch_last_active(app, user_id: int) -> None:
    """
    Оновлюй цю функцію при будь-якій активності користувача (наприклад /files).
    """
    ts = _now_ts()
    with get_db(app) as conn:
        _ensure_users_columns(conn)
        conn.execute(
            "UPDATE users SET last_active_at = ? WHERE id = ?",
            (ts, int(user_id)),
        )
        conn.commit()


def list_users(app):
    """
    Список користувачів для таблиці адмінки.
    Повертає list[dict].
    """
    with get_db(app) as conn:
        _ensure_users_columns(conn)
        rows = conn.execute(
            """
            SELECT
                id, username, role, is_premium, is_active,
                email, first_name, last_name, country, phone,
                accepted_terms_at, email_verified,
                last_active_at, created_at
            FROM users
            ORDER BY id DESC
            """
        ).fetchall()

    out = []
    for r in rows:
        uid = int(r["id"])
        premium = int(r["is_premium"] or 0)
        out.append({
            "id": uid,
            "username": r["username"],
            "role": r["role"] or "user",
            "is_premium": premium,
            "is_active": int(r["is_active"] if r["is_active"] is not None else 1),
            "created_at": r["created_at"],
            "last_active_at": r["last_active_at"],  # можна форматувати в шаблоні або пізніше
            "used_mb": used_mb(app, uid),
            "quota_mb": _user_quota_mb(app, premium),
        })
    return out


def get_user_detail(app, user_id: int):
    """
    Деталі користувача для сторінки user detail.
    Повертає dict або None.
    """
    user_id = int(user_id)

    with get_db(app) as conn:
        _ensure_users_columns(conn)
        r = conn.execute(
            """
            SELECT
                id, username, role, is_premium, is_active,
                email, first_name, last_name, country, phone,
                accepted_terms_at, email_verified,
                last_active_at, created_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

    if not r:
        return None

    premium = int(r["is_premium"] or 0)

    return {
        "id": int(r["id"]),
        "username": r["username"],
        "role": r["role"] or "user",
        "is_premium": premium,
        "is_active": int(r["is_active"] if r["is_active"] is not None else 1),

        "email": r["email"],
        "first_name": r["first_name"],
        "last_name": r["last_name"],
        "country": r["country"],
        "phone": r["phone"],

        "accepted_terms_at": r["accepted_terms_at"],
        "email_verified": int(r["email_verified"] or 0),

        "created_at": r["created_at"],
        "last_active_at": r["last_active_at"],

        "used_mb": used_mb(app, user_id),
        "quota_mb": _user_quota_mb(app, premium),
    }


def toggle_user_premium(app, user_id: int) -> None:
    user_id = int(user_id)
    with get_db(app) as conn:
        _ensure_users_columns(conn)
        cur = conn.execute("SELECT is_premium FROM users WHERE id = ?", (user_id,)).fetchone()
        if not cur:
            return
        new_val = 0 if int(cur["is_premium"] or 0) == 1 else 1
        conn.execute("UPDATE users SET is_premium = ? WHERE id = ?", (new_val, user_id))
        conn.commit()


def toggle_user_active(app, user_id: int) -> None:
    """
    is_active: 1=active, 0=blocked
    """
    user_id = int(user_id)
    with get_db(app) as conn:
        _ensure_users_columns(conn)
        cur = conn.execute("SELECT is_active FROM users WHERE id = ?", (user_id,)).fetchone()
        if not cur:
            return
        new_val = 0 if int(cur["is_active"] if cur["is_active"] is not None else 1) == 1 else 1
        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_val, user_id))
        conn.commit()


def set_user_role(app, user_id: int, role: str) -> None:
    user_id = int(user_id)
    role = (role or "").strip().lower()
    if role not in ("user", "moderator", "admin"):
        return
    with get_db(app) as conn:
        _ensure_users_columns(conn)
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        conn.commit()


def delete_user(app, user_id: int, delete_files: bool = True) -> None:
    """
    Видаляє користувача з БД.
    Під час бета — без додаткових умов (але викликати має лише admin у admin.py).
    Також видаляє файли storage/<user_id> якщо delete_files=True
    """
    user_id = int(user_id)

    # 1) delete DB rows
    with get_db(app) as conn:
        _ensure_users_columns(conn)

        # прибираємо "attempts" по username (не обов’язково, але чисто)
        row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        username = (row["username"] if row else "") or ""

        conn.execute("DELETE FROM login_attempts WHERE username = ?", (username.lower(),))
        conn.execute("DELETE FROM register_attempts WHERE ip IS NULL AND 1=0")  # no-op, залишаю як заглушку

        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()

    # 2) delete files
    if delete_files:
        p = _storage_user_dir(app, user_id)
        if p.exists() and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
