# server/tests/test_account_deletion.py
"""Account deletion (email-confirmed, 14-day hold) and data export."""
import io
import json
import os
import re
import time
import zipfile

from server.app import accounts, deletions, push_store, storage
from server.app.app import create_app


def _app(config):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    app = create_app(config)
    app.config["TESTING"] = True
    return app


def _portal(app, email="user@example.com"):
    c = app.test_client()
    c.post("/signup", data={"email": email, "password": "longenough1"})
    c.post("/account/login", data={"email": email, "password": "longenough1"})
    with app.config["_db"].connect() as conn:
        account_id = accounts.get_account_by_email(conn, email)["account_id"]
    return c, account_id


def _admin(app):
    c = app.test_client()
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    return c


def _last_token(app):
    emails = app.config.get("_test_emails") or []
    assert emails, "no confirmation email was captured"
    match = re.search(r"token=([\w-]+)", emails[-1]["body"])
    assert match, emails[-1]["body"]
    return match.group(1)


def test_self_deletion_needs_email_confirmation_and_admin_can_restore(config,
                                                                     db_path):
    app = _app(config)
    c, account_id = _portal(app)

    resp = c.post("/account/delete-request")
    assert resp.status_code == 303
    token = _last_token(app)
    anon = app.test_client()

    # The link shows what confirming does; nothing is locked yet.
    page = anon.get(f"/account/delete/confirm?token={token}")
    assert page.status_code == 200 and b"Delete this account" in page.data
    assert c.get("/account").status_code == 200

    resp = anon.post("/account/delete/confirm", data={"token": token})
    assert resp.status_code == 303
    assert resp.headers["Location"].startswith("/login")

    # Locked: live sessions are dead and sign-in refuses.
    assert c.get("/account").status_code == 303
    locked = c.post("/login", data={"email": "user@example.com",
                                    "password": "longenough1"})
    assert locked.status_code == 403
    assert b"scheduled for deletion" in locked.data
    assert b"Deletion confirmed" in anon.get("/login?deleted=1").data

    # The operator sees it pending and restores it.
    a = _admin(app)
    listed = a.get("/accounts")
    assert b"Pending deletions" in listed.data
    assert b"self-service" in listed.data and b"user@example.com" in listed.data
    assert a.post(f"/accounts/{account_id}/restore").status_code == 303

    ok = c.post("/login", data={"email": "user@example.com",
                                "password": "longenough1"})
    assert ok.status_code == 302
    assert c.get("/account").status_code == 200


def test_admin_delete_locks_now_lists_and_restores(config, db_path):
    app = _app(config)
    c, account_id = _portal(app)
    a = _admin(app)

    assert a.post(f"/accounts/{account_id}/delete").status_code == 303
    assert not app.config.get("_test_emails"), "admin delete sends no email"
    with app.config["_db"].connect() as conn:
        row = deletions.active_for(conn, account_id)
        assert row is not None and row["confirmed_at"] is not None
        assert row["purge_after"] > time.time()
        assert row["requested_by"].startswith("admin:")

    assert c.post("/login", data={"email": "user@example.com",
                                  "password": "longenough1"}).status_code == 403

    listed = a.get("/accounts")
    assert b"Pending deletions" in listed.data
    assert b"operator" in listed.data
    assert b"deleting" in listed.data  # chip next to the account's status

    assert a.post(f"/accounts/{account_id}/restore").status_code == 303
    assert c.post("/login", data={"email": "user@example.com",
                                  "password": "longenough1"}).status_code == 302


