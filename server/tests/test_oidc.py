# server/tests/test_oidc.py
"""Firebase ID-token verification and the /auth/oidc exchange (Phase D3/D11)."""
import base64
import json
import os
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from server.app import oidc
from server.app.app import create_app
from server.app.crypto import generate_rsa_keypair


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _pub_pem(pub) -> str:
    return pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()


def _jwt(priv, kid, claims) -> str:
    header = _b64(json.dumps({"alg": "RS256", "kid": kid}).encode())
    payload = _b64(json.dumps(claims).encode())
    sig = priv.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64(sig)}"


def _claims(project="proj-1", email="user@example.com"):
    now = int(time.time())
    return {"aud": project, "iss": f"https://securetoken.google.com/{project}",
            "sub": "uid-1", "exp": now + 3600, "email": email, "email_verified": True}


def test_verify_firebase_id_token(config):
    priv, pub = generate_rsa_keypair()
    config.FIREBASE_PROJECT_ID = "proj-1"
    token = _jwt(priv, "k1", _claims())
    claims = oidc.verify_firebase_id_token(config, token, certs={"k1": _pub_pem(pub)})
    assert claims["sub"] == "uid-1"


def test_verify_rejects_bad_audience_signature_and_expiry(config):
    priv, pub = generate_rsa_keypair()
    other, _ = generate_rsa_keypair()
    config.FIREBASE_PROJECT_ID = "proj-1"
    certs = {"k1": _pub_pem(pub)}
    assert oidc.verify_firebase_id_token(
        config, _jwt(priv, "k1", _claims(project="other")), certs=certs) is None
    assert oidc.verify_firebase_id_token(
        config, _jwt(other, "k1", _claims()), certs=certs) is None
    expired = _claims()
    expired["exp"] = 1
    assert oidc.verify_firebase_id_token(
        config, _jwt(priv, "k1", expired), certs=certs) is None


def test_oidc_endpoint_signs_in_an_existing_account(config, db_path, monkeypatch):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    config.IDENTITY_PROVIDER = "firebase"
    app = create_app(config)
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})

    monkeypatch.setattr(oidc, "verify_firebase_id_token",
                        lambda config, token, **k: {"email": "user@example.com",
                                                    "email_verified": True, "sub": "uid"})
    assert c.post("/auth/oidc", json={"id_token": "x"}).status_code == 200
    assert c.get("/account").status_code == 200

    # A first verified login for a new email creates the account (signup).
    monkeypatch.setattr(oidc, "verify_firebase_id_token",
                        lambda config, token, **k: {"email": "nobody@example.com",
                                                    "email_verified": True})
    assert c.post("/auth/oidc", json={"id_token": "x"}).status_code == 200


def test_oidc_rejects_a_denied_email_domain(config, db_path, monkeypatch):
    from server.app import accounts

    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    config.IDENTITY_PROVIDER = "firebase"
    app = create_app(config)
    app.config["TESTING"] = True
    with app.config["_db"].connect() as conn:
        accounts.add_domain_rule(conn, "deny", "tempmail.example")
    monkeypatch.setattr(oidc, "verify_firebase_id_token",
                        lambda config, token, **k: {"email": "x@tempmail.example",
                                                    "email_verified": True})
    assert app.test_client().post("/auth/oidc", json={"id_token": "x"}).status_code == 403
