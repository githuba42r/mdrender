# server/tests/test_accounts_admin.py
"""Admin user (account) management: list/add/edit/ban/delete (design §9)."""
import os

from server.app import accounts
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _login(app):
    c = app.test_client()
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    return c


def test_accounts_admin_crud(config, db_path):
    app = _app(config)
    c = _login(app)

    assert c.get("/accounts").status_code == 200
    c.post("/accounts", data={"email": "user@example.com", "name": "Jane",
                              "password": "longenough1"})
    body = c.get("/accounts").data
    assert b"user@example.com" in body and b"Jane" in body

    with app.config["_db"].connect() as conn:
        account_id = accounts.get_account_by_email(conn, "user@example.com")["account_id"]
        conn.execute(
            "INSERT INTO devices (device_secret, device_auth, device_name, fcm_token,"
            " public_key, push_key, registered_at, last_seen, account_id)"
            " VALUES ('d1','a','Dev','f','P','K',1,1,?)", (account_id,))
        conn.commit()
        assert accounts.device_count(conn, account_id) == 1

    # Edit email/name/password.
    c.post(f"/accounts/{account_id}/update",
           data={"email": "new@example.com", "name": "Janet",
                 "password": "brandnewpass1"})
    body = c.get("/accounts").data
    assert b"new@example.com" in body and b"Janet" in body

    fresh = app.test_client()
    assert fresh.post("/account/login",
                      data={"email": "new@example.com",
                            "password": "brandnewpass1"}).status_code == 302

    # Deactivate (block) then ban.
    c.post(f"/accounts/{account_id}/status", data={"status": "blocked"})
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["status"] == "blocked"
    c.post(f"/accounts/{account_id}/status", data={"status": "banned"})
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["status"] == "banned"

    # Delete removes the account.
    c.post(f"/accounts/{account_id}/delete")
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id) is None


def test_accounts_page_is_session_gated(config, db_path):
    app = _app(config)
    assert app.test_client().get("/accounts").status_code == 303
