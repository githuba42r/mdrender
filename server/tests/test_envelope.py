import base64
import json

from server.app.envelope import slice_files, build_envelope, build_payload
from server.app import crypto


def test_slice_files_respects_cap():
    # ~256-byte entries; 200 of them must not fit in 3500 bytes.
    entries = [{"file_id": f"f{i}", "name": f"n{i}.md", "path": "D", "retrieval_key": f"k{i}"} for i in range(200)]
    slices = slice_files(entries, max_bytes=3500)
    assert len(slices) > 1
    total = sum(len(s) for s in slices)
    assert total == 200
    for s in slices:
        assert len(json.dumps({"files": s})) <= 3500


def test_envelope_roundtrip():
    server_priv, server_pub = crypto.generate_rsa_keypair()
    device_priv, device_pub = crypto.generate_rsa_keypair()
    payload = build_payload("https://push.example.com", "p1", "2026-08-28T12:00:00Z", 1,
                            [{"file_id": "f1", "name": "notes.md", "path": "", "retrieval_key": "k1"}])
    env = build_envelope(server_priv, device_pub, payload)
    assert env["alg"] == "RSA-OAEP-256" and env["enc"] == "A256GCM"
    assert crypto.verify(server_pub, (env["ek"] + env["iv"] + env["ct"]).encode(),
                         base64.b64decode(env["sig"]))
    content_key = crypto.oaep_unwrap(device_priv, base64.b64decode(env["ek"]))
    plaintext = crypto.aes_gcm_decrypt(content_key, base64.b64decode(env["iv"]),
                                       base64.b64decode(env["ct"]), base64.b64decode(env["tag"]))
    assert json.loads(plaintext) == payload
