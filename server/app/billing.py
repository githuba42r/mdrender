# server/app/billing.py
"""Prepaid billing foundation (design §11): plans, groups, entitlements, ledger.

An account belongs to exactly one group; the effective plan is the account's
own plan (override) else its group's plan. A default group holds accounts with
no explicit group. Cycles are anniversary-based (D12). A manual provider adds
credits; real gateways plug in behind the same ledger.
"""
import math
import time
import uuid

SCOPE_SLAVE = "slave"
SCOPE_ACCOUNT = "account"


# ---- Plans ------------------------------------------------------------------

def create_plan(conn, name, scope, *, price_cents=0, currency="AUD",
                interval="month", included_bytes=0, included_messages=0,
                storage_cents_per_mb=0, message_cents_per_1000=0) -> str:
    plan_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO billing_plans (plan_id, name, scope, price_cents, currency,"
        " interval, included_bytes, included_messages, storage_cents_per_mb,"
        " message_cents_per_1000, active, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (plan_id, name, scope, price_cents, currency, interval,
         included_bytes, included_messages, storage_cents_per_mb,
         message_cents_per_1000, int(time.time())))
    conn.commit()
    return plan_id


def get_plan(conn, plan_id):
    return conn.execute("SELECT * FROM billing_plans WHERE plan_id = ?",
                        (plan_id,)).fetchone()


def list_plans(conn):
    return conn.execute("SELECT * FROM billing_plans ORDER BY created_at").fetchall()


# ---- Groups -----------------------------------------------------------------

def ensure_default_group(conn) -> str:
    row = conn.execute("SELECT group_id FROM billing_groups WHERE is_default = 1").fetchone()
    if row:
        return row["group_id"]
    group_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO billing_groups (group_id, name, plan_id, is_default, created_at)"
        " VALUES (?, 'Default', NULL, 1, ?)", (group_id, int(time.time())))
    conn.commit()
    return group_id


def create_group(conn, name, *, plan_id=None) -> str:
    group_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO billing_groups (group_id, name, plan_id, is_default, created_at)"
        " VALUES (?, ?, ?, 0, ?)", (group_id, name, plan_id, int(time.time())))
    conn.commit()
    return group_id


def list_groups(conn):
    return conn.execute("SELECT * FROM billing_groups ORDER BY created_at").fetchall()


def set_group_plan(conn, group_id, plan_id) -> None:
    conn.execute("UPDATE billing_groups SET plan_id = ? WHERE group_id = ?",
                 (plan_id, group_id))
    conn.commit()


def assign_account_group(conn, account_type, account_id, group_id) -> None:
    conn.execute(
        "INSERT INTO account_groups (account_type, account_id, group_id)"
        " VALUES (?, ?, ?) ON CONFLICT(account_type, account_id) DO UPDATE SET"
        " group_id = excluded.group_id", (account_type, account_id, group_id))
    conn.commit()


def set_account_plan(conn, account_type, account_id, plan_id) -> None:
    conn.execute(
        "INSERT INTO account_plans (account_type, account_id, plan_id)"
        " VALUES (?, ?, ?) ON CONFLICT(account_type, account_id) DO UPDATE SET"
        " plan_id = excluded.plan_id", (account_type, account_id, plan_id))
    conn.commit()


def effective_plan(conn, account_type, account_id):
    """Account plan override → its group plan → the default group's plan."""
    row = conn.execute(
        "SELECT plan_id FROM account_plans WHERE account_type = ? AND account_id = ?",
        (account_type, account_id)).fetchone()
    if row:
        return get_plan(conn, row["plan_id"])
    group = conn.execute(
        "SELECT g.plan_id FROM account_groups ag JOIN billing_groups g"
        " ON g.group_id = ag.group_id WHERE ag.account_type = ? AND ag.account_id = ?",
        (account_type, account_id)).fetchone()
    if group and group["plan_id"]:
        return get_plan(conn, group["plan_id"])
    default = conn.execute("SELECT plan_id FROM billing_groups WHERE is_default = 1").fetchone()
    if default and default["plan_id"]:
        return get_plan(conn, default["plan_id"])
    return None


# ---- Ledger -----------------------------------------------------------------

def add_credit(conn, account_type, account_id, amount_cents, *, reason=None,
               provider_ref=None) -> int:
    """Manual provider: add credits. Returns the new balance (cents)."""
    conn.execute(
        "INSERT INTO billing_ledger (account_type, account_id, amount_cents, reason,"
        " provider_ref, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (account_type, account_id, amount_cents, reason, provider_ref, int(time.time())))
    conn.commit()
    return balance(conn, account_type, account_id)


def balance(conn, account_type, account_id) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM billing_ledger"
        " WHERE account_type = ? AND account_id = ?",
        (account_type, account_id)).fetchone()[0]


def debit(conn, account_type, account_id, amount_cents, *, reason=None) -> int:
    """Charge an account (metering); returns the new balance (cents)."""
    return add_credit(conn, account_type, account_id, -abs(int(amount_cents)),
                      reason=reason or "usage")


def entitled(conn, account_type, account_id) -> bool:
    """A billable account may operate if it has a plan or a positive balance."""
    if effective_plan(conn, account_type, account_id) is not None:
        return True
    return balance(conn, account_type, account_id) > 0


def bill_storage(conn, config, *, now=None, min_interval_hours=24) -> list[str]:
    """Charge accounts for pending bytes older than the bill threshold.

    Charges at the account's effective-plan rate ($ per MB, rounded up),
    anniversary-neutral. Idempotent within ``min_interval_hours`` per account.
    Returns the account ids charged. Meant to run on a slow schedule.
    """
    now = int(now or time.time())
    cutoff = now - getattr(config, "STORAGE_BILL_AFTER_HOURS", 1) * 3600
    charged = []
    account_ids = [r["account_id"] for r in conn.execute(
        "SELECT DISTINCT account_id FROM account_files"
        " WHERE status = 'pending' AND created_at < ?", (cutoff,))]
    for account_id in account_ids:
        plan = effective_plan(conn, SCOPE_ACCOUNT, account_id)
        rate = plan["storage_cents_per_mb"] if plan else 0
        if not rate:
            continue
        last = conn.execute(
            "SELECT MAX(created_at) FROM billing_ledger WHERE account_type = ?"
            " AND account_id = ? AND reason = 'storage'",
            (SCOPE_ACCOUNT, account_id)).fetchone()[0]
        if last and now - last < min_interval_hours * 3600:
            continue
        aged = conn.execute(
            "SELECT COALESCE(SUM(size), 0) FROM account_files WHERE account_id = ?"
            " AND status = 'pending' AND created_at < ?", (account_id, cutoff)).fetchone()[0]
        if aged <= 0:
            continue
        mb = math.ceil(aged / (1024 * 1024))
        debit(conn, SCOPE_ACCOUNT, account_id, rate * mb, reason="storage")
        charged.append(account_id)
    return charged
