from werkzeug.security import generate_password_hash


def _insert_user(db, username: str, role: str):
	return db(
		"""
		INSERT INTO users (username, password_hash, role, is_premium, is_active)
		VALUES (?, ?, ?, 0, 1)
		RETURNING id
		""",
		(username, generate_password_hash("password123"), role),
	)[0]["id"]


def _login_as(client, user_id: int, username: str, role: str):
	with client.session_transaction() as sess:
		sess["user_id"] = int(user_id)
		sess["username"] = username
		sess["role"] = role
		sess["is_premium"] = 0


def _extract_csrf_token(html: str) -> str:
	marker = 'name="csrf_token" value="'
	start = html.find(marker)
	assert start != -1
	start += len(marker)
	end = html.find('"', start)
	assert end != -1
	return html[start:end]


def test_moderator_sees_admin_link_and_can_open_users_page(client, db):
	moderator_id = _insert_user(db, "mod_user", "moderator")
	_insert_user(db, "regular_user", "user")
	_login_as(client, moderator_id, "mod_user", "moderator")

	files_resp = client.get("/files")
	assert files_resp.status_code == 200
	assert 'href="/admin/users"' in files_resp.get_data(as_text=True)

	admin_users_resp = client.get("/admin/users")
	assert admin_users_resp.status_code == 200


def test_moderator_cannot_change_roles_or_delete_users(client, db):
	moderator_id = _insert_user(db, "mod_only", "moderator")
	target_id = _insert_user(db, "target_user", "user")
	_login_as(client, moderator_id, "mod_only", "moderator")

	detail_resp = client.get(f"/admin/users/{target_id}")
	assert detail_resp.status_code == 200
	csrf_token = _extract_csrf_token(detail_resp.get_data(as_text=True))

	role_resp = client.post(
		f"/admin/users/{target_id}/role",
		data={"role": "admin", "csrf_token": csrf_token},
	)
	assert role_resp.status_code == 403

	delete_resp = client.post(
		f"/admin/users/{target_id}/delete",
		data={"csrf_token": csrf_token},
	)
	assert delete_resp.status_code == 403

	target_role = db("SELECT role FROM users WHERE id = ?", (target_id,))[0]["role"]
	assert target_role == "user"


def test_admin_can_change_role_and_delete_user(client, db):
	admin_id = _insert_user(db, "admin_user", "admin")
	target_id = _insert_user(db, "user_for_admin_actions", "user")
	_login_as(client, admin_id, "admin_user", "admin")

	detail_resp = client.get(f"/admin/users/{target_id}")
	assert detail_resp.status_code == 200
	csrf_token = _extract_csrf_token(detail_resp.get_data(as_text=True))

	role_resp = client.post(
		f"/admin/users/{target_id}/role",
		data={"role": "moderator", "csrf_token": csrf_token},
		follow_redirects=False,
	)
	assert role_resp.status_code in (302, 303)
	updated_role = db("SELECT role FROM users WHERE id = ?", (target_id,))[0]["role"]
	assert updated_role == "moderator"

	delete_resp = client.post(
		f"/admin/users/{target_id}/delete",
		data={"csrf_token": csrf_token},
		follow_redirects=False,
	)
	assert delete_resp.status_code in (302, 303)
	remaining = db("SELECT id FROM users WHERE id = ?", (target_id,))
	assert not remaining
