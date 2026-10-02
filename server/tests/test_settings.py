# server/tests/test_settings.py
"""Admin Settings: federation gate + signup toggle (design §8)."""
import base64
import os
import unittest.mock as mock

from server.app import accounts, billing, crypto, federation, settings
from server.app.app import create_app
from conftest import consent_code


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


def _enrol(app, server_id="srv-1"):
    priv, pub = crypto.generate_rsa_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()
    pub_b64 = base64.b64encode(crypto.public_to_spki_der(pub)).decode()
    code = consent_code(app, server_id=server_id, hostname="h",
                        base_url="https://h", public_key=pub_b64)
    with mock.patch.object(federation, "verify_slave_callback",
                           lambda base_url, challenge, timeout=10:
                           federation.sign_bytes(priv_pem, challenge.encode())):
        return app.test_client().post("/api/federation/enrol", json={
            "server_id": server_id, "hostname": "h",
            "base_url": "https://h", "public_key": pub_b64, "code": code})


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


def test_new_accounts_are_placed_in_the_signup_group(config, db_path):
    app = _app(config)
    c = _login(app)
    with app.config["_db"].connect() as conn:
        group_id = billing.create_group(conn, "trial")

    page = c.get("/settings").data
    assert b"signup_group_id" in page and b"trial" in page

    c.post("/settings", data={"signup_enabled": "on", "signup_group_id": group_id})
    with app.config["_db"].connect() as conn:
        assert settings.signup_group_id(conn) == group_id

    app.test_client().post("/signup", data={"email": "new@example.com",
                                            "password": "longenough1"})
    with app.config["_db"].connect() as conn:
        account_id = accounts.get_account_by_email(conn, "new@example.com")["account_id"]
        row = conn.execute("SELECT group_id FROM account_groups WHERE account_id = ?",
                           (account_id,)).fetchone()
    assert row is not None and row["group_id"] == group_id


def test_signup_toggle_closes_signup(config, db_path):
    app = _app(config)
    c = _login(app)
    c.post("/settings", data={"accept_new_slaves": "on"})  # signup_enabled omitted -> off

    assert c.get("/signup").status_code == 403
    assert app.test_client().post("/signup",
                                  data={"email": "u@example.com",
                                        "password": "longenough1"}).status_code == 403

    # Login pages no longer advertise signup while it is closed.
    assert b'href="/signup"' not in c.get("/login").data

    # The admin can still create users directly.
    with app.config["_db"].connect() as conn:
        assert accounts.create_account(conn, "x@example.com", "longenough1")


def test_default_server_plan_is_offered_and_applied_on_enrolment(config, db_path):
    app = _app(config)
    c = _login(app)
    with app.config["_db"].connect() as conn:
        host_plan = billing.create_plan(conn, "Host", billing.SCOPE_SLAVE,
                                        price_cents=2000)
        billing.create_plan(conn, "Starter", billing.SCOPE_ACCOUNT, price_cents=500)

    # Only server (host) plans are offered as the default.
    page = c.get("/settings").data
    assert b"default_server_plan_id" in page
    assert b"Host" in page and b"Starter" not in page

    c.post("/settings", data={"signup_enabled": "on", "accept_new_slaves": "on",
                              "default_server_plan_id": host_plan})
    with app.config["_db"].connect() as conn:
        assert settings.default_server_plan_id(conn) == host_plan

    # A server that registers takes the default...
    assert _enrol(app, "srv-def").status_code == 200
    with app.config["_db"].connect() as conn:
        assert federation.get_server(conn, "srv-def")["plan_id"] == host_plan


def test_the_default_server_plan_must_be_a_server_plan(config, db_path):
    app = _app(config)
    c = _login(app)
    with app.config["_db"].connect() as conn:
        account_plan = billing.create_plan(conn, "Starter", billing.SCOPE_ACCOUNT)
        host_plan = billing.create_plan(conn, "Host", billing.SCOPE_SLAVE)

    # An account plan posted straight at the endpoint is ignored.
    c.post("/settings", data={"signup_enabled": "on",
                              "default_server_plan_id": account_plan})
    with app.config["_db"].connect() as conn:
        assert settings.default_server_plan_id(conn) is None

    # Setting and then clearing works.
    c.post("/settings", data={"signup_enabled": "on",
                              "default_server_plan_id": host_plan})
    with app.config["_db"].connect() as conn:
        assert settings.default_server_plan_id(conn) == host_plan
    c.post("/settings", data={"signup_enabled": "on",
                              "default_server_plan_id": ""})
    with app.config["_db"].connect() as conn:
        assert settings.default_server_plan_id(conn) is None
