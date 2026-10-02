# server/tests/test_encryption_policy.py
"""Server-enforced encryption policy (design §7b)."""
import base64
import hashlib
import io
import json
import os
import time
import unittest.mock as mock

from server.app import accounts, crypto, encryption, federation, pairing, push_store, store
from server.app.app import create_app
from conftest import consent_code


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _account_client(app):
    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    c.post("/account/login", data={"email": "user@example.com", "password": "longenough1"})
    return c


def test_server_policy_reports_mode(config, db_path):
    config.ENCRYPTION_MODE = "on"
    app = _app(config)
    assert app.test_client().get("/api/server/policy").get_json() == {"encryption": "on"}


def test_required_encryption_rejects_plaintext_upload(config, db_path):
    config.ENCRYPTION_MODE = "on"
    app = _app(config)
    c = _account_client(app)

    plain = c.post("/api/account/upload", data={"file": (io.BytesIO(b"x"), "x.txt")},
                   content_type="multipart/form-data")
    assert plain.status_code == 422

    sealed = c.post("/api/account/upload",
                    data={"file": (io.BytesIO(b"cipher"), "x"),
                          "alg": "aes-256-gcm", "nonce": "N"},
                    content_type="multipart/form-data")
    assert sealed.status_code == 200


def _register_device_with_content(app, content_pubkey="APPPUB"):
    with app.config["_db"].connect() as conn:
        token = pairing.create_pairing_token(conn, 15)
    priv, pub = crypto.generate_rsa_keypair()
    pub_b64 = base64.b64encode(crypto.public_to_spki_der(pub)).decode()
    push_key = base64.b64encode(os.urandom(32)).decode()
    secret, fcm = "dev-secret", "fcm"
    digest = hashlib.sha256(f"{secret}Dev{fcm}{pub_b64}{push_key}".encode()).digest()
    sig = base64.b64encode(crypto.sign(priv, digest)).decode()
    # The §7c proof: the pairing key signs "content:<pubkey>" so a client can
    # verify the chain before sealing a content key to it.
    proof = base64.b64encode(
        crypto.sign(priv, f"content:{content_pubkey}".encode())).decode()
    resp = app.test_client().post("/api/register-device", json={
        "device_secret": secret, "device_name": "Dev", "fcm_token": fcm,
        "public_key": pub_b64, "push_key": push_key, "pairing_token": token,
        "sig": sig, "content_pubkey": content_pubkey, "content_proof": proof})
    assert resp.status_code == 200, resp.data
    return secret, resp.get_json()["device_auth"]


def test_device_content_key_endpoint(config, db_path):
    config.ENCRYPTION_MODE = "on"
    app = _app(config)
    secret, auth = _register_device_with_content(app)
    with app.config["_db"].connect() as conn:
        assert store.get_device_content_pubkey(conn, secret) == "APPPUB"

    c = app.test_client()
    # No sealed CEK yet.
    assert c.post("/api/device/content-key",
                  json={"device_secret": secret, "device_auth": auth}).status_code == 404
    with app.config["_db"].connect() as conn:
        encryption.set_sealed_cek(conn, secret, "SEALED")
    ok = c.post("/api/device/content-key",
                json={"device_secret": secret, "device_auth": auth})
    assert ok.status_code == 200 and ok.get_json()["sealed_cek"] == "SEALED"


def _enrol_slave(app):
    priv, pub = crypto.generate_rsa_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()
    pub_b64 = base64.b64encode(crypto.public_to_spki_der(pub)).decode()
    code = consent_code(app, server_id="srv-1", hostname="slave",
                        base_url="https://slave", public_key=pub_b64)
    with mock.patch.object(federation, "verify_slave_callback",
                           lambda base_url, challenge, timeout=10:
                           federation.sign_bytes(priv_pem, challenge.encode())):
        resp = app.test_client().post("/api/federation/enrol", json={
            "server_id": "srv-1", "hostname": "slave",
            "base_url": "https://slave", "public_key": pub_b64, "code": code})
    return priv_pem, f"srv-1.{resp.get_json()['server_secret']}"


