# server/tests/test_pairing_events.py
"""SSE pairing notifications: the pub/sub and the registration wiring."""
import base64
import hashlib
import os
import unittest.mock as mock

from server.app import accounts, events, pairing
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


def test_publish_delivers_to_subscribers():
    channel = events.subscribe("tok")
    try:
        events.publish("tok", "paired", {"device_name": "Phone"})
        item = channel.get(timeout=1)
        assert item["event"] == "paired" and item["data"]["device_name"] == "Phone"
    finally:
        events.unsubscribe("tok", channel)
    # Publishing after unsubscribe is a no-op, not an error.
    events.publish("tok", "paired")


def test_registration_publishes_a_paired_event(config, db_path):
    app = _app(config)
    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    c.post("/account/login", data={"email": "user@example.com", "password": "longenough1"})
    with app.config["_db"].connect() as conn:
        account_id = accounts.get_account_by_email(conn, "user@example.com")["account_id"]
        token = pairing.create_pairing_token(conn, 15, account_id=account_id)

    with mock.patch.object(events, "publish") as publish:
        assert _register(c, token).status_code == 200
    publish.assert_called_once()
    args = publish.call_args.args
    assert args[0] == token and args[1] == "paired"


def test_events_endpoint_is_gated_and_validates_the_token(config, db_path):
    app = _app(config)
    assert app.test_client().get("/account/pair/events?token=x").status_code == 401

    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    c.post("/account/login", data={"email": "user@example.com", "password": "longenough1"})
    assert c.get("/account/pair/events?token=nope").status_code == 404
