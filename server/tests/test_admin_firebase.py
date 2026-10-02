# server/tests/test_admin_firebase.py
"""Bind-by-uid admin login: local fallback, Firebase linking, uniqueness."""
import os

from server.app import accounts, identity, oidc
from server.app.app import create_app


def _app(config, **overrides):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    config.IDENTITY_PROVIDER = "firebase"
    for key, value in overrides.items():
        setattr(config, key, value)
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _login(c):
    assert c.post("/admin-login",
                  data={"username": "admin", "password": "testpass"}).status_code == 302


def _admin_id(app, username="admin"):
    with app.config["_db"].connect() as conn:
        return identity.get_admin_by_username(conn, username)["admin_id"]


def _claims(sub="uid-adm", email="admin@example.com", **extra):
    data = {"sub": sub, "email": email, "email_verified": True}
    data.update(extra)
    return data


def _link(c, monkeypatch, admin_id, claims):
    monkeypatch.setattr(oidc, "verify_firebase_id_token",
                        lambda config, token, **k: claims)
    return c.post("/admins/link", json={"id_token": "x", "admin_id": admin_id})


def _firebase_signin(monkeypatch, app, claims, client=None):
    monkeypatch.setattr(oidc, "verify_firebase_id_token",
                        lambda config, token, **k: claims)
    return (client or app.test_client()).post("/auth/oidc", json={"id_token": "x"})


def test_admin_login_route_is_local_only(config, db_path):
    app = _app(config)
    c = app.test_client()
    page = c.get("/admin-login")
    assert page.status_code == 200
    assert b'name="username"' in page.data and b'name="password"' in page.data
    assert c.post("/admin-login",
                  data={"username": "admin", "password": "nope"}).status_code == 401
    assert c.post("/admin-login",
                  data={"username": "admin", "password": "testpass"}).status_code == 302


def test_local_credentials_stay_available_when_firebase_is_on(config, db_path):
    """Firebase admin login augments the password form, never replaces it:
    local credentials (the SERVER_PASSWORD bootstrap path) must still be
    typeable and submittable on the operator console."""
    app = _app(config, ADMIN_FIREBASE_LOGIN=True, FIREBASE_API_KEY="k",
               FIREBASE_AUTH_DOMAIN="proj.firebaseapp.com",
               FIREBASE_PROVIDERS="password")
    c = app.test_client()
    body = c.get("/admin-login").data

    assert b'action="/admin-login"' in body          # the local form
    assert b'name="username"' in body and b'name="password"' in body
    assert b"window.__FIREBASE__" in body            # ...plus the Firebase block
    assert body.index(b'action="/admin-login"') < body.index(b"window.__FIREBASE__")
    # It is a real <form> with a submit button, so Enter submits; the Firebase
    # fields sit outside any form, so they carry their own Enter handlers.
    assert b"onkeydown" in body
    # The operator console never advertises public account creation.
    assert b"/signup" not in body and b"Create one" not in body
    # Local password auth still works with Firebase enabled.
    assert c.post("/admin-login",
                  data={"username": "admin", "password": "testpass"}).status_code == 302


def test_link_then_seamless_firebase_admin_login(config, db_path, monkeypatch):
    app = _app(config, ADMIN_FIREBASE_LOGIN=True)
    c = app.test_client()
    _login(c)
    assert _link(c, monkeypatch, _admin_id(app), _claims()).status_code == 200

    # A fresh browser signs in purely via Firebase and lands as an admin.
    fresh = app.test_client()
    resp = _firebase_signin(monkeypatch, app, _claims(), client=fresh)
    assert resp.status_code == 200
    assert resp.get_json()["redirect"] == "/pushes"
    assert fresh.get("/admins").status_code == 200


def test_firebase_admin_login_off_does_not_elevate(config, db_path, monkeypatch):
    app = _app(config, ADMIN_FIREBASE_LOGIN=False)
    c = app.test_client()
    _login(c)
    assert _link(c, monkeypatch, _admin_id(app), _claims()).status_code == 200

    resp = _firebase_signin(monkeypatch, app, _claims())
    # Not elevated, and the admin identity cannot become a customer account.
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "email belongs to an administrator"


def test_link_rejects_an_email_that_is_a_customer_account(config, db_path, monkeypatch):
    app = _app(config, ADMIN_FIREBASE_LOGIN=True)
    c = app.test_client()
    _login(c)
    with app.config["_db"].connect() as conn:
        accounts.create_account(conn, "someone@example.com")
    resp = _link(c, monkeypatch, _admin_id(app),
                 _claims(email="someone@example.com"))
    assert resp.status_code == 409
    assert "user account" in resp.get_json()["error"]


def test_link_rejects_a_second_admin_sharing_the_identity(config, db_path, monkeypatch):
    app = _app(config, ADMIN_FIREBASE_LOGIN=True)
    c = app.test_client()
    _login(c)
    with app.config["_db"].connect() as conn:
        other_id = identity.create_admin(conn, "other", "longenough1",
                                         email="other@example.com")
    assert _link(c, monkeypatch, _admin_id(app), _claims()).status_code == 200
    assert _link(c, monkeypatch, other_id, _claims()).status_code == 409


def test_customer_cannot_take_an_admin_identity(config, db_path, monkeypatch):
    app = _app(config, ADMIN_FIREBASE_LOGIN=True)
    c = app.test_client()
    _login(c)
    assert _link(c, monkeypatch, _admin_id(app),
                 _claims(email="adm@example.com")).status_code == 200

    # The bound uid signs in as an admin.
    assert _firebase_signin(monkeypatch, app,
                            _claims(email="adm@example.com")).status_code == 200
    # A different uid claiming the same email cannot create a customer account.
    other = _firebase_signin(monkeypatch, app,
                             _claims(sub="uid-other", email="adm@example.com"))
    assert other.status_code == 403


def test_signup_offers_link_only_but_login_has_password(config, db_path):
    app = _app(config, FIREBASE_API_KEY="k", FIREBASE_AUTH_DOMAIN="proj.firebaseapp.com")
    signup = app.test_client().get("/signup")
    assert signup.status_code == 200
    assert b"Send create account link" in signup.data
    assert b"auth-password-panel" not in signup.data
    assert b"Sign in with a password instead" not in signup.data
    # The attach-email panel is available for phone-only sign-ins.
    assert b"auth-email-link-panel" in signup.data

    login = app.test_client().get("/admin-login")
    assert b"Send sign-in link" in login.data
    assert b"auth-password-panel" in login.data
    assert b"auth-email-link-panel" in login.data


def test_only_configured_providers_are_rendered(config, db_path):
    app = _app(config, FIREBASE_API_KEY="k", FIREBASE_AUTH_DOMAIN="proj.firebaseapp.com",
               FIREBASE_PROVIDERS="email_link")
    page = app.test_client().get("/admin-login").data
    assert b"Send sign-in link" in page
    assert b"mdrenderGoogle" not in page
    assert b"mdrenderGithub" not in page
    assert b"social-row" not in page
    assert b"auth-password-panel" not in page  # password not enabled
    assert b"phone-number" not in page and b"+61 400" not in page


def test_unlink_removes_the_binding(config, db_path, monkeypatch):
    app = _app(config, ADMIN_FIREBASE_LOGIN=True)
    c = app.test_client()
    _login(c)
    admin_id = _admin_id(app)
    assert _link(c, monkeypatch, admin_id, _claims()).status_code == 200
    assert c.post(f"/admins/{admin_id}/unlink").status_code == 303
    with app.config["_db"].connect() as conn:
        assert identity.get_admin(conn, admin_id)["firebase_uid"] is None
