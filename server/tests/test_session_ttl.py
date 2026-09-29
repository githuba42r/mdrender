# server/tests/test_session_ttl.py
"""Browser session lifetime is configurable and defaults to a long window."""
import os

from server.app import auth
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _ttl(app, config):
    with app.config["_db"].connect() as conn:
        auth.create_session(conn, config.session_secret, config)
        row = conn.execute("SELECT expires_at, created_at FROM sessions").fetchone()
    return row["expires_at"] - row["created_at"]


def test_default_session_ttl_is_long(config, db_path):
    app = _app(config)
    assert config.SESSION_TTL_SECONDS >= 24 * 3600
    assert _ttl(app, config) == config.SESSION_TTL_SECONDS


def test_session_ttl_honours_config(config, db_path):
    config.SESSION_TTL_SECONDS = 120
    app = _app(config)
    assert _ttl(app, config) == 120
