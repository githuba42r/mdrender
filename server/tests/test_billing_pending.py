# server/tests/test_billing_pending.py
"""Plan pending-storage cap and expiry, enforced on push."""
import base64
import io
import os

from server.app import accounts, auth, billing, billing_worker, push_store, store
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _account_with_plan(app, config, *, max_pending_bytes=0, expiry_hours=0):
    with app.config["_db"].connect() as conn:
        plan_id = billing.create_plan(
            conn, "plan", billing.SCOPE_ACCOUNT,
            max_pending_bytes=max_pending_bytes, pending_expiry_hours=expiry_hours)
        group_id = billing.ensure_default_group(conn)
        billing.set_group_plan(conn, group_id, plan_id)
        account_id = accounts.create_account(conn, "u@example.com")
        push_key = base64.b64encode(b"\x00" * 32).decode()
        conn.execute(
            "INSERT INTO devices (device_secret, device_auth, device_name, fcm_token,"
            " public_key, push_key, registered_at, last_seen, account_id)"
            " VALUES ('sec','auth','Dev','tok','PUB',?,1,1,?)", (push_key, account_id))
        conn.commit()
        client_id = store.create_client(conn, "cli", "hash")
    return account_id, auth.issue_access_token(config, client_id)


def _push(app, token, data=b"hello"):
    return app.test_client().post(
        "/api/push", headers={"Authorization": f"Bearer {token}"},
        data={"target_device": "Dev", "file": (io.BytesIO(data), "f.txt")},
        content_type="multipart/form-data")


def test_pending_policy_comes_from_the_plan(config, db_path):
    app = _app(config)
    with app.config["_db"].connect() as conn:
        plan_id = billing.create_plan(conn, "small", billing.SCOPE_ACCOUNT,
                                      max_pending_bytes=1048576,
                                      pending_expiry_hours=0)
        group_id = billing.ensure_default_group(conn)
        billing.set_group_plan(conn, group_id, plan_id)
        account_id = accounts.create_account(conn, "u@example.com")
        policy = billing.pending_policy(conn, config, account_id)
    assert policy["max_bytes"] == 1048576
    assert policy["expiry_hours"] is None  # 0 = never expire


def test_push_over_the_plan_cap_is_rejected(config, db_path):
    app = _app(config)
    account_id, token = _account_with_plan(app, config, max_pending_bytes=4)
    resp = _push(app, token, data=b"hello")  # 5 bytes > 4
    assert resp.status_code == 507
    with app.config["_db"].connect() as conn:
        assert push_store.pending_bytes(conn, account_id) == 0


def test_push_within_the_cap_is_accepted(config, db_path):
    app = _app(config)
    account_id, token = _account_with_plan(app, config, max_pending_bytes=100)
    assert _push(app, token, data=b"hello").status_code == 200
    with app.config["_db"].connect() as conn:
        assert push_store.pending_bytes(conn, account_id) == 5


def test_sweep_expires_pending_files_past_the_plan_window(config, db_path):
    app = _app(config)
    with app.config["_db"].connect() as conn:
        plan_id = billing.create_plan(conn, "p", billing.SCOPE_ACCOUNT,
                                      pending_expiry_hours=1)
        group_id = billing.ensure_default_group(conn)
        billing.set_group_plan(conn, group_id, plan_id)
        account_id = accounts.create_account(conn, "u@example.com")
        push_store.create_push(conn, "p1", "Dev", account_id=account_id)
        push_store.add_file(conn, file_id="f1", push_id="p1", file_name="a",
                            file_path="", size=1, retrieval_key="rk",
                            stored_path=None, created_at=1)
        removed = billing_worker.sweep_pending_expiry(conn, config, now=1 + 2 * 3600)
        assert removed == 1
        assert push_store.pending_bytes(conn, account_id) == 0
