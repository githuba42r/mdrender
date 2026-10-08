# server/tests/test_firebase_link.py
"""Binding a local account to Firebase: profile buttons, POST /account/link,
and the Users page's "Sync to Firebase" action."""
import os
import urllib.parse

from server.app import accounts, identity, identity_admin, oidc
from server.app.app import create_app


def _app(config, **overrides):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    for key, value in overrides.items():
        setattr(config, key, value)
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _firebase(config):
    return {"IDENTITY_PROVIDER": "firebase", "FIREBASE_API_KEY": "k",
            "FIREBASE_AUTH_DOMAIN": "proj.firebaseapp.com",
            "FIREBASE_PROJECT_ID": "proj",
            "FIREBASE_PROVIDERS": "google,password,email_link"}


def _account(app, email="user@example.com", password="longenough1", uid=None):
    with app.config["_db"].connect() as conn:
        account_id = accounts.create_account(conn, email, password)
        if uid:
            accounts.set_firebase_uid(conn, account_id, uid)
    client = app.test_client()
    client.post("/login", data={"email": email, "password": password})
    return client, account_id


def _claims(sub="uid-1", email="user@example.com", **extra):
    data = {"sub": sub, "email": email, "email_verified": True}
    data.update(extra)
    return data


# ---- Profile page ----------------------------------------------------------

def test_profile_offers_linking_when_firebase_is_enabled(config, db_path):
    app = _app(config, **_firebase(config))
    client, _ = _account(app)
    page = client.get("/account/profile").data

    assert b"Sign-in linking" in page
    assert b"Not yet linked to a sign-in account." in page
    # The block runs in link mode: the token goes to /account/link, and the
    # configured providers (here: Google) are offered as link buttons.
    assert b'window.__FIREBASE_ACTION__ = "link"' in page
    assert b"mdrenderGoogle()" in page
    assert b"Send link" in page


def test_profile_shows_the_linked_status(config, db_path):
    app = _app(config, **_firebase(config))
    client, _ = _account(app, uid="uid-linked")
    page = client.get("/account/profile").data
    assert b"Linked to your sign-in account." in page
    assert b"Sign-in account linked." not in page  # until ?linked=1


def test_profile_lists_linked_methods_and_disables_google(config, db_path,
                                                          monkeypatch):
    app = _app(config, **_firebase(config))
    client, _ = _account(app, uid="uid-linked")
    monkeypatch.setattr(identity_admin, "available", lambda config: True)
    monkeypatch.setattr(identity_admin, "get_user", lambda config, uid: ({
        "localId": uid,
        "providerData": [
            {"providerId": "google.com", "email": "philg@gmail.com"},
            {"providerId": "password", "email": "user@example.com"},
        ]}, None))
    page = client.get("/account/profile").data

    # Each method Firebase holds is listed with its address.
    assert b"philg@gmail.com" in page
    assert b"Email &amp; password" in page
    # Google is already linked: its link button is disabled, not offered.
    assert b"mdrenderGoogle()" not in page
    assert b"Google &middot; linked" in page


def test_profile_falls_back_when_the_lookup_fails(config, db_path,
                                                  monkeypatch):
    app = _app(config, **_firebase(config))
    client, _ = _account(app, uid="uid-linked")
    monkeypatch.setattr(identity_admin, "available", lambda config: True)
    monkeypatch.setattr(identity_admin, "get_user",
                        lambda config, uid: (None, "PERMISSION_DENIED"))
    page = client.get("/account/profile").data
    # The plain wording stands in, and the social buttons stay usable — the
    # already-linked message from Firebase covers a late double-link attempt.
    assert b"Linked to your sign-in account." in page
    assert b"mdrenderGoogle()" in page


def test_profile_hides_linking_without_firebase(config, db_path):
    app = _app(config)  # local provider, as on the slave
    client, _ = _account(app)
    page = client.get("/account/profile").data
    assert b"Sign-in linking" not in page
    assert b"__FIREBASE_ACTION__" not in page


# ---- POST /account/link ----------------------------------------------------

def test_account_link_needs_a_session(config, db_path):
    app = _app(config, **_firebase(config))
    resp = app.test_client().post("/account/link", json={"id_token": "x"})
    assert resp.status_code == 401


