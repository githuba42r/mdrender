# server/tests/test_billing_metering.py
"""Metered storage charging (Phase H)."""
import time

from server.app import billing, storage
from server.app.db import Database


def _setup(db_path):
    db = Database(db_path)
    conn = db.connect()
    db.init_schema(conn)
    return conn


def test_bill_storage_charges_per_mb_once_per_interval(config, db_path):
    conn = _setup(db_path)
    plan = billing.create_plan(conn, "Metered", billing.SCOPE_ACCOUNT,
                               storage_cents_per_mb=5)
    billing.set_group_plan(conn, billing.ensure_default_group(conn), plan)
    storage.add_file(conn, file_id="f1", account_id="acct-1",
                     size=2 * 1024 * 1024, stored_path="/tmp/x", created_at=1)

    now = int(time.time())
    assert billing.bill_storage(conn, config, now=now) == ["acct-1"]
    assert billing.balance(conn, "account", "acct-1") == -10  # 2 MB * 5c

    # Within the minimum interval it does not charge again.
    assert billing.bill_storage(conn, config, now=now + 60) == []
    conn.close()


def test_bill_storage_skips_recent_files(config, db_path):
    conn = _setup(db_path)
    plan = billing.create_plan(conn, "Metered", billing.SCOPE_ACCOUNT,
                               storage_cents_per_mb=5)
    billing.set_group_plan(conn, billing.ensure_default_group(conn), plan)
    storage.add_file(conn, file_id="f1", account_id="acct-1", size=1024,
                     stored_path="/tmp/x")  # created now

    assert billing.bill_storage(conn, config, now=int(time.time())) == []
    conn.close()
