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
	assert 'action="/delete' not in html
	assert "data-delete-file" in html


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



def test_chunked_upload_js_contains_no_delete_trigger():
	script = Path("app/static/chunked_upload.js").read_text(encoding="utf-8")
	assert "/delete" not in script
