# server/tests/test_device_binding.py
"""Account-bound pairing with approval, and no-PII publishing (Phase D/E)."""
import base64
import hashlib
import os

from server.app import accounts, pairing
from server.app.app import create_app
from server.app.crypto import generate_rsa_keypair, public_to_spki_der, sign


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _register(client, token, name="Clever Juniper"):
    priv, pub = generate_rsa_keypair()
    pub_b64 = base64.b64encode(public_to_spki_der(pub)).decode()
    push_key = base64.b64encode(os.urandom(32)).decode()
    secret, fcm = "sec-1", "fcm-1"
    digest = hashlib.sha256(f"{secret}{name}{fcm}{pub_b64}{push_key}".encode()).digest()
    sig = base64.b64encode(sign(priv, digest)).decode()
    return client.post("/api/register-device", json={
        "device_secret": secret, "device_name": name, "fcm_token": fcm,
        "public_key": pub_b64, "push_key": push_key,
        "pairing_token": token, "sig": sig})


def test_account_bound_pairing_requires_approval(config, db_path):
    app = _app(config)
    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    c.post("/account/login", data={"email": "user@example.com", "password": "longenough1"})

    with app.config["_db"].connect() as conn:
        account_id = accounts.get_account_by_email(conn, "user@example.com")["account_id"]
        token = pairing.create_pairing_token(conn, 15, account_id=account_id)

    assert _register(c, token).status_code == 200
    with app.config["_db"].connect() as conn:
        dev = conn.execute("SELECT account_id, approved_at FROM devices"
                           " WHERE device_secret = 'sec-1'").fetchone()
        assert dev["account_id"] == account_id
        assert dev["approved_at"] is None

    # The portal shows it pending and can approve it.
    assert b"pending approval" in c.get("/account/devices").data
    c.post("/account/devices/sec-1/approve")
    with app.config["_db"].connect() as conn:
        assert conn.execute("SELECT approved_at FROM devices WHERE device_secret = 'sec-1'"
                            ).fetchone()["approved_at"] is not None
        # Published to the master's no-PII routing registry.
        assert accounts.get_device(conn, account_id=account_id, device_id="sec-1",
                                   server_id="master") is not None


def test_account_pair_page_renders_qr(config, db_path):
    app = _app(config)
    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    c.post("/account/login", data={"email": "user@example.com", "password": "longenough1"})
    page = c.get("/account/pair")
    assert page.status_code == 200
    assert b"<svg" in page.data
