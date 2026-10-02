# server/tests/test_setup.py
"""First-run admin setup and username/password login (Phase A)."""
import os

from server.app.app import create_app


def _app_without_bootstrap(config):
    # No SERVER_PASSWORD => no bootstrap admin => the setup flow is live.
    config.SERVER_PASSWORD = ""
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _setup(client, username="phil", password="longenough1", email=None,
           confirm=None):
    """POST /setup with the full first-admin field set: username, email,
    password and the confirmation that has to match it."""
    return client.post("/setup", data={
        "username": username,
        "email": email if email is not None else f"{username}@example.com",
        "password": password,
        "password_confirm": password if confirm is None else confirm,
    })


def test_first_run_requires_setup_then_closes(config, db_path):
    app = _app_without_bootstrap(config)
    client = app.test_client()

    # Anonymous landing and /login both divert to setup while no admin exists.
    root = client.get("/")
    assert root.status_code == 302 and root.headers["Location"] == "/setup"
    assert client.get("/login").headers["Location"] == "/setup"

    page = client.get("/setup")
    assert page.status_code == 200
    assert b"Create admin" in page.data
    assert b'name="username"' in page.data
    assert b'name="email"' in page.data
    assert b'name="password_confirm"' in page.data

    # Weak credentials are refused without creating an admin.
    bad = _setup(client, username="ab", password="short")
    assert bad.status_code == 400
    assert b"3+" in bad.data

    # Creating the first admin signs them in.
    ok = _setup(client)
    assert ok.status_code == 302 and ok.headers["Location"] == "/pushes"
    assert client.get("/pushes").status_code == 200

    # Setup is now permanently closed.
    assert _setup(client, username="x").status_code == 409


def test_setup_requires_a_matching_confirm_password(config, db_path):
    app = _app_without_bootstrap(config)
    client = app.test_client()

    resp = _setup(client, confirm="somethingelse1")
    assert resp.status_code == 400
    assert b"do not match" in resp.data
    # No admin was created, so setup is still open and still validating.
    assert b'name="username"' in client.get("/setup").data


def test_setup_requires_a_real_email(config, db_path):
    app = _app_without_bootstrap(config)
    client = app.test_client()

    for email in ("", "not-an-email", "user@nodot", "user@"):
        resp = _setup(client, email=email)
        assert resp.status_code == 400, email
        assert b"valid email" in resp.data, email
    assert _setup(client).status_code == 302  # the valid one still works


def test_first_login_on_a_fresh_server_is_account_creation(config, db_path):
    """No admins yet: every entry to the admin login diverts to /setup."""
    app = _app_without_bootstrap(config)
    client = app.test_client()

    for path in ("/", "/login", "/admin-login"):
        resp = client.get(path)
        assert resp.status_code == 302 and resp.headers["Location"] == "/setup", path
    # Even a posted sign-in form (autofill) cannot fail against an account
    # that cannot exist yet.
    posted = client.post("/admin-login",
                         data={"username": "admin", "password": "admin"})
    assert posted.status_code == 302 and posted.headers["Location"] == "/setup"

    # /setup is where the username and password are chosen, and completing it
    # signs the operator straight in.
    ok = _setup(client, username="admin", password="longenough1")
    assert ok.status_code == 302
    assert client.get("/pushes").status_code == 200
    # ... and only now does the login form exist.
    assert client.get("/admin-login").status_code == 200


def test_setup_lands_an_unconnected_slave_on_the_connect_screen(config):
    """Step 1 -> 2 -> 3: first admin created, straight to /federation."""
    config.ROLE = "slave"
    config.MASTER_URL = "https://master.example"
    app = _app_without_bootstrap(config)
    client = app.test_client()

    ok = _setup(client, username="phil", password="longenough1")
    assert ok.status_code == 302 and ok.headers["Location"] == "/federation"
    page = client.get("/federation")
    assert page.status_code == 200
    assert b' action="/federation/connect"' in page.data


