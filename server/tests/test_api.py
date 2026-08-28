# server/tests/test_api.py
"""Integration test for the full Flask endpoint surface (Task A9).

Flow: health -> login -> enrol -> oauth -> /pair -> register-device -> push
-> decrypt envelope -> download -> ack -> status, plus auth-gating and 400s.
"""
import base64
import hashlib
import io
import json
import os

from server.app import crypto
from server.app.app import create_app
from server.app.crypto import generate_rsa_keypair, public_to_spki_der


class _FakeFcm:
    def __init__(self):
        self.sent = []

    def send(self, data_message, fcm_token):
        self.sent.append((data_message, fcm_token))


def test_full_push_flow(config, db_path, monkeypatch):
    # Keep storage inside the temp dir that db_path already created.
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"

    fcm = _FakeFcm()
    monkeypatch.setattr("server.app.app.make_fcm_client", lambda cfg: fcm)

    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    # Health (unauthenticated)
    assert client.get("/api/health").json == {"ok": True}

    # Session-gated: /pair before login -> 401
    assert client.get("/pair").status_code == 401

    # Password login (session cookie)
    r = client.post("/login", data={"password": "testpass"})
    assert r.status_code == 302
    assert client.post("/login", data={"password": "wrong"}).status_code == 401

    # Enrol a tool via a one-time key
    enrol = client.post("/api/enrol/start", json={}).json
    eid = enrol["enrolment_id"]
    key = app.config["_enrol_keys"][eid]["key"]
    creds = client.post("/api/enrol", json={"enrolment_id": eid, "key": key}).json
    assert "client_id" in creds and "client_secret" in creds

    # One-time key is consumed after use
    assert eid not in app.config["_enrol_keys"]

    # Wrong enrolment key -> 401
    bad = client.post("/api/enrol", json={"enrolment_id": eid, "key": "nope"})
    assert bad.status_code == 401

    # OAuth2 client-credentials token
    tok = client.post("/oauth/token", data={
        "grant_type": "client_credentials",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    }).json["access_token"]

    # GET /pair now succeeds and populates the pairing-token test hook
    assert client.get("/pair").status_code == 200
    pairing_token = app.config["_test_pairing_token"]
    assert pairing_token

    # Register a device: sig over sha256(secret + name + token + pub), PKCS1v15
    phone_priv, phone_pub = generate_rsa_keypair()
    pub_b64 = base64.b64encode(public_to_spki_der(phone_pub)).decode()
    digest = hashlib.sha256(f"sec-1Sunny Falconfcm-1{pub_b64}".encode()).digest()
    sig = base64.b64encode(crypto.sign(phone_priv, digest)).decode()
    reg = client.post("/api/register-device", json={
        "device_secret": "sec-1", "device_name": "Sunny Falcon",
        "fcm_token": "fcm-1", "public_key": pub_b64,
        "pairing_token": pairing_token, "sig": sig,
    })
    assert reg.status_code == 200, reg.data
    assert reg.json["ok"] is True
    device_auth = reg.json["device_auth"]
    assert device_auth

    # Push with a target_device (required)
    r = client.post("/api/push",
                    headers={"Authorization": f"Bearer {tok}"},
                    data={"target_device": "Sunny Falcon",
                          "file": (io.BytesIO(b"hello"), "notes.md")},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    push_id = r.json["push_id"]
    assert r.json["files_sent"] == 1

    # Missing target_device -> 400
    r = client.post("/api/push",
                    headers={"Authorization": f"Bearer {tok}"},
                    data={"file": (io.BytesIO(b"hi"), "a.md")},
                    content_type="multipart/form-data")
    assert r.status_code == 400
    assert r.json["error"] == "device not found"

    # Unknown target_device -> 400
    r = client.post("/api/push",
                    headers={"Authorization": f"Bearer {tok}"},
                    data={"target_device": "Ghost", "file": (io.BytesIO(b"hi"), "a.md")},
                    content_type="multipart/form-data")
    assert r.status_code == 400

    # No Bearer token -> 401
    assert client.post("/api/push",
                       data={"target_device": "Sunny Falcon",
                             "file": (io.BytesIO(b"hi"), "a.md")},
                       content_type="multipart/form-data").status_code == 401

    # One FCM message was sent (single small push = one slice)
    assert len(fcm.sent) == 1

    # Decrypt the envelope end-to-end
    env = json.loads(fcm.sent[0][0]["p"])
    assert env["alg"] == "RSA-OAEP-256" and env["enc"] == "A256GCM"
    content_key = crypto.oaep_unwrap(phone_priv, base64.b64decode(env["ek"]))
    plaintext = crypto.aes_gcm_decrypt(content_key, base64.b64decode(env["iv"]),
                                       base64.b64decode(env["ct"]),
                                       base64.b64decode(env["tag"]))
    payload = json.loads(plaintext)
    assert payload["push_id"] == push_id
    assert payload["total_files"] == 1
    assert payload["server_url"] == config.PUSH_PUBLIC_URL
    f0 = payload["files"][0]
    assert f0["name"] == "notes.md"
    retrieval_key = f0["retrieval_key"]
    file_id = f0["file_id"]
    assert retrieval_key

    # Download roundtrip (key NOT consumed)
    dl = client.post(f"/api/push/{file_id}/download", json={"key": retrieval_key})
    assert dl.status_code == 200
    assert dl.data == b"hello"

    # Wrong key -> 403
    assert client.post(f"/api/push/{file_id}/download",
                       json={"key": "nope"}).status_code == 403

    # Received -> ack
    assert client.post(f"/api/push/{file_id}/received",
                       json={"key": retrieval_key}).status_code == 200

    # Download after ack -> 404 (stored_path cleared)
    assert client.post(f"/api/push/{file_id}/download",
                       json={"key": retrieval_key}).status_code == 404

    # Device status
    assert client.post("/api/device/status",
                       json={"device_secret": "sec-1",
                             "device_auth": device_auth}).json == {"ok": True}
    assert client.post("/api/device/status",
                       json={"device_secret": "sec-1",
                             "device_auth": "wrong"}).status_code == 404

    # Session-gated JSON views
    assert client.get("/devices").status_code == 200
    assert client.get("/pushes").status_code == 200
    assert client.get("/pending").status_code == 200

    # Push status (Bearer)
    ps = client.get(f"/api/push/{push_id}/status",
                    headers={"Authorization": f"Bearer {tok}"})
    assert ps.status_code == 200
    assert ps.json["files"][0]["status"] == "acked"

    # Retry (session-gated): all-acked push -> 200, no extra FCM
    ret = client.post(f"/api/push/{push_id}/retry")
    assert ret.status_code == 200
    assert len(fcm.sent) == 1
