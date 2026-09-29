# server/tests/test_encryption_policy.py
"""Server-enforced encryption policy (design §7b)."""
import base64
import io
import json
import os
import unittest.mock as mock

from server.app import accounts, crypto, encryption, federation
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _account_client(app):
    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    c.post("/account/login", data={"email": "user@example.com", "password": "longenough1"})
    return c


def test_server_policy_reports_mode(config, db_path):
    config.ENCRYPTION_MODE = "on"
    app = _app(config)
    assert app.test_client().get("/api/server/policy").get_json() == {"encryption": "on"}


def test_required_encryption_rejects_plaintext_upload(config, db_path):
    config.ENCRYPTION_MODE = "on"
    app = _app(config)
    c = _account_client(app)

    plain = c.post("/api/account/upload", data={"file": (io.BytesIO(b"x"), "x.txt")},
                   content_type="multipart/form-data")
    assert plain.status_code == 422

    sealed = c.post("/api/account/upload",
                    data={"file": (io.BytesIO(b"cipher"), "x"),
                          "alg": "aes-256-gcm", "nonce": "N"},
                    content_type="multipart/form-data")
    assert sealed.status_code == 200


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


def test_required_encryption_gates_the_doorbell(config, db_path):
    config.ENCRYPTION_MODE = "on"
    app = _app(config)

    class FakeFcm:
        def __init__(self):
            self.sent = []

        def send(self, data, token):
            self.sent.append((data, token))

    app.config["_fcm"] = FakeFcm()
    priv_pem, token = _enrol_slave(app)
    with app.config["_db"].connect() as conn:
        accounts.upsert_device(conn, account_id="acct-1", device_id="dev-1",
                               server_id="srv-1", fcm_token="tok")

    c = app.test_client()

    def _doorbell():
        body = json.dumps({"account_id": "acct-1", "device_id": "dev-1",
                           "sealed": {"c": "C", "i": "I"}}).encode()
        headers = federation.sign_request(priv_pem, "POST",
                                          "/api/federation/doorbell", body)
        headers["Authorization"] = f"Bearer {token}"
        return c.post("/api/federation/doorbell", data=body,
                      content_type="application/json", headers=headers)

    # No sealed CEK yet -> refused until negotiated.
    assert _doorbell().status_code == 409

    with app.config["_db"].connect() as conn:
        encryption.set_sealed_cek(conn, "dev-1", "SEALED")
    assert _doorbell().status_code == 200
