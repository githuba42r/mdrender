# server/app/pairing.py
import base64
import hashlib
import time
import uuid

from server.app import crypto

# Device helpers live in store.py; re-export for the registry module's public
# surface so callers can import them from server.app.pairing.
from server.app.store import (
    check_device, get_device_by_name, update_device_name, update_device_push_key,
    update_device_token,
)


def create_pairing_token(conn, ttl_minutes: int) -> str:
    token = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO pairing_tokens (token, expires_at, used) VALUES (?, ?, 0)",
        (token, int(time.time()) + ttl_minutes * 60),
    )
    conn.commit()
    return token


def consume_pairing_token(conn, token: str, now: float | None = None) -> bool:
    now = now or time.time()
    row = conn.execute("SELECT expires_at, used FROM pairing_tokens WHERE token = ?",
                       (token,)).fetchone()
    if row is None or row["used"] or now > row["expires_at"]:
        return False
    conn.execute("UPDATE pairing_tokens SET used = 1 WHERE token = ?", (token,))
    conn.commit()
    return True


def pairing_payload(server_url, server_pk_b64, token, expires_iso):
    return {"v": 1, "server_url": server_url, "pk": server_pk_b64,
            "token": token, "expires": expires_iso}


def build_pairing_qr(server_url, server_pk_b64, token, expires_iso) -> str:
    import json
    return json.dumps(pairing_payload(server_url, server_pk_b64, token, expires_iso))


def registration_proof_input(device_secret, device_name, fcm_token, public_key_b64,
                             push_key_b64) -> bytes:
    """The exact bytes a device signs to prove key possession at pairing.

    push_key is last so that it is inside the signature: without that, an
    attacker who could rewrite the request body could substitute their own
    doorbell key and read every subsequent trigger.
    """
    return hashlib.sha256(
        f"{device_secret}{device_name}{fcm_token}{public_key_b64}{push_key_b64}".encode()
    ).digest()


def register_device(conn, *, device_secret, device_name, fcm_token, public_key_b64,
                    push_key_b64, pairing_token, sig_b64):
    if not push_key_b64:
        return None, None
    if not consume_pairing_token(conn, pairing_token):
        return None, None
    public_key = crypto.public_from_spki_der(base64.b64decode(public_key_b64))
    data = registration_proof_input(
        device_secret, device_name, fcm_token, public_key_b64, push_key_b64
    )
    if not crypto.verify(public_key, data, base64.b64decode(sig_b64)):
        return None, None

    displaced = None
    existing = conn.execute(
        "SELECT device_secret FROM devices WHERE device_name = ?", (device_name,)
    ).fetchone()
    if existing and existing["device_secret"] != device_secret:
        displaced = existing["device_secret"]
        conn.execute("DELETE FROM devices WHERE device_secret = ?", (displaced,))

    device_auth = uuid.uuid4().hex
    conn.execute(
        "INSERT OR REPLACE INTO devices (device_secret, device_auth, device_name, fcm_token,"
        " public_key, push_key, registered_at, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (device_secret, device_auth, device_name, fcm_token, public_key_b64,
         push_key_b64, int(time.time()), int(time.time())),
    )
    conn.commit()
    return device_auth, displaced
