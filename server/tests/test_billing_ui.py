# server/tests/test_billing_ui.py
"""Billing admin UI: Plans and Groups tabs, edit/activate, group plans."""
import os

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
    assert c.get("/billing").status_code == 303  # session-gated
    c.post("/login", data={"username": "admin", "password": "testpass"})
    return c


def test_billing_page_creates_plan_group_and_credit(config, db_path):
    app = _app(config)
    c = _admin(app)
    assert c.get("/billing").status_code == 200

    c.post("/billing/plans", data={"name": "Starter", "scope": "account",
                                   "price_cents": "500", "interval": "month"})
    c.post("/billing/groups", data={"name": "Beta"})
    c.post("/billing/credit", data={"account_type": "account",
                                    "account_id": "acct-1",
                                    "amount_cents": "1000", "reason": "manual"})

    assert b"Starter" in c.get("/billing?tab=plans").data
    assert b"Beta" in c.get("/billing?tab=groups").data
    with app.config["_db"].connect() as conn:
        assert billing.balance(conn, "account", "acct-1") == 1000


def test_plan_can_be_edited_and_toggled(config, db_path):
    app = _app(config)
    c = _admin(app)
    c.post("/billing/plans", data={"name": "Starter", "scope": "account",
                                   "price_cents": "500"})
    with app.config["_db"].connect() as conn:
        plan = billing.list_plans(conn)[0]

    c.post(f"/billing/plans/{plan['plan_id']}", data={
        "name": "Pro", "scope": "account", "price_cents": "900", "interval": "year"})
    with app.config["_db"].connect() as conn:
        plan = billing.get_plan(conn, plan["plan_id"])
    assert plan["name"] == "Pro" and plan["price_cents"] == 900 and plan["interval"] == "year"

    # Deactivate then reactivate.
    c.post(f"/billing/plans/{plan['plan_id']}/active", data={"active": "0"})
    with app.config["_db"].connect() as conn:
        assert billing.get_plan(conn, plan["plan_id"])["active"] == 0
    c.post(f"/billing/plans/{plan['plan_id']}/active", data={"active": "1"})
    with app.config["_db"].connect() as conn:
        assert billing.get_plan(conn, plan["plan_id"])["active"] == 1


def test_plan_fields_and_credit_lives_on_the_users_page(config, db_path):
    app = _app(config)
    c = _admin(app)
    c.post("/billing/plans", data={
        "name": "Metered", "scope": "account", "price_cents": "1000",
        "message_cents_per_1000": "5", "storage_cents_per_mb": "2",
        "storage_grace_days": "30", "max_messages_per_month": "500"})
    with app.config["_db"].connect() as conn:
        plan = billing.list_plans(conn)[0]
    assert plan["price_cents"] == 1000
    assert plan["message_cents_per_1000"] == 5 and plan["storage_cents_per_mb"] == 2
    assert plan["storage_grace_days"] == 30 and plan["max_messages_per_month"] == 500

    # Plans tab: the new-plan control is a button opening a dialog.
    plans_page = c.get("/billing?tab=plans").data
    assert b"data-new-plan" in plans_page and b'id="plan-dialog"' in plans_page
    # Credit is added from a user's row, not a billing section.
    assert b'action="/billing/credit"' not in c.get("/billing?tab=plans").data
    c.post("/accounts", data={"email": "user@example.com", "password": "longenough1"})
    users = c.get("/accounts").data
    assert b"data-add-credit" in users and b'id="credit-dialog"' in users


def test_groups_attach_plans_and_default_group_is_fixed(config, db_path):
    app = _app(config)
    c = _admin(app)
    c.get("/billing?tab=groups")  # ensures the default group exists
    c.post("/billing/plans", data={"name": "Starter", "scope": "account"})
    c.post("/billing/groups", data={"name": "Beta"})

    with app.config["_db"].connect() as conn:
        plan_id = billing.list_plans(conn)[0]["plan_id"]
        groups = {gr["name"]: gr for gr in billing.list_groups(conn)}
    assert "system" in groups  # default group is named system
    default_id = groups["system"]["group_id"]
    beta_id = groups["Beta"]["group_id"]

    # Attach a plan to a normal group.
    c.post(f"/billing/groups/{beta_id}/plan", data={"plan_id": plan_id})
    with app.config["_db"].connect() as conn:
        assert billing.get_group(conn, beta_id)["plan_id"] == plan_id

    # Rename and delete a normal group.
    c.post(f"/billing/groups/{beta_id}/rename", data={"name": "Beta 2"})
    with app.config["_db"].connect() as conn:
        assert billing.get_group(conn, beta_id)["name"] == "Beta 2"
    c.post(f"/billing/groups/{beta_id}/delete")
    with app.config["_db"].connect() as conn:
        assert billing.get_group(conn, beta_id) is None

    # The system group cannot be renamed or removed.
    c.post(f"/billing/groups/{default_id}/rename", data={"name": "nope"})
    c.post(f"/billing/groups/{default_id}/delete")
    with app.config["_db"].connect() as conn:
        default = billing.get_group(conn, default_id)
    assert default is not None and default["name"] == "system" and default["is_default"] == 1
