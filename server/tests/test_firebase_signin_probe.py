# server/tests/test_firebase_signin_probe.py
"""Discovering what the Firebase console actually allows.

FIREBASE_PROVIDERS lists operator intent; the login UI must offer only what
Firebase will honour. The probe distinguishes disabled (OPERATION_NOT_ALLOWED)
from enabled (any other identitytoolkit error) without ever sending an SMS.
"""
import os

from server.app import firebase_signin
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


def test_disabled_phone_is_detected_from_the_error():
    off = {"error": {"message": "OPERATION_NOT_ALLOWED"}}
    assert firebase_signin._interpret(off) is False
    # identitytoolkit sometimes appends a sentence after the code.
    assert firebase_signin._interpret(
        {"error": {"message": "OPERATION_NOT_ALLOWED : Phone auth is disabled."}}) is False
    assert firebase_signin._interpret(
        {"error": {"message": "BILLING_NOT_ENABLED"}}) is False


def test_any_other_answer_means_the_gate_passed():
    for message in ("INVALID_PHONE_NUMBER", "MISSING_RECAPTCHA_TOKEN",
                    "INVALID_RECAPTCHA_TOKEN", "QUOTA_EXCEEDED"):
        assert firebase_signin._interpret({"error": {"message": message}}) is True, message
    # A well-formed 2xx body would also mean the method exists.
    assert firebase_signin._interpret({"kind": "identitytoolkit#..."}) is True


def test_unanswerable_responses_are_unknown():
    assert firebase_signin._interpret({"error": {"message": "API_KEY_INVALID"}}) is None
    assert firebase_signin._interpret({}) is None
    assert firebase_signin._interpret(None) is None
    assert firebase_signin.phone_allowed("") is None      # no key: fail open


def test_answer_is_cached_and_failure_is_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(firebase_signin, "_cache", {"at": 0.0, "allowed": None})
    monkeypatch.setattr(firebase_signin, "_fetch",
                        lambda key: calls.append(key) or False)

    assert firebase_signin.phone_allowed("k", now=1_000) is False
    assert firebase_signin.phone_allowed("k", now=1_000 + 3599) is False
    assert len(calls) == 1
    # An hour later the answer is re-checked.
    assert firebase_signin.phone_allowed("k", now=1_000 + 3601) is False
    assert len(calls) == 2

    # An unknown answer (probe failed) is retried within a minute.
    monkeypatch.setattr(firebase_signin, "_cache", {"at": 0.0, "allowed": None})
    monkeypatch.setattr(firebase_signin, "_fetch",
                        lambda key: calls.append(key) or None)
    assert firebase_signin.phone_allowed("k", now=9_000) is None
    assert len(calls) == 3                       # probed, came back unknown
    assert firebase_signin.phone_allowed("k", now=9_030) is None
    assert len(calls) == 3                       # still cached as unknown
    assert firebase_signin.phone_allowed("k", now=9_061) is None
    assert len(calls) == 4                       # retried after the TTL


def test_login_ui_drops_phone_when_firebase_refuses_it(config, db_path, monkeypatch):
    """Default providers include phone; the console says no -> no phone UI."""
    app = _app(config)   # FIREBASE_PROVIDERS defaults to google,github,phone,password,email_link
    monkeypatch.setattr(firebase_signin, "phone_allowed", lambda key: False)
    client = app.test_client()

    body = client.get("/login").data
    assert b"Email or phone number" not in body     # label degrades to "Email"
    assert b"+61 400" not in body                   # placeholder too
    assert b"phone code" not in body                # lede too
    assert b"magic email link" in body
    assert b'placeholder="you@example.com"' in body

    admin = client.get("/admin-login").data
    assert b"Email or phone number" not in admin
    assert b"+61 400" not in admin

    signup = client.get("/signup").data
    assert b"phone link" not in signup


def test_login_ui_keeps_phone_when_firebase_allows_it(config, db_path, monkeypatch):
    app = _app(config)
    monkeypatch.setattr(firebase_signin, "phone_allowed", lambda key: True)
    body = app.test_client().get("/login").data
    assert b"Email or phone number" in body
    assert b"+61 400 000 000" in body
    assert b"phone code" in body


def test_probe_skipped_when_phone_not_configured(config, db_path, monkeypatch):
    app = _app(config, FIREBASE_PROVIDERS="email_link")
    monkeypatch.setattr(
        firebase_signin, "phone_allowed",
        lambda key: (_ for _ in ()).throw(AssertionError("should not probe")))
    assert b"Email or phone number" not in app.test_client().get("/login").data
