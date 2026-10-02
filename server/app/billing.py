# server/app/billing.py
"""Prepaid billing foundation (design §11): plans, groups, entitlements, ledger.

An account belongs to exactly one group; the effective plan is the account's
own plan (override) else its group's plan. A default group holds accounts with
no explicit group. Cycles are anniversary-based (D12). A manual provider adds
credits; real gateways plug in behind the same ledger.

PayPal is the live gateway (see ``paypal.py``): recurring subscriptions cover
both plan scopes (a flat fee for a federated server, a plan for a user
account) and one-time orders top up prepaid credit. This module owns every
local row the gateway drives - the client itself stays database-free.
"""
import math
import time
import uuid

from server.app import paypal, settings as server_settings

SCOPE_SLAVE = "slave"
SCOPE_ACCOUNT = "account"


# ---- Plans ------------------------------------------------------------------

def create_plan(conn, name, scope, *, price_cents=0, currency="AUD",
                interval="month", included_bytes=0, included_messages=0,
                storage_cents_per_mb=0, message_cents_per_1000=0,
                max_messages_per_month=0, storage_grace_days=0,
                max_pending_bytes=0, pending_expiry_hours=0) -> str:
    plan_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO billing_plans (plan_id, name, scope, price_cents, currency,"
        " interval, included_bytes, included_messages, storage_cents_per_mb,"
        " message_cents_per_1000, max_messages_per_month, storage_grace_days,"
        " max_pending_bytes, pending_expiry_hours, active, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (plan_id, name, scope, price_cents, currency, interval,
         included_bytes, included_messages, storage_cents_per_mb,
         message_cents_per_1000, max_messages_per_month, storage_grace_days,
         max_pending_bytes, pending_expiry_hours, int(time.time())))
    conn.commit()
    return plan_id


def get_plan(conn, plan_id):
    return conn.execute("SELECT * FROM billing_plans WHERE plan_id = ?",
                        (plan_id,)).fetchone()


def list_plans(conn):
    return conn.execute("SELECT * FROM billing_plans ORDER BY created_at").fetchall()


def update_plan(conn, plan_id, **fields) -> None:
    """Update the given plan columns (only known, non-None fields are applied)."""
    allowed = ("name", "scope", "price_cents", "currency", "interval",
               "included_bytes", "included_messages", "storage_cents_per_mb",
               "message_cents_per_1000", "max_messages_per_month",
               "storage_grace_days", "max_pending_bytes", "pending_expiry_hours",
               "active")
    pairs = [(k, v) for k, v in fields.items() if k in allowed and v is not None]
    if not pairs:
        return
    assignments = ", ".join(f"{k} = ?" for k, _ in pairs)
    conn.execute(f"UPDATE billing_plans SET {assignments} WHERE plan_id = ?",
                 (*[v for _, v in pairs], plan_id))
    conn.commit()


def set_plan_active(conn, plan_id, active) -> None:
    update_plan(conn, plan_id, active=1 if active else 0)


# ---- Groups -----------------------------------------------------------------

DEFAULT_GROUP_NAME = "system"


def ensure_default_group(conn) -> str:
    """The one group every account falls back to. Named 'system', never removed."""
    row = conn.execute("SELECT group_id, name FROM billing_groups WHERE is_default = 1").fetchone()
    if row:
        if row["name"] != DEFAULT_GROUP_NAME:
            conn.execute("UPDATE billing_groups SET name = ? WHERE group_id = ?",
                         (DEFAULT_GROUP_NAME, row["group_id"]))
            conn.commit()
        return row["group_id"]
    group_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO billing_groups (group_id, name, plan_id, is_default, created_at)"
        " VALUES (?, ?, NULL, 1, ?)", (group_id, DEFAULT_GROUP_NAME, int(time.time())))
    conn.commit()
    return group_id


def get_group(conn, group_id):
    return conn.execute("SELECT * FROM billing_groups WHERE group_id = ?",
                        (group_id,)).fetchone()


def rename_group(conn, group_id, name) -> bool:
    """Rename a non-default group. The default ('system') group is fixed."""
    cur = conn.execute("UPDATE billing_groups SET name = ? WHERE group_id = ? AND is_default = 0",
                       (name, group_id))
    conn.commit()
    return cur.rowcount > 0


