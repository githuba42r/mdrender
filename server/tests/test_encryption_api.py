# server/tests/test_encryption_api.py
"""Content key-exchange endpoints (Phase J server side)."""
import os

from server.app import encryption
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _client(app):
    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    c.post("/account/login", data={"email": "user@example.com", "password": "longenough1"})
    return c


def test_content_key_endpoints(config, db_path):
    app = _app(config)
    c = _client(app)
    assert c.put("/api/account/keys", json={"public_key": "ACCTPUB"}).status_code == 200

    # A paired device carries its content key, the pairing-key proof, and its
    # pairing public key, so the client can verify the chain (design §7c).
    with app.config["_db"].connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO devices (device_secret, device_auth, device_name,"
            " fcm_token, public_key, push_key, registered_at, last_seen,"
            " content_pubkey, content_proof) VALUES"
            " ('dev-1','auth','Dev','fcm','DEVPUB','PUSH',1,1,'APPPUB','PROOF')")
        conn.commit()

    got = c.get("/api/account/devices/dev-1/content-pubkey")
    assert got.status_code == 200
    body = got.get_json()
    assert body["content_pubkey"] == "APPPUB"
    assert body["content_proof"] == "PROOF"
    assert body["device_public_key"] == "DEVPUB"

    assert c.put("/api/account/devices/dev-1/sealed-cek",
                 json={"sealed_cek": "SEALED"}).status_code == 200
    with app.config["_db"].connect() as conn:
        assert encryption.get_sealed_cek(conn, "dev-1")["sealed_cek"] == "SEALED"


def test_content_key_endpoints_require_an_account(config, db_path):
    app = _app(config)
    assert app.test_client().put("/api/account/keys",
                                 json={"public_key": "X"}).status_code == 401
