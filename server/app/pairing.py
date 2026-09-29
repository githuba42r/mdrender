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


def create_pairing_token(conn, ttl_minutes: int, *, account_id: str | None = None) -> str:
    """Mint a single-use pairing token.

    When *account_id* is given the token binds the registering device to that
    account. The account already authorised the pairing by minting the token
    from their signed-in session, so such a device is approved on registration.
    """
    token = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO pairing_tokens (token, expires_at, used, account_id) VALUES (?, ?, 0, ?)",
        (token, int(time.time()) + ttl_minutes * 60, account_id),
    )
    conn.commit()
    return token


def approve_device(conn, device_secret, now: float | None = None) -> None:
    conn.execute("UPDATE devices SET approved_at = ? WHERE device_secret = ?",
                 (int(now or time.time()), device_secret))
    conn.commit()


def consume_pairing_token(conn, token: str, now: float | None = None) -> bool:
    now = now or time.time()
    row = conn.execute("SELECT expires_at, used FROM pairing_tokens WHERE token = ?",
                       (token,)).fetchone()
    if row is None or row["used"] or now > row["expires_at"]:
        return False
    conn.execute("UPDATE pairing_tokens SET used = 1 WHERE token = ?", (token,))
    conn.commit()
    return True


def pairing_payload(server_url, token):
    # Deliberately minimal. The QR only has to name the server and carry a
    # one-time token; the server public key is handed back in the registration
    # response instead of being encoded here. A 3072-bit key costs ~740 base64
    # characters, which is enough on its own to force a high-density QR that is
    # painful to scan from a phone held over a screen. The token already proves
    # the user stood at this server's authenticated /pair page, so a key
    # delivered over that same TLS connection is no less trustworthy than one
    # carried in the QR.
    return {"v": 1, "server_url": server_url, "token": token}


def build_pairing_qr(server_url, token) -> str:
    import json
    return json.dumps(pairing_payload(server_url, token))


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
    token_row = conn.execute("SELECT account_id FROM pairing_tokens WHERE token = ?",
                             (pairing_token,)).fetchone()
    account_id = token_row["account_id"] if token_row else None
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
    now = int(time.time())
    # An account-bound device is approved on the spot: the account proved
    # authorisation by minting the pairing token while signed in. Admin-paired
    # devices (no account) still require manual approval.
    approved_at = now if account_id else None
    conn.execute(
        "INSERT OR REPLACE INTO devices (device_secret, device_auth, device_name, fcm_token,"
        " public_key, push_key, registered_at, last_seen, account_id, approved_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (device_secret, device_auth, device_name, fcm_token, public_key_b64,
         push_key_b64, now, now, account_id, approved_at),
    )
    conn.commit()
    return device_auth, displaced
