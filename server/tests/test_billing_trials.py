# server/tests/test_billing_trials.py
"""Expiring (trial) groups: create/edit and the expiry sweep."""
import os
import time

from server.app import billing
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _admin(app):
    c = app.test_client()
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    return c


def test_group_can_be_created_as_a_trial_and_edited(config, db_path):
    app = _app(config)
    c = _admin(app)
    with app.config["_db"].connect() as conn:
        standard_id = billing.create_group(conn, "standard")

    c.post("/billing/groups", data={"name": "trial", "trial_days": "7",
                                    "next_group_id": standard_id})
    with app.config["_db"].connect() as conn:
        trial = next(g for g in billing.list_groups(conn) if g["name"] == "trial")
    assert trial["trial_days"] == 7 and trial["next_group_id"] == standard_id

    c.post(f"/billing/groups/{trial['group_id']}/update",
           data={"name": "trial", "trial_days": "14", "next_group_id": standard_id})
    with app.config["_db"].connect() as conn:
        assert billing.get_group(conn, trial["group_id"])["trial_days"] == 14


def test_expired_trial_moves_account_to_the_next_group(config, db_path):
    app = _app(config)
    with app.config["_db"].connect() as conn:
        standard_id = billing.create_group(conn, "standard")
        trial_id = billing.create_group(conn, "trial", trial_days=7,
                                        next_group_id=standard_id)
        billing.assign_account_group(conn, "account", "acct-1", trial_id)
        # Not yet expired.
        assert billing.sweep_trial_groups(conn) == 0
        assert _group_of(conn, "acct-1") == trial_id

        # Backdate the assignment past the trial window.
        conn.execute("UPDATE account_groups SET assigned_at = ?"
                     " WHERE account_type='account' AND account_id='acct-1'",
                     (int(time.time()) - 8 * 86400,))
        conn.commit()
        assert billing.sweep_trial_groups(conn) == 1
        assert _group_of(conn, "acct-1") == standard_id


def test_reassignment_keeps_the_trial_clock_within_a_group(config, db_path):
    app = _app(config)
    with app.config["_db"].connect() as conn:
        trial_id = billing.create_group(conn, "trial", trial_days=7,
                                        next_group_id=billing.ensure_default_group(conn))
        billing.assign_account_group(conn, "account", "acct-1", trial_id)
        first = _assigned_at(conn, "acct-1")
        billing.assign_account_group(conn, "account", "acct-1", trial_id)  # no-op
        assert _assigned_at(conn, "acct-1") == first


def _group_of(conn, account_id):
    return conn.execute("SELECT group_id FROM account_groups WHERE account_type='account'"
                        " AND account_id = ?", (account_id,)).fetchone()["group_id"]


def _assigned_at(conn, account_id):
    return conn.execute("SELECT assigned_at FROM account_groups WHERE account_type='account'"
                        " AND account_id = ?", (account_id,)).fetchone()["assigned_at"]
