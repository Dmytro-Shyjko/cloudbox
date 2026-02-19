import os
import sqlite3
from pathlib import Path


def test_tmp_upload_dir_created(app):
	tmp_dir = Path(app.config["UPLOAD_TMP_DIR_ABS"])
	assert tmp_dir.exists()
	assert tmp_dir.is_dir()


def test_chunked_upload_schema_created(app):
	conn = sqlite3.connect(app.config["DATABASE"])
	conn.row_factory = sqlite3.Row

	tables = {
		row["name"]
		for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
	}
	assert "uploads" in tables
	assert "upload_chunks" in tables
	assert "user_files" in tables

	indexes = {
		row["name"]
		for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
	}
	assert "idx_uploads_user_status" in indexes
	assert "idx_uploads_expires_at" in indexes
	assert "idx_uploads_last_activity_at" in indexes
	assert "idx_upload_chunks_upload_id" in indexes
	assert "idx_user_files_user_sha256" in indexes
	conn.close()


def test_chunk_config_validation(tmp_path):
	os.environ["SECRET_KEY"] = "test-secret-key-1234567890-abcdefgh"
	os.environ["CHUNK_SIZE_DEFAULT_MB"] = "12"
	os.environ["CHUNK_SIZE_MAX_MB"] = "10"

	from app import create_app

	try:
		create_app(
			{
				"TESTING": True,
				"DATABASE": str(tmp_path / "test.sqlite3"),
			}
		)
		assert False, "Expected RuntimeError when CHUNK_SIZE_DEFAULT_MB > CHUNK_SIZE_MAX_MB"
	except RuntimeError as exc:
		assert "CHUNK_SIZE_DEFAULT_MB" in str(exc)
	finally:
		os.environ.pop("CHUNK_SIZE_DEFAULT_MB", None)
		os.environ.pop("CHUNK_SIZE_MAX_MB", None)
