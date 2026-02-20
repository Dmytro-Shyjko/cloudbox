import os
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


def _create_upload(client, csrf, total_size=3072, chunk_size=1024, total_chunks=3, filename="chunk.bin", target_path="", overwrite=False):
	resp = client.post(
		"/api/uploads/init",
		json={
			"filename": filename,
			"total_size": total_size,
			"chunk_size": chunk_size,
			"total_chunks": total_chunks,
			"target_path": target_path,
			"overwrite": overwrite,
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
	assert payload["upload_id"] == "up-status"
	assert payload["status"] == "initiated"
	assert payload["total_chunks"] == 3
	assert payload["expected_total_size"] == 13
	assert payload["chunk_size"] == 5
	assert payload["uploaded_chunks"] == []
	assert payload["missing_chunks"] == [0, 1, 2]


def test_exists_returns_false_for_missing_file(client, db):
	_insert_user(db)
	_set_logged_in(client)

	resp = client.get("/api/uploads/exists?path=&filename=missing.txt")
	assert resp.status_code == 200
	payload = resp.get_json()
	assert payload == {"exists": False, "file": None}


def test_exists_returns_true_with_metadata_for_existing_file(client, db):
	_insert_user(db)
	_set_logged_in(client)
	db(
		"""
		INSERT INTO user_files (user_id, path, filename, size, sha256, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
		""",
		(1, "docs", "report.pdf", 123, "a" * 64),
	)

	resp = client.get("/api/uploads/exists?path=/docs&filename=report.pdf")
	assert resp.status_code == 200
	payload = resp.get_json()
	assert payload["exists"] is True
	assert payload["file"]["filename"] == "report.pdf"
	assert payload["file"]["path"] == "docs"
	assert payload["file"]["size"] == 123
	assert payload["file"]["sha256"] == "a" * 64


def test_init_returns_409_when_existing_file_and_overwrite_false(client, db):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	db(
		"""
		INSERT INTO user_files (user_id, path, filename, size, sha256)
		VALUES (?, ?, ?, ?, ?)
		""",
		(1, "docs", "chunk.bin", 11, "b" * 64),
	)

	resp = client.post(
		"/api/uploads/init",
		json={
			"filename": "chunk.bin",
			"total_size": 10,
			"chunk_size": 5,
			"total_chunks": 2,
			"target_path": "docs",
			"overwrite": False,
		},
		headers={"X-CSRFToken": csrf},
	)
	assert resp.status_code == 409
	payload = resp.get_json()
	assert payload["error"] == "file_exists"
	assert payload["filename"] == "chunk.bin"
	assert payload["path"] == "docs"


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


def test_cancel_deletes_chunks_and_db_rows(client, db, app):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	upload_id = _create_upload(client, csrf, total_size=6, chunk_size=3, total_chunks=2)

	first_chunk = b"abc"
	second_chunk = b"def"
	for chunk_index, chunk_data in ((0, first_chunk), (1, second_chunk)):
		chunk_resp = client.post(
			f"/api/uploads/{upload_id}/chunk",
			data=chunk_data,
			headers={
				"Content-Type": "application/octet-stream",
				"X-Chunk-Index": str(chunk_index),
				"X-Chunk-Size": str(len(chunk_data)),
				"X-CSRFToken": csrf,
			},
		)
		assert chunk_resp.status_code == 200

	tmp_dir = Path(app.config["UPLOAD_TMP_DIR_ABS"]) / "1" / upload_id
	assert (tmp_dir / "chunks" / "0.part").exists()
	assert (tmp_dir / "chunks" / "1.part").exists()
	chunk_rows_before = db("SELECT COUNT(*) AS c FROM upload_chunks WHERE upload_id = ?", (upload_id,))
	assert chunk_rows_before[0]["c"] == 2

	cancel_resp = client.post(f"/api/uploads/{upload_id}/cancel", headers={"X-CSRFToken": csrf})
	assert cancel_resp.status_code == 200
	assert cancel_resp.get_json() == {"ok": True, "status": "canceled"}

	chunk_rows_after = db("SELECT COUNT(*) AS c FROM upload_chunks WHERE upload_id = ?", (upload_id,))
	assert chunk_rows_after[0]["c"] == 0
	upload_rows = db("SELECT status FROM uploads WHERE id = ?", (upload_id,))
	assert upload_rows[0]["status"] == "canceled"
	assert not tmp_dir.exists()



def test_status_canceled_is_terminal(client, db):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	upload_id = _create_upload(client, csrf, total_size=8, chunk_size=4, total_chunks=2)

	chunk_resp = client.post(
		f"/api/uploads/{upload_id}/chunk",
		data=b"abcd",
		headers={
			"Content-Type": "application/octet-stream",
			"X-Chunk-Index": "0",
			"X-Chunk-Size": "4",
			"X-CSRFToken": csrf,
		},
	)
	assert chunk_resp.status_code == 200

	cancel_resp = client.post(f"/api/uploads/{upload_id}/cancel", headers={"X-CSRFToken": csrf})
	assert cancel_resp.status_code == 200

	status_resp = client.get(f"/api/uploads/{upload_id}/status")
	assert status_resp.status_code == 200
	payload = status_resp.get_json()
	assert payload["status"] == "canceled"
	assert payload["uploaded_chunks"] == []
	assert payload["missing_chunks"] == [0, 1]
	assert payload["total_chunks"] == 2
	assert payload["expected_total_size"] == 8
	assert payload["chunk_size"] == 4


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


def _prepare_upload_for_complete(client, db):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	chunks = [b"aaaa", b"bbbb"]
	upload_id = _create_upload(client, csrf, total_size=8, chunk_size=4, total_chunks=2)
	for idx, chunk in enumerate(chunks):
		resp = _upload_chunk(client, upload_id, csrf, idx, chunk)
		assert resp.status_code == 200
	return upload_id, csrf, b"".join(chunks)


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
	final_path = storage_dir / "chunk.bin"
	final_path.unlink(missing_ok=True)
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

	assert not final_path.exists()

	count_rows = db("SELECT COUNT(*) AS c FROM user_files WHERE user_id = ? AND sha256 = ?", (1, sha256_content))
	assert count_rows[0]["c"] == 1
	assert existing_path.exists()
	assert existing_path.read_bytes() == content


def test_complete_duplicate_does_not_delete_existing_same_target_file(client, db, app):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	content = b"same-target-duplicate"
	sha256_content = hashlib.sha256(content).hexdigest()

	storage_dir = Path(app.root_path).parent / "storage" / "1"
	storage_dir.mkdir(parents=True, exist_ok=True)
	existing_path = storage_dir / "chunk.bin"
	existing_path.write_bytes(content)

	db(
		"""
		INSERT INTO user_files (user_id, path, filename, size, sha256)
		VALUES (?, ?, ?, ?, ?)
		""",
		(1, "", "chunk.bin", len(content), sha256_content),
	)
	existing_row = db("SELECT id FROM user_files WHERE user_id = ? AND sha256 = ?", (1, sha256_content))[0]

	upload_id = _create_upload(client, csrf, total_size=len(content), chunk_size=5, total_chunks=5, overwrite=True)
	for idx, start in enumerate(range(0, len(content), 5)):
		chunk = content[start : start + 5]
		resp = _upload_chunk(client, upload_id, csrf, idx, chunk)
		assert resp.status_code == 200

	complete_resp = client.post(f"/api/uploads/{upload_id}/complete", headers={"X-CSRFToken": csrf})
	assert complete_resp.status_code == 200
	payload = complete_resp.get_json()
	assert payload["duplicate"] is False

	assert existing_path.exists()
	assert existing_path.read_bytes() == content

	count_rows = db("SELECT COUNT(*) AS c FROM user_files WHERE user_id = ? AND sha256 = ?", (1, sha256_content))
	assert count_rows[0]["c"] == 1


def test_delete_then_reupload_same_file_persists(client, db, app):
	upload_id, csrf, content = _prepare_upload_for_complete(client, db)
	first_complete = client.post(f"/api/uploads/{upload_id}/complete", headers={"X-CSRFToken": csrf})
	assert first_complete.status_code == 200

	delete_resp = client.post(
		"/delete/chunk.bin",
		json={"path": "", "filename": "chunk.bin"},
		headers={
			"X-CSRFToken": csrf,
			"X-Requested-With": "XMLHttpRequest",
		},
	)
	assert delete_resp.status_code == 200
	assert delete_resp.get_json()["ok"] is True

	rows_after_delete = db(
		"SELECT COUNT(*) AS c FROM user_files WHERE user_id = ? AND path = ? AND filename = ?",
		(1, "", "chunk.bin"),
	)
	assert rows_after_delete[0]["c"] == 0

	second_upload_id = _create_upload(client, csrf, total_size=len(content), chunk_size=4, total_chunks=2)
	for idx, chunk in enumerate((content[:4], content[4:])):
		resp = _upload_chunk(client, second_upload_id, csrf, idx, chunk)
		assert resp.status_code == 200

	second_complete = client.post(f"/api/uploads/{second_upload_id}/complete", headers={"X-CSRFToken": csrf})
	assert second_complete.status_code == 200
	payload = second_complete.get_json()
	assert payload["duplicate"] is False

	final_path = Path(app.root_path).parent / "storage" / "1" / "chunk.bin"
	assert final_path.exists()
	assert final_path.read_bytes() == content


def test_complete_ignores_stale_duplicate_db_row_when_file_missing(client, db, app):
	upload_id, csrf, content = _prepare_upload_for_complete(client, db)
	sha256_content = hashlib.sha256(content).hexdigest()

	db(
		"""
		INSERT INTO user_files (user_id, path, filename, size, sha256)
		VALUES (?, ?, ?, ?, ?)
		""",
		(1, "", "ghost.bin", len(content), sha256_content),
	)

	complete_resp = client.post(f"/api/uploads/{upload_id}/complete", headers={"X-CSRFToken": csrf})
	assert complete_resp.status_code == 200
	payload = complete_resp.get_json()
	assert payload["duplicate"] is False

	final_path = Path(app.root_path).parent / "storage" / "1" / "chunk.bin"
	assert final_path.exists()
	assert final_path.read_bytes() == content


def test_complete_must_not_return_ok_if_replace_fails(client, db, monkeypatch):
	upload_id, csrf, _ = _prepare_upload_for_complete(client, db)

	def _raise_replace(_src, _dst):
		raise OSError("replace failed")

	monkeypatch.setattr("app.api.uploads.os.replace", _raise_replace)
	resp = client.post(f"/api/uploads/{upload_id}/complete", headers={"X-CSRFToken": csrf})
	assert resp.status_code == 500
	payload = resp.get_json()
	assert payload["error"] == "failed to assemble upload"

	rows = db("SELECT status FROM uploads WHERE id = ?", (upload_id,))
	assert rows[0]["status"] == "failed"


def test_complete_must_not_return_ok_if_final_missing(client, db, monkeypatch):
	upload_id, csrf, _ = _prepare_upload_for_complete(client, db)
	original_exists = os.path.exists

	def _fake_exists(path):
		if str(path).endswith("/storage/1/chunk.bin"):
			return False
		return original_exists(path)

	monkeypatch.setattr("app.api.uploads.os.path.exists", _fake_exists)
	resp = client.post(f"/api/uploads/{upload_id}/complete", headers={"X-CSRFToken": csrf})
	assert resp.status_code == 500
	payload = resp.get_json()
	assert payload["error"] == "failed to assemble upload"

	rows = db("SELECT status FROM uploads WHERE id = ?", (upload_id,))
	assert rows[0]["status"] == "failed"


def test_cleanup_guard_prevents_deleting_outside_tmp(client, db, app, monkeypatch):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	upload_id = "outside-cleanup"

	db(
		"""
		INSERT INTO uploads (
			id, user_id, target_path, filename_original, filename_final,
			total_size, chunk_size, total_chunks, status,
			created_at, updated_at, expires_at, last_activity_at
		)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
		""",
		(upload_id, 1, "", "a.bin", "a.bin", 2, 1, 2, "initiated"),
	)

	outside = Path(app.root_path).parent / "outside-cleanup"
	outside.mkdir(parents=True, exist_ok=True)
	marker = outside / "marker.txt"
	marker.write_text("keep", encoding="utf-8")

	from app.api import uploads as uploads_api
	original_resolve = uploads_api.resolve_chunk_upload_dir

	def _fake_resolve_chunk_upload_dir(user_id, upload_id_arg):
		if upload_id_arg == upload_id:
			return outside / "chunks"
		return original_resolve(user_id, upload_id_arg)

	monkeypatch.setattr("app.api.uploads.resolve_chunk_upload_dir", _fake_resolve_chunk_upload_dir)
	resp = client.post(f"/api/uploads/{upload_id}/cancel", headers={"X-CSRFToken": csrf})
	assert resp.status_code == 200
	assert marker.exists()


def test_complete_happy_path_ok_implies_file_exists_and_size_matches(client, db, app):
	upload_id, csrf, content = _prepare_upload_for_complete(client, db)
	resp = client.post(f"/api/uploads/{upload_id}/complete", headers={"X-CSRFToken": csrf})
	assert resp.status_code == 200
	payload = resp.get_json()
	assert payload["ok"] is True

	final_path = Path(app.root_path).parent / "storage" / "1" / "chunk.bin"
	assert final_path.exists()
	assert final_path.is_file()
	assert final_path.stat().st_size == len(content)


def test_overwrite_flow_replaces_existing_file_after_complete(client, db, app):
	_insert_user(db)
	_set_logged_in(client)
	csrf = _csrf_from_page(client)
	filename = "same.bin"
	target_path = "folder-x"
	storage_dir = Path(app.root_path).parent / "storage" / "1" / target_path
	storage_dir.mkdir(parents=True, exist_ok=True)

	content_a = b"AAAA"
	upload_id_a = _create_upload(
		client,
		csrf,
		total_size=len(content_a),
		chunk_size=len(content_a),
		total_chunks=1,
		filename=filename,
		target_path=target_path,
	)
	resp_chunk_a = _upload_chunk(client, upload_id_a, csrf, 0, content_a)
	assert resp_chunk_a.status_code == 200
	complete_a = client.post(f"/api/uploads/{upload_id_a}/complete", headers={"X-CSRFToken": csrf})
	assert complete_a.status_code == 200

	content_b = b"BBBB-new"
	upload_id_b = _create_upload(
		client,
		csrf,
		total_size=len(content_b),
		chunk_size=4,
		total_chunks=2,
		filename=filename,
		target_path=target_path,
		overwrite=True,
	)
	resp_chunk_b0 = _upload_chunk(client, upload_id_b, csrf, 0, content_b[:4])
	resp_chunk_b1 = _upload_chunk(client, upload_id_b, csrf, 1, content_b[4:])
	assert resp_chunk_b0.status_code == 200
	assert resp_chunk_b1.status_code == 200
	complete_b = client.post(f"/api/uploads/{upload_id_b}/complete", headers={"X-CSRFToken": csrf})
	assert complete_b.status_code == 200

	rows = db(
		"SELECT id, size, sha256 FROM user_files WHERE user_id = ? AND path = ? AND filename = ?",
		(1, target_path, filename),
	)
	assert len(rows) == 1
	assert rows[0]["size"] == len(content_b)
	assert rows[0]["sha256"] == hashlib.sha256(content_b).hexdigest()

	final_path = storage_dir / filename
	assert final_path.exists()
	assert final_path.read_bytes() == content_b
