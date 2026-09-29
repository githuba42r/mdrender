# server/tests/test_templates.py
"""Smoke tests that the browser pages render (Task A11).

Covers the template wiring: login form, QR pairing page (inline SVG), the
admin tables, the device-remove form redirect, and the enrol key page.
"""
import base64
import hashlib
import os
import re

from server.app.app import create_app
from server.app.crypto import generate_rsa_keypair, public_to_spki_der, sign


def _register_device(app, client):
    """Register one device using the pairing-token flow; returns its secret."""
    pairing_token = app.config["_test_pairing_token"]
    assert pairing_token
    priv, pub = generate_rsa_keypair()
    pub_b64 = base64.b64encode(public_to_spki_der(pub)).decode()
    push_key_b64 = base64.b64encode(os.urandom(32)).decode()
    digest = hashlib.sha256(
        f"sec-t1Test Devfcm-t1{pub_b64}{push_key_b64}".encode()
    ).digest()
    sig = base64.b64encode(sign(priv, digest)).decode()
    reg = client.post("/api/register-device", json={
        "device_secret": "sec-t1", "device_name": "Test Dev",
        "fcm_token": "fcm-t1", "public_key": pub_b64,
        "push_key": push_key_b64,
        "pairing_token": pairing_token, "sig": sig,
    })
    assert reg.status_code == 200, reg.data
    return "sec-t1"


def test_browser_pages_render(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    # Login form renders (public)
    assert client.get("/login").status_code == 200

    # Session-gated before login
    assert client.get("/pair").status_code == 401
    assert client.get("/devices").status_code == 401
    assert client.get("/pushes").status_code == 401
    assert client.get("/pending").status_code == 401

    # Wrong password -> 401; correct password -> 302 + session cookie
    assert client.post("/login", data={"password": "wrong"}).status_code == 401
    assert client.post("/login", data={"password": "testpass"}).status_code == 302

    # Pairing page renders the QR as inline SVG and sets the token hook
    pair = client.get("/pair")
    assert pair.status_code == 200
    assert b"<svg" in pair.data
    # Regression: the QR must be inline-renderable. SvgImage emits namespace-
    # prefixed <svg:rect> children that browsers render as nothing when the
    # SVG is embedded in HTML, so assert on the factory's actual raster: the
    # <path> element. Also keep the token hook stable across both fetches.
    assert b'xmlns:svg=' not in pair.data
    assert re.search(rb"<path d=", pair.data)
    assert app.config["_test_pairing_token"]

    # Admin pages render (empty states)
    assert client.get("/devices").status_code == 200
    assert client.get("/pushes").status_code == 200
    assert client.get("/pending").status_code == 200

    # Enrol page shows the one-time key; unknown id shows not-found
    eid = client.post("/api/enrol/start", json={}).json["enrolment_id"]
    enrol = client.get(f"/enrol/{eid}")
    assert enrol.status_code == 200
    key = app.config["_enrol_keys"][eid]["key"]
    assert key.encode() in enrol.data
    unknown = client.get("/enrol/nope")
    assert unknown.status_code == 200
    assert b"not found" in unknown.data.lower()

    # After registering a device, /devices renders a table row
    secret = _register_device(app, client)
    devices = client.get("/devices")
    assert devices.status_code == 200
    assert b"<table" in devices.data
    assert b"Test Dev" in devices.data

    # The remove form lands back on /devices via a 303 redirect
    removed = client.post(f"/devices/{secret}/delete")
    assert removed.status_code == 303
    assert removed.headers["Location"] == "/devices"
    assert client.get("/devices").status_code == 200
