# server/tests/test_login_tabs.py
"""The login cards present Magic link / Email & password as tabs.

Firebase, when enabled, is the default tab; a failed local sign-in re-renders
on the password tab so its error is visible. With phone sign-in disabled the
copy drops every phone reference, and returning from an email magic link
shows a completion spinner instead of a form that looks dead.
"""
import os

from server.app import accounts
from server.app.app import create_app


def _app(config, **overrides):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    config.IDENTITY_PROVIDER = "firebase"
    config.FIREBASE_API_KEY = "k"
    config.FIREBASE_AUTH_DOMAIN = "proj.firebaseapp.com"
    for key, value in overrides.items():
        setattr(config, key, value)
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _has(body, needle):
    return needle.encode() in body


def _panel_shown(body, panel):
    """The panel div renders with no `hidden` attribute when it is active."""
    return _has(body, 'aria-labelledby="%s">' % panel)


def _panel_hidden(body, panel):
    return _has(body, 'aria-labelledby="%s" hidden' % panel)


def test_tabs_default_to_magic_link_on_both_logins(config, db_path):
    app = _app(config, FIREBASE_PROVIDERS="phone,email_link")
    client = app.test_client()
    for path in ("/login", "/admin-login"):
        body = client.get(path).data
        assert _has(body, 'id="tab-link"'), path
        assert _has(body, 'id="tab-password"'), path
        # Magic link visible, password panel stashed.
        assert _panel_shown(body, "tab-link"), path
        assert _panel_hidden(body, "tab-password"), path
        assert _has(body, "Completing sign-in"), path   # spinner markup


def test_failed_local_login_opens_the_password_tab(config, db_path):
    app = _app(config, FIREBASE_PROVIDERS="email_link")
    client = app.test_client()
    with app.config["_db"].connect() as conn:
        accounts.create_account(conn, "user@example.com", "letmein99")

    resp = client.post("/login", data={"email": "user@example.com",
                                       "password": "wrong"})
    assert resp.status_code == 401
    body = resp.data
    assert _has(body, "Invalid email or password.")
    # The error lives on the password panel, so that panel must be showing.
    assert _panel_shown(body, "tab-password")
    assert _panel_hidden(body, "tab-link")

    admin = client.post("/admin-login", data={"username": "admin",
                                              "password": "nope"})
    assert admin.status_code == 401
    assert _has(admin.data, "Invalid username or password.")
    assert _panel_shown(admin.data, "tab-password")
    assert _panel_hidden(admin.data, "tab-link")


def test_admin_form_that_is_a_user_email_opens_the_password_tab(config, db_path):
    app = _app(config, FIREBASE_PROVIDERS="email_link")
    client = app.test_client()
    with app.config["_db"].connect() as conn:
        accounts.create_account(conn, "user@example.com", "letmein99")
    resp = client.post("/admin-login", data={"username": "user@example.com",
                                             "password": "wrong"})
    assert resp.status_code == 401
    assert _has(resp.data, "sign in on the user login")
    assert _panel_shown(resp.data, "tab-password")
    assert _panel_hidden(resp.data, "tab-link")


def test_no_tabs_when_firebase_is_off(config, db_path):
    config.IDENTITY_PROVIDER = "local"
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()
    for path in ("/login", "/admin-login"):
        body = client.get(path).data
        assert not _has(body, 'id="tab-link"'), path
        assert not _has(body, 'id="tab-password"'), path
    # Plain single-form login still works.
    assert client.post("/admin-login", data={"username": "admin",
                                             "password": "testpass"}).status_code == 302


def test_phone_disabled_drops_every_phone_reference(config, db_path):
    app = _app(config, FIREBASE_PROVIDERS="email_link")
    body = app.test_client().get("/login").data
    assert not _has(body, "phone code")          # lede
    assert not _has(body, "phone number")        # identifier label
    assert not _has(body, "+61 400")             # placeholder
    assert not _has(body, "email or phone")
    assert _has(body, "magic email link")
    assert _has(body, "Send sign-in link")
    assert _has(body, 'placeholder="you@example.com"')


def test_phone_enabled_keeps_the_phone_copy(config, db_path):
    app = _app(config, FIREBASE_PROVIDERS="phone,email_link")
    body = app.test_client().get("/login").data
    assert _has(body, "phone code")
    assert _has(body, "Email or phone number")
    assert _has(body, "+61 400 000 000")


def test_social_and_or_providers_build_the_lede(config, db_path):
    app = _app(config, FIREBASE_PROVIDERS="google,github,phone,email_link")
    body = app.test_client().get("/login").data
    assert _has(body, "a social account, a phone code, or a magic email link")
