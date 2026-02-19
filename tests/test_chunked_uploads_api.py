import re
import hashlib
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


def _create_upload(client, csrf, total_size=3072, chunk_size=1024, total_chunks=3):
	resp = client.post(
		"/api/uploads/init",
		json={
			"filename": "chunk.bin",
			"total_size": total_size,
			"chunk_size": chunk_size,
			"total_chunks": total_chunks,
			"target_path": "",
		},
		headers={"X-CSRFToken": csrf},
	)
	assert resp.status_code == 200
	return resp.get_json()["upload_id"]


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


def test_upload_chunk_happy_path(client, db, app):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	upload_id = _create_upload(client, csrf)

	chunk = b"a" * 1024
	resp = client.post(
		f"/api/uploads/{upload_id}/chunk",
		data=chunk,
		headers={
			"Content-Type": "application/octet-stream",
			"X-Chunk-Index": "0",
			"X-Chunk-Size": str(len(chunk)),
			"X-CSRFToken": csrf,
		},
	)
	assert resp.status_code == 200
	payload = resp.get_json()
	assert payload == {"ok": True, "upload_id": upload_id, "chunk_index": 0, "received": 1024}

	chunk_path = Path(app.config["UPLOAD_TMP_DIR_ABS"]) / "1" / upload_id / "chunks" / "0.part"
	assert chunk_path.exists()
	assert chunk_path.read_bytes() == chunk

	rows = db("SELECT chunk_index, size FROM upload_chunks WHERE upload_id = ?", (upload_id,))
	assert len(rows) == 1
	assert rows[0]["chunk_index"] == 0
	assert rows[0]["size"] == 1024

	upload_rows = db("SELECT status FROM uploads WHERE id = ?", (upload_id,))
	assert upload_rows[0]["status"] == "uploading"


def test_upload_chunk_idempotent(client, db):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	upload_id = _create_upload(client, csrf)

	chunk = b"b" * 1024
	headers = {
		"Content-Type": "application/octet-stream",
		"X-Chunk-Index": "0",
		"X-Chunk-Size": str(len(chunk)),
		"X-CSRFToken": csrf,
	}
	resp_first = client.post(f"/api/uploads/{upload_id}/chunk", data=chunk, headers=headers)
	assert resp_first.status_code == 200
	resp_second = client.post(f"/api/uploads/{upload_id}/chunk", data=chunk, headers=headers)
	assert resp_second.status_code == 200

	rows = db("SELECT COUNT(*) AS c FROM upload_chunks WHERE upload_id = ?", (upload_id,))
	assert rows[0]["c"] == 1


def test_upload_chunk_rejects_out_of_range_index(client, db):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	upload_id = _create_upload(client, csrf)

	resp = client.post(
		f"/api/uploads/{upload_id}/chunk",
		data=b"x" * 1024,
		headers={
			"Content-Type": "application/octet-stream",
			"X-Chunk-Index": "5",
			"X-CSRFToken": csrf,
		},
	)
	assert resp.status_code == 400


def test_upload_chunk_rejects_wrong_owner(client, db):
	_insert_user(db, 1, "owner")
	_insert_user(db, 2, "other")
	_set_logged_in(client, 1, "owner")
	csrf_owner = _csrf_from_page(client)
	upload_id = _create_upload(client, csrf_owner)

	_set_logged_in(client, 2, "other")
	csrf_other = _csrf_from_page(client)
	resp = client.post(
		f"/api/uploads/{upload_id}/chunk",
		data=b"x" * 1024,
		headers={
			"Content-Type": "application/octet-stream",
			"X-Chunk-Index": "0",
			"X-CSRFToken": csrf_other,
		},
	)
	assert resp.status_code == 404


def test_upload_chunk_rejects_after_cancel(client, db):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	upload_id = _create_upload(client, csrf)

	cancel_resp = client.post(f"/api/uploads/{upload_id}/cancel", headers={"X-CSRFToken": csrf})
	assert cancel_resp.status_code == 200

	resp = client.post(
		f"/api/uploads/{upload_id}/chunk",
		data=b"x" * 1024,
		headers={
			"Content-Type": "application/octet-stream",
			"X-Chunk-Index": "0",
			"X-CSRFToken": csrf,
		},
	)
	assert resp.status_code == 409


def test_status_reflects_uploaded_chunks(client, db):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	upload_id = _create_upload(client, csrf)

	chunk_resp = client.post(
		f"/api/uploads/{upload_id}/chunk",
		data=b"z" * 1024,
		headers={
			"Content-Type": "application/octet-stream",
			"X-Chunk-Index": "0",
			"X-CSRFToken": csrf,
		},
	)
	assert chunk_resp.status_code == 200

	status_resp = client.get(f"/api/uploads/{upload_id}/status")
	assert status_resp.status_code == 200
	payload = status_resp.get_json()
	assert payload["uploaded_chunks"] == [0]
	assert 0 not in payload["missing_chunks"]
	assert payload["missing_chunks"] == [1, 2]


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
	upload_id = _create_upload(client, csrf, total_size=4, chunk_size=2, total_chunks=2)

	resp_chunk_missing_csrf = client.post(
		f"/api/uploads/{upload_id}/chunk",
		data=b"ab",
		headers={
			"Content-Type": "application/octet-stream",
			"X-Chunk-Index": "0",
		},
	)
	assert resp_chunk_missing_csrf.status_code in (400, 403)

	resp_ok = client.post(
		f"/api/uploads/{upload_id}/chunk",
		data=b"ab",
		headers={
			"Content-Type": "application/octet-stream",
			"X-Chunk-Index": "0",
			"X-CSRFToken": csrf,
		},
	)
	assert resp_ok.status_code == 200


