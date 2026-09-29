# server/tests/test_account_portal.py
"""Account login/session and portal, and admin/account separation (Phase D)."""
import os

from server.app import accounts
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def test_account_login_and_portal_lists_devices(config, db_path):
    app = _app(config)
    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})

    # Wrong password rejected.
    assert c.post("/account/login",
                  data={"email": "user@example.com", "password": "nope"}).status_code == 401

    ok = c.post("/account/login",
                data={"email": "user@example.com", "password": "longenough1"})
    assert ok.status_code == 302 and ok.headers["Location"] == "/account"

    # A paired device for this account shows in the portal.
    with app.config["_db"].connect() as conn:
        account_id = accounts.get_account_by_email(conn, "user@example.com")["account_id"]
        conn.execute(
            "INSERT INTO devices (device_secret, device_auth, device_name, fcm_token,"
            " public_key, push_key, registered_at, last_seen, account_id, approved_at)"
            " VALUES ('dev-1','auth','Clever Juniper','tok','PUB','PUSH',1,1,?,1)",
            (account_id,))
        conn.commit()
    body = c.get("/account/devices").data
    assert b"Clever Juniper" in body


def test_account_session_cannot_reach_admin_pages(config, db_path):
    app = _app(config)
    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    c.post("/account/login", data={"email": "user@example.com", "password": "longenough1"})

    # Admin pages bounce an account session to the admin login.
    assert c.get("/pushes").status_code == 303
    assert c.get("/devices").status_code == 303
    assert c.get("/clients").status_code == 303

    # And an admin session cannot be used as an account.
    admin = app.test_client()
    admin.post("/login", data={"username": "admin", "password": "testpass"})
    assert admin.get("/account").headers["Location"] == "/account/login"


def test_auth_providers_and_oidc_rejects_a_missing_token(config, db_path):
    config.IDENTITY_PROVIDER = "firebase"
    app = _app(config)
    c = app.test_client()
    assert c.get("/auth/providers").get_json() == {"providers": ["local", "firebase"]}
    # No id_token (and no project configured) -> unauthorized.
    assert c.post("/auth/oidc", json={}).status_code == 401


def test_account_portal_is_gated(config, db_path):
    app = _app(config)
    resp = app.test_client().get("/account")
    assert resp.status_code == 303
    assert resp.headers["Location"] == "/account/login"
