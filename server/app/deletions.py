# server/app/deletions.py
"""Account deletion: an emailed confirmation, then a grace period.

A deletion row moves through: requested (an emailed one-time token, account
still usable) -> confirmed (account locked, purge_after = confirmed + the
grace period) -> purged (all data removed) or restored (row deleted).
Billing records are deliberately kept through a purge so the books stay
reconcilable; everything personal goes.
"""
import hashlib
import logging
import os
import secrets
import shutil
import time
import uuid

log = logging.getLogger(__name__)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now(now=None) -> int:
    return int(now if now is not None else time.time())


def request_self(conn, config, account) -> str:
    """Start (or restart) an emailed confirmation for this account.

    Any existing row is replaced, so only the newest link in the inbox works.
    Returns the one-time confirm token.
    """
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM account_deletions WHERE account_id = ?",
                 (account["account_id"],))
    conn.execute(
        "INSERT INTO account_deletions (deletion_id, account_id, requested_at,"
        " requested_by, token_hash) VALUES (?, ?, ?, 'self', ?)",
        (uuid.uuid4().hex, account["account_id"], _now(), _token_hash(token)))
    conn.commit()
    return token


def schedule_admin(conn, config, account_id, admin_id) -> int:
    """Operator-requested deletion: confirmed immediately, no email needed.

    Returns the purge timestamp.
    """
    conn.execute("DELETE FROM account_deletions WHERE account_id = ?",
                 (account_id,))
    now = _now()
    purge_after = now + int(config.DELETION_GRACE_DAYS) * 86400
    conn.execute(
        "INSERT INTO account_deletions (deletion_id, account_id, requested_at,"
        " requested_by, confirmed_at, purge_after) VALUES (?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, account_id, now, f"admin:{admin_id}",
         now, purge_after))
    conn.commit()
    return purge_after


def token_row(conn, token: str):
    return conn.execute("SELECT * FROM account_deletions WHERE token_hash = ?",
                        (_token_hash(token),)).fetchone()


def token_usable(conn, config, token: str, now=None):
    """The unconfirmed row behind a token, while the link is still fresh."""
    row = token_row(conn, token)
    if row is None or row["confirmed_at"] is not None:
        return None
    ttl = int(config.DELETION_EMAIL_TTL_HOURS) * 3600
    if _now(now) > row["requested_at"] + ttl:
        return None
    return row


def confirm(conn, config, token: str, now=None):
    """Confirm an emailed token: the account locks and the clock starts."""
    row = token_usable(conn, config, token, now=now)
    if row is None:
        return None
    now = _now(now)
    purge_after = now + int(config.DELETION_GRACE_DAYS) * 86400
    conn.execute(
        "UPDATE account_deletions SET confirmed_at = ?, purge_after = ?,"
        " token_hash = NULL WHERE deletion_id = ?",
        (now, purge_after, row["deletion_id"]))
    conn.commit()
    return conn.execute("SELECT * FROM account_deletions WHERE deletion_id = ?",
                        (row["deletion_id"],)).fetchone()


def active_for(conn, account_id):
    """The confirmed (locking) deletion row for an account, if any."""
    return conn.execute(
        "SELECT * FROM account_deletions"
        " WHERE account_id = ? AND confirmed_at IS NOT NULL",
        (account_id,)).fetchone()


def restore(conn, account_id) -> int:
    """Undelete: drop every deletion row for the account. Returns rows lost."""
    cur = conn.execute("DELETE FROM account_deletions WHERE account_id = ?",
                       (account_id,))
    conn.commit()
    return cur.rowcount


def list_pending(conn):
    """Confirmed deletions, newest first, with the account's identity."""
    return conn.execute(
        "SELECT d.*, a.email, a.name FROM account_deletions d"
        " JOIN accounts a ON a.account_id = d.account_id"
        " WHERE d.confirmed_at IS NOT NULL"
        " ORDER BY d.requested_at DESC").fetchall()


def purge_expired(conn, config, now=None) -> int:
    """Purge every account whose grace period has elapsed. Returns the count."""
    now = _now(now)
    rows = conn.execute(
        "SELECT * FROM account_deletions WHERE confirmed_at IS NOT NULL"
        " AND purge_after IS NOT NULL AND purge_after <= ?", (now,)).fetchall()
    purged = 0
    for row in rows:
        try:
            purge_account_data(conn, config, row["account_id"])
            conn.execute("DELETE FROM account_deletions WHERE deletion_id = ?",
                         (row["deletion_id"],))
            conn.commit()
            purged += 1
            log.info("deletions: purged account %s", row["account_id"])
        except Exception:  # noqa: BLE001 - one bad account must not stop the rest
            conn.rollback()
            log.warning("deletions: purge failed for %s",
                        row["account_id"], exc_info=True)
    return purged


def purge_account_data(conn, config, account_id) -> None:
    """Remove everything tied to one account except its billing records.

    Deletes rows by hand (the schema has no foreign keys) and unlinks the
    stored push and pending-storage blobs. The caller commits.
    """
    placeholders = lambda ids: ",".join("?" * len(ids))  # noqa: E731

    push_ids = [r["push_id"] for r in conn.execute(
        "SELECT push_id FROM pushes WHERE account_id = ?", (account_id,))]
    device_secrets = [r["device_secret"] for r in conn.execute(
        "SELECT device_secret FROM devices WHERE account_id = ?", (account_id,))]
    fed_device_ids = [r["device_id"] for r in conn.execute(
        "SELECT device_id FROM account_devices WHERE account_id = ?",
        (account_id,))]

    stored_paths = []
    if push_ids:
        stored_paths += [r["stored_path"] for r in conn.execute(
            f"SELECT stored_path FROM push_files WHERE stored_path IS NOT NULL"
            f" AND push_id IN ({placeholders(push_ids)})", push_ids)]
    stored_paths += [r["stored_path"] for r in conn.execute(
        "SELECT stored_path FROM account_files WHERE account_id = ?",
        (account_id,))]

    # Live sessions for the account (the caller's own session among them).
    conn.execute("DELETE FROM sessions WHERE principal_type = 'account'"
                 " AND principal_id = ?", (account_id,))
    conn.execute("DELETE FROM pairing_tokens WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM clients WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM account_devices WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM devices WHERE account_id = ?", (account_id,))

    crypto_ids = device_secrets + fed_device_ids
    if crypto_ids:
        conn.execute(
            f"DELETE FROM device_content_pubkeys WHERE device_id IN"
            f" ({placeholders(crypto_ids)})", crypto_ids)
        conn.execute(
            f"DELETE FROM device_content_keys WHERE device_id IN"
            f" ({placeholders(crypto_ids)})", crypto_ids)

    if push_ids:
        conn.execute(f"DELETE FROM push_files WHERE push_id IN"
                     f" ({placeholders(push_ids)})", push_ids)
    conn.execute("DELETE FROM pushes WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM account_files WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM account_quotas WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM account_keys WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM account_groups WHERE account_id = ?"
                 " AND account_type = 'account'", (account_id,))
    conn.execute("DELETE FROM account_plans WHERE account_id = ?"
                 " AND account_type = 'account'", (account_id,))
    conn.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))

    # Blobs on disk: individual files first, then their directory trees.
    for path in stored_paths:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
    storage = config.PUSH_STORAGE_DIR
    for push_id in push_ids:
        shutil.rmtree(os.path.join(storage, push_id), ignore_errors=True)
    shutil.rmtree(os.path.join(storage, "accounts", account_id),
                  ignore_errors=True)