def test_account_link_binds_the_uid(config, db_path, monkeypatch):
    app = _app(config, **_firebase(config))
    client, account_id = _account(app)
    monkeypatch.setattr(oidc, "verify_firebase_id_token",
                        lambda config, token, **k: _claims())
    resp = client.post("/account/link", json={"id_token": "x"})
    assert resp.status_code == 200 and resp.get_json()["uid"] == "uid-1"
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["firebase_uid"] == "uid-1"


def test_account_link_rejects_a_bad_token(config, db_path, monkeypatch):
    app = _app(config, **_firebase(config))
    client, account_id = _account(app)
    monkeypatch.setattr(oidc, "verify_firebase_id_token",
                        lambda config, token, **k: None)
    assert client.post("/account/link", json={"id_token": "x"}).status_code == 401
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["firebase_uid"] is None


def test_account_link_refuses_an_identity_bound_elsewhere(config, db_path, monkeypatch):
    app = _app(config, **_firebase(config))
    client, account_id = _account(app)
    with app.config["_db"].connect() as conn:
        other = accounts.create_account(conn, "other@example.com")
        accounts.set_firebase_uid(conn, other, "uid-1")
    monkeypatch.setattr(oidc, "verify_firebase_id_token",
                        lambda config, token, **k: _claims())
    assert client.post("/account/link", json={"id_token": "x"}).status_code == 409
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["firebase_uid"] is None


def test_account_link_refuses_an_admin_identity(config, db_path, monkeypatch):
    app = _app(config, **_firebase(config))
    client, account_id = _account(app)
    with app.config["_db"].connect() as conn:
        admin_id = identity.get_admin_by_username(conn, "admin")["admin_id"]
        identity.link_firebase(conn, admin_id, uid="uid-1")
    monkeypatch.setattr(oidc, "verify_firebase_id_token",
                        lambda config, token, **k: _claims())
    assert client.post("/account/link", json={"id_token": "x"}).status_code == 409
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["firebase_uid"] is None


def test_account_link_refuses_a_foreign_email(config, db_path, monkeypatch):
    app = _app(config, **_firebase(config))
    client, account_id = _account(app)
    with app.config["_db"].connect() as conn:
        accounts.create_account(conn, "taken@example.com")
    monkeypatch.setattr(oidc, "verify_firebase_id_token",
                        lambda config, token, **k: _claims(email="taken@example.com"))
    assert client.post("/account/link", json={"id_token": "x"}).status_code == 409


def test_account_link_ignores_an_unverified_email(config, db_path, monkeypatch):
    """A token without a verified email still binds by uid; the account's own
    email is not treated as a foreign claim."""
    app = _app(config, **_firebase(config))
    client, account_id = _account(app)
    monkeypatch.setattr(
        oidc, "verify_firebase_id_token",
        lambda config, token, **k: _claims(email_verified=False))
    resp = client.post("/account/link", json={"id_token": "x"})
    assert resp.status_code == 200
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["firebase_uid"] == "uid-1"


# ---- Users page: indicator + Sync to Firebase ------------------------------

def _admin_client(app):
    client = app.test_client()
    assert client.post("/admin-login", data={
        "username": "admin", "password": "testpass"}).status_code == 302
    return client


def test_users_page_offers_sync_when_firebase_is_enabled(config, db_path, monkeypatch):
    monkeypatch.setattr(identity_admin, "available", lambda config: True)
    app = _app(config, **_firebase(config))
    with app.config["_db"].connect() as conn:
        accounts.create_account(conn, "pat@example.com")
        accounts.create_account(conn, "linked@example.com")
    with app.config["_db"].connect() as conn:
        linked = accounts.get_account_by_email(conn, "linked@example.com")
        accounts.set_firebase_uid(conn, linked["account_id"], "uid-pat")
    page = _admin_client(app).get("/accounts").data
    assert b"<th>Sign-in</th>" in page
    assert page.count(b"not linked") == 1
    assert page.count(b"Sync to Firebase") == 1
    assert b"linked</span>" in page


def test_users_page_hides_the_column_without_firebase(config, db_path):
    app = _app(config)  # local provider (slave): no linking column at all
    page = _admin_client(app).get("/accounts").data
    assert b"<th>Sign-in</th>" not in page
    assert b"Sync to Firebase" not in page


