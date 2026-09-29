# server/tests/test_deployment.py
"""Deployment role detection and stable server identity (Phase B)."""
import os

from server.app.app import create_app
from server.app.db import Database
from server.app.deployment import detect_role, get_or_create_identity, probe_fcm


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
    client.post("/login", data={"username": "admin", "password": "testpass"})

    page = client.get("/status")
    assert page.status_code == 200
    assert b"Server ID" in page.data
    assert app.config["_server_identity"]["server_id"].encode() in page.data
    # No FCM key configured in tests => the server is a slave.
    assert app.config["_server_role"] == "slave"
