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


def create_account(conn, email, password=None, *, host="master", name=None) -> str:
    account_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO accounts (account_id, email, name, password_hash, host, status,"
        " balance, created_at) VALUES (?, ?, ?, ?, ?, 'active', 0, ?)",
        (account_id, email.strip().lower(), name, hash_secret(password) if password else None,
         host, int(time.time())))
    conn.commit()
    return account_id


def update_account(conn, account_id, *, name=None, email=None, password=None) -> None:
    """Edit a user/account's name, email, and (optionally) password."""
    if name is not None:
        conn.execute("UPDATE accounts SET name = ? WHERE account_id = ?",
                     (name, account_id))
    if email is not None:
        conn.execute("UPDATE accounts SET email = ? WHERE account_id = ?",
                     (email.strip().lower(), account_id))
    if password:
        conn.execute("UPDATE accounts SET password_hash = ? WHERE account_id = ?",
                     (hash_secret(password), account_id))
    conn.commit()


def delete_account(conn, account_id) -> None:
    """Remove a user/account and its device rows."""
    conn.execute("DELETE FROM account_devices WHERE account_id = ?", (account_id,))
    conn.execute("UPDATE devices SET account_id = NULL, approved_at = NULL"
                 " WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))
    conn.commit()


def device_count(conn, account_id) -> int:
    return conn.execute("SELECT COUNT(*) FROM devices WHERE account_id = ?",
                        (account_id,)).fetchone()[0]


def get_account(conn, account_id):
    return conn.execute("SELECT * FROM accounts WHERE account_id = ?",
                        (account_id,)).fetchone()


def get_account_by_email(conn, email):
    return conn.execute("SELECT * FROM accounts WHERE email = ?",
                        (email.strip().lower(),)).fetchone()


def list_accounts(conn):
    return conn.execute("SELECT * FROM accounts ORDER BY created_at").fetchall()


def increment_messages(conn, account_id, n: int = 1) -> None:
    conn.execute("UPDATE accounts SET messages_sent = messages_sent + ?"
                 " WHERE account_id = ?", (n, account_id))
    conn.commit()


def domain_allowed(conn, email) -> bool:
    """Allow signup unless the email's domain is denied (design §9).

    An explicit allow rule wins over a deny rule, so operators can exempt a
    domain from a broad deny.
    """
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    if not domain:
        return False
    rules = conn.execute("SELECT kind, domain FROM email_domain_rules").fetchall()
    allow = [r["domain"].lower() for r in rules if r["kind"] == "allow"]
    deny = [r["domain"].lower() for r in rules if r["kind"] == "deny"]

    def matches(rule):
        return domain == rule or domain.endswith("." + rule)

    if any(matches(d) for d in allow):
        return True
    if any(matches(d) for d in deny):
        return False
    return True


def add_domain_rule(conn, kind, domain) -> None:
    conn.execute("INSERT INTO email_domain_rules (kind, domain, created_at)"
                 " VALUES (?, ?, ?)", (kind, domain.strip().lower(), int(time.time())))
    conn.commit()


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


def get_device(conn, *, account_id, device_id, server_id):
    return conn.execute(
        "SELECT * FROM account_devices WHERE server_id = ? AND account_id = ?"
        " AND device_id = ?", (server_id, account_id, device_id)).fetchone()


def list_devices(conn, account_id, server_id):
    return conn.execute(
        "SELECT device_id, fcm_token, name, updated_at FROM account_devices"
        " WHERE account_id = ? AND server_id = ? ORDER BY updated_at",
        (account_id, server_id)).fetchall()