def _upload_chunk(client, upload_id, csrf, chunk_index, chunk_data):
	return client.post(
		f"/api/uploads/{upload_id}/chunk",
		data=chunk_data,
		headers={
			"Content-Type": "application/octet-stream",
			"X-Chunk-Index": str(chunk_index),
			"X-Chunk-Size": str(len(chunk_data)),
			"X-CSRFToken": csrf,
		},
	)


def test_complete_happy_path(client, db, app):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	chunks = [b"a" * 4, b"bcde", b"fgh"]
	content = b"".join(chunks)
	upload_id = _create_upload(client, csrf, total_size=len(content), chunk_size=4, total_chunks=3)

	for idx, chunk in enumerate(chunks):
		resp = _upload_chunk(client, upload_id, csrf, idx, chunk)
		assert resp.status_code == 200

	complete_resp = client.post(f"/api/uploads/{upload_id}/complete", headers={"X-CSRFToken": csrf})
	assert complete_resp.status_code == 200
	payload = complete_resp.get_json()
	assert payload["ok"] is True
	assert payload["status"] == "completed"
	assert payload["duplicate"] is False

	upload_rows = db("SELECT status, sha256_final FROM uploads WHERE id = ?", (upload_id,))
	assert upload_rows[0]["status"] == "completed"
	assert upload_rows[0]["sha256_final"] == hashlib.sha256(content).hexdigest()

	tmp_upload_dir = Path(app.config["UPLOAD_TMP_DIR_ABS"]) / "1" / upload_id
	assert not tmp_upload_dir.exists()

	final_path = Path(app.root_path).parent / "storage" / "1" / "chunk.bin"
	assert final_path.exists()
	assert final_path.read_bytes() == content

	file_rows = db("SELECT id, filename, size, sha256 FROM user_files WHERE user_id = ?", (1,))
	assert len(file_rows) == 1
	assert str(file_rows[0]["id"]) == payload["file_id"]
	assert file_rows[0]["filename"] == "chunk.bin"
	assert file_rows[0]["size"] == len(content)
	assert file_rows[0]["sha256"] == payload["sha256"]


def test_complete_missing_chunks(client, db):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	upload_id = _create_upload(client, csrf, total_size=12, chunk_size=4, total_chunks=3)

	resp_chunk = _upload_chunk(client, upload_id, csrf, 0, b"aaaa")
	assert resp_chunk.status_code == 200

	complete_resp = client.post(f"/api/uploads/{upload_id}/complete", headers={"X-CSRFToken": csrf})
	assert complete_resp.status_code == 409
	payload = complete_resp.get_json()
	assert payload["ok"] is False
	assert payload["error"] == "missing_chunks"
	assert payload["missing_chunks"] == [1, 2]

	upload_rows = db("SELECT status FROM uploads WHERE id = ?", (upload_id,))
	assert upload_rows[0]["status"] in ("initiated", "uploading")


def test_complete_idempotency_double_call(client, db):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	chunks = [b"aaa", b"bbb", b"ccc"]
	content = b"".join(chunks)
	upload_id = _create_upload(client, csrf, total_size=len(content), chunk_size=3, total_chunks=3)

	for idx, chunk in enumerate(chunks):
		resp = _upload_chunk(client, upload_id, csrf, idx, chunk)
		assert resp.status_code == 200

	first_complete = client.post(f"/api/uploads/{upload_id}/complete", headers={"X-CSRFToken": csrf})
	assert first_complete.status_code == 200

	second_complete = client.post(f"/api/uploads/{upload_id}/complete", headers={"X-CSRFToken": csrf})
	assert second_complete.status_code == 409

	file_rows = db("SELECT id FROM user_files WHERE user_id = ? AND sha256 = ?", (1, hashlib.sha256(content).hexdigest()))
	assert len(file_rows) == 1


def test_complete_duplicate_detection(client, db, app):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	content = b"duplicate-content"
	sha256_content = hashlib.sha256(content).hexdigest()

	storage_dir = Path(app.root_path).parent / "storage" / "1"
	storage_dir.mkdir(parents=True, exist_ok=True)
	existing_path = storage_dir / "existing.bin"
	existing_path.write_bytes(content)

	db(
		"""
		INSERT INTO user_files (user_id, path, filename, size, sha256)
		VALUES (?, ?, ?, ?, ?)
		""",
		(1, "", "existing.bin", len(content), sha256_content),
	)
	existing_row = db("SELECT id FROM user_files WHERE user_id = ? AND sha256 = ?", (1, sha256_content))[0]

	upload_id = _create_upload(client, csrf, total_size=len(content), chunk_size=5, total_chunks=4)
	for idx, start in enumerate(range(0, len(content), 5)):
		chunk = content[start : start + 5]
		resp = _upload_chunk(client, upload_id, csrf, idx, chunk)
		assert resp.status_code == 200

	complete_resp = client.post(f"/api/uploads/{upload_id}/complete", headers={"X-CSRFToken": csrf})
	assert complete_resp.status_code == 200
	payload = complete_resp.get_json()
	assert payload["duplicate"] is True
	assert payload["file_id"] == str(existing_row["id"])
	assert payload["sha256"] == sha256_content

	final_path = Path(app.root_path).parent / "storage" / "1" / "chunk.bin"
	assert not final_path.exists()

	count_rows = db("SELECT COUNT(*) AS c FROM user_files WHERE user_id = ? AND sha256 = ?", (1, sha256_content))
	assert count_rows[0]["c"] == 1
