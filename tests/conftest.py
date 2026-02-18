import os
import sys
import sqlite3
import re
import pytest
from pathlib import Path


# --- make sure project root is importable ---
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # .../cloudbox
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def import_app():
    # now this should work reliably
    from app import create_app
    return create_app


@pytest.fixture()
def app(tmp_path):
    create_app = import_app()

    db_path = str(tmp_path / "test.sqlite3")
    os.environ["FLASK_ENV"] = "testing"
    os.environ["SECRET_KEY"] = "test-secret-key-1234567890-abcdefgh"

    app = create_app(
        {
            "TESTING": True,
            "DATABASE": db_path,
        }
    )

    # IMPORTANT:
    # create_app() вже викликає init_db(app), тому схему окремо створювати НЕ треба
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    """Simple DB helper: db(sql, params) -> rows (sqlite3.Row)."""
    def _query(sql: str, params=()):
        conn = sqlite3.connect(app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        conn.commit()
        conn.close()
        return rows
    return _query



def extract_csrf_token(html: str) -> str:
	match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
	assert match, "CSRF token not found in HTML"
	return match.group(1)


@pytest.fixture()
def csrf_token(client):
	def _token(path: str) -> str:
		resp = client.get(path)
		assert resp.status_code == 200
		return extract_csrf_token(resp.get_data(as_text=True))

	return _token