def delete_group(conn, group_id) -> bool:
    """Remove a non-default group. The default group cannot be removed."""
    cur = conn.execute("DELETE FROM billing_groups WHERE group_id = ? AND is_default = 0",
                       (group_id,))
    conn.commit()
    return cur.rowcount > 0


def create_group(conn, name, *, plan_id=None, trial_days=0, next_group_id=None,
                 affiliate_code=None, affiliate_enabled=0) -> str:
    """A group can be a trial (members move to *next_group_id* after
    *trial_days*) and/or joinable by an affiliate code."""
    group_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO billing_groups (group_id, name, plan_id, is_default, trial_days,"
        " next_group_id, affiliate_code, affiliate_enabled, created_at)"
        " VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?)",
        (group_id, name, plan_id, int(trial_days or 0), next_group_id or None,
         (affiliate_code or None), 1 if affiliate_enabled else 0, int(time.time())))
    conn.commit()
    return group_id


def update_group(conn, group_id, *, name=None, plan_id=None, trial_days=None,
                 next_group_id=None, affiliate_code=None, affiliate_enabled=0) -> bool:
    """Update a group.

    A normal group gets every field; the default ('system') group is fixed apart
    from its attached plan, so only plan_id is applied for it.
    """
    group = get_group(conn, group_id)
    if group is None:
        return False
    if group["is_default"]:
        conn.execute("UPDATE billing_groups SET plan_id = ? WHERE group_id = ?",
                     (plan_id or None, group_id))
        conn.commit()
        return True
    conn.execute(
        "UPDATE billing_groups SET name = COALESCE(?, name), plan_id = ?,"
        " trial_days = ?, next_group_id = ?, affiliate_code = ?, affiliate_enabled = ?"
        " WHERE group_id = ?",
        (name, plan_id or None, int(trial_days or 0), next_group_id or None,
         (affiliate_code or None), 1 if affiliate_enabled else 0, group_id))
    conn.commit()
    return True


def group_by_affiliate(conn, code):
    """The group whose enabled affiliate code matches *code* (case-insensitive)."""
    if not code:
        return None
    return conn.execute(
        "SELECT * FROM billing_groups WHERE affiliate_enabled = 1"
        " AND lower(affiliate_code) = lower(?)", (code.strip(),)).fetchone()


def list_groups(conn):
    return conn.execute("SELECT * FROM billing_groups ORDER BY created_at").fetchall()


def set_group_plan(conn, group_id, plan_id) -> None:
    conn.execute("UPDATE billing_groups SET plan_id = ? WHERE group_id = ?",
                 (plan_id, group_id))
    conn.commit()


def assign_account_group(conn, account_type, account_id, group_id) -> None:
    """Assign an account to a group, stamping when (a trial's clock starts).

    A genuine move resets the stamp so the new group's trial runs afresh; a
    no-op reassignment keeps the original stamp and does not restart the trial.
    """
    now = int(time.time())
    conn.execute(
        "INSERT INTO account_groups (account_type, account_id, group_id, assigned_at)"
        " VALUES (?, ?, ?, ?) ON CONFLICT(account_type, account_id) DO UPDATE SET"
        " group_id = excluded.group_id,"
        " assigned_at = CASE WHEN account_groups.group_id = excluded.group_id"
        "   THEN account_groups.assigned_at ELSE excluded.assigned_at END",
        (account_type, account_id, group_id, now))
    conn.commit()


def sweep_trial_groups(conn, now=None) -> int:
    """Move accounts out of an expired trial group into its next group.

    Returns how many accounts were moved.
    """
    now = int(now if now is not None else time.time())
    rows = conn.execute(
        "SELECT ag.account_type, ag.account_id, ag.assigned_at,"
        " g.trial_days, g.next_group_id FROM account_groups ag"
        " JOIN billing_groups g ON g.group_id = ag.group_id"
        " WHERE g.trial_days > 0 AND g.next_group_id IS NOT NULL"
        "   AND g.next_group_id != ag.group_id"
        "   AND ag.assigned_at IS NOT NULL").fetchall()
    moved = 0
    for row in rows:
        if now - row["assigned_at"] >= row["trial_days"] * 86400:
            conn.execute(
                "UPDATE account_groups SET group_id = ?, assigned_at = ?"
                " WHERE account_type = ? AND account_id = ?",
                (row["next_group_id"], now, row["account_type"], row["account_id"]))
            moved += 1
    if moved:
        conn.commit()
    return moved


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


