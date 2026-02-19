import re
from pathlib import Path


def _set_logged_in(client, user_id=1, username="apiuser"):
	with client.session_transaction() as sess:
		sess["user_id"] = user_id
		sess["username"] = username


def _insert_user(db, user_id=1, username="apiuser"):
	db(
		"INSERT INTO users (id, username, password_hash) VALUES (?, ?, ?)",
		(user_id, username, "hash"),
	)


def _csrf_from_page(client, path="/login"):
	resp = client.get(path)
	assert resp.status_code == 200
	html = resp.get_data(as_text=True)
	match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
	assert match
	return match.group(1)


def test_init_creates_upload_row_and_tmp_dir(client, db, app):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)

	resp = client.post(
		"/api/uploads/init",
		json={
			"filename": "report final.pdf",
			"total_size": 16,
			"chunk_size": 8,
			"target_path": "docs",
		},
		headers={"X-CSRFToken": csrf},
	)
	assert resp.status_code == 200
	payload = resp.get_json()
	assert payload["status"] == "initiated"
	assert payload["missing_chunks"] == [0, 1]

	rows = db(
		"SELECT filename_original, filename_final, status FROM uploads WHERE id = ?",
		(payload["upload_id"],),
	)
	assert rows
	assert rows[0]["filename_original"] == "report final.pdf"
	assert rows[0]["filename_final"] == "report_final.pdf"
	assert rows[0]["status"] == "initiated"

	chunk_dir = Path(app.config["UPLOAD_TMP_DIR_ABS"]) / "1" / payload["upload_id"] / "chunks"
	assert chunk_dir.exists()
	assert chunk_dir.is_dir()


def test_init_enforces_max_active_uploads_per_user(client, db, app):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)

	db(
		"""
		INSERT INTO uploads (
			id, user_id, target_path, filename_original, filename_final,
			total_size, chunk_size, total_chunks, status,
			created_at, updated_at, expires_at, last_activity_at
		)
		VALUES
			('u1', 1, '', 'a.bin', 'a.bin', 10, 5, 2, 'initiated', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
			('u2', 1, '', 'b.bin', 'b.bin', 10, 5, 2, 'uploading', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
			('u3', 1, '', 'c.bin', 'c.bin', 10, 5, 2, 'assembling', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
		"""
	)

	resp = client.post(
		"/api/uploads/init",
		json={
			"filename": "blocked.bin",
			"total_size": 10,
			"chunk_size": 5,
			"target_path": "",
		},
		headers={"X-CSRFToken": csrf},
	)
	assert resp.status_code == 429
	assert "active uploads" in resp.get_json()["error"]


def test_status_returns_full_missing_range_when_no_chunks(client, db):
	_insert_user(db)
	_set_logged_in(client)

	db(
		"""
		INSERT INTO uploads (
			id, user_id, target_path, filename_original, filename_final,
			total_size, chunk_size, total_chunks, status,
			created_at, updated_at, expires_at, last_activity_at
		)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
		""",
		("up-status", 1, "", "a.bin", "a.bin", 13, 5, 3, "initiated"),
	)

	resp = client.get("/api/uploads/up-status/status")
	assert resp.status_code == 200
	payload = resp.get_json()
	assert payload["uploaded_chunks"] == []
	assert payload["missing_chunks"] == [0, 1, 2]


def test_cancel_updates_status_and_removes_tmp_dir(client, db, app):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)

	upload_id = "up-cancel"
	db(
		"""
		INSERT INTO uploads (
			id, user_id, target_path, filename_original, filename_final,
			total_size, chunk_size, total_chunks, status,
			created_at, updated_at, expires_at, last_activity_at
		)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
		""",
		(upload_id, 1, "", "a.bin", "a.bin", 10, 5, 2, "initiated"),
	)
	chunk_dir = Path(app.config["UPLOAD_TMP_DIR_ABS"]) / "1" / upload_id / "chunks"
	chunk_dir.mkdir(parents=True, exist_ok=True)
	(chunk_dir / "0.part").write_text("x", encoding="utf-8")

	resp = client.post(f"/api/uploads/{upload_id}/cancel", headers={"X-CSRFToken": csrf})
	assert resp.status_code == 200
	payload = resp.get_json()
	assert payload == {"ok": True, "status": "canceled"}

	rows = db("SELECT status FROM uploads WHERE id = ?", (upload_id,))
	assert rows[0]["status"] == "canceled"
	assert not (Path(app.config["UPLOAD_TMP_DIR_ABS"]) / "1" / upload_id).exists()


def test_unauthorized_access_blocked(client):
	resp = client.get("/api/uploads/unknown/status")
	assert resp.status_code == 401


def test_csrf_header_required_for_post_endpoints(client, db):
	_insert_user(db)
	_set_logged_in(client)

	resp = client.post(
		"/api/uploads/init",
		json={
			"filename": "no-csrf.txt",
			"total_size": 4,
			"chunk_size": 2,
			"target_path": "",
		},
	)
	assert resp.status_code in (400, 403)

	csrf = _csrf_from_page(client)
	resp_ok = client.post(
		"/api/uploads/init",
		json={
			"filename": "with-csrf.txt",
			"total_size": 4,
			"chunk_size": 2,
			"target_path": "",
		},
		headers={"X-CSRFToken": csrf},
	)
	assert resp_ok.status_code == 200
