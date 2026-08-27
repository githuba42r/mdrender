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
    return conn.execute("SELECT client_id, name, created_at, revoked_at FROM clients").fetchall()


def revoke_client(conn, client_id):
    conn.execute("UPDATE clients SET revoked_at = ? WHERE client_id = ?",
                 (int(time.time()), client_id))
    conn.commit()