def test_required_encryption_gates_the_doorbell(config, db_path):
    config.ENCRYPTION_MODE = "on"
    app = _app(config)

    class FakeFcm:
        def __init__(self):
            self.sent = []

        def send(self, data, token, **kwargs):
            self.sent.append((data, token, kwargs))

    fcm = FakeFcm()
    app.config["_fcm"] = fcm
    priv_pem, token = _enrol_slave(app)
    with app.config["_db"].connect() as conn:
        accounts.upsert_device(conn, account_id="acct-1", device_id="dev-1",
                               server_id="srv-1", fcm_token="tok")

    c = app.test_client()

    def _doorbell():
        body = json.dumps({"account_id": "acct-1", "device_id": "dev-1",
                           "sealed": {"c": "C", "i": "I"}}).encode()
        headers = federation.sign_request(priv_pem, "POST",
                                          "/api/federation/doorbell", body)
        headers["Authorization"] = f"Bearer {token}"
        return c.post("/api/federation/doorbell", data=body,
                      content_type="application/json", headers=headers)

    # No sealed CEK yet -> refused until negotiated.
    assert _doorbell().status_code == 409
    assert fcm.sent == []

    with app.config["_db"].connect() as conn:
        encryption.set_sealed_cek(conn, "dev-1", "SEALED")
    assert _doorbell().status_code == 200
    # Ringing is a wake-up: it must be high-priority FCM or the phone drops
    # it in the background (Android 12+ FGS restriction).
    assert fcm.sent[0][2] == {"high_priority": True}


def _bearer_token(app):
    """Mint an OAuth client via enrol and return its access token."""
    c = app.test_client()
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    eid = c.post("/api/enrol/start", json={}).json["enrolment_id"]
    code = app.config["_enrol_keys"][eid]["code"]
    creds = c.post("/api/enrol", json={"enrolment_id": eid, "code": code}).json
    tok = c.post("/oauth/token", data={
        "grant_type": "client_credentials",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    }).json["access_token"]
    return tok


def _seed_plaintext_push(conn, push_id="p-plain", device="Dev",
                         folder="Documents/Invoices", file_name="secret-plan.md",
                         file_path="work/secret-plan.md", account_id=None,
                         stored_path="/tmp/secret-plan.md"):
    push_store.create_push(conn, push_id, device, "ck", folder, "replace",
                           account_id=account_id)
    push_store.add_file(conn, file_id="f-plain", push_id=push_id,
                        file_name=file_name, file_path=file_path, size=42,
                        retrieval_key="rk", stored_path=stored_path,
                        created_at=int(time.time()))


