# server/app/accounts.py
"""Accounts (billable tenants) and the no-PII device routing registry.

An account is hosted on exactly one server (`host`: "master" or a slave
`server_id`). The master stores only the routing tuple needed to ring a device —
opaque `account_id`/`device_id` + FCM token — never PII (design §6).
"""
import time
import uuid

from server.app.auth import hash_secret, verify_secret

ACTIVE, BLOCKED, BANNED = "active", "blocked", "banned"


def create_account(conn, email, password=None, *, host="master") -> str:
    account_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO accounts (account_id, email, password_hash, host, status,"
        " balance, created_at) VALUES (?, ?, ?, ?, 'active', 0, ?)",
        (account_id, email.strip().lower(),
         hash_secret(password) if password else None, host, int(time.time())))
    conn.commit()
    return account_id


def get_account(conn, account_id):
    return conn.execute("SELECT * FROM accounts WHERE account_id = ?",
                        (account_id,)).fetchone()


def get_account_by_email(conn, email):
    return conn.execute("SELECT * FROM accounts WHERE email = ?",
                        (email.strip().lower(),)).fetchone()


def list_accounts(conn):
    return conn.execute("SELECT * FROM accounts ORDER BY created_at").fetchall()


def set_account_status(conn, account_id, status) -> None:
    conn.execute("UPDATE accounts SET status = ? WHERE account_id = ?",
                 (status, account_id))
    conn.commit()


def verify_account_password(conn, email, password) -> str | None:
    row = get_account_by_email(conn, email)
    if row is None or row["status"] != ACTIVE or not row["password_hash"]:
        return None
    return row["account_id"] if verify_secret(password, row["password_hash"]) else None


# ---- Device routing registry (no PII) ---------------------------------------

def upsert_device(conn, *, account_id, device_id, server_id, fcm_token,
                  name=None) -> None:
    conn.execute(
        "INSERT INTO account_devices (server_id, account_id, device_id, fcm_token,"
        " name, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(server_id, account_id, device_id) DO UPDATE SET"
        " fcm_token = excluded.fcm_token, name = excluded.name,"
        " updated_at = excluded.updated_at",
        (server_id, account_id, device_id, fcm_token, name, int(time.time())))
    conn.commit()


def delete_device(conn, *, account_id, device_id, server_id) -> None:
    conn.execute(
        "DELETE FROM account_devices WHERE server_id = ? AND account_id = ?"
        " AND device_id = ?", (server_id, account_id, device_id))
    conn.commit()


def list_devices(conn, account_id, server_id):
    return conn.execute(
        "SELECT device_id, fcm_token, name, updated_at FROM account_devices"
        " WHERE account_id = ? AND server_id = ? ORDER BY updated_at",
        (account_id, server_id)).fetchall()
