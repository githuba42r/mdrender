# server/app/billing_worker.py
"""Periodic sweeps: trial-group expiry, pending-file expiry, deletion purges."""
import logging
import os
import time

from server.app import billing, deletions, push_store

log = logging.getLogger(__name__)


def sweep_pending_expiry(conn, config, now=None) -> int:
    """Delete pending push files older than each account's plan expiry.

    A plan expiry of 0 (or no plan and no configured TTL) means never. Returns
    how many files were removed.
    """
    now = int(now if now is not None else time.time())
    accounts = [r["account_id"] for r in conn.execute(
        "SELECT DISTINCT p.account_id FROM pushes p"
        " JOIN push_files f ON f.push_id = p.push_id"
        " WHERE f.status = 'pending' AND p.account_id IS NOT NULL")]
    removed = 0
    for account_id in accounts:
        hours = billing.pending_policy(conn, config, account_id)["expiry_hours"]
        if not hours:
            continue
        cutoff = now - hours * 3600
        for row in push_store.expired_pending_files(conn, account_id, cutoff):
            if row["stored_path"]:
                try:
                    os.remove(row["stored_path"])
                except OSError:
                    pass
            push_store.delete_file(conn, row["file_id"])
            removed += 1
    return removed


def run_forever(config, db, *, interval_seconds=300):
    while True:
        try:
            with db.connect() as conn:
                moved = billing.sweep_trial_groups(conn)
                if moved:
                    log.info("billing: moved %d account(s) out of expired trial groups",
                             moved)
                expired = sweep_pending_expiry(conn, config)
                if expired:
                    log.info("billing: expired %d pending file(s)", expired)
                purged = deletions.purge_expired(conn, config)
                if purged:
                    log.info("deletions: purged %d expired account(s)", purged)
        except Exception:  # noqa: BLE001 - the worker must never die
            log.warning("billing: sweep failed", exc_info=True)
        time.sleep(interval_seconds)
