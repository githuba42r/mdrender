# server/app/federation.py
"""Federation registry, signed requests, nonces, and the master→slave outbox.

Security model (design §5/§5a): a slave holds a federation RSA keypair minted at
first run; the master verifies every slave request with the slave's public key
over a canonical request digest, rejecting stale timestamps and replayed
nonces. See `docs/superpowers/specs/2026-09-29-federated-push-server-design.md`.
"""
import base64
import hashlib
import json
import sqlite3
import time
import urllib.request
import uuid

from cryptography.hazmat.primitives import serialization

from server.app import crypto
from server.app.auth import hash_secret, verify_secret

SIGNATURE_WINDOW_SECONDS = 300
NONCE_TTL_SECONDS = 600


# ---- Signing ----------------------------------------------------------------

def _load_private(pem: str):
    return serialization.load_pem_private_key(pem.encode(), password=None)


def new_challenge() -> str:
    return uuid.uuid4().hex


def sign_bytes(private_pem: str, data: bytes) -> str:
    """Base64 signature over arbitrary bytes with this server's key."""
    return base64.b64encode(crypto.sign(_load_private(private_pem), data)).decode()


def canonical_request(method: str, path: str, timestamp: int | str,
                      nonce: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body or b"").hexdigest()
    return "\n".join([method.upper(), path, str(timestamp), nonce, digest]).encode()


def sign_request(private_pem: str, method: str, path: str, body: bytes = b"") -> dict:
    """Return the federation signature headers for a request."""
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    sig = crypto.sign(_load_private(private_pem),
                      canonical_request(method, path, ts, nonce, body))
    return {
        "X-Federation-Timestamp": str(ts),
        "X-Federation-Nonce": nonce,
        "X-Federation-Signature": base64.b64encode(sig).decode(),
    }


def verify_request(conn, server_row, method, path, body, headers) -> bool:
    """Verify a signed slave→master request and burn its nonce.

    Returns False on a missing header, a stale timestamp, a bad signature, or a
    replayed nonce.
    """
    ts = headers.get("X-Federation-Timestamp")
    nonce = headers.get("X-Federation-Nonce")
    sig = headers.get("X-Federation-Signature")
    if not ts or not nonce or not sig:
        return False
    try:
        ts_i = int(ts)
        sig_bytes = base64.b64decode(sig)
        pub = crypto.public_from_spki_der(base64.b64decode(server_row["public_key"]))
    except Exception:  # noqa: BLE001 - malformed input is just a failure
        return False
    if abs(time.time() - ts_i) > SIGNATURE_WINDOW_SECONDS:
        return False
    if not crypto.verify(pub, canonical_request(method, path, ts, nonce, body), sig_bytes):
        return False
    return mark_nonce(conn, server_row["server_id"], nonce)


def mark_nonce(conn, server_id: str, nonce: str, now: int | None = None) -> bool:
    """Record a nonce; False if it was already seen (a replay)."""
    now = int(now or time.time())
    conn.execute("DELETE FROM federated_nonces WHERE expires_at < ?", (now,))
    try:
        conn.execute(
            "INSERT INTO federated_nonces (server_id, nonce, expires_at) VALUES (?, ?, ?)",
            (server_id, nonce, now + NONCE_TTL_SECONDS))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


# ---- Registry ---------------------------------------------------------------

def get_server(conn, server_id):
    return conn.execute("SELECT * FROM federated_servers WHERE server_id = ?",
                        (server_id,)).fetchone()


def list_servers(conn):
    return conn.execute("SELECT * FROM federated_servers ORDER BY created_at").fetchall()


def register_active(conn, *, server_id, hostname, base_url, public_key_b64):
    """Record a verified slave and issue its federation secret.

    Returns the plaintext secret (only the hash is stored). The caller returns it
    to the slave exactly once.
    """
    secret = uuid.uuid4().hex
    conn.execute(
        "INSERT OR REPLACE INTO federated_servers"
        " (server_id, hostname, base_url, public_key, secret_hash, status,"
        "  created_at, last_seen)"
        " VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
        (server_id, hostname, base_url, public_key_b64, hash_secret(secret),
         int(time.time()), int(time.time())),
    )
    conn.commit()
    return secret


def check_bearer(conn, token: str | None):
    """Resolve a ``server_id.secret`` bearer token to an active slave row."""
    if not token or "." not in token:
        return None
    server_id, secret = token.split(".", 1)
    row = get_server(conn, server_id)
    if row is None or row["status"] in ("revoked", "banned"):
        return None
    if not row["secret_hash"] or not verify_secret(secret, row["secret_hash"]):
        return None
    return row


def touch_seen(conn, server_id, *, up: bool = True) -> None:
    now = int(time.time())
    if up:
        conn.execute("UPDATE federated_servers SET last_seen = ?, status = 'active',"
                     " down_since = NULL WHERE server_id = ?", (now, server_id))
    else:
        conn.execute("UPDATE federated_servers SET down_since = ? WHERE server_id = ?",
                     (now, server_id))
    conn.commit()


def set_status(conn, server_id, status) -> None:
    conn.execute("UPDATE federated_servers SET status = ? WHERE server_id = ?",
                 (status, server_id))
    conn.commit()


def revoke_server(conn, server_id) -> None:
    conn.execute("UPDATE federated_servers SET status = 'revoked', secret_hash = NULL"
                 " WHERE server_id = ?", (server_id,))
    conn.commit()


def delete_server(conn, server_id) -> None:
    conn.execute("DELETE FROM federated_servers WHERE server_id = ?", (server_id,))
    conn.execute("DELETE FROM federated_outbox WHERE server_id = ?", (server_id,))
    conn.commit()


# ---- Outbox (master → slave) ------------------------------------------------

def enqueue(conn, server_id: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO federated_outbox (server_id, payload, created_at, next_retry_at)"
        " VALUES (?, ?, ?, ?)",
        (server_id, json.dumps(payload), int(time.time()), int(time.time())))
    conn.commit()


def pending_outbox(conn, server_id: str):
    return conn.execute(
        "SELECT id, payload FROM federated_outbox"
        " WHERE server_id = ? AND acked_at IS NULL ORDER BY id", (server_id,)).fetchall()


def mark_outbox_sent(conn, ids) -> None:
    if not ids:
        return
    conn.executemany("UPDATE federated_outbox SET acked_at = ? WHERE id = ?",
                     [(int(time.time()), i) for i in ids])
    conn.commit()


# ---- Slave callback (master → slave) ----------------------------------------

def verify_slave_callback(base_url: str, challenge: str, *, timeout: int = 10) -> str:
    """Ask the slave to sign *challenge*; return its signature.

    POSTs to the slave's federation verify endpoint. Raises on transport errors;
    the caller treats any failure as "not verified".
    """
    url = base_url.rstrip("/") + "/api/federation/verify"
    data = json.dumps({"challenge": challenge}).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read()).get("signature", "")


def verify_callback_signature(public_key_b64: str, challenge: str, signature_b64: str) -> bool:
    try:
        pub = crypto.public_from_spki_der(base64.b64decode(public_key_b64))
        return crypto.verify(pub, challenge.encode(), base64.b64decode(signature_b64))
    except Exception:  # noqa: BLE001
        return False
