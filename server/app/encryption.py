# server/app/encryption.py
"""Server-blind content keys (design §7a).

The server never sees a private key or the content key. It stores only the
account's **content public key** and a per-device **opaque sealed CEK**; the
push client seals the CEK to the app's content public key, and only the app can
open it. The Android app change (a decryption-capable Keystore key) is Phase J.
"""
import time


def set_account_public_key(conn, account_id, public_key_b64) -> None:
    conn.execute(
        "INSERT INTO account_keys (account_id, public_key, created_at) VALUES (?, ?, ?)"
        " ON CONFLICT(account_id) DO UPDATE SET public_key = excluded.public_key,"
        " created_at = excluded.created_at, retired_at = NULL",
        (account_id, public_key_b64, int(time.time())))
    conn.commit()


def get_account_public_key(conn, account_id):
    row = conn.execute("SELECT public_key FROM account_keys WHERE account_id = ?",
                       (account_id,)).fetchone()
    return row["public_key"] if row else None


def set_sealed_cek(conn, device_id, sealed_cek, *, alg="rsa-oaep-sha256") -> None:
    conn.execute(
        "INSERT INTO device_content_keys (device_id, sealed_cek, alg, created_at)"
        " VALUES (?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET"
        " sealed_cek = excluded.sealed_cek, alg = excluded.alg,"
        " created_at = excluded.created_at, retired_at = NULL",
        (device_id, sealed_cek, alg, int(time.time())))
    conn.commit()


def get_sealed_cek(conn, device_id):
    return conn.execute("SELECT sealed_cek, alg FROM device_content_keys"
                        " WHERE device_id = ?", (device_id,)).fetchone()
