# server/app/storage.py
"""Per-account pending storage, quotas, and age/usage sweeps (design §10)."""
import os
import time

DEFAULT_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_FILES = 1000
DEFAULT_MAX_AGE_HOURS = 24 * 7


def get_quota(conn, account_id):
    return conn.execute("SELECT * FROM account_quotas WHERE account_id = ?",
                        (account_id,)).fetchone()


def set_quota(conn, account_id, *, max_bytes=None, max_files=None,
              max_age_hours=None) -> None:
    conn.execute(
        "INSERT INTO account_quotas (account_id, max_bytes, max_files, max_age_hours)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(account_id) DO UPDATE SET max_bytes = excluded.max_bytes,"
        " max_files = excluded.max_files, max_age_hours = excluded.max_age_hours",
        (account_id, max_bytes, max_files, max_age_hours))
    conn.commit()


def effective_quota(conn, account_id, config) -> dict:
    """Per-account overrides falling back to the configured defaults."""
    row = get_quota(conn, account_id)
    pick = lambda col, default: (row[col] if row and row[col] is not None else default)  # noqa: E731
    return {
        "max_bytes": pick("max_bytes", getattr(config, "ACCOUNT_MAX_BYTES", DEFAULT_MAX_BYTES)),
        "max_files": pick("max_files", getattr(config, "ACCOUNT_MAX_FILES", DEFAULT_MAX_FILES)),
        "max_age_hours": pick("max_age_hours", getattr(config, "ACCOUNT_MAX_AGE_HOURS", DEFAULT_MAX_AGE_HOURS)),
    }


def usage(conn, account_id) -> dict:
    row = conn.execute(
        "SELECT COALESCE(SUM(size), 0) AS bytes, COUNT(*) AS files"
        " FROM account_files WHERE account_id = ? AND status = 'pending'",
        (account_id,)).fetchone()
    return {"bytes": row["bytes"], "files": row["files"]}


def can_store(conn, account_id, size, config) -> bool:
    quota = effective_quota(conn, account_id, config)
    used = usage(conn, account_id)
    return (used["files"] + 1 <= quota["max_files"]
            and used["bytes"] + size <= quota["max_bytes"])


def add_file(conn, *, file_id, account_id, size, stored_path, alg=None, nonce=None,
             created_at=None) -> None:
    conn.execute(
        "INSERT INTO account_files (file_id, account_id, size, stored_path, alg,"
        " nonce, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (file_id, account_id, size, stored_path, alg, nonce,
         int(created_at or time.time())))
    conn.commit()


def purge_expired(conn, config, now=None) -> list[str]:
    """Delete pending files older than each account's max age; returns their ids."""
    now = int(now or time.time())
    removed = []
    accounts = [r["account_id"] for r in conn.execute(
        "SELECT DISTINCT account_id FROM account_files WHERE status = 'pending'")]
    for account_id in accounts:
        cutoff = now - effective_quota(conn, account_id, config)["max_age_hours"] * 3600
        rows = conn.execute(
            "SELECT file_id, stored_path FROM account_files WHERE account_id = ?"
            " AND status = 'pending' AND created_at < ?", (account_id, cutoff)).fetchall()
        for row in rows:
            try:
                os.remove(row["stored_path"])
            except OSError:
                pass
            conn.execute("DELETE FROM account_files WHERE file_id = ?", (row["file_id"],))
            removed.append(row["file_id"])
    conn.commit()
    return removed
