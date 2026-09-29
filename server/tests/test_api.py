# server/tests/test_api.py
"""Integration test for the full Flask endpoint surface (Task A9).

Flow: health -> login -> enrol -> oauth -> /pair -> register-device -> push
-> decrypt the doorbell -> exchange it for a signed manifest -> download -> ack
-> status, plus auth-gating and 400 checks.
"""
import base64
import hashlib
import io
import json
import os

from server.app import crypto
from server.app.app import MANIFEST_SIGNATURE_HEADER, create_app
from server.app.crypto import generate_rsa_keypair, public_to_spki_der
from server.app.trigger import manifest_bytes, open_trigger, seal_trigger


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

    # Register a device. The sig covers the negotiated push_key, so the doorbell
    # key is bound to the pairing proof.
    phone_priv, phone_pub = generate_rsa_keypair()
    pub_b64 = base64.b64encode(public_to_spki_der(phone_pub)).decode()
    push_key = os.urandom(32)
    push_key_b64 = base64.b64encode(push_key).decode()
    digest = hashlib.sha256(
        f"sec-1Sunny Falconfcm-1{pub_b64}{push_key_b64}".encode()
    ).digest()
    sig = base64.b64encode(crypto.sign(phone_priv, digest)).decode()
    reg = client.post("/api/register-device", json={
        "device_secret": "sec-1", "device_name": "Sunny Falcon",
        "fcm_token": "fcm-1", "public_key": pub_b64, "push_key": push_key_b64,
        "pairing_token": pairing_token, "sig": sig,
    })
    assert reg.status_code == 200, reg.data
    assert reg.json["ok"] is True
    device_auth = reg.json["device_auth"]
    assert device_auth
    # The device learns the server's signing key here rather than from the QR.
    # It must be the key that signed the manifests it will later verify.
    assert reg.json["server_pk"] == app.config["_server_pk_b64"]

    # Push with a target_device (required)
    r = client.post("/api/push",
                    headers={"Authorization": f"Bearer {tok}"},
                    data={"target_device": "Sunny Falcon",
                          "file": [(io.BytesIO(b"hello"), "notes.md"),
                                   (io.BytesIO(b"world"), "sub/other.md")]},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    push_id = r.json["push_id"]
    assert r.json["files_sent"] == 2

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

    # Exactly one FCM message, whatever the file count — no batching.
    assert len(fcm.sent) == 1
    msg = fcm.sent[0][0]
    # The FCM data message carries the two doorbell fields and nothing else: no
    # file names, no paths, no retrieval keys.
    assert set(msg) == {"p", "i"}

    # Decrypt the doorbell end-to-end.
    doorbell = open_trigger(push_key, {"i": msg["i"], "c": msg["p"]})
    assert doorbell is not None
    assert doorbell["push_id"] == push_id
    assert doorbell["server_url"] == config.PUSH_PUBLIC_URL
    challenge_key = doorbell["challenge_key"]
    assert challenge_key

    # A doorbell sealed for this device under someone else's key is a silent
    # no-op, not an error: there is nothing useful to tell the sender.
    forged = seal_trigger(push_key, config.PUSH_PUBLIC_URL, "other-push", "ck")
    assert open_trigger(os.urandom(32), {"i": forged["i"], "c": forged["c"]}) is None

    # Exchange the doorbell for a signed manifest. The body is exactly the
    # signed bytes and the signature rides in a header, so the phone never has
    # to slice the signed bytes back out of a JSON envelope.
    r = client.post(f"/api/push/{push_id}/manifest",
                    json={"challenge_key": challenge_key})
    assert r.status_code == 200, r.data
    signed_bytes = r.data
    sig_b64 = r.headers[MANIFEST_SIGNATURE_HEADER]
    manifest = json.loads(signed_bytes)
    # The body must be byte-for-byte the canonical form that was signed, not
    # merely an equivalent parse of it.
    assert signed_bytes == manifest_bytes(manifest)
    # The signature must verify against the key the QR pinned, over the exact
    # bytes transmitted.
    server_pub = crypto.public_from_spki_der(
        base64.b64decode(app.config["_server_pk_b64"])
    )
    assert crypto.verify(server_pub, signed_bytes, base64.b64decode(sig_b64))
    # Tampering with the manifest after signing must break verification.
    # Tampering with a *re-serialised* copy must not verify: this is precisely
    # why the phone must check the body bytes and not a re-encoded object.
    tampered = json.loads(signed_bytes)
    tampered["files"].append({"file_id": "injected", "name": "x", "path": "",
                              "size": 1, "retrieval_key": "k"})
    assert not crypto.verify(server_pub, manifest_bytes(tampered),
                             base64.b64decode(sig_b64))

    files = {f["name"]: f for f in manifest["files"]}
    # Names are stored basenamed; the folder is not yet implemented (see the
    # plan's known-gaps note), so the manifest path is empty.
    assert set(files) == {"notes.md", "other.md"}
    assert all(f["path"] == "" for f in manifest["files"])
    f0 = files["notes.md"]
    assert f0["size"] == 5
    retrieval_key = f0["retrieval_key"]
    file_id = f0["file_id"]

    # Wrong challenge_key -> 403
    assert client.post(f"/api/push/{push_id}/manifest",
                       json={"challenge_key": "nope"}).status_code == 403
    # Unknown push -> 404
    assert client.post("/api/push/nope/manifest",
                       json={"challenge_key": challenge_key}).status_code == 404

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

    # A re-fetched manifest omits the acked file — this is what makes a retry
    # safe, since a re-ringed doorbell re-reads the same endpoint.
    r = client.post(f"/api/push/{push_id}/manifest",
                    json={"challenge_key": challenge_key})
    names = {f["name"] for f in json.loads(r.data)["files"]}
    assert "notes.md" not in names
    assert "other.md" in names

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
    statuses = {f["name"]: f["status"] for f in ps.json["files"]}
    assert statuses["notes.md"] == "acked"
    assert statuses["other.md"] == "pending"

    # Retry (session-gated): one file still pending -> one more doorbell.
    ret = client.post(f"/api/push/{push_id}/retry")
    assert ret.status_code == 200
    assert ret.json["files_sent"] == 1
    assert len(fcm.sent) == 2
    # And it is the same doorbell, byte for byte.
    assert fcm.sent[1][0] == msg