def test_export_zip_for_self_and_operator(config, db_path):
    app = _app(config)
    c, account_id = _portal(app)
    with app.config["_db"].connect() as conn:
        conn.execute(
            "INSERT INTO devices (device_secret, device_auth, device_name,"
            " device_model, fcm_token, public_key, push_key, registered_at,"
            " last_seen, account_id, approved_at)"
            " VALUES ('sec-x', 'auth', 'Clever Juniper', 'Pixel', 'tok',"
            " 'PUB', 'PUSH', 1, 1, ?, 1)", (account_id,))
        conn.execute(
            "INSERT INTO billing_ledger (account_type, account_id,"
            " amount_cents, reason, provider_ref, created_at)"
            " VALUES ('account', ?, 1000, 'topup', 'PAY-1', 1)", (account_id,))
        conn.commit()

    resp = c.get("/account/export")
    assert resp.status_code == 200 and resp.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        names = set(zf.namelist())
        assert {"account.json", "devices.json", "clients.json", "pushes.json",
                "billing.json"} <= names
        account = json.loads(zf.read("account.json"))
        assert account["account"]["email"] == "user@example.com"
        assert "password_hash" not in account["account"]
        assert account["balance_cents"] == 1000
        assert json.loads(zf.read("devices.json"))[0]["device_name"] == \
            "Clever Juniper"

    # The binary blob for a stored pending file rides along too.
    blob = os.path.join(config.PUSH_STORAGE_DIR, "accounts",
                        account_id, "f-1")
    os.makedirs(os.path.dirname(blob), exist_ok=True)
    with open(blob, "wb") as fh:
        fh.write(b"hello export")
    with app.config["_db"].connect() as conn:
        storage.add_file(conn, file_id="f-1", account_id=account_id,
                         size=11, stored_path=blob)
    resp = c.get("/account/export")
    with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        assert zf.read("files/pending/f-1") == b"hello export"

    # The operator can pull the same archive from the Users list.
    a = _admin(app)
    op = a.get(f"/accounts/{account_id}/export")
    assert op.status_code == 200 and op.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(op.data)) as zf:
        assert "account.json" in zf.namelist()


def test_purge_after_grace_removes_data_but_keeps_billing(config, db_path):
    app = _app(config)
    c, account_id = _portal(app)
    with app.config["_db"].connect() as conn:
        conn.execute(
            "INSERT INTO devices (device_secret, device_auth, device_name,"
            " device_model, fcm_token, public_key, push_key, registered_at,"
            " last_seen, account_id, approved_at)"
            " VALUES ('sec-p', 'auth', 'Quiet Heron', 'Pixel', 'tok',"
            " 'PUB', 'PUSH', 1, 1, ?, 1)", (account_id,))
        push_store.create_push(conn, "push-p", "Quiet Heron",
                               account_id=account_id)
        push_store.add_file(conn, file_id="push-p-f1", push_id="push-p",
                            file_name="a.pdf", file_path="", size=3,
                            retrieval_key="rk", stored_path=None, created_at=1)
        conn.execute(
            "INSERT INTO billing_ledger (account_type, account_id,"
            " amount_cents, reason, provider_ref, created_at)"
            " VALUES ('account', ?, 500, 'topup', 'PAY-KEEP', 1)",
            (account_id,))
        conn.commit()

    a = _admin(app)
    assert a.post(f"/accounts/{account_id}/delete").status_code == 303

    # Age the deletion past the grace period, then run the worker's sweep.
    with app.config["_db"].connect() as conn:
        conn.execute("UPDATE account_deletions SET purge_after = ?"
                     " WHERE account_id = ?", (int(time.time()) - 1,
                                               account_id))
        conn.commit()
        assert deletions.purge_expired(conn, config) == 1

        assert accounts.get_account(conn, account_id) is None
        assert conn.execute("SELECT COUNT(*) FROM devices WHERE account_id = ?",
                            (account_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM pushes WHERE account_id = ?",
                            (account_id,)).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM push_files WHERE push_id = 'push-p'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE principal_id = ?",
            (account_id,)).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM account_deletions WHERE account_id = ?",
            (account_id,)).fetchone()[0] == 0
        # Billing history survives the purge.
        keep = conn.execute(
            "SELECT provider_ref FROM billing_ledger WHERE account_id = ?",
            (account_id,)).fetchone()
        assert keep is not None and keep["provider_ref"] == "PAY-KEEP"

    # Gone for good: the old password no longer signs anything in.
    assert c.post("/login", data={"email": "user@example.com",
                                  "password": "longenough1"}).status_code == 401


def test_bad_or_expired_confirmation_tokens_are_refused(config, db_path):
    app = _app(config)
    anon = app.test_client()
    page = anon.get("/account/delete/confirm?token=not-a-real-token")
    assert page.status_code == 400 and b"not valid" in page.data

    c, account_id = _portal(app)
    assert c.post("/account/delete-request").status_code == 303
    token = _last_token(app)
    with app.config["_db"].connect() as conn:
        stale = int(time.time()) - int(config.DELETION_EMAIL_TTL_HOURS) * 3600 - 60
        conn.execute("UPDATE account_deletions SET requested_at = ?"
                     " WHERE account_id = ?", (stale, account_id))
        conn.commit()
    page = anon.get(f"/account/delete/confirm?token={token}")
    assert page.status_code == 400 and b"not valid" in page.data
    # The account was never locked by the expired link.
    with app.config["_db"].connect() as conn:
        assert deletions.active_for(conn, account_id) is None
