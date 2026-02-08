def set_lang_uk(client):
    # у тебе є /lang/<lang>
    client.get("/lang/uk")


def register_payload(**overrides):
    data = {
        "username": "user1",
        "password": "password123",
        "email": "user1@example.com",
        "first_name": "Dmytro",
        "last_name": "Shyiko",
        "country": "Germany",
        "phone": "",
        "accept_terms": "on",
        "company": "",  # honeypot
    }
    data.update(overrides)
    return data


def user_exists(db, username: str) -> bool:
    rows = db("SELECT id FROM users WHERE username = ?", (username,))
    return len(rows) > 0


def email_exists(db, email: str) -> bool:
    rows = db("SELECT id FROM users WHERE email = ?", (email.strip().lower(),))
    return len(rows) > 0


def test_register_success_redirects_to_files_and_creates_user(client, db):
    set_lang_uk(client)

    resp = client.post("/register", data=register_payload(), follow_redirects=False)

    assert resp.status_code in (302, 303)
    assert resp.headers["Location"].endswith("/files")

    assert user_exists(db, "user1")
    assert email_exists(db, "user1@example.com")

    # перевіримо, що terms timestamp записався
    rows = db("SELECT accepted_terms_at FROM users WHERE username = ?", ("user1",))
    assert rows and rows[0]["accepted_terms_at"] is not None


def test_register_honeypot_blocks_and_does_not_create_user(client, db):
    set_lang_uk(client)

    resp = client.post(
        "/register",
        data=register_payload(username="bot1", email="bot1@example.com", company="I am a bot"),
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert not user_exists(db, "bot1")


def test_register_terms_required(client, db):
    set_lang_uk(client)

    resp = client.post(
        "/register",
        data=register_payload(username="u2", email="u2@example.com", accept_terms=""),
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert not user_exists(db, "u2")


def test_register_duplicate_username_fails(client, db):
    set_lang_uk(client)

    r1 = client.post("/register", data=register_payload(username="dup", email="dup1@example.com"), follow_redirects=False)
    assert r1.status_code in (302, 303)
    assert user_exists(db, "dup")

    r2 = client.post("/register", data=register_payload(username="dup", email="dup2@example.com"), follow_redirects=False)
    assert r2.status_code == 200

    rows = db("SELECT COUNT(*) AS c FROM users WHERE username = ?", ("dup",))
    assert int(rows[0]["c"]) == 1


def test_register_duplicate_email_fails(client, db):
    set_lang_uk(client)

    r1 = client.post("/register", data=register_payload(username="e11", email="same@example.com"), follow_redirects=False)
    assert r1.status_code in (302, 303)
    assert user_exists(db, "e11")

    r2 = client.post("/register", data=register_payload(username="e22", email="same@example.com"), follow_redirects=False)
    assert r2.status_code == 200
    assert not user_exists(db, "e22")


def test_register_rate_limit_blocks_after_many_fails(client, db):
    """
    10 fail спроб (без accept_terms) -> наступна нормальна реєстрація має бути заблокована.
    Порог відповідає models.is_register_blocked: max_fail_ip=10.
    """
    set_lang_uk(client)

    for i in range(10):
        resp = client.post(
            "/register",
            data=register_payload(username=f"bad{i}", email=f"bad{i}@example.com", accept_terms=""),
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert not user_exists(db, f"bad{i}")

    resp2 = client.post(
        "/register",
        data=register_payload(username="good_after", email="good_after@example.com"),
        follow_redirects=False,
    )

    assert resp2.status_code == 200
    body = resp2.get_data(as_text=True).lower()

    assert (
        "забагато" in body
        or "too many" in body
        or "zu viele" in body
    )
    assert not user_exists(db, "good_after")