def test_push_requires_encryption_metadata_and_stores_no_plaintext(config, db_path):
    config.ENCRYPTION_MODE = "on"
    app = _app(config)
    _register_device_with_content(app)
    tok = _bearer_token(app)
    headers = {"Authorization": f"Bearer {tok}"}

    c = app.test_client()
    # Plaintext upload -> 422, same gate as /api/account/upload (§7b).
    r = c.post("/api/push", headers=headers,
               data={"target_device": "Dev",
                     "file": (io.BytesIO(b"plain"), "secret-plan.md")},
               content_type="multipart/form-data")
    assert r.status_code == 422
    assert r.get_json()["error"] == "encryption required"

    # Encrypted upload, but a plaintext destination folder -> 400 (D13).
    r = c.post("/api/push", headers=headers,
               data={"target_device": "Dev", "alg": "aes-256-gcm", "nonce": "N",
                     "target_folder": "Documents/Invoices",
                     "file": (io.BytesIO(b"cipher"), "secret-plan.md")},
               content_type="multipart/form-data")
    assert r.status_code == 400
    assert r.get_json()["error"] == "target folder not permitted"

    # Clean encrypted push -> 200 with no metadata of any kind at rest.
    # (sealed_cek rides along: an encryption-on server refuses a push the
    # device could never open — see the seal-required test below.)
    r = c.post("/api/push", headers=headers,
               data={"target_device": "Dev", "alg": "aes-256-gcm", "nonce": "N",
                     "sealed_cek": "U0VBTEEL",
                     "file": (io.BytesIO(b"cipher"), "secret-plan.md")},
               content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    push_id = r.json["push_id"]

    with app.config["_db"].connect() as conn:
        push = push_store.get_push_by_id(conn, push_id)
        files = push_store.get_push_files(conn, push_id)
        assert push["target_folder"] == ""
        # The opaque file_id stands in for the filename (§7a step 2).
        assert files[0]["file_name"] == files[0]["file_id"]
        # The plaintext filename never reaches the database.
        blobs = conn.execute("SELECT * FROM pushes WHERE push_id = ?",
                             (push_id,)).fetchone()
        assert "secret-plan" not in json.dumps(dict(blobs))
        assert "secret-plan" not in json.dumps([dict(f) for f in files])
        # ...nor the on-disk path: the stored file is named by its file_id.
        assert os.path.basename(files[0]["stored_path"]) == files[0]["file_id"]


def test_boot_scrubs_plaintext_metadata_when_encrypted(config, db_path):
    # A legacy plaintext row exists (written before encryption was turned on).
    config.ENCRYPTION_MODE = "off"
    app = _app(config)
    # ...with its content stored under the plaintext filename on disk.
    legacy_path = os.path.join(config.PUSH_STORAGE_DIR, "p-plain", "f-plain",
                               "secret-plan.md")
    os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
    with open(legacy_path, "w") as fh:
        fh.write("plaintext content")
    with app.config["_db"].connect() as conn:
        _seed_plaintext_push(conn, stored_path=legacy_path)

    # Reboot with encryption on: the scrub runs on every boot (§7b/D13).
    config.ENCRYPTION_MODE = "on"
    app = _app(config)
    with app.config["_db"].connect() as conn:
        push = conn.execute("SELECT * FROM pushes WHERE push_id = 'p-plain'").fetchone()
        f = conn.execute("SELECT * FROM push_files WHERE file_id = 'f-plain'").fetchone()
        assert push["target_folder"] == ""
        assert f["file_name"] == "f-plain"
        assert f["file_path"] == ""
        # The file itself is renamed to its opaque id: no directory listing
        # or path column carries the old name either.
        assert os.path.basename(f["stored_path"]) == "f-plain"
        assert os.path.exists(f["stored_path"])
        assert not os.path.exists(legacy_path)
    # Booting again is a no-op.
    app = _app(config)
    with app.config["_db"].connect() as conn:
        assert conn.execute("SELECT file_name FROM push_files"
                            " WHERE file_id = 'f-plain'").fetchone()["file_name"] == "f-plain"


def test_encryption_off_pages_still_show_metadata(config, db_path):
    # Regression guard: mode-off servers keep full operator visibility.
    config.ENCRYPTION_MODE = "off"
    app = _app(config)
    with app.config["_db"].connect() as conn:
        _seed_plaintext_push(conn, account_id=None)
    c = app.test_client()
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    body = c.get("/pushes").data.decode()
    assert "secret-plan.md" in body
    assert "Documents/Invoices" in body
    assert "(encrypted)" not in body


def test_push_pages_conceal_metadata_when_encrypted(config, db_path):
    config.ENCRYPTION_MODE = "on"
    app = _app(config)
    # A plaintext row survives post-boot (e.g. a replica restored from an
    # older backup): every surface must still refuse to render it.
    ac = _account_client(app)
    with app.config["_db"].connect() as conn:
        acct = accounts.get_account_by_email(conn, "user@example.com")
        _seed_plaintext_push(conn, account_id=acct["account_id"])

    c = app.test_client()
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})

    for path in ("/pushes", "/pending"):
        body = c.get(path).data.decode()
        assert "secret-plan.md" not in body, path
        assert "Documents/Invoices" not in body, path
        assert "(encrypted)" in body, path

    # Account portal uses the same concealment.
    for path in ("/account/pushes", "/account/pending"):
        body = ac.get(path).data.decode()
        assert "secret-plan.md" not in body, path
        assert "Documents/Invoices" not in body, path
        assert "(encrypted)" in body, path

    # Admin per-user detail page too.
    detail = c.get(f"/accounts/{acct['account_id']}").data.decode()
    assert "secret-plan.md" not in detail
    assert "Documents/Invoices" not in detail
    assert "(encrypted)" in detail


