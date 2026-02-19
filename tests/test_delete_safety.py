from pathlib import Path


def _login(client, user_id=1, username="delete_user"):
	with client.session_transaction() as sess:
		sess["user_id"] = user_id
		sess["username"] = username


def test_files_page_has_no_delete_post_form(client):
	_login(client)
	resp = client.get("/files")
	assert resp.status_code == 200
	html = resp.get_data(as_text=True)
	assert 'action="/delete/' not in html
	assert "data-delete-file" in html


def test_delete_route_rejects_non_xhr(client, app, csrf_token):
	_login(client)
	storage = Path(app.root_path).parent / "storage" / "1"
	storage.mkdir(parents=True, exist_ok=True)
	target = storage / "plain-post.txt"
	target.write_text("keep", encoding="utf-8")

	resp = client.post(
		"/delete/plain-post.txt",
		json={"path": "", "filename": "plain-post.txt"},
		headers={"X-CSRFToken": csrf_token("/files")},
	)

	assert resp.status_code == 400
	assert resp.get_json()["error"] == "ajax_required"
	assert target.exists()


def test_delete_route_rejects_missing_or_invalid_json(client, app, csrf_token):
	_login(client)
	storage = Path(app.root_path).parent / "storage" / "1"
	storage.mkdir(parents=True, exist_ok=True)
	target = storage / "invalid-json.txt"
	target.write_text("keep", encoding="utf-8")

	missing_json = client.post(
		"/delete/invalid-json.txt",
		headers={
			"X-CSRFToken": csrf_token("/files"),
			"X-Requested-With": "XMLHttpRequest",
		},
	)
	assert missing_json.status_code == 400
	assert missing_json.get_json()["error"] == "json_required"
	assert target.exists()

	missing_fields = client.post(
		"/delete/invalid-json.txt",
		json={"path": ""},
		headers={
			"X-CSRFToken": csrf_token("/files"),
			"X-Requested-With": "XMLHttpRequest",
		},
	)
	assert missing_fields.status_code == 400
	assert missing_fields.get_json()["error"] == "missing_fields"
	assert target.exists()


def test_delete_route_rejects_filename_path_mismatch(client, app, csrf_token):
	_login(client)
	storage = Path(app.root_path).parent / "storage" / "1" / "docs"
	storage.mkdir(parents=True, exist_ok=True)
	target = storage / "safe.txt"
	target.write_text("keep", encoding="utf-8")

	resp = client.post(
		"/delete/docs/safe.txt",
		json={"path": "docs", "filename": "other.txt"},
		headers={
			"X-CSRFToken": csrf_token("/files"),
			"X-Requested-With": "XMLHttpRequest",
		},
	)

	assert resp.status_code == 400
	assert target.exists()


def test_delete_route_rejects_outside_user_storage(client, app, csrf_token):
	_login(client)
	resp = client.post(
		"/delete/../../etc/passwd",
		json={"path": "", "filename": "passwd"},
		headers={
			"X-CSRFToken": csrf_token("/files"),
			"X-Requested-With": "XMLHttpRequest",
		},
	)
	assert resp.status_code == 400


def test_delete_route_succeeds_only_with_valid_xhr_payload(client, app, csrf_token):
	_login(client)
	storage = Path(app.root_path).parent / "storage" / "1" / "docs"
	storage.mkdir(parents=True, exist_ok=True)
	target = storage / "remove-me.txt"
	target.write_text("delete", encoding="utf-8")

	resp = client.post(
		"/delete/docs/remove-me.txt",
		json={"path": "docs", "filename": "remove-me.txt"},
		headers={
			"X-CSRFToken": csrf_token("/files"),
			"X-Requested-With": "XMLHttpRequest",
			"X-Request-ID": "test-delete-ok",
		},
	)

	assert resp.status_code == 200
	payload = resp.get_json()
	assert payload["ok"] is True
	assert "/files" in payload["redirect"]
	assert not target.exists()


def test_chunked_upload_js_contains_no_delete_trigger():
	script = Path("app/static/chunked_upload.js").read_text(encoding="utf-8")
	assert "/delete" not in script


def test_files_delete_js_requires_trusted_click():
	script = Path("app/static/files_delete.js").read_text(encoding="utf-8")
	assert "event.isTrusted" in script


def test_upload_redirect_view_has_no_auto_delete_trigger(client):
	_login(client)
	resp = client.get("/files?uploaded=1")
	assert resp.status_code == 200
	html = resp.get_data(as_text=True)
	assert "action=\"/delete/" not in html
	assert "data-delete-file" in html
