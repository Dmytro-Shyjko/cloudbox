def register_payload(**overrides):
	data = {
		"username": "csrf_user",
		"password": "password123",
		"email": "csrf_user@example.com",
		"first_name": "Csrf",
		"last_name": "User",
		"country": "Germany",
		"phone": "",
		"accept_terms": "on",
		"company": "",
	}
	data.update(overrides)
	return data


def test_post_without_csrf_is_rejected(client):
	resp = client.post("/register", data=register_payload(), follow_redirects=False)
	assert resp.status_code in (400, 403)


def test_post_with_csrf_succeeds(client, db, csrf_token):
	payload = register_payload(username="csrf_ok", email="csrf_ok@example.com")
	payload["csrf_token"] = csrf_token("/register")

	resp = client.post("/register", data=payload, follow_redirects=False)
	assert resp.status_code in (302, 303)

	rows = db("SELECT id FROM users WHERE username = ?", ("csrf_ok",))
	assert rows
