# server/app/envelope.py
import base64
import json
import os

from server.app import crypto

MAX_PAYLOAD_BYTES = 3500


def file_entry(file_id: str, name: str, path: str, retrieval_key: str) -> dict:
    return {"file_id": file_id, "name": name, "path": path, "retrieval_key": retrieval_key}


def slice_files(entries: list[dict], max_bytes: int = MAX_PAYLOAD_BYTES) -> list[list[dict]]:
    slices: list[list[dict]] = []
    current: list[dict] = []
    for entry in entries:
        candidate = current + [entry]
        if json.dumps({"files": candidate}).__len__() > max_bytes and current:
            slices.append(current)
            current = [entry]
        else:
            current = candidate
    if current:
        slices.append(current)
    return slices


def build_payload(server_url: str, push_id: str, date_iso: str, total_files: int, files: list[dict]) -> dict:
    return {"server_url": server_url, "push_id": push_id, "date": date_iso,
            "total_files": total_files, "files": files}


def build_envelope(server_private_key, device_public_key, payload: dict) -> dict:
    content_key = os.urandom(32)
    iv = os.urandom(12)
    plaintext = json.dumps(payload).encode()
    ciphertext, tag = crypto.aes_gcm_encrypt(content_key, iv, plaintext)
    ek = crypto.oaep_wrap(device_public_key, content_key)
    ek_b64 = base64.b64encode(ek).decode()
    iv_b64 = base64.b64encode(iv).decode()
    ct_b64 = base64.b64encode(ciphertext).decode()
    tag_b64 = base64.b64encode(tag).decode()
    sig = crypto.sign(server_private_key, (ek_b64 + iv_b64 + ct_b64).encode())
    return {
        "v": 1, "alg": "RSA-OAEP-256", "enc": "A256GCM", "kid": "",
        "ek": ek_b64, "iv": iv_b64, "tag": tag_b64, "ct": ct_b64,
        "sig": base64.b64encode(sig).decode(),
    }
