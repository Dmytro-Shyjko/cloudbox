import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

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
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()

def create_user(app, username: str, password: str) -> bool:
    password_hash = generate_password_hash(password)
    try:
        with get_db(app) as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, password_hash),
            )
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def verify_user(app, username: str, password: str):
    with get_db(app) as conn:
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    if row and check_password_hash(row["password_hash"], password):
        return {"id": row["id"], "username": row["username"]}
    return None