def pending_policy(conn, config, account_id) -> dict:
    """Pending-storage cap (bytes) and expiry (hours) for an account.

    Taken from the account's effective plan when it has one, else the configured
    defaults. A plan expiry of 0 means pending files never expire (None here).
    """
    plan = effective_plan(conn, SCOPE_ACCOUNT, account_id)
    max_bytes = getattr(config, "ACCOUNT_MAX_BYTES", 0) or None
    expiry = getattr(config, "PUSH_FILE_TTL_HOURS", 0) or None
    if plan is not None:
        if plan["max_pending_bytes"]:
            max_bytes = plan["max_pending_bytes"]
        expiry = plan["pending_expiry_hours"] or None
    return {"max_bytes": max_bytes, "expiry_hours": expiry}


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


def entitled(conn, account_type, account_id, *, grace_days=None) -> bool:
    """A billable account may operate if it has a plan, a live subscription,
    or a positive balance."""
    if effective_plan(conn, account_type, account_id) is not None:
        return True
    kwargs = {} if grace_days is None else {"grace_days": grace_days}
    if subscription_entitled(conn, account_type, account_id, **kwargs):
        return True
    return balance(conn, account_type, account_id) > 0


def bill_messages(conn, config, *, now=None) -> list[str]:
    """Charge accounts for doorbells sent since the last billing.

    Charges ``ceil(messages / 1000) * message_cents_per_1000`` at the account's
    effective-plan rate and resets the counter (so it is naturally idempotent).
    Returns the account ids charged.
    """
    now = int(now or time.time())
    charged = []
    rows = conn.execute("SELECT account_id, messages_sent FROM accounts"
                        " WHERE messages_sent > 0").fetchall()
    for row in rows:
        account_id, count = row["account_id"], row["messages_sent"]
        plan = effective_plan(conn, SCOPE_ACCOUNT, account_id)
        rate = plan["message_cents_per_1000"] if plan else 0
        if rate:
            thousands = math.ceil(count / 1000)
            debit(conn, SCOPE_ACCOUNT, account_id, rate * thousands,
                  reason="messages")
            charged.append(account_id)
        conn.execute("UPDATE accounts SET messages_sent = 0 WHERE account_id = ?",
                     (account_id,))
    conn.commit()
    return charged


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


# ---- PayPal gateway wiring ---------------------------------------------

def provision_plan(conn, config, plan_id, *, force=False) -> str:
    """Create (once) the PayPal billing plan behind a local plan row.

    Returns the PayPal plan id. Free plans and an unconfigured gateway are
    errors here - they never need provisioning. ``force`` re-creates the
    PayPal plan after a price change (the old one is left alone on PayPal's
    side; it keeps serving existing subscribers).
    """
    plan = get_plan(conn, plan_id)
    if plan is None:
        raise ValueError("unknown plan")
    if plan["paypal_plan_id"] and not force:
        return plan["paypal_plan_id"]
    if plan["price_cents"] <= 0:
        raise ValueError("free plans do not need a PayPal plan")
    if not paypal.enabled(config):
        raise ValueError("PayPal is not configured")
    client = paypal.client(config)
    product_id = client.ensure_product((
        lambda: server_settings.get(conn, "paypal_product_id"),
        lambda value: server_settings.set_value(conn, "paypal_product_id", value)))
    if not product_id:
        raise paypal.PaypalError("could not create the PayPal product")
    paypal_plan_id = client.create_billing_plan(
        name=plan["name"], price_cents=plan["price_cents"],
        currency=plan["currency"] or config.PAYPAL_CURRENCY,
        interval=plan["interval"])
    if not paypal_plan_id:
        raise paypal.PaypalError("PayPal plan creation returned no id")
    client.activate_plan(paypal_plan_id)
    conn.execute("UPDATE billing_plans SET paypal_plan_id = ? WHERE plan_id = ?",
                 (paypal_plan_id, plan_id))
    conn.commit()
    return paypal_plan_id


