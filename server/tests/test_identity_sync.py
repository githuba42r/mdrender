# server/tests/test_identity_sync.py
"""Two-way write-through between the account profile and Firebase Auth."""
import os

from server.app import accounts, identity_admin, oidc
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    config.IDENTITY_PROVIDER = "firebase"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _account(app, email="user@example.com", uid=None):
    c = app.test_client()
    c.post("/signup", data={"email": email, "password": "longenough1"})
    c.post("/account/login", data={"email": email, "password": "longenough1"})
    with app.config["_db"].connect() as conn:
        account = accounts.get_account_by_email(conn, email)
        if uid:
            accounts.set_firebase_uid(conn, account["account_id"], uid)
    return c, account["account_id"]


def test_oidc_binds_the_firebase_uid(config, db_path, monkeypatch):
    app = _app(config)
    c = app.test_client()
    monkeypatch.setattr(oidc, "verify_firebase_id_token",
                        lambda config, token, **k: {"sub": "uid-9",
                                                    "email": "new@example.com",
                                                    "email_verified": True})
    assert c.post("/auth/oidc", json={"id_token": "x"}).status_code == 200
    with app.config["_db"].connect() as conn:
        assert accounts.get_account_by_firebase_uid(conn, "uid-9") is not None


def test_profile_writes_through_to_firebase(config, db_path, monkeypatch):
    app = _app(config)
    c, account_id = _account(app, uid="uid-1")
    calls = []
    monkeypatch.setattr(identity_admin, "available", lambda config: True)
    monkeypatch.setattr(identity_admin, "update_user",
                        lambda config, uid, **f: (calls.append((uid, f)), ({"localId": uid}, None))[1])

    resp = c.post("/account/profile", data={
        "name": "Sam", "email": "sam@example.com", "phone": "+61400000000",
        "password": "newpassword1", "password_confirm": "newpassword1"})
    assert resp.status_code == 303
    uid, fields = calls[0]
    assert uid == "uid-1"
    assert fields["email"] == "sam@example.com" and fields["emailVerified"] is False
    assert fields["phoneNumber"] == "+61400000000"
    assert fields["displayName"] == "Sam"
    assert fields["password"] == "newpassword1"

    with app.config["_db"].connect() as conn:
        account = accounts.get_account(conn, account_id)
    assert account["email"] == "sam@example.com" and account["phone"] == "+61400000000"


def test_identity_change_blocks_when_firebase_rejects(config, db_path, monkeypatch):
    app = _app(config)
    c, account_id = _account(app, uid="uid-1")
    monkeypatch.setattr(identity_admin, "available", lambda config: True)
    monkeypatch.setattr(identity_admin, "update_user",
                        lambda config, uid, **f: (None, "PERMISSION_DENIED"))

    resp = c.post("/account/profile", data={
        "name": "x", "email": "sam@example.com", "phone": ""})
    assert resp.status_code == 502
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["email"] == "user@example.com"


def test_profile_rejects_a_non_e164_phone(config, db_path, monkeypatch):
    app = _app(config)
    c, _ = _account(app, uid="uid-1")
    monkeypatch.setattr(identity_admin, "available", lambda config: True)
    called = []
    monkeypatch.setattr(identity_admin, "update_user",
                        lambda config, uid, **f: (called.append(f), ({}, None))[1])
    resp = c.post("/account/profile", data={
        "name": "", "email": "user@example.com", "phone": "0447546890"})
    assert resp.status_code == 400 and not called


def test_name_only_change_is_best_effort(config, db_path, monkeypatch):
    app = _app(config)
    c, account_id = _account(app, uid="uid-1")
    monkeypatch.setattr(identity_admin, "available", lambda config: True)
    monkeypatch.setattr(identity_admin, "update_user",
                        lambda config, uid, **f: (None, "PERMISSION_DENIED"))

    resp = c.post("/account/profile", data={
        "name": "Renamed", "email": "user@example.com", "phone": ""})
    assert resp.status_code == 303
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["name"] == "Renamed"
