# server/tests/test_billing_ui.py
"""Billing admin UI (Phase H)."""
import os

from server.app import billing
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def test_billing_page_creates_plan_group_and_credit(config, db_path):
    app = _app(config)
    c = app.test_client()
    assert c.get("/billing").status_code == 303  # session-gated
    c.post("/login", data={"username": "admin", "password": "testpass"})
    assert c.get("/billing").status_code == 200

    c.post("/billing/plans", data={"name": "Starter", "scope": "account",
                                   "price_cents": "500", "interval": "month"})
    c.post("/billing/groups", data={"name": "Beta"})
    c.post("/billing/credit", data={"account_type": "account",
                                    "account_id": "acct-1",
                                    "amount_cents": "1000", "reason": "manual"})

    body = c.get("/billing").data
    assert b"Starter" in body and b"Beta" in body
    with app.config["_db"].connect() as conn:
        assert billing.balance(conn, "account", "acct-1") == 1000
