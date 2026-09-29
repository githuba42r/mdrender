# server/app/billing.py
"""Prepaid billing foundation (design §11): plans, groups, entitlements, ledger.

An account belongs to exactly one group; the effective plan is the account's
own plan (override) else its group's plan. A default group holds accounts with
no explicit group. Cycles are anniversary-based (D12). A manual provider adds
credits; real gateways plug in behind the same ledger.
"""
import time
import uuid

SCOPE_SLAVE = "slave"
SCOPE_ACCOUNT = "account"


# ---- Plans ------------------------------------------------------------------

def create_plan(conn, name, scope, *, price_cents=0, currency="AUD",
                interval="month", included_bytes=0, included_messages=0) -> str:
    plan_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO billing_plans (plan_id, name, scope, price_cents, currency,"
        " interval, included_bytes, included_messages, active, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (plan_id, name, scope, price_cents, currency, interval,
         included_bytes, included_messages, int(time.time())))
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
