# server/tests/test_upload.py
"""Account upload API with quota enforcement (Phase F)."""
import io
import os

from server.app import accounts, storage
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _account_client(app, email="user@example.com"):
    c = app.test_client()
    c.post("/signup", data={"email": email, "password": "longenough1"})
    c.post("/account/login", data={"email": email, "password": "longenough1"})
    return c


def test_upload_stores_and_records_pending_files(config, db_path):
    app = _app(config)
    c = _account_client(app)
    resp = c.post("/api/account/upload", data={
        "file": (io.BytesIO(b"hello"), "note.txt"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200
    file_ids = resp.get_json()["file_ids"]
    with app.config["_db"].connect() as conn:
        account_id = accounts.get_account_by_email(conn, "user@example.com")["account_id"]
        assert storage.usage(conn, account_id) == {"bytes": 5, "files": 1}
        assert file_ids


def test_upload_enforces_quota(config, db_path):
    app = _app(config)
    c = _account_client(app)
    with app.config["_db"].connect() as conn:
        account_id = accounts.get_account_by_email(conn, "user@example.com")["account_id"]
        storage.set_quota(conn, account_id, max_files=1)

    assert c.post("/api/account/upload", data={
        "file": (io.BytesIO(b"a"), "a.txt")},
        content_type="multipart/form-data").status_code == 200
    over = c.post("/api/account/upload", data={
        "file": (io.BytesIO(b"b"), "b.txt")},
        content_type="multipart/form-data")
    assert over.status_code == 413


def test_upload_requires_an_account_session(config, db_path):
    app = _app(config)
    assert app.test_client().post("/api/account/upload", data={
        "file": (io.BytesIO(b"x"), "x.txt")},
        content_type="multipart/form-data").status_code == 401
