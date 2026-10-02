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
import time

from server.app import accounts
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

    # Session-gated before login: each page bounces through the login form and
    # remembers where the visitor was headed.
    for page in ("/pair", "/devices", "/clients", "/pushes", "/pending"):
        anon = client.get(page)
        assert anon.status_code == 303, page
        assert anon.headers["Location"] == f"/admin-login?next={page}", page

    # Wrong password -> 401; correct password -> 302 + session cookie
    assert client.post("/admin-login", data={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.post("/admin-login", data={"username": "admin", "password": "testpass"}).status_code == 302

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

    # Enrol page shows the Complete registration button and the manual key;
    # unknown id shows not-found
    eid = client.post("/api/enrol/start", json={}).json["enrolment_id"]
    enrol = client.get(f"/enrol/{eid}")
    assert enrol.status_code == 200
    assert f'action="/enrol/{eid}/approve"'.encode() in enrol.data
    assert b"Complete registration" in enrol.data
    code = app.config["_enrol_keys"][eid]["code"]
    assert code.encode() in enrol.data
    unknown = client.get("/enrol/nope")
    assert unknown.status_code == 200
    assert b"not found" in unknown.data.lower()

    # After registering a device, /devices renders a table row
    secret = _register_device(app, client)
    devices = client.get("/devices")
    assert devices.status_code == 200
    assert b"<table" in devices.data
    assert b"Test Dev" in devices.data
    # The remove confirmation names the device it will remove.
    assert b"Remove device 'Test Dev'?" in devices.data

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


def test_root_serves_the_user_login_when_anonymous_and_menu_is_hidden(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    # `/` is the public face: anonymous visitors are handed to the *user*
    # login, never shown the operator console.
    root = client.get("/")
    assert root.status_code == 302 and root.headers["Location"] == "/login"

    login = client.get("/login").data
    assert b"<form" in login
    assert b'name="password"' in login
    assert b'action="/admin-login"' not in login

    # An anonymous visitor must not be offered the admin menu, on the page
    # it lands on or the one it came from.
    for page in (login,):
        assert b'href="/devices"' not in page
        assert b'href="/clients"' not in page
        assert b'href="/pair"' not in page
        assert b'href="/pending"' not in page


def test_root_sends_an_account_session_to_the_portal(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    with app.config["_db"].connect() as conn:
        accounts.create_account(conn, "user@example.com", "longenough1")
    client = app.test_client()
    assert client.post("/login", data={"email": "user@example.com",
                                       "password": "longenough1"}).status_code == 302
    root = client.get("/")
    assert root.status_code == 302 and root.headers["Location"] == "/account"


def test_root_redirects_to_pushes_once_logged_in(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    assert client.post("/admin-login", data={"username": "admin", "password": "testpass"}).status_code == 302
    root = client.get("/")
    assert root.status_code == 302
    assert root.headers["Location"] == "/pushes"

    # And the menu is back once there is a session.
    menu = client.get("/pushes").data
    assert b'href="/accounts"' in menu and b'href="/admins"' in menu
    # Account-scoped pages (pair/devices/clients) are not in the admin menu.
    assert b'href="/devices"' not in menu
    assert b'href="/clients"' not in menu
    assert b'href="/pair"' not in menu


def test_session_survives_a_restart_and_logout_revokes_it(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"

    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/admin-login", data={"username": "admin", "password": "testpass"})

    # The cookie is only meaningful alongside a server-side row, so a brand new
    # app over the same database must still honour it.
    restarted = create_app(config)
    restarted.config["TESTING"] = True
    later = restarted.test_client()
    later.set_cookie("mdrender_session", client.get_cookie("mdrender_session").value)
    assert later.get("/devices").status_code == 200

    # Logging out deletes the row, so the same cookie is now rejected (a page
    # GET bounces to login rather than returning raw JSON).
    assert later.post("/logout").status_code == 302
    assert later.get("/devices").status_code == 303


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

    bounced = app.test_client().post("/pushes/push-gone/delete")
    assert bounced.status_code == 303
    assert bounced.headers["Location"] == "/admin-login?next=/pushes"

    client.post("/admin-login", data={"username": "admin", "password": "testpass"})
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
    client.post("/admin-login", data={"username": "admin", "password": "testpass"})

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
    client.post("/admin-login", data={"username": "admin", "password": "testpass"})

    for hostile in ("https://evil.example.com", "//evil.example.com", "evil"):
        _seed_push(app, "push-x", "Sunny Falcon", ["x.md"])
        resp = client.post("/pushes/push-x/delete", data={"next": hostile})
        assert resp.status_code == 303
        assert resp.headers["Location"] == "/pushes", hostile


def _seed_client(app, name="push-cli"):
    """Create an enrolled OAuth client directly; returns its client_id."""
    from server.app import store
    from server.app.auth import hash_secret

    with app.config["_db"].connect() as conn:
        return store.create_client(conn, name, hash_secret("secret-1"))


def _logged_in_client(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/admin-login", data={"username": "admin", "password": "testpass"})
    return app, client


def test_clients_page_lists_enrolled_clients_and_shows_instructions(config, db_path):
    app, client = _logged_in_client(config, db_path)

    empty = client.get("/clients")
    assert empty.status_code == 200
    assert b"No clients enrolled yet" in empty.data

    cid = _seed_client(app, "cli-laptop")
    body = client.get("/clients").data
    assert b"cli-laptop" in body
    assert cid.encode() in body
    # The revoke confirmation names the client it will revoke.
    assert b"Revoke client 'cli-laptop'?" in body

    # Registration and pushing instructions use the installed command, with
    # this server's URL, and are collapsed under a disclosure once a client
    # exists.
    assert b"mdrender-send --enrol --server" in body
    assert config.PUSH_PUBLIC_URL.encode() in body
    assert b'mdrender-send --name' in body
    assert b"disclosure" in body
    # The browser "Register a new client" link is gone (it minted an enrolment
    # with no CLI to collect the credentials).
    assert b"/clients/new" not in body


def test_revoking_a_client_removes_its_row_and_live_tokens(config, db_path):
    app, client = _logged_in_client(config, db_path)
    cid = _seed_client(app, "cli-laptop")

    # A live bearer token for the client, as if it had already authenticated.
    from server.app import auth, store
    token = auth.issue_access_token(config, cid)
    assert auth.validate_access_token(config, token) == cid

    resp = client.post(f"/clients/{cid}/revoke")
    assert resp.status_code == 303
    assert resp.headers["Location"] == "/clients"

    with app.config["_db"].connect() as conn:
        assert store.get_client(conn, cid)["revoked_at"] is not None

    # The row is gone from the page, not merely marked.
    body = client.get("/clients").data
    assert b"cli-laptop" not in body
    assert cid.encode() not in body
    assert b"No clients enrolled yet" in body

    # Revocation bites immediately: the outstanding token no longer validates.
    assert auth.validate_access_token(config, token) is None


def test_clients_pages_are_session_gated(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    assert client.get("/clients").status_code == 303
    revoke = client.post("/clients/anything/revoke")
    assert revoke.status_code == 303
    assert revoke.headers["Location"] == "/admin-login?next=/clients"


def test_login_round_trips_back_to_the_requested_page(config, db_path):
    """A tool's --enrol opens /enrol/<eid> in a fresh browser: login then key.

    Regression for the raw-401 dead end: an anonymous visit to a gated page must
    bounce through /login and land back on the page once signed in.
    """
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    eid = client.post("/api/enrol/start", json={}).json["enrolment_id"]

    bounced = client.get(f"/enrol/{eid}")
    assert bounced.status_code == 303
    assert bounced.headers["Location"] == f"/login?next=/enrol/{eid}"

    # The login form carries the destination forward...
    form = client.get(bounced.headers["Location"])
    assert f'name="next" value="/enrol/{eid}"'.encode() in form.data

    # ...and a successful login lands back on the enrol page with its key.
    landed = client.post("/admin-login", data={"username": "admin", "password": "testpass",
                                         "next": f"/enrol/{eid}"})
    assert landed.status_code == 302
    assert landed.headers["Location"] == f"/enrol/{eid}"
    code = app.config["_enrol_keys"][eid]["code"]
    page = client.get(landed.headers["Location"])
    assert page.status_code == 200
    assert code.encode() in page.data


def test_login_ignores_an_offsite_next(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    for hostile in ("https://evil.example.com", "//evil.example.com", "evil"):
        resp = client.post("/admin-login", data={"username": "admin", "password": "testpass", "next": hostile})
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/pushes", hostile


def test_browser_approval_completes_the_enrolment(config, db_path):
    """The CLI polls /api/enrol/complete while the operator clicks the button."""
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    eid = client.post("/api/enrol/start",
                      json={"info": {"alias": "cli-host"}}).json["enrolment_id"]

    # Before approval the CLI's poll sees only "pending".
    pending = client.post("/api/enrol/complete", json={"enrolment_id": eid})
    assert pending.status_code == 200
    assert pending.json == {"status": "pending"}

    # Approval is a browser action, so anonymous callers are bounced to login.
    anon = client.post(f"/enrol/{eid}/approve")
    assert anon.status_code == 303
    assert anon.headers["Location"] == f"/login?next=/enrol/{eid}"

    client.post("/admin-login", data={"username": "admin", "password": "testpass"})
    approved = client.post(f"/enrol/{eid}/approve")
    assert approved.status_code == 200
    assert b"Enrolment complete" in approved.data

    # The CLI's next poll collects the credentials, exactly once.
    done = client.post("/api/enrol/complete", json={"enrolment_id": eid})
    assert done.status_code == 200
    body = done.json
    assert body["status"] == "approved"
    assert body["client_id"] and body["client_secret"]
    assert client.post("/api/enrol/complete",
                       json={"enrolment_id": eid}).status_code == 404

    # The minted client can actually get a token, and took the tool's name.
    tok = client.post("/oauth/token", data={
        "grant_type": "client_credentials",
        "client_id": body["client_id"],
        "client_secret": body["client_secret"],
    })
    assert tok.status_code == 200 and tok.json["access_token"]

    from server.app import store
    with app.config["_db"].connect() as conn:
        assert store.get_client(conn, body["client_id"])["name"] == "cli-host"


def test_approving_an_unknown_enrolment_is_not_found(config, db_path):
    app, client = _logged_in_client(config, db_path)
    assert client.post("/enrol/nope/approve").status_code == 404


def test_api_devices_lists_registered_targets_for_bearer_clients(config, db_path):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    # Bearer-gated, like /api/push.
    assert client.get("/api/devices").status_code == 401

    client.post("/admin-login", data={"username": "admin", "password": "testpass"})
    client.get("/pair")  # sets the pairing-token test hook
    _register_device(app, client)  # "Test Dev"

    eid = client.post("/api/enrol/start", json={}).json["enrolment_id"]
    code = app.config["_enrol_keys"][eid]["code"]
    creds = client.post("/api/enrol", json={"enrolment_id": eid, "code": code}).json
    token = client.post("/oauth/token", data={
        "grant_type": "client_credentials",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    }).json["access_token"]

    resp = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    devices = resp.json["devices"]
    assert any(d["name"] == "Test Dev" for d in devices)
    assert {"name", "registered_at", "last_seen"} <= set(devices[0])
    # The device secret must never be exposed to push clients.
    assert all("device_secret" not in d for d in devices)


def _start_enrol(app, client):
    eid = client.post("/api/enrol/start", json={}).json["enrolment_id"]
    return eid, app.config["_enrol_keys"][eid]["code"]


def test_short_enrol_code_is_typed_friendly_and_case_insensitive(config, db_path):
    app, client = _logged_in_client(config, db_path)
    eid, code = _start_enrol(app, client)

    # Six characters, no confusable glyphs (0/O, 1/I/L).
    assert len(code) == 6 and code.isalnum()
    assert not (set(code) & set("01OIL"))

    # Typed lowercase still works, and the code is single-use.
    ok = client.post("/api/enrol", json={"enrolment_id": eid, "code": code.lower()})
    assert ok.status_code == 200 and ok.json["client_id"]
    assert client.post("/api/enrol",
                       json={"enrolment_id": eid, "code": code}).status_code == 401


def test_expired_enrol_code_is_rejected_and_can_be_refreshed(config, db_path):
    app, client = _logged_in_client(config, db_path)
    eid, first = _start_enrol(app, client)
    app.config["_enrol_keys"][eid]["code_expires"] = 0  # force expiry

    expired = client.post("/api/enrol", json={"enrolment_id": eid, "code": first})
    assert expired.status_code == 401

    # A signed-in operator can mint a fresh code; it works.
    assert client.post(f"/enrol/{eid}/new-code").status_code == 303
    second = app.config["_enrol_keys"][eid]["code"]
    assert second != first
    assert client.post("/api/enrol",
                       json={"enrolment_id": eid, "code": second}).status_code == 200


def test_enrol_code_burns_out_after_repeated_wrong_guesses(config, db_path):
    app, client = _logged_in_client(config, db_path)
    eid, code = _start_enrol(app, client)

    for _ in range(config.ENROL_CODE_MAX_ATTEMPTS):
        wrong = client.post("/api/enrol",
                            json={"enrolment_id": eid, "code": "WRONGX"})
        assert wrong.status_code == 401

    # Even the correct code is refused once the attempt budget is spent.
    assert client.post("/api/enrol",
                       json={"enrolment_id": eid, "code": code}).status_code == 401


def test_viewing_the_enrol_page_refreshes_an_expired_code(config, db_path):
    """Signing in can outlast the code's TTL, so viewing the page renews it."""
    app, client = _logged_in_client(config, db_path)
    eid, first = _start_enrol(app, client)
    entry = app.config["_enrol_keys"][eid]
    entry["code_expires"] = 0  # as if the operator took too long to sign in

    page = client.get(f"/enrol/{eid}")
    assert page.status_code == 200
    assert entry["code"] != first
    assert entry["code_expires"] > time.time()
    # The fresh code is the one shown and it works.
    assert entry["code"].encode() in page.data
    assert client.post("/api/enrol",
                       json={"enrolment_id": eid, "code": entry["code"]}).status_code == 200


def test_viewing_the_enrol_page_keeps_a_valid_code(config, db_path):
    app, client = _logged_in_client(config, db_path)
    eid, first = _start_enrol(app, client)
    entry = app.config["_enrol_keys"][eid]
    assert entry["code_expires"] > time.time()

    client.get(f"/enrol/{eid}")
    assert entry["code"] == first  # not rotated while still valid
