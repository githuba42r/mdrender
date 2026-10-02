# server/tests/test_account_detail.py
"""Admin per-user detail page: devices, clients, pushes, block/remove (§9)."""
import base64
import io
import os

from server.app import accounts, auth, push_store, store
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


def _seed_user(app, email="user@example.com", password="longenough1"):
    c = _login(app)
    c.post("/accounts", data={"email": email, "password": password})
    with app.config["_db"].connect() as conn:
        account_id = accounts.get_account_by_email(conn, email)["account_id"]
    return c, account_id


def _seed_device(app, account_id, secret="sec-1", auth_token="auth",
                 name="Clever Juniper", approved=1):
    # A real 32-byte key in base64: ringing the doorbell seals to it (AES).
    push_key = base64.b64encode(os.urandom(32)).decode()
    with app.config["_db"].connect() as conn:
        conn.execute(
            "INSERT INTO devices (device_secret, device_auth, device_name,"
            " device_model, fcm_token, public_key, push_key, registered_at,"
            " last_seen, account_id, approved_at) VALUES (?, ?, ?, 'Pixel', 'tok',"
            " 'PUB', ?, 1, 1, ?, ?)",
            (secret, auth_token, name, push_key, account_id, approved))
        conn.commit()


def _seed_client(app, account_id, name="laptop"):
    with app.config["_db"].connect() as conn:
        return store.create_client(conn, name, "hash", account_id=account_id)


def _seed_push(app, account_id, push_id="push-1", target="Clever Juniper"):
    with app.config["_db"].connect() as conn:
        push_store.create_push(conn, push_id, target, account_id=account_id)
        push_store.add_file(conn, file_id=f"{push_id}-f1", push_id=push_id,
                            file_name="a.pdf", file_path="", size=3,
                            retrieval_key="rk", stored_path=None, created_at=1)


def test_detail_page_requires_admin_and_renders_every_section(config, db_path):
    app = _app(config)
    c, account_id = _seed_user(app)
    _seed_device(app, account_id)
    client_id = _seed_client(app, account_id)
    _seed_push(app, account_id)

    # Anonymous: bounced to login, remembering where it was headed.
    anon = app.test_client()
    r = anon.get(f"/accounts/{account_id}")
    assert r.status_code == 303
    assert r.headers["Location"] == f"/admin-login?next=/accounts/{account_id}"

    page = c.get(f"/accounts/{account_id}")
    body = page.data
    assert page.status_code == 200
    assert b"user@example.com" in body
    for section in (b"Devices", b"CLI clients", b"Recent pushes",
                    b"Pending pushes"):
        assert section in body
    assert b"Clever Juniper" in body
    assert b"laptop" in body and client_id.encode() in body
    assert b"push-1" in body
    # Block controls exist for both kinds of row.
    assert b"Block device" in body and b"Block client" in body

    # The Users list links to this page with the eye button.
    listing = c.get("/accounts").data
    assert b"title=\"Open\"" in listing
    assert f"/accounts/{account_id}".encode() in listing


def test_unknown_account_redirects_to_the_list(config, db_path):
    app = _app(config)
    c = _login(app)
    r = c.get("/accounts/nope")
    assert r.status_code == 303
    assert r.headers["Location"].startswith("/accounts?error=")