def test_setup_lands_a_slave_without_a_master_on_the_push_list(config):
    config.ROLE = "slave"
    config.MASTER_URL = ""
    app = _app_without_bootstrap(config)
    client = app.test_client()
    ok = _setup(client, username="phil", password="longenough1")
    assert ok.headers["Location"] == "/pushes"


def test_setup_lands_a_master_on_the_push_list(config):
    config.ROLE = "master"
    config.MASTER_URL = ""
    app = _app_without_bootstrap(config)
    client = app.test_client()
    ok = _setup(client, username="phil", password="longenough1")
    assert ok.headers["Location"] == "/pushes"


def test_login_is_username_and_password(config, db_path):
    app = _app_without_bootstrap(config)
    client = app.test_client()
    _setup(client, username="phil", password="longenough1")

    fresh = app.test_client()
    assert fresh.post("/admin-login", data={"username": "phil", "password": "nope"}).status_code == 401
    assert fresh.post("/admin-login", data={"username": "ghost", "password": "longenough1"}).status_code == 401
    ok = fresh.post("/admin-login", data={"username": "phil", "password": "longenough1"})
    assert ok.status_code == 302
    assert fresh.get("/pushes").status_code == 200


def test_add_admin_from_admin_ui(config, db_path):
    app = _app_without_bootstrap(config)
    client = app.test_client()
    assert client.get("/admins").status_code == 303  # session-gated
    _setup(client, username="phil", password="longenough1")

    assert b"phil" in client.get("/admins").data
    client.post("/admins", data={"username": "ops", "password": "anotherpass1"})
    assert b"ops" in client.get("/admins").data

    # The new admin can sign in independently.
    fresh = app.test_client()
    assert fresh.post("/admin-login",
                      data={"username": "ops", "password": "anotherpass1"}).status_code == 302


def test_admin_can_log_in_with_username_or_email(config, db_path):
    app = _app_without_bootstrap(config)
    client = app.test_client()
    _setup(client, username="phil", password="longenough1",
           email="phil@example.com")
    # The address asked for during setup is the one login accepts.
    with app.config["_db"].connect() as conn:
        row = conn.execute("SELECT email FROM admins WHERE username = 'phil'").fetchone()
    assert row["email"] == "phil@example.com"

    fresh = app.test_client()
    assert fresh.post("/admin-login",
                      data={"username": "phil", "password": "longenough1"}).status_code == 302
    assert fresh.post("/admin-login",
                      data={"username": "phil@example.com",
                            "password": "longenough1"}).status_code == 302


def test_admin_name_email_and_password_can_be_edited(config, db_path):
    from server.app.identity import get_admin_by_username

    app = _app_without_bootstrap(config)
    c = app.test_client()
    _setup(c, username="phil", password="longenough1")
    c.post("/admins", data={"name": "Ops", "username": "ops",
                            "email": "ops@example.com", "password": "anotherpass1"})

    body = c.get("/admins").data
    assert b"Ops" in body and b"ops@example.com" in body

    with app.config["_db"].connect() as conn:
        admin_id = get_admin_by_username(conn, "ops")["admin_id"]
    c.post(f"/admins/{admin_id}/update",
           data={"name": "Operations", "email": "new@example.com",
                 "password": "brandnewpass1"})

    body = c.get("/admins").data
    assert b"Operations" in body and b"new@example.com" in body

    fresh = app.test_client()
    assert fresh.post("/admin-login",
                      data={"username": "ops", "password": "brandnewpass1"}).status_code == 302
    assert fresh.post("/admin-login",
                      data={"username": "ops", "password": "anotherpass1"}).status_code == 401


def test_bootstrap_admin_from_server_password(config, db_path):
    # conftest sets SERVER_PASSWORD="testpass"; a fresh app seeds admin/testpass.
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()
    assert client.post("/admin-login",
                       data={"username": "admin", "password": "testpass"}).status_code == 302
