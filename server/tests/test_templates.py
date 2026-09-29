# server/tests/test_templates.py
"""Smoke tests that the browser pages render (Task A11).

Covers the template wiring: login form, QR pairing page (inline SVG), the
admin tables, the device-remove form redirect, and the enrol key page.
"""
import base64
import hashlib
import json
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


def test_pairing_qr_carries_only_the_url_and_token():
    """The QR must stay small enough to scan comfortably from a phone.

    A 3072-bit RSA public key is ~740 base64 characters on its own, which forces
    a high-density code that is painful to scan off a screen. The token already
    proves the user reached this server's authenticated pairing page, so the key
    is delivered in the registration response instead.
    """
    from server.app.pairing import build_pairing_qr, pairing_payload

    payload = pairing_payload("https://push.example.com", "tok-1")
    assert payload == {"v": 1, "server_url": "https://push.example.com", "token": "tok-1"}
    assert "pk" not in payload
    assert "expires" not in payload

    qr_text = build_pairing_qr("https://push.example.com", "tok-1")
    # Comfortably inside a low-density QR rather than a dense, hard-to-scan one.
    assert len(qr_text) < 120
    assert json.loads(qr_text) == payload


def test_root_shows_login_when_anonymous_and_menu_is_hidden(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    # No root route existed before, so this used to be a 404.
    root = client.get("/")
    assert root.status_code == 200
    assert b"<form" in root.data
    assert b'name="password"' in root.data

    # An anonymous visitor must not be offered the admin menu, on the landing
    # page or on the login page itself.
    for page in (root.data, client.get("/login").data):
        assert b'href="/devices"' not in page
        assert b'href="/pair"' not in page
        assert b'href="/pending"' not in page


def test_root_redirects_to_pushes_once_logged_in(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    assert client.post("/login", data={"password": "testpass"}).status_code == 302
    root = client.get("/")
    assert root.status_code == 302
    assert root.headers["Location"] == "/pushes"

    # And the menu is back once there is a session.
    assert b'href="/devices"' in client.get("/pushes").data


def test_session_survives_a_restart_and_logout_revokes_it(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"

    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/login", data={"password": "testpass"})

    # The cookie is only meaningful alongside a server-side row, so a brand new
    # app over the same database must still honour it.
    restarted = create_app(config)
    restarted.config["TESTING"] = True
    later = restarted.test_client()
    later.set_cookie("mdrender_session", client.get_cookie("mdrender_session").value)
    assert later.get("/devices").status_code == 200

    # Logging out deletes the row, so the same cookie is now rejected.
    assert later.post("/logout").status_code == 302
    assert later.get("/devices").status_code == 401


def _seed_push(app, push_id, device, file_names, created_at=1000, target_folder="Story/x"):
    from server.app import push_store
    db = app.config["_db"]
    with db.connect() as conn:
        push_store.create_push(conn, push_id, device, target_folder=target_folder)
        for i, name in enumerate(file_names):
            push_store.add_file(conn, file_id=f"{push_id}-f{i}", push_id=push_id,
                                file_name=name, file_path="", size=10,
                                retrieval_key=f"k{i}", stored_path=None,
                                created_at=created_at + i)


def test_pushes_page_removes_a_push_and_its_stored_files(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    _seed_push(app, "push-gone", "Sunny Falcon", ["a.md"])
    # Bytes that ack already orphaned in the database: stored_path is NULL, so
    # only removing storage_dir/<push_id>/ can ever clear them.
    orphan = os.path.join(config.PUSH_STORAGE_DIR, "push-gone", "f0")
    os.makedirs(orphan, exist_ok=True)
    with open(os.path.join(orphan, "a.md"), "wb") as fh:
        fh.write(b"x")

    assert app.test_client().post("/pushes/push-gone/delete").status_code == 401

    client.post("/login", data={"password": "testpass"})
    # The button exists on the row.
    assert b"/pushes/push-gone/delete" in client.get("/pushes").data

    resp = client.post("/pushes/push-gone/delete")
    assert resp.status_code == 303
    assert resp.headers["Location"] == "/pushes"
    assert not os.path.exists(os.path.join(config.PUSH_STORAGE_DIR, "push-gone"))
    from server.app import push_store
    with app.config["_db"].connect() as conn:
        assert push_store.get_push_by_id(conn, "push-gone") is None
        assert push_store.get_push_files(conn, "push-gone") == []


def test_pending_groups_files_under_their_push_and_deletes_the_whole_push(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/login", data={"password": "testpass"})

    _seed_push(app, "push-a", "Sunny Falcon", ["a1.md", "a2.md"], created_at=2000)
    _seed_push(app, "push-b", "Clever Juniper", ["b1.md"], created_at=1000)

    body = client.get("/pending").data
    # One section per push, not one flat table of files.
    assert body.count(b'class="push-group"') == 2
    for token in (b"push-a", b"a1.md", b"a2.md", b"push-b", b"b1.md", b"Clever Juniper"):
        assert token in body

    # Deleting from here drops the entire push, not just the listed file.
    resp = client.post("/pushes/push-a/delete", data={"next": "/pending"})
    assert resp.status_code == 303
    assert resp.headers["Location"] == "/pending"
    from server.app import push_store
    with app.config["_db"].connect() as conn:
        assert push_store.get_push_by_id(conn, "push-a") is None
        assert push_store.get_push_files(conn, "push-a") == []
        assert push_store.get_push_by_id(conn, "push-b") is not None


def test_delete_refuses_an_offsite_next_and_falls_back_to_pushes(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/login", data={"password": "testpass"})

    for hostile in ("https://evil.example.com", "//evil.example.com", "evil"):
        _seed_push(app, "push-x", "Sunny Falcon", ["x.md"])
        resp = client.post("/pushes/push-x/delete", data={"next": hostile})
        assert resp.status_code == 303
        assert resp.headers["Location"] == "/pushes", hostile
