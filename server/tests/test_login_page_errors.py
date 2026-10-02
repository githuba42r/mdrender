# server/tests/test_login_page_errors.py
"""A failed admin sign-in re-renders the form instead of returning JSON."""
import os

from server.app import accounts
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def test_bad_password_rerenders_the_form(config, db_path):
    client = _app(config).test_client()

    resp = client.post("/admin-login", data={"username": "admin", "password": "nope",
                                       "next": "/pair"})
    assert resp.status_code == 401
    assert resp.content_type.startswith("text/html")
    body = resp.data
    assert b"Invalid username or password." in body
    assert b"invalid_credentials" not in body   # no raw JSON for a browser
    assert b'name="username"' in body and b'value="admin"' in body
    assert b'name="next"' in body and b'value="/pair"' in body
    # The password field is empty again and the page can be resubmitted.
    assert client.post("/admin-login", data={"username": "admin",
                                       "password": "testpass"}).status_code == 302
    assert client.get("/pushes").status_code == 200


def test_lockout_says_so_after_max_attempts(config, db_path):
    client = _app(config).test_client()
    attempts = config.LOGIN_MAX_ATTEMPTS
    for _ in range(attempts - 1):
        resp = client.post("/admin-login", data={"username": "admin", "password": "nope"})
        assert b"Invalid username or password." in resp.data
    locked = client.post("/admin-login", data={"username": "admin", "password": "nope"})
    assert locked.status_code == 401
    assert b"Too many attempts." in locked.data


def test_account_email_on_the_admin_form_points_at_the_account_page(config, db_path):
    app = _app(config)
    client = app.test_client()
    with app.config["_db"].connect() as conn:
        accounts.create_account(conn, "user@example.com", "letmein99")

    # The user sign-in offers the administrator page up front ...
    assert b'href="/admin-login"' in client.get("/login").data

    # ... and an account email typed into it is redirected there, not called
    # an invalid credential (which is what kept sending the user in circles).
    resp = client.post("/admin-login", data={"username": "user@example.com",
                                       "password": "wrong-password"})
    assert resp.status_code == 401
    assert b"Invalid username or password." not in resp.data
    assert b"sign in on the user login" in resp.data
    assert b'href="/login"' in resp.data

    # The account itself signs in normally on /login (and its /account/login
    # alias keeps working for old links).
    ok = client.post("/login", data={"email": "user@example.com",
                                     "password": "letmein99"})
    assert ok.status_code == 302 and ok.headers["Location"].endswith("/account")
    alias = client.post("/account/login", data={"email": "user@example.com",
                                                "password": "letmein99"})
    assert alias.status_code == 302