def paypal_plan_for(conn, config, plan) -> str:
    """The plan's PayPal id, provisioning it on first use (checkout-time)."""
    if plan["paypal_plan_id"]:
        return plan["paypal_plan_id"]
    return provision_plan(conn, config, plan["plan_id"])


# ---- Subscriptions ----------------------------------------------------

SUB_LIVE = ("pending", "active", "suspended")


def create_subscription(conn, account_type, account_id, plan_id) -> dict:
    """Open a pending subscription checkout for one target (server or account).

    Only one *live* (pending/active/suspended) subscription may exist per
    target; a stale pending checkout is replaced rather than stacking.
    """
    now = int(time.time())
    stale = conn.execute(
        "SELECT subscription_id, status FROM billing_subscriptions"
        " WHERE account_type = ? AND account_id = ? AND status = 'pending'",
        (account_type, account_id)).fetchall()
    for row in stale:
        conn.execute("DELETE FROM billing_subscriptions WHERE subscription_id = ?",
                     (row["subscription_id"],))
    subscription_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO billing_subscriptions (subscription_id, account_type,"
        " account_id, plan_id, provider, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'paypal', 'pending', ?, ?)",
        (subscription_id, account_type, account_id, plan_id, now, now))
    conn.commit()
    return get_subscription(conn, subscription_id)


def get_subscription(conn, subscription_id):
    return conn.execute("SELECT * FROM billing_subscriptions WHERE subscription_id = ?",
                        (subscription_id,)).fetchone()


def delete_subscription(conn, subscription_id) -> None:
    """Remove an abandoned pending checkout (only pending rows are deletable)."""
    conn.execute("DELETE FROM billing_subscriptions WHERE subscription_id = ?"
                 " AND status = 'pending'", (subscription_id,))
    conn.commit()


def find_subscription_by_provider(conn, provider_subscription_id):
    if not provider_subscription_id:
        return None
    return conn.execute(
        "SELECT * FROM billing_subscriptions WHERE provider_subscription_id = ?",
        (provider_subscription_id,)).fetchone()


def live_subscription(conn, account_type, account_id):
    """The target's pending/active/suspended subscription, newest first."""
    return conn.execute(
        "SELECT * FROM billing_subscriptions WHERE account_type = ?"
        " AND account_id = ? AND status IN ('pending', 'active', 'suspended')"
        " ORDER BY created_at DESC, subscription_id DESC LIMIT 1",
        (account_type, account_id)).fetchone()


def latest_subscription(conn, account_type, account_id):
    """The target's most recent subscription row of any status."""
    return conn.execute(
        "SELECT * FROM billing_subscriptions WHERE account_type = ?"
        " AND account_id = ? ORDER BY created_at DESC, subscription_id DESC"
        " LIMIT 1", (account_type, account_id)).fetchone()


def list_subscriptions(conn, limit=200):
    return conn.execute(
        "SELECT * FROM billing_subscriptions ORDER BY created_at DESC"
        " LIMIT ?", (limit,)).fetchall()


def set_subscription_provider_id(conn, subscription_id, provider_subscription_id):
    conn.execute(
        "UPDATE billing_subscriptions SET provider_subscription_id = ?,"
        " updated_at = ? WHERE subscription_id = ?",
        (provider_subscription_id, int(time.time()), subscription_id))
    conn.commit()


