from datetime import datetime, timedelta, timezone
from pathlib import Path


def _insert_user(db, user_id=1, username="cleanup-user"):
	db(
		"INSERT INTO users (id, username, password_hash) VALUES (?, ?, ?)",
		(user_id, username, "hash"),
	)


def _dt(hours_ago: int) -> str:
	return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


def test_uploads_cleanup_cli_expires_old_upload_and_removes_artifacts(app, db, tmp_path):
	app.config["UPLOAD_TMP_DIR_ABS"] = str(tmp_path / "tmp_uploads")
	Path(app.config["UPLOAD_TMP_DIR_ABS"]).mkdir(parents=True, exist_ok=True)
	_insert_user(db)

	old_ts = _dt(hours_ago=30)
	db(
		"""
		INSERT INTO uploads (
			id, user_id, target_path, filename_original, filename_final,
			total_size, chunk_size, total_chunks, status,
			created_at, updated_at, expires_at, last_activity_at
		)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		("u-old", 1, "", "video.bin", "video.bin", 6, 3, 2, "uploading", old_ts, old_ts, old_ts, old_ts),
	)
	db(
		"INSERT INTO upload_chunks (upload_id, chunk_index, size, sha256) VALUES (?, ?, ?, ?)",
		("u-old", 0, 3, "abc"),
	)
	db(
		"INSERT INTO upload_chunks (upload_id, chunk_index, size, sha256) VALUES (?, ?, ?, ?)",
		("u-old", 1, 3, "def"),
	)

	upload_root = Path(app.config["UPLOAD_TMP_DIR_ABS"]) / "1" / "u-old"
	chunks_dir = upload_root / "chunks"
	chunks_dir.mkdir(parents=True, exist_ok=True)
	(chunks_dir / "0.part").write_bytes(b"aaa")
	(chunks_dir / "1.part").write_bytes(b"bbb")

	runner = app.test_cli_runner()
	result = runner.invoke(args=["uploads", "cleanup", "--ttl-hours", "24"])
	assert result.exit_code == 0

	rows = db("SELECT status FROM uploads WHERE id = ?", ("u-old",))
	assert rows[0]["status"] == "expired"
	chunks_rows = db("SELECT COUNT(*) AS c FROM upload_chunks WHERE upload_id = ?", ("u-old",))
	assert int(chunks_rows[0]["c"]) == 0
	assert not upload_root.exists()


def test_uploads_cleanup_cli_skips_terminal_and_recent(app, db, tmp_path):
	app.config["UPLOAD_TMP_DIR_ABS"] = str(tmp_path / "tmp_uploads")
	Path(app.config["UPLOAD_TMP_DIR_ABS"]).mkdir(parents=True, exist_ok=True)
	_insert_user(db)

	old_ts = _dt(hours_ago=30)
	recent_ts = _dt(hours_ago=2)
	for upload_id, status, ts in (
		("u-completed", "completed", old_ts),
		("u-canceled", "canceled", old_ts),
		("u-recent", "uploading", recent_ts),
	):
		db(
			"""
			INSERT INTO uploads (
				id, user_id, target_path, filename_original, filename_final,
				total_size, chunk_size, total_chunks, status,
				created_at, updated_at, expires_at, last_activity_at
			)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			""",
			(upload_id, 1, "", f"{upload_id}.bin", f"{upload_id}.bin", 10, 5, 2, status, ts, ts, ts, ts),
		)

	runner = app.test_cli_runner()
	result = runner.invoke(args=["uploads", "cleanup", "--ttl-hours", "24"])
	assert result.exit_code == 0

	rows = db("SELECT id, status FROM uploads WHERE id IN (?, ?, ?)", ("u-completed", "u-canceled", "u-recent"))
	status_map = {row["id"]: row["status"] for row in rows}
	assert status_map["u-completed"] == "completed"
	assert status_map["u-canceled"] == "canceled"
	assert status_map["u-recent"] == "uploading"


def test_uploads_cleanup_cli_dry_run_does_not_change_or_delete(app, db, tmp_path):
	app.config["UPLOAD_TMP_DIR_ABS"] = str(tmp_path / "tmp_uploads")
	Path(app.config["UPLOAD_TMP_DIR_ABS"]).mkdir(parents=True, exist_ok=True)
	_insert_user(db)

	old_ts = _dt(hours_ago=30)
	db(
		"""
		INSERT INTO uploads (
			id, user_id, target_path, filename_original, filename_final,
			total_size, chunk_size, total_chunks, status,
			created_at, updated_at, expires_at, last_activity_at
		)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		("u-dry", 1, "", "dry.bin", "dry.bin", 4, 2, 2, "uploading", old_ts, old_ts, old_ts, old_ts),
	)
	db(
		"INSERT INTO upload_chunks (upload_id, chunk_index, size, sha256) VALUES (?, ?, ?, ?)",
		("u-dry", 0, 2, "aaa"),
	)

	upload_root = Path(app.config["UPLOAD_TMP_DIR_ABS"]) / "1" / "u-dry"
	(upload_root / "chunks").mkdir(parents=True, exist_ok=True)
	(upload_root / "chunks" / "0.part").write_bytes(b"aa")

	runner = app.test_cli_runner()
	result = runner.invoke(args=["uploads", "cleanup", "--ttl-hours", "24", "--dry-run"])
	assert result.exit_code == 0

	rows = db("SELECT status FROM uploads WHERE id = ?", ("u-dry",))
	assert rows[0]["status"] == "uploading"
	chunks_rows = db("SELECT COUNT(*) AS c FROM upload_chunks WHERE upload_id = ?", ("u-dry",))
	assert int(chunks_rows[0]["c"]) == 1
	assert upload_root.exists()