def test_sync_creates_the_firebase_user_and_binds_it(config, db_path, monkeypatch):
    monkeypatch.setattr(identity_admin, "available", lambda config: True)
    monkeypatch.setattr(identity_admin, "get_user_by_email",
                        lambda config, email: (None, None))
    monkeypatch.setattr(identity_admin, "create_user",
                        lambda config, **fields: ("uid-new", None))
    app = _app(config, **_firebase(config))
    client = _admin_client(app)
    with app.config["_db"].connect() as conn:
        account_id = accounts.create_account(conn, "new@example.com")

    resp = client.post(f"/accounts/{account_id}/sync-firebase")
    assert resp.status_code == 303 and resp.headers["Location"] == "/accounts"
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["firebase_uid"] == "uid-new"


def test_sync_adopts_an_existing_firebase_user(config, db_path, monkeypatch):
    monkeypatch.setattr(identity_admin, "available", lambda config: True)
    monkeypatch.setattr(identity_admin, "get_user_by_email",
                        lambda config, email: ({"localId": "uid-existing"}, None))
    created = []
    monkeypatch.setattr(
        identity_admin, "create_user",
        lambda config, **fields: created.append(fields) or ("uid-x", None))
    app = _app(config, **_firebase(config))
    client = _admin_client(app)
    with app.config["_db"].connect() as conn:
        account_id = accounts.create_account(conn, "existing@example.com")

    resp = client.post(f"/accounts/{account_id}/sync-firebase")
    assert resp.status_code == 303 and resp.headers["Location"] == "/accounts"
    assert created == []  # the existing user is bound, not duplicated
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["firebase_uid"] == "uid-existing"


def test_sync_reports_provider_errors_and_stays_unlinked(config, db_path, monkeypatch):
    monkeypatch.setattr(identity_admin, "available", lambda config: True)
    monkeypatch.setattr(identity_admin, "get_user_by_email",
                        lambda config, email: (None, None))
    monkeypatch.setattr(identity_admin, "create_user",
                        lambda config, **fields: (None, "EMAIL_EXISTS"))
    app = _app(config, **_firebase(config))
    client = _admin_client(app)
    with app.config["_db"].connect() as conn:
        account_id = accounts.create_account(conn, "fail@example.com")

    resp = client.post(f"/accounts/{account_id}/sync-firebase")
    assert resp.status_code == 303
    assert resp.headers["Location"].startswith("/accounts?error=")
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["firebase_uid"] is None
    # The message reaches the page (it is round-tripped through the query).
    shown = client.get("/accounts?error=" + urllib.parse.quote(
        "Could not create the Firebase user: EMAIL_EXISTS")).data
    assert b"Could not create the Firebase user: EMAIL_EXISTS" in shown


def test_sync_requires_admin_credentials(config, db_path, monkeypatch):
    monkeypatch.setattr(identity_admin, "available", lambda config: False)
    app = _app(config, **_firebase(config))
    client = _admin_client(app)
    with app.config["_db"].connect() as conn:
        account_id = accounts.create_account(conn, "nocreds@example.com")

    resp = client.post(f"/accounts/{account_id}/sync-firebase")
    assert resp.status_code == 303
    assert "credentials" in urllib.parse.unquote(resp.headers["Location"])
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["firebase_uid"] is None


def test_sync_needs_an_admin_session(config, db_path, monkeypatch):
    monkeypatch.setattr(identity_admin, "available", lambda config: True)
    app = _app(config, **_firebase(config))
    with app.config["_db"].connect() as conn:
        account_id = accounts.create_account(conn, "anon@example.com")
    resp = app.test_client().post(f"/accounts/{account_id}/sync-firebase")
    assert resp.status_code == 303
    assert resp.headers["Location"].startswith("/admin-login")


def test_sync_is_idempotent_once_linked(config, db_path, monkeypatch):
    monkeypatch.setattr(identity_admin, "available", lambda config: True)
    created = []
    monkeypatch.setattr(
        identity_admin, "create_user",
        lambda config, **fields: created.append(fields) or ("uid-new", None))
    app = _app(config, **_firebase(config))
    client = _admin_client(app)
    with app.config["_db"].connect() as conn:
        account_id = accounts.create_account(conn, "done@example.com")
        accounts.set_firebase_uid(conn, account_id, "uid-done")

    resp = client.post(f"/accounts/{account_id}/sync-firebase")
    assert resp.status_code == 303 and resp.headers["Location"] == "/accounts"
    assert created == []
    with app.config["_db"].connect() as conn:
        assert accounts.get_account(conn, account_id)["firebase_uid"] == "uid-done"
