# server/tests/test_upload.py
"""Account upload API with quota enforcement (Phase F)."""
import io
import os

from server.app import accounts, billing, storage
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


def test_billing_enforcement_gates_metered_uploads_on_credit(config, db_path):
    config.BILLING_ENFORCEMENT = True
    app = _app(config)
    c = _account_client(app)
    with app.config["_db"].connect() as conn:
        account_id = conn.execute(
            "SELECT account_id FROM accounts").fetchone()["account_id"]
        plan = billing.create_plan(conn, "Metered", billing.SCOPE_ACCOUNT,
                                   storage_cents_per_mb=100)
        billing.set_account_plan(conn, billing.SCOPE_ACCOUNT, account_id, plan)

    # Storage-metered plan + no credit: refused.
    blocked = c.post("/api/account/upload", data={"file": (io.BytesIO(b"x"), "x.txt")},
                     content_type="multipart/form-data")
    assert blocked.status_code == 402
    assert blocked.get_json()["error"] == "payment required"

    with app.config["_db"].connect() as conn:
        billing.add_credit(conn, billing.SCOPE_ACCOUNT, account_id, 500,
                           reason="topup")

    ok = c.post("/api/account/upload", data={"file": (io.BytesIO(b"x"), "x.txt")},
                content_type="multipart/form-data")
    assert ok.status_code == 200

    # Spending the balance back to zero blocks the next upload again.
    with app.config["_db"].connect() as conn:
        billing.debit(conn, billing.SCOPE_ACCOUNT, account_id, 500,
                      reason="storage")
    again = c.post("/api/account/upload", data={"file": (io.BytesIO(b"x"), "x.txt")},
                   content_type="multipart/form-data")
    assert again.status_code == 402


def test_zero_rate_plans_are_exempt_from_the_upload_gate(config, db_path):
    config.BILLING_ENFORCEMENT = True
    app = _app(config)
    c = _account_client(app)
    with app.config["_db"].connect() as conn:
        account_id = conn.execute(
            "SELECT account_id FROM accounts").fetchone()["account_id"]

    def upload():
        return c.post("/api/account/upload",
                      data={"file": (io.BytesIO(b"x"), "x.txt")},
                      content_type="multipart/form-data")

    # No plan at all: nothing meters storage, so no credit is required.
    assert upload().status_code == 200

    # An explicit zero-storage plan (message rates only) is exempt too.
    with app.config["_db"].connect() as conn:
        free = billing.create_plan(conn, "MsgOnly", billing.SCOPE_ACCOUNT,
                                   message_cents_per_1000=500)
        billing.set_account_plan(conn, billing.SCOPE_ACCOUNT, account_id, free)
    assert upload().status_code == 200

    # Flip the storage rate on and the gate binds immediately.
    with app.config["_db"].connect() as conn:
        metered = billing.create_plan(conn, "Metered", billing.SCOPE_ACCOUNT,
                                      storage_cents_per_mb=100)
        billing.set_account_plan(conn, billing.SCOPE_ACCOUNT, account_id, metered)
    assert upload().status_code == 402


def test_upload_rejects_files_more_expensive_than_credit(config, db_path):
    config.BILLING_ENFORCEMENT = True
    app = _app(config)
    c = _account_client(app)
    with app.config["_db"].connect() as conn:
        account_id = conn.execute(
            "SELECT account_id FROM accounts").fetchone()["account_id"]
        plan = billing.create_plan(conn, "Metered", billing.SCOPE_ACCOUNT,
                                   storage_cents_per_mb=100)
        billing.set_account_plan(conn, billing.SCOPE_ACCOUNT, account_id, plan)
        billing.add_credit(conn, billing.SCOPE_ACCOUNT, account_id, 250,
                           reason="topup")

    # 3 MB at 100c/MB = 300c > the 250c credit: refused, naming the file.
    big = c.post("/api/account/upload",
                 data={"file": (io.BytesIO(b"x" * (3 * 1024 * 1024)), "big.bin")},
                 content_type="multipart/form-data")
    assert big.status_code == 402
    detail = big.get_json()["detail"]
    assert "big.bin" in detail and "300" in detail and "250" in detail

    # A file that fits inside the credit still uploads.
    small = c.post("/api/account/upload",
                   data={"file": (io.BytesIO(b"x" * 1024), "small.txt")},
                   content_type="multipart/form-data")
    assert small.status_code == 200


def test_upload_requires_an_account_session(config, db_path):
    app = _app(config)
    assert app.test_client().post("/api/account/upload", data={
        "file": (io.BytesIO(b"x"), "x.txt")},
        content_type="multipart/form-data").status_code == 401
