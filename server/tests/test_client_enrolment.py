# server/tests/test_client_enrolment.py
"""Account-bound CLI enrolment, and revoked clients being unable to push."""
import base64
import io
import os

from server.app import accounts, auth, store
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _device(app, name="Dev"):
    push_key = base64.b64encode(b"\x00" * 32).decode()
    with app.config["_db"].connect() as conn:
        conn.execute(
            "INSERT INTO devices (device_secret, device_auth, device_name, fcm_token,"
            " public_key, push_key, registered_at, last_seen) VALUES"
            " ('sec', 'auth', ?, 'tok', ?, ?, 1, 1)",
            (name, base64.b64encode(b"\x01" * 294).decode(), push_key))
        conn.commit()


def _push(app, token):
    return app.test_client().post(
        "/api/push",
        headers={"Authorization": f"Bearer {token}"},
        data={"target_device": "Dev", "file": (io.BytesIO(b"x"), "x.txt")},
        content_type="multipart/form-data")


def test_account_approved_enrolment_binds_the_client(config, db_path):
    app = _app(config)
    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    c.post("/account/login", data={"email": "user@example.com", "password": "longenough1"})

    start = app.test_client().post("/api/enrol/start", json={"name": "cli"})
    eid = start.get_json()["enrolment_id"]
    assert c.get(f"/enrol/{eid}").status_code == 200
    assert c.post(f"/enrol/{eid}/approve").status_code == 200

    with app.config["_db"].connect() as conn:
        account_id = accounts.get_account_by_email(conn, "user@example.com")["account_id"]
        rows = store.list_clients(conn, account_id)
    assert [r["name"] for r in rows] == ["cli"]


def test_revoked_client_cannot_push_even_with_a_live_token(config, db_path):
    app = _app(config)
    _device(app)
    with app.config["_db"].connect() as conn:
        client_id = store.create_client(conn, "cli", "hash")

    token = auth.issue_access_token(config, client_id)
    assert _push(app, token).status_code == 200

    # Revoke the row but leave the token in the in-memory map, i.e. the case the
    # revoked_at check exists to catch.
    with app.config["_db"].connect() as conn:
        store.revoke_client(conn, client_id)
    assert _push(app, token).status_code == 401


def test_revoked_client_cannot_fetch_a_token(config, db_path):
    app = _app(config)
    with app.config["_db"].connect() as conn:
        client_id = store.create_client(conn, "cli", auth.hash_secret("secret"))
        store.revoke_client(conn, client_id)
    resp = app.test_client().post("/oauth/token", data={
        "grant_type": "client_credentials", "client_id": client_id,
        "client_secret": "secret"})
    assert resp.status_code == 401
