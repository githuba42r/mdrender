# server/tests/test_bans_ui.py
"""Ban admin UI and edge enforcement (Phase G)."""
import os

from server.app import bans
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def test_bans_page_add_and_remove(config, db_path):
    app = _app(config)
    c = app.test_client()
    assert c.get("/bans").status_code == 303  # session-gated
    c.post("/login", data={"username": "admin", "password": "testpass"})
    assert c.get("/bans").status_code == 200

    c.post("/bans", data={"kind": "cidr", "value": "203.0.113.0/24", "reason": "spam"})
    assert b"203.0.113.0/24" in c.get("/bans").data

    with app.config["_db"].connect() as conn:
        ban_id = bans.list_bans(conn)[0]["id"]
    c.post(f"/bans/{ban_id}/delete")
    assert b"203.0.113.0/24" not in c.get("/bans").data


def test_ip_ban_blocks_every_endpoint(config, db_path):
    app = _app(config)
    with app.config["_db"].connect() as conn:
        bans.add_ban(conn, "ip", "127.0.0.1")

    c = app.test_client()
    assert c.get("/login").status_code == 403
    assert c.get("/api/health").status_code == 403

    with app.config["_db"].connect() as conn:
        bans.remove_ban(conn, bans.list_bans(conn)[0]["id"])
    assert c.get("/api/health").status_code == 200
