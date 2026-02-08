def set_lang_uk(client):
    client.get("/lang/uk")


def login_payload(username="u1", password="secret123", company=""):
    return {"username": username, "password": password, "company": company}


def test_login_success_redirects_to_files(client, db):
    set_lang_uk(client)

    r = client.post(
        "/register",
        data={
            "username": "login_ok",
            "password": "secret123",
            "email": "login_ok@example.com",
            "first_name": "Test",
            "last_name": "User",
            "country": "Germany",
            "accept_terms": "on",
            "company": "",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    client.get("/logout")

    resp = client.post("/login", data=login_payload("login_ok", "secret123"), follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert resp.headers["Location"].endswith("/files")


def test_login_wrong_password_shows_error_and_no_session(client, db):
    set_lang_uk(client)

    r = client.post(
        "/register",
        data={
            "username": "login_bad",
            "password": "secret123",
            "email": "login_bad@example.com",
            "first_name": "Test",
            "last_name": "User",
            "country": "Germany",
            "accept_terms": "on",
            "company": "",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    client.get("/logout")

    resp = client.post("/login", data=login_payload("login_bad", "WRONGPASS"), follow_redirects=False)
    assert resp.status_code == 200

    html = resp.get_data(as_text=True).lower()
    assert ("невір" in html) or ("парол" in html) or ("credentials" in html)

    # не залогінило: доступ до /files має редіректити на /login
    resp2 = client.get("/files", follow_redirects=False)
    assert resp2.status_code in (302, 303)
    assert "/login" in resp2.headers["Location"]


def test_login_honeypot_blocks(client, db):
    set_lang_uk(client)

    r = client.post(
        "/register",
        data={
            "username": "login_bot",
            "password": "secret123",
            "email": "login_bot@example.com",
            "first_name": "Test",
            "last_name": "User",
            "country": "Germany",
            "accept_terms": "on",
            "company": "",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    client.get("/logout")

    resp = client.post(
        "/login",
        data=login_payload("login_bot", "secret123", company="I am a bot"),
        follow_redirects=False,
    )
    assert resp.status_code == 200

    html = resp.get_data(as_text=True).lower()
    assert ("запит" in html) or ("request" in html) or ("некорект" in html) or ("bad request" in html)

    resp2 = client.get("/files", follow_redirects=False)
    assert resp2.status_code in (302, 303)
    assert "/login" in resp2.headers["Location"]


def test_login_rate_limit_blocks_after_many_fails(client, db):
    """
    Порог відповідає auth.py: max_fail_user=10 у вікні 15 хв.
    Після 10 невдалих — наступна спроба має бути заблокована (200 + "too many").
    """
    set_lang_uk(client)

    r = client.post(
        "/register",
        data={
            "username": "login_rl",
            "password": "secret123",
            "email": "login_rl@example.com",
            "first_name": "Test",
            "last_name": "User",
            "country": "Germany",
            "accept_terms": "on",
            "company": "",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    client.get("/logout")

    for _ in range(10):
        resp = client.post("/login", data=login_payload("login_rl", "WRONGPASS"), follow_redirects=False)
        assert resp.status_code == 200

    resp2 = client.post("/login", data=login_payload("login_rl", "secret123"), follow_redirects=False)
    assert resp2.status_code == 200

    html = resp2.get_data(as_text=True).lower()
    assert ("забагато" in html) or ("too many" in html) or ("zu viele" in html) or ("спроб" in html)
