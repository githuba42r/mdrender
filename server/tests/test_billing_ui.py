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
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    return c


def test_billing_page_creates_plan_group_and_credit(config, db_path):
    app = _app(config)
    c = _admin(app)
    assert c.get("/billing").status_code == 200

    c.post("/billing/plans", data={"name": "Starter", "scope": "account",
                                   "price": "5", "interval": "month"})
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
                                   "price": "5"})
    with app.config["_db"].connect() as conn:
        plan = billing.list_plans(conn)[0]

    c.post(f"/billing/plans/{plan['plan_id']}", data={
        "name": "Pro", "scope": "account", "price": "9", "interval": "year"})
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


def test_default_group_accepts_a_plan_but_stays_fixed(config, db_path):
    app = _app(config)
    c = _admin(app)
    c.get("/billing?tab=groups")  # ensures the system group exists
    c.post("/billing/plans", data={"name": "Starter", "scope": "account"})
    with app.config["_db"].connect() as conn:
        plan_id = billing.list_plans(conn)[0]["plan_id"]
        group_id = billing.ensure_default_group(conn)

    c.post(f"/billing/groups/{group_id}/update",
           data={"name": "hacked", "plan_id": plan_id, "trial_days": "9"})
    with app.config["_db"].connect() as conn:
        group = billing.get_group(conn, group_id)
    assert group["plan_id"] == plan_id      # plan can be attached
    assert group["name"] == "system"        # but nothing else changes
    assert group["trial_days"] == 0


def test_plan_fields_and_credit_lives_on_the_users_page(config, db_path):
    app = _app(config)
    c = _admin(app)
    c.post("/billing/plans", data={
        "name": "Metered", "scope": "account", "price": "10",
        "included_messages": "100", "message_cost": "0.05",
        "storage_cost": "0.02", "storage_grace_days": "30",
        "max_messages_per_month": "500", "pending_mb": "5",
        "pending_expiry_hours": "48"})
    with app.config["_db"].connect() as conn:
        plan = billing.list_plans(conn)[0]
    assert plan["price_cents"] == 1000 and plan["included_messages"] == 100
    assert plan["message_cents_per_1000"] == 5 and plan["storage_cents_per_mb"] == 2
    assert plan["storage_grace_days"] == 30 and plan["max_messages_per_month"] == 500
    assert plan["max_pending_bytes"] == 5 * 1048576
    assert plan["pending_expiry_hours"] == 48

    # Plans tab: the new-plan control is a button opening a dialog, and money
    # renders in dollars (no cents when whole).
    plans_page = c.get("/billing?tab=plans").data
    assert b"data-new-plan" in plans_page and b'id="plan-dialog"' in plans_page
    assert b"$10" in plans_page and b"$0.05" in plans_page
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

    # The new-group control is a button opening a dialog; the inline Attach
    # action is gone from the list (plans are attached via the group editor).
    groups_page = c.get("/billing?tab=groups").data
    assert b"data-new-group" in groups_page and b'id="group-dialog"' in groups_page
    assert b">Attach<" not in groups_page

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


def test_server_plan_form_keeps_message_fees_and_hides_only_pending_storage(config, db_path):
    """The Message fees row applies to every plan type; only Pending,
    Storage fees and the Always-require-credit checkbox are account-only,
    so only those carry the class the scope toggle hides."""
    import re

    app = _app(config)
    page = _admin(app).get("/billing?tab=plans").data.decode()

    message_fees = re.search(
        r'<div class="([^"]*)">\s*<span class="row-title">Message fees:', page)
    assert message_fees, "the Message fees row is missing"
    assert "plan-account-only" not in message_fees.group(1)

    for title in ("Pending:", "Storage fees:"):
        row = re.search(
            r'<div class="([^"]*)">\s*<span class="row-title">' + title, page)
        assert row, f"the {title} row is missing"
        assert "plan-account-only" in row.group(1)
    # Two field rows plus the require-credit checkbox label.
    assert page.count("plan-account-only") == 3


def test_server_plan_stores_and_shows_message_fees(config, db_path):
    app = _app(config)
    c = _admin(app)
    c.post("/billing/plans", data={"name": "Host", "scope": "slave",
                                   "price": "20",
                                   "max_messages_per_month": "10000",
                                   "included_messages": "1000",
                                   "message_cost": "0.15"})
    with app.config["_db"].connect() as conn:
        plan = billing.list_plans(conn)[0]
    assert plan["scope"] == "slave"
    assert plan["included_messages"] == 1000
    assert plan["message_cents_per_1000"] == 15
    assert plan["max_messages_per_month"] == 10000

    # The plans table shows them for a server plan (storage/pending stay "—").
    row = next(r for r in c.get("/billing?tab=plans").data.decode().split("<tr>")
               if ">Host<" in r)
    cells = row.split("<td>")
    assert "server (host)" in cells[2]
    assert "1000" in cells[4] and "0.15" in cells[5]
    assert "&mdash;" in cells[6] and "&mdash;" in cells[8]


def test_editing_a_server_plan_leaves_absent_account_fields_untouched(config, db_path):
    """A hidden (disabled) field is not submitted; the update must treat an
    absent value as "leave as-is", while the visible message fees still land."""
    app = _app(config)
    c = _admin(app)
    c.post("/billing/plans", data={"name": "Old", "scope": "account",
                                   "price": "5", "pending_mb": "100",
                                   "pending_expiry_hours": "24",
                                   "storage_cost": "0.01", "storage_grace_days": "7"})
    with app.config["_db"].connect() as conn:
        plan = billing.list_plans(conn)[0]

    # Now switch it to a server plan without sending the account-only fields.
    c.post(f"/billing/plans/{plan['plan_id']}", data={
        "name": "Old", "scope": "slave", "price": "5",
        "max_messages_per_month": "5000",
        "included_messages": "500", "message_cost": "0.10"})
    with app.config["_db"].connect() as conn:
        plan = billing.get_plan(conn, plan["plan_id"])
    assert plan["scope"] == "slave"
    assert plan["included_messages"] == 500
    assert plan["message_cents_per_1000"] == 10
    # Absent inputs: unchanged, not zeroed.
    assert plan["max_pending_bytes"] == 100 * 1048576
    assert plan["pending_expiry_hours"] == 24
    assert plan["storage_cents_per_mb"] == 1
    assert plan["storage_grace_days"] == 7


def test_plan_details_dialog_marks_server_irrelevant_terms(config, db_path):
    """The info popup tags the storage/pending/expiry rows so app.js hides
    them for server (host) plans, and a zero message cap reads 'unlimited'."""
    app = _app(config)
    c = _admin(app)
    c.get("/billing?tab=groups")
    c.post("/billing/plans", data={"name": "Starter", "scope": "account"})
    c.post("/billing/groups", data={"name": "Beta"})
    with app.config["_db"].connect() as conn:
        plan_id = billing.list_plans(conn)[0]["plan_id"]
        groups = {gr["name"]: gr for gr in billing.list_groups(conn)}
    c.post(f"/billing/groups/{groups['Beta']['group_id']}/plan",
           data={"plan_id": plan_id})

    page = c.get("/billing?tab=groups").data.decode()
    # 4 <dt> + 4 <dd> rows: storage, grace, pending storage, pending expiry.
    assert page.count("pd-hide-for-server") == 8
    assert 'data-plan-max="unlimited"' in page

    js = os.path.join(os.path.dirname(__file__), "..", "app", "static",
                      "app.js")
    with open(js, encoding="utf-8") as fh:
        source = fh.read()
    assert "pd-hide-for-server" in source and "isServerPlan" in source
