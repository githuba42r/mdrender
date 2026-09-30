# server/tests/test_billing_affiliate.py
"""Affiliate codes route new signups into a group."""
import os

from server.app import accounts, billing, oidc
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _group_of(app, email):
    with app.config["_db"].connect() as conn:
        row = accounts.get_account_by_email(conn, email)
        if row is None:
            return None
        ag = conn.execute("SELECT group_id FROM account_groups WHERE account_type='account'"
                          " AND account_id = ?", (row["account_id"],)).fetchone()
        return ag["group_id"] if ag else None


def _make_group(app, name, code, enabled=1):
    with app.config["_db"].connect() as conn:
        return billing.create_group(conn, name, affiliate_code=code,
                                    affiliate_enabled=enabled)


def test_group_affiliate_code_is_matched_case_insensitively(config, db_path):
    app = _app(config)
    group_id = _make_group(app, "partner", "PARTNER10")
    with app.config["_db"].connect() as conn:
        found = billing.group_by_affiliate(conn, "partner10")
    assert found is not None and found["group_id"] == group_id


def test_signup_with_affiliate_field_joins_the_group(config, db_path):
    app = _app(config)
    group_id = _make_group(app, "partner", "PARTNER10")
    app.test_client().post("/signup", data={
        "email": "a@example.com", "password": "longenough1",
        "affiliate_code": "PARTNER10"})
    assert _group_of(app, "a@example.com") == group_id


def test_signup_with_affiliate_query_joins_the_group(config, db_path):
    app = _app(config)
    group_id = _make_group(app, "partner", "PARTNER10")
    app.test_client().post("/signup?affiliate=PARTNER10", data={
        "email": "b@example.com", "password": "longenough1"})
    assert _group_of(app, "b@example.com") == group_id


def test_signup_without_or_unknown_code_has_no_group(config, db_path):
    app = _app(config)
    _make_group(app, "partner", "PARTNER10")
    client = app.test_client()
    client.post("/signup", data={"email": "c@example.com", "password": "longenough1"})
    client.post("/signup", data={"email": "d@example.com", "password": "longenough1",
                                 "affiliate_code": "NOPE"})
    assert _group_of(app, "c@example.com") is None
    assert _group_of(app, "d@example.com") is None


def test_disabled_affiliate_code_is_ignored(config, db_path):
    app = _app(config)
    _make_group(app, "partner", "PARTNER10", enabled=0)
    app.test_client().post("/signup", data={
        "email": "e@example.com", "password": "longenough1",
        "affiliate_code": "PARTNER10"})
    assert _group_of(app, "e@example.com") is None


def test_oidc_signup_uses_the_affiliate_code(config, db_path, monkeypatch):
    config.IDENTITY_PROVIDER = "firebase"
    app = _app(config)
    group_id = _make_group(app, "partner", "PARTNER10")
    monkeypatch.setattr(oidc, "verify_firebase_id_token",
                        lambda config, token, **k: {"sub": "uid-1",
                                                    "email": "new@example.com",
                                                    "email_verified": True})
    resp = app.test_client().post("/auth/oidc", json={
        "id_token": "x", "affiliate_code": "PARTNER10"})
    assert resp.status_code == 200
    assert _group_of(app, "new@example.com") == group_id