def _push_form(**extra):
    form = {"target_device": "Dev", "alg": "aes-256-gcm", "nonce": "Tg==",
            "file": (io.BytesIO(b"opaque-blob"), "x")}
    form.update(extra)
    return form


def test_push_content_key_route_and_seal_flow(config, db_path):
    config.ENCRYPTION_MODE = "on"
    app = _app(config)
    secret, _ = _register_device_with_content(app, content_pubkey="Q09OVEVORQ==")
    tok = _bearer_token(app)
    h = {"Authorization": f"Bearer {tok}"}
    c = app.test_client()

    # No bearer token -> 401; unknown device -> 404.
    assert c.get("/api/push/content-key?device=Dev").status_code == 401
    assert c.get("/api/push/content-key?device=Ghost",
                 headers=h).status_code == 404

    # The route hands the client everything needed to verify and seal (§7c):
    # the content key, its pairing-key proof, and the pairing public key.
    r = c.get("/api/push/content-key?device=Dev", headers=h)
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["content_pubkey"] == "Q09OVEVORQ=="
    assert body["content_proof"] and body["device_public_key"]

    # A push to a device with no sealed CEK is refused up front: it could
    # never be delivered, and a silent 200 would leave zombie rows (§7b).
    r = c.post("/api/push", headers=h,
               data=_push_form(), content_type="multipart/form-data")
    assert r.status_code == 428
    assert r.get_json()["error"] == "seal required"
    with app.config["_db"].connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM pushes").fetchone()["c"] == 0

    # The same request carrying sealed_cek stores it (opaque; the server
    # cannot open it) and the push goes through.
    r = c.post("/api/push", headers=h,
               data=_push_form(sealed_cek="U0VBTEEL"), content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    with app.config["_db"].connect() as conn:
        row = encryption.get_sealed_cek(conn, secret)
        assert row is not None and row["sealed_cek"] == "U0VBTEEL"

    # A later push to the same device needs no re-seal.
    r = c.post("/api/push", headers=h,
               data=_push_form(), content_type="multipart/form-data")
    assert r.status_code == 200, r.data


def test_retry_worker_never_rings_unnegotiated_pushes(config, db_path):
    from server.app import push_store, retry as retry_mod

    config.ENCRYPTION_MODE = "on"
    app = _app(config)
    secret, _ = _register_device_with_content(app)

    class FakeFcm:
        def __init__(self):
            self.sent = []

        def send(self, data, token, **kwargs):
            self.sent.append((token, kwargs))

    fcm = FakeFcm()
    worker = retry_mod.RetryWorker(config, app.config["_db"], fcm_client=fcm)

    # A legacy push (pre-428 era) sits pending with no sealed CEK: the worker
    # must exhaust it rather than doorbell a push nothing could decrypt (§7b).
    with app.config["_db"].connect() as conn:
        push_store.create_push(conn, "p-legacy", "Dev", "ck")
        push_store.add_file(conn, file_id="f-legacy", push_id="p-legacy",
                            file_name="f-legacy", file_path="", size=1,
                            retrieval_key="rk", stored_path="/tmp/none",
                            created_at=0)
    touched = worker.tick(time.time())
    assert "f-legacy" in touched
    assert fcm.sent == []
    with app.config["_db"].connect() as conn:
        assert conn.execute("SELECT status FROM push_files WHERE file_id"
                            " = 'f-legacy'").fetchone()["status"] == "exhausted"

        # Sealing the CEK makes delivery possible again: a fresh pending file
        # is re-rung normally.
        encryption.set_sealed_cek(conn, secret, "U0VBTEEL")
        push_store.add_file(conn, file_id="f-ok", push_id="p-legacy",
                            file_name="f-ok", file_path="", size=1,
                            retrieval_key="rk2", stored_path="/tmp/none2",
                            created_at=0)
    touched = worker.tick(time.time())
    assert "f-ok" in touched
    assert len(fcm.sent) == 1
    # Retry rings are wake-ups too: high priority so the phone may act while
    # the app is backgrounded.
    assert fcm.sent[0][1] == {"high_priority": True}
    with app.config["_db"].connect() as conn:
        assert conn.execute("SELECT status FROM push_files WHERE file_id"
                            " = 'f-ok'").fetchone()["status"] == "pending"
