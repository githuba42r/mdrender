# server/app/data_export.py
"""Build a ZIP holding everything the server knows about one account.

JSON for the structured records (password hashes and secret hashes are
dropped), plus the stored push and pending-storage blobs under files/.
Billing records are included: they are part of the account's own history.
"""
import io
import json
import os
import re
import zipfile

from server.app import billing


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _add_json(zf, name, payload):
    zf.writestr(name, json.dumps(payload, indent=2, sort_keys=True,
                                 default=str))


def account_zip(conn, config, account_id) -> tuple[bytes, str]:
    """Returns (zip bytes, suggested download name)."""
    account = dict(conn.execute(
        "SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone())
    account.pop("password_hash", None)

    plan = _rows(conn,
                 "SELECT p.* FROM billing_plans p"
                 " JOIN account_plans ap ON ap.plan_id = p.plan_id"
                 " WHERE ap.account_type = 'account' AND ap.account_id = ?",
                 (account_id,))
    group = _rows(conn,
                  "SELECT g.* FROM billing_groups g"
                  " JOIN account_groups ag ON ag.group_id = g.group_id"
                  " WHERE ag.account_type = 'account' AND ag.account_id = ?",
                  (account_id,))
    devices = _rows(conn, "SELECT * FROM devices WHERE account_id = ?",
                    (account_id,))
    clients = _rows(conn, "SELECT * FROM clients WHERE account_id = ?",
                    (account_id,))
    for c in clients:
        c.pop("client_secret_hash", None)
    pushes = _rows(conn, "SELECT * FROM pushes WHERE account_id = ?",
                   (account_id,))
    push_files = _rows(conn,
                       "SELECT f.* FROM push_files f JOIN pushes p"
                       " ON p.push_id = f.push_id WHERE p.account_id = ?",
                       (account_id,))
    for f in push_files:
        f.pop("stored_path", None)
    pending_files = _rows(conn,
                          "SELECT file_id, size, alg, nonce, status,"
                          " created_at FROM account_files WHERE account_id = ?",
                          (account_id,))
    ledger = _rows(conn,
                   "SELECT * FROM billing_ledger WHERE account_type = 'account'"
                   " AND account_id = ? ORDER BY created_at", (account_id,))
    orders = _rows(conn,
                   "SELECT * FROM billing_orders WHERE account_type = 'account'"
                   " AND account_id = ? ORDER BY created_at", (account_id,))
    deletions = _rows(conn, "SELECT * FROM account_deletions WHERE account_id = ?",
                      (account_id,))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        _add_json(zf, "account.json", {"account": account, "plan": plan,
                                       "group": group,
                                       "balance_cents": billing.balance(
                                           conn, billing.SCOPE_ACCOUNT,
                                           account_id)})
        _add_json(zf, "devices.json", devices)
        _add_json(zf, "clients.json", clients)
        _add_json(zf, "pushes.json", pushes)
        _add_json(zf, "push_files.json", push_files)
        _add_json(zf, "pending_files.json", pending_files)
        _add_json(zf, "billing.json", {"ledger": ledger, "orders": orders})
        _add_json(zf, "deletions.json", deletions)

        for f in push_files:
            path = f.get("stored_path")
            if path and os.path.exists(path):
                safe = os.path.basename(path) or f["file_id"]
                zf.write(path, f"files/pushes/{f['push_id']}/{safe}")
        for row in conn.execute(
                "SELECT stored_path FROM account_files WHERE account_id = ?",
                (account_id,)):
            path = row["stored_path"]
            if path and os.path.exists(path):
                zf.write(path, "files/pending/" + os.path.basename(path))

    local = re.sub(r"[^A-Za-z0-9._-]+", "-", account.get("email") or
                   account_id).strip("-") or "account"
    return buf.getvalue(), f"mdrender-export-{local}.zip"