def activate_subscription(conn, subscription_id, *, provider_subscription_id=None,
                          period_start=None, period_end=None) -> None:
    """Mark a subscription active (first approval or a renewal).

    For an account-scoped subscription this also pins the paid plan as the
    account's override, so entitlement follows the plan the buyer chose; the
    sweep clears the override again when the subscription lapses.
    """
    now = int(time.time())
    row = get_subscription(conn, subscription_id)
    if row is None:
        return
    conn.execute(
        "UPDATE billing_subscriptions SET status = 'active',"
        " provider_subscription_id = COALESCE(?, provider_subscription_id),"
        " current_period_start = COALESCE(?, current_period_start),"
        " current_period_end = COALESCE(?, current_period_end),"
        " cancel_at_period_end = 0, updated_at = ?"
        " WHERE subscription_id = ?",
        (provider_subscription_id, period_start, period_end, now, subscription_id))
    if row["account_type"] == SCOPE_ACCOUNT and row["plan_id"]:
        set_account_plan(conn, SCOPE_ACCOUNT, row["account_id"], row["plan_id"])
    conn.commit()


def mark_subscription(conn, subscription_id, status, *, period_end=None,
                      cancel_at_period_end=None) -> None:
    """Record a lifecycle transition coming from a webhook or the API."""
    now = int(time.time())
    conn.execute(
        "UPDATE billing_subscriptions SET status = ?,"
        " current_period_end = COALESCE(?, current_period_end),"
        " cancel_at_period_end = COALESCE(?, cancel_at_period_end),"
        " updated_at = ? WHERE subscription_id = ?",
        (status, period_end,
         None if cancel_at_period_end is None else int(cancel_at_period_end),
         now, subscription_id))
    conn.commit()


def clear_account_override(conn, account_type, account_id, plan_id) -> None:
    """Drop a subscription-owned plan override when its subscription lapses.

    An override the admin (or the buyer) changed to something else is left
    alone - only the exact plan this subscription set is removed.
    """
    if account_type != SCOPE_ACCOUNT or not plan_id:
        return
    conn.execute(
        "DELETE FROM account_plans WHERE account_type = ? AND account_id = ?"
        " AND plan_id = ?", (account_type, account_id, plan_id))
    conn.commit()


def subscription_entitled(conn, account_type, account_id, *, now=None,
                          grace_days=3) -> bool:
    """Is there a subscription granting service right now (grace included)?

    Grace rules, mirroring the terms' suspension policy:
      * active      - until period end + grace (a missed renewal webhook
                      should not cut service on the minute);
      * suspended   - dunning: keep serving for the grace window from whichever
                      is later, the period end or the suspension timestamp;
      * cancelled   - access is retained until the paid period ends (+grace);
      * pending     - checkout not completed: no access;
      * expired     - no access.

    Every row is considered (any() over the target's history) so a cancelled
    row still granting paid access counts even when a newer pending checkout
    sits above it in the list.
    """
    now = int(now if now is not None else time.time())
    rows = conn.execute(
        "SELECT * FROM billing_subscriptions WHERE account_type = ?"
        " AND account_id = ?", (account_type, account_id)).fetchall()
    return any(_within_grace(row, now, grace_days) for row in rows)


def _within_grace(row, now, grace_days) -> bool:
    status = row["status"]
    if status == "pending":
        return False
    if status == "expired":
        return False
    grace = grace_days * 86400
    anchor = row["current_period_end"] or 0
    if status in ("suspended", "cancelled"):
        anchor = max(anchor or 0, row["updated_at"] or 0)
    if status == "active" and not anchor:
        return True
    return anchor + grace >= now


def slave_effective_plan(conn, server_id):
    """A server's plan: its assignment, else the deployment default (§7)."""
    row = conn.execute("SELECT plan_id FROM federated_servers WHERE server_id = ?",
                       (server_id,)).fetchone()
    plan_id = (row["plan_id"] if row else None) or server_settings.default_server_plan_id(conn)
    if not plan_id:
        return None
    return get_plan(conn, plan_id)


def slave_entitled(conn, server_id, *, now=None, grace_days=3) -> bool:
    """A federated server may relay doorbells when its plan is free or paid for.

    No plan / a free plan / an inactive-price plan never blocks anything; a
    *paid* plan requires a live PayPal subscription (design F7, terms §Billing).
    """
    plan = slave_effective_plan(conn, server_id)
    if plan is None or not plan["price_cents"]:
        return True
    now = int(now if now is not None else time.time())
    rows = conn.execute(
        "SELECT * FROM billing_subscriptions WHERE account_type = ?"
        " AND account_id = ?", (SCOPE_SLAVE, server_id)).fetchall()
    return any(_within_grace(row, now, grace_days) for row in rows
               if row["plan_id"] == plan["plan_id"])


