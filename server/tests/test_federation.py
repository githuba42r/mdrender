# server/tests/test_federation.py
"""Federation enrolment, signed requests, nonces, and outbox (Phase C)."""
import base64
import os
import unittest.mock as mock

import pytest

from server.app import crypto, federation
from server.app.app import create_app


def _slave_keypair():
    priv, pub = crypto.generate_rsa_keypair()
    return priv, base64.b64encode(crypto.public_to_spki_der(pub)).decode()


@pytest.fixture()
def fed_app(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _enrol(app):
    """Enrol a slave using a fake callback signed by its own key; return
    (private_pem, bearer_token)."""
    priv, pub_b64 = _slave_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()

    def fake_callback(base_url, challenge, timeout=10):
        return federation.sign_bytes(priv_pem, challenge.encode())

    with mock.patch.object(federation, "verify_slave_callback", fake_callback):
        resp = app.test_client().post("/api/federation/enrol", json={
            "server_id": "srv-1", "hostname": "slave.example",
            "base_url": "https://slave.example", "public_key": pub_b64})
    assert resp.status_code == 200, resp.data
    secret = resp.get_json()["server_secret"]
    return priv_pem, f"srv-1.{secret}"


def test_slave_enrolment_via_signed_callback(fed_app, monkeypatch):
    priv, pub_b64 = _slave_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()
    monkeypatch.setattr(federation, "verify_slave_callback",
                        lambda base_url, challenge, timeout=10:
                        federation.sign_bytes(priv_pem, challenge.encode()))

    c = fed_app.test_client()
    resp = c.post("/api/federation/enrol", json={
        "server_id": "srv-1", "hostname": "slave.example",
        "base_url": "https://slave.example", "public_key": pub_b64})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "active"

    token = f"srv-1.{body['server_secret']}"
    who = c.get("/api/federation/whoami", headers={"Authorization": f"Bearer {token}"})
    assert who.status_code == 200
    assert who.get_json()["server_id"] == "srv-1"


def test_enrol_rejects_a_bad_callback_signature(fed_app, monkeypatch):
    priv, pub_b64 = _slave_keypair()
    other, _ = _slave_keypair()
    other_pem = crypto.private_to_pem(other).decode()
    monkeypatch.setattr(federation, "verify_slave_callback",
                        lambda base_url, challenge, timeout=10:
                        federation.sign_bytes(other_pem, challenge.encode()))

    resp = fed_app.test_client().post("/api/federation/enrol", json={
        "server_id": "srv-x", "hostname": "h", "base_url": "https://h",
        "public_key": pub_b64})
    assert resp.status_code == 400


def test_heartbeat_requires_signature_and_flushes_outbox(fed_app):
    priv_pem, token = _enrol(fed_app)
    db = fed_app.config["_db"]
    with db.connect() as conn:
        federation.enqueue(conn, "srv-1", {"type": "notice", "text": "hello"})

    c = fed_app.test_client()
    # Bearer alone (unsigned) is rejected.
    assert c.post("/api/federation/heartbeat",
                  headers={"Authorization": f"Bearer {token}"}).status_code == 401

    headers = federation.sign_request(priv_pem, "POST", "/api/federation/heartbeat", b"")
    headers["Authorization"] = f"Bearer {token}"
    ok = c.post("/api/federation/heartbeat", headers=headers)
    assert ok.status_code == 200
    assert ok.get_json()["queued"] == [{"type": "notice", "text": "hello"}]

    with db.connect() as conn:
        assert federation.pending_outbox(conn, "srv-1") == []  # drained


def test_replayed_request_is_rejected(fed_app):
    priv_pem, token = _enrol(fed_app)
    c = fed_app.test_client()
    headers = federation.sign_request(priv_pem, "POST", "/api/federation/ping", b"")
    headers["Authorization"] = f"Bearer {token}"
    assert c.post("/api/federation/ping", headers=headers).status_code == 200
    # Same nonce again -> replay rejected.
    assert c.post("/api/federation/ping", headers=headers).status_code == 401


def test_slave_verify_responder_signs_the_challenge(fed_app):
    body = fed_app.test_client().post("/api/federation/verify",
                                      json={"challenge": "abc123"}).get_json()
    ident = fed_app.config["_server_identity"]
    assert body["server_id"] == ident["server_id"]
    pub = crypto.public_from_spki_der(base64.b64decode(ident["public_key"]))
    assert crypto.verify(pub, b"abc123", base64.b64decode(body["signature"]))


def test_probe_responder_reports_status(fed_app):
    body = fed_app.test_client().post("/api/federation/probe",
                                      json={"challenge": "c"}).get_json()
    assert body["status"] == fed_app.config["_server_role"]