def test_device_block_round_trip(config, db_path):
    app = _app(config)
    c, account_id = _seed_user(app)
    _seed_device(app, account_id)
    client_id = _seed_client(app, account_id, name="pusher")
    tok = auth.issue_access_token(config, client_id)

    def device_status():
        return c.post("/api/device/status",
                      json={"device_secret": "sec-1", "device_auth": "auth"})

    def push_to_device():
        return c.post("/api/push",
                      headers={"Authorization": f"Bearer {tok}"},
                      data={"target_device": "Clever Juniper",
                            "file": (io.BytesIO(b"hi"), "a.md")},
                      content_type="multipart/form-data")

    assert device_status().json == {"ok": True}
    assert push_to_device().status_code == 200

    r = c.post(f"/accounts/{account_id}/devices/sec-1/block",
               data={"blocked": "1", "next": f"/accounts/{account_id}"})
    assert r.status_code == 303 and r.headers["Location"] == f"/accounts/{account_id}"
    with app.config["_db"].connect() as conn:
        row = conn.execute("SELECT blocked_at FROM devices"
                           " WHERE device_secret='sec-1'").fetchone()
    assert row["blocked_at"] is not None
    assert b"blocked" in c.get(f"/accounts/{account_id}").data

    # Enforcement: authenticated device surface and pushes to it are refused.
    assert device_status().status_code == 404
    assert push_to_device().status_code == 403
    assert push_to_device().json["error"] == "device blocked"
    rotate = c.post("/api/register-device",
                    json={"device_secret": "sec-1", "device_auth": "auth",
                          "device_name": "Renamed"})
    assert rotate.status_code == 401

    # Unblock restores everything.
    c.post(f"/accounts/{account_id}/devices/sec-1/block",
           data={"blocked": "0", "next": f"/accounts/{account_id}"})
    with app.config["_db"].connect() as conn:
        row = conn.execute("SELECT blocked_at FROM devices"
                           " WHERE device_secret='sec-1'").fetchone()
    assert row["blocked_at"] is None
    assert device_status().json == {"ok": True}
    assert push_to_device().status_code == 200


def test_device_actions_are_scoped_to_the_account(config, db_path):
    app = _app(config)
    c, account_a = _seed_user(app, email="a@example.com")
    _seed_device(app, account_a)
    c.post("/accounts", data={"email": "b@example.com", "password": "longenough1"})
    with app.config["_db"].connect() as conn:
        account_b = accounts.get_account_by_email(conn, "b@example.com")["account_id"]

    # Another account cannot block or revoke this device.
    c.post(f"/accounts/{account_b}/devices/sec-1/block", data={"blocked": "1"})
    with app.config["_db"].connect() as conn:
        row = conn.execute("SELECT blocked_at FROM devices"
                           " WHERE device_secret='sec-1'").fetchone()
    assert row["blocked_at"] is None
    c.post(f"/accounts/{account_b}/devices/sec-1/revoke")
    with app.config["_db"].connect() as conn:
        assert conn.execute("SELECT 1 FROM devices"
                            " WHERE device_secret='sec-1'").fetchone() is not None

    # The owning account can revoke (unpair) it.
    c.post(f"/accounts/{account_a}/devices/sec-1/revoke")
    with app.config["_db"].connect() as conn:
        assert conn.execute("SELECT 1 FROM devices"
                            " WHERE device_secret='sec-1'").fetchone() is None


def test_client_block_enforces_the_bearer_gate(config, db_path):
    app = _app(config)
    c, account_id = _seed_user(app)
    client_id = _seed_client(app, account_id, name="pusher")
    tok = auth.issue_access_token(config, client_id)

    def call():
        return c.get("/api/push/some-push/status",
                     headers={"Authorization": f"Bearer {tok}"})

    assert call().status_code == 200

    r = c.post(f"/accounts/{account_id}/clients/{client_id}/block",
               data={"blocked": "1", "next": f"/accounts/{account_id}"})
    assert r.status_code == 303 and r.headers["Location"] == f"/accounts/{account_id}"
    assert call().status_code == 401
    assert call().json["error"] == "client_blocked"
    assert b"blocked" in c.get(f"/accounts/{account_id}").data

    # Unblock restores service without re-issuing the token.
    c.post(f"/accounts/{account_id}/clients/{client_id}/block",
           data={"blocked": "0", "next": f"/accounts/{account_id}"})
    assert call().status_code == 200

    # Revoke is permanent: the token dies with it and the row leaves the lists.
    c.post(f"/accounts/{account_id}/clients/{client_id}/revoke",
           data={"next": f"/accounts/{account_id}"})
    assert call().status_code == 401
    assert call().json["error"] == "unauthorized"  # tokens dropped on revoke
    assert client_id.encode() not in c.get(f"/accounts/{account_id}").data
    with app.config["_db"].connect() as conn:
        assert store.get_client(conn, client_id)["revoked_at"] is not None