def sweep_subscriptions(conn, *, now=None, grace_days=3, pending_ttl_days=7) -> int:
    """Expire lapsed subscriptions and drop their account plan overrides.

    Returns how many rows were expired. Webhooks do the live bookkeeping;
    this is the safety net for missed deliveries and hard cancellations.
    """
    now = int(now if now is not None else time.time())
    expired = 0
    rows = conn.execute(
        "SELECT * FROM billing_subscriptions WHERE status IN"
        " ('active', 'suspended', 'cancelled', 'pending')").fetchall()
    for row in rows:
        lapse = None
        if row["status"] == "pending":
            if now - (row["created_at"] or 0) > pending_ttl_days * 86400:
                lapse = "expired"
        elif row["current_period_end"]:
            anchor = row["current_period_end"]
            if row["status"] in ("suspended", "cancelled"):
                anchor = max(anchor, row["updated_at"] or 0)
            if now > anchor + grace_days * 86400:
                lapse = "expired"
        if lapse is None:
            continue
        conn.execute(
            "UPDATE billing_subscriptions SET status = ?, updated_at = ?"
            " WHERE subscription_id = ?", (lapse, now, row["subscription_id"]))
        clear_account_override(conn, row["account_type"], row["account_id"],
                               row["plan_id"])
        expired += 1
    if expired:
        conn.commit()
    return expired


# ---- One-time orders (prepaid top-ups) ---------------------------------

def create_order(conn, account_type, account_id, amount_cents, currency) -> dict:
    """Open a pending top-up order (credits land only on capture)."""
    now = int(time.time())
    order_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO billing_orders (order_id, account_type, account_id,"
        " amount_cents, currency, provider, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, 'paypal', 'pending', ?)",
        (order_id, account_type, account_id, int(amount_cents), currency, now))
    conn.commit()
    return get_order(conn, order_id)


def get_order(conn, order_id):
    return conn.execute("SELECT * FROM billing_orders WHERE order_id = ?",
                        (order_id,)).fetchone()


def find_order_by_provider(conn, provider_order_id):
    if not provider_order_id:
        return None
    return conn.execute("SELECT * FROM billing_orders WHERE provider_order_id = ?",
                        (provider_order_id,)).fetchone()


def attach_order_provider(conn, order_id, provider_order_id):
    conn.execute("UPDATE billing_orders SET provider_order_id = ? WHERE order_id = ?",
                 (provider_order_id, order_id))
    conn.commit()


def credit_once(conn, account_type, account_id, amount_cents, *,
                provider_ref, reason) -> bool:
    """Ledger credit that fires exactly once per provider reference.

    Returns True when this call added the credit (a replayed webhook or a
    double return-URL hit is a no-op).
    """
    if provider_ref:
        seen = conn.execute(
            "SELECT 1 FROM billing_ledger WHERE account_type = ?"
            " AND account_id = ? AND provider_ref = ? LIMIT 1",
            (account_type, account_id, provider_ref)).fetchone()
        if seen:
            return False
    add_credit(conn, account_type, account_id, int(amount_cents),
               reason=reason, provider_ref=provider_ref)
    return True


def complete_order(conn, order_id, provider_order_id) -> bool:
    """Mark a top-up captured and credit the ledger once. True if credited."""
    order = get_order(conn, order_id)
    if order is None:
        return False
    if order["status"] == "captured":
        return False
    conn.execute(
        "UPDATE billing_orders SET status = 'captured', provider_order_id = COALESCE(?,"
        " provider_order_id), captured_at = ? WHERE order_id = ?",
        (provider_order_id, int(time.time()), order_id))
    conn.commit()
    return credit_once(conn, order["account_type"], order["account_id"],
                       order["amount_cents"],
                       provider_ref=provider_order_id or order_id,
                       reason="topup")


def list_orders(conn, limit=100):
    return conn.execute(
        "SELECT * FROM billing_orders ORDER BY created_at DESC LIMIT ?",
        (limit,)).fetchall()
