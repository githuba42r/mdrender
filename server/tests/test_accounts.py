# server/tests/test_accounts.py
"""Accounts and the no-PII device routing registry (Phase D)."""
import base64
import json
import os
import unittest.mock as mock

import pytest

from server.app import accounts, crypto, federation
from server.app.app import create_app


@pytest.fixture()
def app_(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def test_account_create_lookup_and_password(app_):
    db = app_.config["_db"]
    with db.connect() as conn:
        aid = accounts.create_account(conn, "User@Example.com", "longenough1")
        assert accounts.get_account(conn, aid)["email"] == "user@example.com"
        assert accounts.get_account_by_email(conn, "USER@example.com")["account_id"] == aid
        assert accounts.verify_account_password(conn, "user@example.com", "longenough1") == aid
        assert accounts.verify_account_password(conn, "user@example.com", "nope") is None
        accounts.set_account_status(conn, aid, accounts.BLOCKED)
        # A blocked account cannot authenticate.
        assert accounts.verify_account_password(conn, "user@example.com", "longenough1") is None


def test_device_registry_upserts_and_scopes(app_):
    db = app_.config["_db"]
    with db.connect() as conn:
        accounts.upsert_device(conn, account_id="acct-1", device_id="dev-1",
                               server_id="srv-1", fcm_token="tok-1")
        accounts.upsert_device(conn, account_id="acct-1", device_id="dev-1",
                               server_id="srv-1", fcm_token="tok-2")
        rows = accounts.list_devices(conn, "acct-1", "srv-1")
        assert len(rows) == 1 and rows[0]["fcm_token"] == "tok-2"
        accounts.delete_device(conn, account_id="acct-1", device_id="dev-1",
                               server_id="srv-1")
        assert accounts.list_devices(conn, "acct-1", "srv-1") == []


def test_signup_page_creates_an_account(app_):
    c = app_.test_client()
    assert c.get("/signup").status_code == 200
    assert c.post("/signup", data={"email": "nope", "password": "short"}).status_code == 400
    ok = c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    assert ok.status_code == 200 and b"Account created" in ok.data
    dup = c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    assert dup.status_code == 400


def _enrol_slave(app):
    priv, pub = crypto.generate_rsa_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()
    pub_b64 = base64.b64encode(crypto.public_to_spki_der(pub)).decode()
    with mock.patch.object(federation, "verify_slave_callback",
                           lambda base_url, challenge, timeout=10:
                           federation.sign_bytes(priv_pem, challenge.encode())):
        resp = app.test_client().post("/api/federation/enrol", json={
            "server_id": "srv-1", "hostname": "slave",
            "base_url": "https://slave", "public_key": pub_b64})
    return priv_pem, f"srv-1.{resp.get_json()['server_secret']}"


def test_master_device_sync_accepts_signed_no_pii_updates(app_):
    priv_pem, token = _enrol_slave(app_)
    c = app_.test_client()
    path = "/api/federation/accounts/acct-1/devices/dev-1"

    body = b'{"fcm_token":"tok-9"}'
    headers = federation.sign_request(priv_pem, "PUT", path, body)
    headers["Authorization"] = f"Bearer {token}"
    resp = c.put(path, data=body, content_type="application/json", headers=headers)
    assert resp.status_code == 200
    with app_.config["_db"].connect() as conn:
        rows = accounts.list_devices(conn, "acct-1", "srv-1")
    assert rows[0]["fcm_token"] == "tok-9"
    assert rows[0]["name"] is None  # no PII stored at the master

    headers = federation.sign_request(priv_pem, "DELETE", path, b"")
    headers["Authorization"] = f"Bearer {token}"
    assert c.delete(path, headers=headers).status_code == 200
    with app_.config["_db"].connect() as conn:
        assert accounts.list_devices(conn, "acct-1", "srv-1") == []


class _FakeFcm:
    def __init__(self):
        self.sent = []

    def send(self, data, token):
        self.sent.append((data, token))


def test_master_relays_a_doorbell_to_the_owning_device(app_):
    priv_pem, token = _enrol_slave(app_)
    fake = _FakeFcm()
    app_.config["_fcm"] = fake
    with app_.config["_db"].connect() as conn:
        accounts.upsert_device(conn, account_id="acct-1", device_id="dev-1",
                               server_id="srv-1", fcm_token="tok-1")

    c = app_.test_client()
    body = json.dumps({"account_id": "acct-1", "device_id": "dev-1",
                       "sealed": {"c": "CIPHER", "i": "IV"}}).encode()
    headers = federation.sign_request(priv_pem, "POST", "/api/federation/doorbell", body)
    headers["Authorization"] = f"Bearer {token}"
    resp = c.post("/api/federation/doorbell", data=body,
                  content_type="application/json", headers=headers)
    assert resp.status_code == 200, resp.data
    assert fake.sent == [({"p": "CIPHER", "i": "IV"}, "tok-1")]

    # A device that isn't registered for this slave/account is refused.
    body = json.dumps({"account_id": "acct-2", "device_id": "dev-9",
                       "sealed": {"c": "C", "i": "I"}}).encode()
    headers = federation.sign_request(priv_pem, "POST", "/api/federation/doorbell", body)
    headers["Authorization"] = f"Bearer {token}"
    assert c.post("/api/federation/doorbell", data=body,
                  content_type="application/json",
                  headers=headers).status_code == 403
