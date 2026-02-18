import io


def test_upload_over_max_content_length_returns_413(app, client, csrf_token):
	app.config["MAX_CONTENT_LENGTH"] = 1024

	with client.session_transaction() as sess:
		sess["user_id"] = 999
		sess["username"] = "limit_user"

	resp = client.post(
		"/files",
		data={
			"action": "upload",
			"csrf_token": csrf_token("/files"),
			"file": (io.BytesIO(b"a" * 2048), "big.txt"),
		},
		content_type="multipart/form-data",
	)

	assert resp.status_code == 413
	body = resp.get_data(as_text=True)
	assert "Upload too large" in body or "Request Entity Too Large" in body
