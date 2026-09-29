# server/app/trigger.py
"""Doorbell trigger and signed manifest.

The FCM message carries no file manifest. It carries a constant-size encrypted
trigger naming where to look; the phone then fetches a signed manifest and the
bytes over HTTPS. This keeps the message under FCM's 4 KB limit regardless of
how many files a push contains.
"""
import base64
import hashlib
import hmac
import json

from server.app import crypto

# FCM rejects data messages above this. The trigger is constant-size, so this is
# a guard rail rather than a budget that file count can exhaust.
MAX_FCM_MESSAGE_BYTES = 4096

GCM_TAG_LENGTH = 16


def derive_iv(push_key: bytes, push_id: str) -> bytes:
    """Deterministic 12-byte IV, unique per (key, push_id) with no stored state.

    Guarantees the server never repeats a (key, IV) pair under a long-lived key,
    and makes a retry of the same push byte-identical.
    """
    return hmac.new(push_key, push_id.encode(), hashlib.sha256).digest()[:12]


def trigger_plaintext(server_url: str, push_id: str, challenge_key: str) -> bytes:
    return json.dumps(
        {"v": 1, "server_url": server_url, "push_id": push_id,
         "challenge_key": challenge_key},
        separators=(",", ":"), sort_keys=True,
    ).encode()


def seal_trigger(push_key: bytes, server_url: str, push_id: str,
                 challenge_key: str) -> dict:
    """Return the two FCM data fields: {"i": iv_b64, "c": ct||tag b64}.

    The caller sends them as {"p": sealed["c"], "i": sealed["i"]}. The format
    version travels inside the plaintext, so server and client never have to
    agree on a third field.
    """
    iv = derive_iv(push_key, push_id)
    ciphertext, tag = crypto.aes_gcm_encrypt(
        push_key, iv, trigger_plaintext(server_url, push_id, challenge_key)
    )
    return {"i": base64.b64encode(iv).decode(),
            "c": base64.b64encode(ciphertext + tag).decode()}


def open_trigger(push_key: bytes, sealed: dict):
    """Return the decoded doorbell, or None if it does not authenticate.

    A doorbell addressed to a different device, or one tampered with in flight,
    fails the GCM tag check and drops silently — there is nothing to report.
    """
    try:
        iv = base64.b64decode(sealed["i"], validate=True)
        raw = base64.b64decode(sealed["c"], validate=True)
        if len(iv) != 12 or len(raw) <= GCM_TAG_LENGTH:
            return None
        return json.loads(crypto.aes_gcm_decrypt(
            push_key, iv, raw[:-GCM_TAG_LENGTH], raw[-GCM_TAG_LENGTH:]
        ))
    except Exception:
        return None


def build_manifest(push_id: str, date_iso: str, rows) -> dict:
    return {
        "push_id": push_id,
        "date": date_iso,
        "files": [
            {"file_id": r["file_id"], "name": r["file_name"],
             "path": r["file_path"] or "", "size": r["size"],
             "retrieval_key": r["retrieval_key"]}
            for r in rows
        ],
    }


def manifest_bytes(manifest: dict) -> bytes:
    """Canonical serialisation used for both signing and transmission.

    The signature only means something if the bytes the phone verifies are the
    bytes the server signed, so there must be exactly one way to serialise.
    """
    return json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
