# server/tests/test_settings.py
"""Admin Settings: federation gate + signup toggle (design §8)."""
import base64
import os
import unittest.mock as mock

from server.app import accounts, crypto, federation, settings
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _login(app):
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "testpass"})
    return c


def _enrol(app, server_id="srv-1"):
    priv, pub = crypto.generate_rsa_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()
    pub_b64 = base64.b64encode(crypto.public_to_spki_der(pub)).decode()
    with mock.patch.object(federation, "verify_slave_callback",
                           lambda base_url, challenge, timeout=10:
                           federation.sign_bytes(priv_pem, challenge.encode())):
        return app.test_client().post("/api/federation/enrol", json={
            "server_id": server_id, "hostname": "h",
            "base_url": "https://h", "public_key": pub_b64})


def test_settings_page_is_session_gated(config, db_path):
    app = _app(config)
    assert app.test_client().get("/settings").status_code == 303


def test_federation_toggle_gates_enrolment(config, db_path):
    app = _app(config)
    c = _login(app)
    assert c.get("/settings").status_code == 200

    assert _enrol(app, "s1").status_code == 200  # accepting by default

    c.post("/settings", data={"signup_enabled": "on"})  # accept_new_slaves omitted -> off
    with app.config["_db"].connect() as conn:
        assert settings.accept_new_slaves(conn) is False

    assert _enrol(app, "s2").status_code == 403


def test_federation_env_disables_enrolment(config, db_path):
    config.FEDERATION_ENABLED = False
    app = _app(config)
    assert _enrol(app, "s1").status_code == 403


def test_signup_toggle_closes_signup(config, db_path):
    app = _app(config)
    c = _login(app)
    c.post("/settings", data={"accept_new_slaves": "on"})  # signup_enabled omitted -> off

    assert c.get("/signup").status_code == 403
    assert app.test_client().post("/signup",
                                  data={"email": "u@example.com",
                                        "password": "longenough1"}).status_code == 403

    # The admin can still create users directly.
    with app.config["_db"].connect() as conn:
        assert accounts.create_account(conn, "x@example.com", "longenough1")
