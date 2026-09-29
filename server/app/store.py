# server/app/store.py
import base64
import hashlib
import time
import uuid

from cryptography.hazmat.primitives import serialization

from server.app import crypto


def get_or_create_server_keypair(conn):
    row = conn.execute("SELECT private_key_pem FROM server_keys WHERE id = 1").fetchone()
    if row is None:
        priv, pub = crypto.generate_rsa_keypair()
        pem = crypto.private_to_pem(priv).decode()
        conn.execute(
            "INSERT INTO server_keys (id, private_key_pem, created_at) VALUES (1, ?, ?)",
            (pem, int(time.time())),
        )
        conn.commit()
    else:
        pem = row["private_key_pem"]
    # Rebuild pub from private to keep a single source of truth:
    priv = _load_private(pem)
    pub = priv.public_key()
    return pem, base64.b64encode(crypto.public_to_spki_der(pub)).decode()


def _load_private(pem: str):
    return serialization.load_pem_private_key(pem.encode(), password=None)


def session_secret_from_pem(pem: str) -> str:
    return hashlib.sha256(pem.encode()).hexdigest()


def create_client(conn, name: str, secret_hash: str) -> str:
    client_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO clients (client_id, client_secret_hash, name, scopes, created_at, revoked_at)"
        " VALUES (?, ?, ?, 'push', ?, NULL)",
        (client_id, secret_hash, name, int(time.time())),
    )
    conn.commit()
    return client_id


def get_client(conn, client_id):
    return conn.execute("SELECT * FROM clients WHERE client_id = ?", (client_id,)).fetchone()


def list_clients(conn):
    """Active clients only; a revoked client disappears from the admin list."""
    return conn.execute(
        "SELECT client_id, name, created_at, revoked_at FROM clients "
        "WHERE revoked_at IS NULL ORDER BY created_at"
    ).fetchall()


def revoke_client(conn, client_id):
    conn.execute("UPDATE clients SET revoked_at = ? WHERE client_id = ?",
                 (int(time.time()), client_id))
    conn.commit()


def update_device_token(conn, device_secret, device_auth, new_token) -> bool:
    cur = conn.execute(
        "UPDATE devices SET fcm_token = ? WHERE device_secret = ? AND device_auth = ?",
        (new_token, device_secret, device_auth),
    )
    conn.commit()
    return cur.rowcount > 0


def update_device_name(conn, device_secret, device_auth, new_name) -> bool:
    try:
        cur = conn.execute(
            "UPDATE devices SET device_name = ? WHERE device_secret = ? AND device_auth = ?",
            (new_name, device_secret, device_auth),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        return False  # name already taken by another device


def update_device_push_key(conn, device_secret, device_auth, new_push_key_b64) -> bool:
    """Replace a device's doorbell key.

    This is the revocation lever for a leaked push_key: once rotated, previously
    captured doorbells no longer open, though Firebase may still hold undelivered
    ones.
    """
    cur = conn.execute(
        "UPDATE devices SET push_key = ? WHERE device_secret = ? AND device_auth = ?",
        (new_push_key_b64, device_secret, device_auth),
    )
    conn.commit()
    return cur.rowcount > 0


def check_device(conn, device_secret, device_auth) -> bool:
    return conn.execute(
        "SELECT 1 FROM devices WHERE device_secret = ? AND device_auth = ?",
        (device_secret, device_auth),
    ).fetchone() is not None


def touch_last_seen(conn, device_secret):
    conn.execute("UPDATE devices SET last_seen = ? WHERE device_secret = ?",
                 (int(time.time()), device_secret))
    conn.commit()


def get_device_by_name(conn, name):
    return conn.execute("SELECT * FROM devices WHERE device_name = ?", (name,)).fetchone()


def get_device_by_secret(conn, device_secret):
    return conn.execute("SELECT * FROM devices WHERE device_secret = ?",
                        (device_secret,)).fetchone()


def delete_device(conn, device_secret):
    conn.execute("DELETE FROM devices WHERE device_secret = ?", (device_secret,))
    conn.commit()


def list_devices(conn):
    return conn.execute(
        "SELECT device_secret, device_name, registered_at, last_seen FROM devices ORDER BY registered_at"
    ).fetchall()


def sweep_stale_devices(conn, ttl_days: int) -> list[str]:
    cutoff = int(time.time()) - ttl_days * 86400
    rows = conn.execute("SELECT device_secret FROM devices WHERE last_seen < ?", (cutoff,)).fetchall()
    for r in rows:
        conn.execute("DELETE FROM devices WHERE device_secret = ?", (r["device_secret"],))
    conn.commit()
    return [r["device_secret"] for r in rows]
