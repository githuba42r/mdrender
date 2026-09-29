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

    # Weak credentials are refused without creating an admin.
    bad = client.post("/setup", data={"username": "ab", "password": "short"})
    assert bad.status_code == 400
    assert b"3+" in bad.data

    # Creating the first admin signs them in.
    ok = client.post("/setup", data={"username": "phil", "password": "longenough1"})
    assert ok.status_code == 302 and ok.headers["Location"] == "/pushes"
    assert client.get("/pushes").status_code == 200

    # Setup is now permanently closed.
    assert client.post("/setup",
                       data={"username": "x", "password": "longenough1"}).status_code == 409


def test_login_is_username_and_password(config, db_path):
    app = _app_without_bootstrap(config)
    client = app.test_client()
    client.post("/setup", data={"username": "phil", "password": "longenough1"})

    fresh = app.test_client()
    assert fresh.post("/login", data={"username": "phil", "password": "nope"}).status_code == 401
    assert fresh.post("/login", data={"username": "ghost", "password": "longenough1"}).status_code == 401
    ok = fresh.post("/login", data={"username": "phil", "password": "longenough1"})
    assert ok.status_code == 302
    assert fresh.get("/pushes").status_code == 200


def test_add_admin_from_admin_ui(config, db_path):
    app = _app_without_bootstrap(config)
    client = app.test_client()
    assert client.get("/admins").status_code == 303  # session-gated
    client.post("/setup", data={"username": "phil", "password": "longenough1"})

    assert b"phil" in client.get("/admins").data
    client.post("/admins", data={"username": "ops", "password": "anotherpass1"})
    assert b"ops" in client.get("/admins").data

    # The new admin can sign in independently.
    fresh = app.test_client()
    assert fresh.post("/login",
                      data={"username": "ops", "password": "anotherpass1"}).status_code == 302


def test_admin_can_log_in_with_username_or_email(config, db_path):
    app = _app_without_bootstrap(config)
    client = app.test_client()
    client.post("/setup", data={"username": "phil", "password": "longenough1"})
    with app.config["_db"].connect() as conn:
        conn.execute("UPDATE admins SET email = 'phil@example.com' WHERE username = 'phil'")
        conn.commit()

    fresh = app.test_client()
    assert fresh.post("/login",
                      data={"username": "phil", "password": "longenough1"}).status_code == 302
    assert fresh.post("/login",
                      data={"username": "phil@example.com",
                            "password": "longenough1"}).status_code == 302


def test_bootstrap_admin_from_server_password(config, db_path):
    # conftest sets SERVER_PASSWORD="testpass"; a fresh app seeds admin/testpass.
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()
    assert client.post("/login",
                       data={"username": "admin", "password": "testpass"}).status_code == 302
