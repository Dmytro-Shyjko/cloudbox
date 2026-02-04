import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
import time

def get_db(app):
    db_path = app.config["DATABASE"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(app):
    with get_db(app) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                role TEXT NOT NULL DEFAULT 'user',
                is_premium INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL,
                username TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 0,
                ts INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_ts ON login_attempts(ip, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_user_ts ON login_attempts(username, ts)")
        conn.commit()

def create_user(app, username: str, password: str) -> bool:
    password_hash = generate_password_hash(password)
    try:
        with get_db(app) as conn:
            conn.execute(
                """
                INSERT INTO users (username, password_hash, role, is_premium, is_active)
                VALUES (?, ?, 'user', 0, 1)
                """,
                (username, password_hash),
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
            (username,),
        ).fetchone()

    if not row:
        return None

    # blocked user
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

def _now_ts() -> int:
    return int(time.time())

def cleanup_old_attempts(app, older_than_seconds: int = 7 * 24 * 3600):
    cutoff = _now_ts() - older_than_seconds
    with get_db(app) as conn:
        conn.execute("DELETE FROM login_attempts WHERE ts < ?", (cutoff,))
        conn.commit()

def record_login_attempt(app, ip: str, username: str, success: bool):
    ip = (ip or "").strip()[:80]
    username = (username or "").strip()[:80]
    ts = _now_ts()
    with get_db(app) as conn:
        conn.execute(
            "INSERT INTO login_attempts (ip, username, success, ts) VALUES (?, ?, ?, ?)",
            (ip, username.lower(), 1 if success else 0, ts),
        )
        conn.commit()

def is_login_blocked(app, ip: str, username: str,
                     window_seconds: int = 15 * 60,
                     max_fail_ip: int = 20,
                     max_fail_user: int = 10,
                     block_seconds: int = 15 * 60):
    """
    Returns (blocked: bool, retry_after_seconds: int)
    Blocks if too many FAILS in the last window.
    """
    ip = (ip or "").strip()[:80]
    username = (username or "").strip().lower()[:80]
    now = _now_ts()
    window_start = now - window_seconds

    with get_db(app) as conn:
        # fails by IP
        ip_fails = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM login_attempts
            WHERE ip = ? AND success = 0 AND ts >= ?
            """,
            (ip, window_start),
        ).fetchone()["c"]

        # fails by username
        user_fails = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM login_attempts
            WHERE username = ? AND success = 0 AND ts >= ?
            """,
            (username, window_start),
        ).fetchone()["c"]

        # if over limit => determine retry-after based on last failure time
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

