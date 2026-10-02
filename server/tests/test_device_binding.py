# server/tests/test_device_binding.py
"""Account-bound pairing auto-approves, and publishes to no-PII routing (D/E)."""
import base64
import hashlib
import os

from server.app import accounts, federation_client, pairing
from server.app.app import create_app
from server.app.crypto import generate_rsa_keypair, public_to_spki_der, sign


def _app(config, *, role="", master_url=""):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    if role:
        config.ROLE = role
    if master_url:
        config.MASTER_URL = master_url
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


def test_account_bound_pairing_is_auto_approved(config, db_path):
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
        assert dev["approved_at"] is not None  # approved on registration, no step
        # Published to the master's no-PII routing registry straight away.
        assert accounts.get_device(conn, account_id=account_id, device_id="sec-1",
                                   server_id="master") is not None

    # The portal shows it approved, not pending.
    page = c.get("/account/devices").data
    assert b"Clever Juniper" in page and b"pending approval" not in page


def test_account_pair_page_renders_qr(config, db_path):
    app = _app(config)
    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    c.post("/account/login", data={"email": "user@example.com", "password": "longenough1"})
    page = c.get("/account/pair")
    assert page.status_code == 200
    assert b"<svg" in page.data


def test_slave_publishes_the_tuple_to_its_master(config, db_path, monkeypatch):
    """On an enrolled slave the routing tuple must not stay local.

    Only the master can resolve it when the doorbell is relayed (design §6/§7B),
    so a slave publishes the no-PII tuple there instead of keeping a copy that
    nothing would ever read.
    """
    app = _app(config, role="slave", master_url="https://master.example")
    with app.config["_db"].connect() as conn:
        federation_client.save_registration(conn, "https://master.example", "s3cr3t")
    synced = []
    monkeypatch.setattr(
        federation_client, "sync_device",
        lambda cfg, conn, identity, account_id, device_id, fcm_token:
        synced.append((account_id, device_id, fcm_token)))

    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    c.post("/account/login", data={"email": "user@example.com", "password": "longenough1"})

    with app.config["_db"].connect() as conn:
        account_id = accounts.get_account_by_email(conn, "user@example.com")["account_id"]
        token = pairing.create_pairing_token(conn, 15, account_id=account_id)

    assert _register(c, token).status_code == 200
    assert synced == [(account_id, "sec-1", "fcm-1")]
    with app.config["_db"].connect() as conn:
        assert accounts.get_device(conn, account_id=account_id, device_id="sec-1",
                                   server_id="master") is None