def test_client_actions_are_scoped_to_the_account(config, db_path):
    app = _app(config)
    c, account_a = _seed_user(app, email="a@example.com")
    client_id = _seed_client(app, account_a, name="pusher")
    c.post("/accounts", data={"email": "b@example.com", "password": "longenough1"})
    with app.config["_db"].connect() as conn:
        account_b = accounts.get_account_by_email(conn, "b@example.com")["account_id"]

    c.post(f"/accounts/{account_b}/clients/{client_id}/block", data={"blocked": "1"})
    c.post(f"/accounts/{account_b}/clients/{client_id}/revoke")
    with app.config["_db"].connect() as conn:
        row = store.get_client(conn, client_id)
    assert row["blocked_at"] is None and row["revoked_at"] is None


def test_push_delete_and_purges_stay_inside_the_account(config, db_path):
    app = _app(config)
    c, account_a = _seed_user(app, email="a@example.com")
    c.post("/accounts", data={"email": "b@example.com", "password": "longenough1"})
    with app.config["_db"].connect() as conn:
        account_b = accounts.get_account_by_email(conn, "b@example.com")["account_id"]

    _seed_push(app, account_a, push_id="push-a1")
    _seed_push(app, account_a, push_id="push-a2")
    _seed_push(app, account_b, push_id="push-b1")
    for pid in ("push-a1", "push-a2", "push-b1"):
        os.makedirs(os.path.join(config.PUSH_STORAGE_DIR, pid), exist_ok=True)

    # Single push delete removes the row and its storage tree.
    r = c.post(f"/accounts/{account_a}/pushes/push-a1/delete",
               data={"next": f"/accounts/{account_a}"})
    assert r.status_code == 303 and r.headers["Location"] == f"/accounts/{account_a}"
    assert not os.path.exists(os.path.join(config.PUSH_STORAGE_DIR, "push-a1"))
    with app.config["_db"].connect() as conn:
        assert push_store.get_push_by_id(conn, "push-a1") is None
        assert push_store.get_push_by_id(conn, "push-a2") is not None
        assert push_store.get_push_by_id(conn, "push-b1") is not None

    # Purge pending only touches this account's pending pushes.
    c.post(f"/accounts/{account_a}/pending/purge", data={"next": f"/accounts/{account_a}"})
    with app.config["_db"].connect() as conn:
        assert push_store.get_push_by_id(conn, "push-a2") is None
        assert push_store.get_push_by_id(conn, "push-b1") is not None

    # Another account cannot delete or purge this account's pushes.
    _seed_push(app, account_a, push_id="push-a3")
    c.post(f"/accounts/{account_b}/pushes/push-a3/delete")
    c.post(f"/accounts/{account_b}/pushes/purge")
    with app.config["_db"].connect() as conn:
        assert push_store.get_push_by_id(conn, "push-a3") is not None
        assert push_store.get_push_by_id(conn, "push-b1") is None

    # Purge all removes every remaining push of this account.
    c.post(f"/accounts/{account_a}/pushes/purge", data={"next": f"/accounts/{account_a}"})
    with app.config["_db"].connect() as conn:
        assert push_store.get_push_by_id(conn, "push-a3") is None


def test_account_status_form_returns_to_the_detail_page(config, db_path):
    app = _app(config)
    c, account_id = _seed_user(app)

    # Without a next field the list remains the default destination.
    r = c.post(f"/accounts/{account_id}/status", data={"status": "blocked"})
    assert r.headers["Location"] == "/accounts"
    # With next, the detail page can post its Deactivate button and stay put.
    r = c.post(f"/accounts/{account_id}/status",
               data={"status": "active", "next": f"/accounts/{account_id}"})
    assert r.headers["Location"] == f"/accounts/{account_id}"
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["status"] == "active"
