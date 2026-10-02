# server/tests/test_deployment.py
"""Deployment role detection and stable server identity (Phase B)."""
import os

from server.app.app import create_app
from server.app.db import Database
from server.app.deployment import (detect_role, get_or_create_identity,
                                   probe_fcm, public_hostname)


def test_detect_role_explicit_wins(config):
    config.ROLE = "standalone"
    assert detect_role(config, fcm_available=True) == "standalone"
    config.ROLE = "slave"
    assert detect_role(config, fcm_available=True) == "slave"


def test_detect_role_by_fcm_availability(config):
    config.ROLE = ""
    assert detect_role(config, fcm_available=True) == "master"
    assert detect_role(config, fcm_available=False) == "slave"


def test_probe_fcm_without_a_key_is_false(config):
    config.FCM_SERVER_KEY = ""
    assert probe_fcm(config) is False


def test_server_identity_is_stable(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        first = get_or_create_identity(conn)
        second = get_or_create_identity(conn)
    assert first["server_id"] == second["server_id"]
    assert first["public_key"] == second["public_key"]
    assert first["hostname"]
    assert first["private_key_pem"].startswith("-----BEGIN")


def test_status_page_shows_role_and_identity(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    assert client.get("/status").status_code == 303  # session-gated
    client.post("/admin-login", data={"username": "admin", "password": "testpass"})

    page = client.get("/status")
    assert page.status_code == 200
    assert b"Server ID" in page.data
    assert app.config["_server_identity"]["server_id"].encode() in page.data
    # No FCM key configured in tests => the server is a slave.
    assert app.config["_server_role"] == "slave"


def test_public_hostname_prefers_explicit_then_push_public_url(config):
    config.SERVER_HOSTNAME = "fed.example.com"
    config.PUSH_PUBLIC_URL = "https://ignored.example.com"
    assert public_hostname(config) == "fed.example.com"

    config.SERVER_HOSTNAME = ""
    config.PUSH_PUBLIC_URL = "https://federated.z42z.com/path"
    assert public_hostname(config) == "federated.z42z.com"


def test_identity_hostname_is_corrected_without_touching_the_keys(config, db_path):
    """A recreated container gets a new socket hostname; the operator's name
    (SERVER_HOSTNAME / PUSH_PUBLIC_URL host) must win on the next start."""
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        first = get_or_create_identity(conn, hostname="1a2b3c4d5e6f")
        assert first["hostname"] == "1a2b3c4d5e6f"

        fixed = get_or_create_identity(conn, hostname="federated.z42z.com")
        assert fixed["hostname"] == "federated.z42z.com"
        assert fixed["server_id"] == first["server_id"]
        assert fixed["public_key"] == first["public_key"]
        assert fixed["private_key_pem"] == first["private_key_pem"]
