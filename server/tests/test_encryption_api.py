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
    assert c.put("/api/account/devices/dev-1/content-pubkey",
                 json={"public_key": "APPPUB"}).status_code == 200

    got = c.get("/api/account/devices/dev-1/content-pubkey")
    assert got.get_json()["public_key"] == "APPPUB"

    assert c.put("/api/account/devices/dev-1/sealed-cek",
                 json={"sealed_cek": "SEALED"}).status_code == 200
    with app.config["_db"].connect() as conn:
        assert encryption.get_sealed_cek(conn, "dev-1")["sealed_cek"] == "SEALED"


def test_content_key_endpoints_require_an_account(config, db_path):
    app = _app(config)
    assert app.test_client().put("/api/account/keys",
                                 json={"public_key": "X"}).status_code == 401
