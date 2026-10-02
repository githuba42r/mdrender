# server/tests/test_billing_subscriptions.py
"""PayPal subscriptions and top-ups end-to-end: checkout, return, webhook,
entitlement gating for both plan scopes (server + user)."""
import base64
import json
import os
import time
import unittest.mock as mock
from datetime import datetime, timezone
from urllib.parse import unquote

import pytest

from server.app import accounts, billing, crypto, federation, paypal
from server.app.app import create_app
from conftest import consent_code


class FakeGateway:
    """Stands in for the PayPal API at `paypal.client(config)`."""

    def __init__(self):
        self.approve = "https://www.sandbox.paypal.com/checkoutnow?token=EC-FAKE"
        self.subscriptions = {}
        self.orders = {}
        self.cancelled = []
        self.activated_plans = []
        self.verify_result = True
        self._plan_n = 0
        self._sub_n = 0
        self._order_n = 0

    # -- catalog / provisioning --
    def create_product(self, name):
        return "PROD-1"

    def ensure_product(self, cache):
        product_id = cache[0]()
        if product_id:
            return product_id
        product_id = self.create_product("x")
        cache[1](product_id)
        return product_id

    def create_billing_plan(self, *, product_id, name, price_cents, currency,
                            interval):
        self._plan_n += 1
        return f"PP-PLAN-{self._plan_n}"

    def activate_plan(self, plan_id):
        self.activated_plans.append(plan_id)

    # -- subscriptions --
    def create_subscription(self, *, plan_id, custom_id, return_url, cancel_url):
        self._sub_n += 1
        sid = f"I-{self._sub_n:04d}"
        self.subscriptions[sid] = {"id": sid, "status": "APPROVAL_PENDING",
                                   "custom_id": custom_id, "plan_id": plan_id}
        return {"id": sid, "links": [{"rel": "approve", "href": self.approve}]}

    def get_subscription(self, sid):
        if sid not in self.subscriptions:
            raise paypal.PaypalError("not found", status=404)
        return {"id": sid, "status": "ACTIVE",
                "create_time": "2026-10-01T00:00:00Z",
                "billing_info": {"next_billing_time": "2026-11-01T00:00:00Z"}}

    def cancel_subscription(self, sid, reason=""):
        self.cancelled.append(sid)
        if sid in self.subscriptions:
            self.subscriptions[sid]["status"] = "CANCELLED"

    # -- orders --
    def create_order(self, *, amount_cents, currency, custom_id,
                     return_url, cancel_url, description):
        self._order_n += 1
        oid = f"ORDER-{self._order_n}"
        self.orders[oid] = {"id": oid, "status": "CREATED",
                            "amount_cents": amount_cents,
                            "custom_id": custom_id}
        return {"id": oid, "links": [{"rel": "approve", "href": self.approve}]}

    def get_order(self, oid):
        if oid not in self.orders:
            raise paypal.PaypalError("not found", status=404)
        return dict(self.orders[oid], status="APPROVED")

    def capture_order(self, oid):
        self.orders[oid]["status"] = "COMPLETED"
        return {"id": oid, "status": "COMPLETED"}

    # -- webhooks --
    def verify_webhook(self, headers, raw):
        return self.verify_result


def _make_app(config, db_path, *, verify_webhooks=False):
    config.PUSH_STORAGE_DIR = os.path.join(os.path.dirname(config.DB_PATH), "push")
    config.PUSH_PUBLIC_URL = "https://push.example.com"
    config.PAYPAL_CLIENT_ID = "cid"
    config.PAYPAL_CLIENT_SECRET = "sec"
    config.PAYPAL_VERIFY_WEBHOOKS = verify_webhooks
    config.ROLE = "master"  # these tests exercise the master's billing views
    app = create_app(config)
    app.config["TESTING"] = True
    app.config["_cfg"] = config   # the object the routes close over
    return app


@pytest.fixture()
def gateway(monkeypatch):
    gw = FakeGateway()
    monkeypatch.setattr(paypal, "client", lambda config: gw)
    return gw


@pytest.fixture()
def app_(config, db_path):
    return _make_app(config, db_path)


@pytest.fixture()
def app_verify(config, db_path):
    """A second app with webhook signature verification switched on."""
    return _make_app(config, db_path, verify_webhooks=True)


def _account_client(app):
    c = app.test_client()
    c.post("/signup", data={"email": "user@example.com", "password": "longenough1"})
    c.post("/account/login", data={"email": "user@example.com",
                                   "password": "longenough1"})
    return c


def _admin_client(app):
    c = app.test_client()
    c.post("/admin-login", data={"username": "admin", "password": "testpass"})
    return c


def _account_id(app):
    with app.config["_db"].connect() as conn:
        return conn.execute("SELECT account_id FROM accounts").fetchone()["account_id"]


def _paid_plan(app, scope, *, price=500, name="Pro", **kw):
    with app.config["_db"].connect() as conn:
        return billing.create_plan(conn, name, scope, price_cents=price, **kw)


def _epoch(text):
    return int(datetime.fromisoformat(text.replace("Z", "+00:00"))
               .astimezone(timezone.utc).timestamp())


# ---- Account (user) plans -------------------------------------------------

def test_account_page_renders_and_checkout_redirects_to_paypal(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_ACCOUNT)
    c = _account_client(app_)

    page = c.get("/account/billing")
    assert page.status_code == 200
    assert b"PayPal" in page.data

    resp = c.post("/account/billing/checkout", data={"plan_id": plan_id})
    assert resp.status_code == 302
    assert resp.headers["Location"] == gateway.approve

    with app_.config["_db"].connect() as conn:
        sub = billing.latest_subscription(conn, billing.SCOPE_ACCOUNT,
                                          _account_id(app_))
    assert sub["status"] == "pending"
    assert sub["provider_subscription_id"] == "I-0001"
    assert sub["plan_id"] == plan_id


def test_return_activates_subscription_and_pins_the_plan(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_ACCOUNT)
    c = _account_client(app_)
    c.post("/account/billing/checkout", data={"plan_id": plan_id})
    with app_.config["_db"].connect() as conn:
        sub_id = billing.latest_subscription(conn, billing.SCOPE_ACCOUNT,
                                             _account_id(app_))["subscription_id"]

    resp = c.get(f"/billing/paypal/return?checkout={sub_id}")
    assert resp.status_code == 303
    assert resp.headers["Location"] == "/account/billing?ok=subscribed"

    with app_.config["_db"].connect() as conn:
        sub = billing.get_subscription(conn, sub_id)
        assert sub["status"] == "active"
        assert sub["current_period_end"] == _epoch("2026-11-01T00:00:00Z")
        # The paid plan is pinned as the account's override, so entitlement
        # follows the plan the buyer chose.
        assert billing.effective_plan(conn, billing.SCOPE_ACCOUNT,
                                      _account_id(app_))["plan_id"] == plan_id
        assert billing.entitled(conn, billing.SCOPE_ACCOUNT, _account_id(app_))
    assert b"Subscription active" in c.get(resp.headers["Location"]).data


def test_completed_return_is_idempotent(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_ACCOUNT)
    c = _account_client(app_)
    c.post("/account/billing/checkout", data={"plan_id": plan_id})
    with app_.config["_db"].connect() as conn:
        sub_id = billing.latest_subscription(conn, billing.SCOPE_ACCOUNT,
                                             _account_id(app_))["subscription_id"]
    for _ in range(3):
        assert c.get(f"/billing/paypal/return?checkout={sub_id}").status_code == 303
    with app_.config["_db"].connect() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM billing_subscriptions").fetchone()[0]
    assert rows == 1


def test_cancelled_checkout_is_abandoned(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_ACCOUNT)
    c = _account_client(app_)
    c.post("/account/billing/checkout", data={"plan_id": plan_id})
    with app_.config["_db"].connect() as conn:
        sub_id = billing.latest_subscription(conn, billing.SCOPE_ACCOUNT,
                                             _account_id(app_))["subscription_id"]
    resp = c.get(f"/billing/paypal/cancel?checkout={sub_id}")
    assert resp.headers["Location"] == "/account/billing?cancelled=1"
    with app_.config["_db"].connect() as conn:
        assert billing.get_subscription(conn, sub_id) is None


def test_topup_credits_the_ledger_exactly_once(app_, gateway):
    c = _account_client(app_)
    resp = c.post("/account/billing/checkout", data={"amount": "10"})
    assert resp.status_code == 302 and resp.headers["Location"] == gateway.approve
    with app_.config["_db"].connect() as conn:
        order = conn.execute("SELECT * FROM billing_orders").fetchone()
        assert order["amount_cents"] == 1000 and order["status"] == "pending"
        order_id = order["order_id"]

    for _ in range(2):
        assert c.get(f"/billing/paypal/return?checkout={order_id}").status_code == 303
    with app_.config["_db"].connect() as conn:
        assert billing.balance(conn, billing.SCOPE_ACCOUNT,
                               _account_id(app_)) == 1000
        assert conn.execute("SELECT COUNT(*) FROM billing_ledger"
                            " WHERE reason = 'topup'").fetchone()[0] == 1
        assert billing.get_order(conn, order_id)["status"] == "captured"


def test_topup_amount_bounds_are_enforced(app_, gateway):
    c = _account_client(app_)
    low = c.post("/account/billing/checkout", data={"amount": "1"})
    assert "Minimum" in low.headers["Location"]
    high = c.post("/account/billing/checkout", data={"amount": "9999"})
    assert "Maximum" in high.headers["Location"]
    junk = c.post("/account/billing/checkout", data={"amount": "free"})
    assert "error=" in junk.headers["Location"]
    with app_.config["_db"].connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM billing_orders").fetchone()[0] == 0


def test_checkout_requires_a_session(config, db_path, gateway):
    app = _make_app(config, db_path)
    plan_id = _paid_plan(app, billing.SCOPE_ACCOUNT)
    resp = app.test_client().post("/account/billing/checkout",
                                  data={"plan_id": plan_id})
    assert resp.status_code == 303
    assert resp.headers["Location"].startswith("/account/login")


def test_cancel_subscription_calls_gateway_and_keeps_access(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_ACCOUNT)
    c = _account_client(app_)
    c.post("/account/billing/checkout", data={"plan_id": plan_id})
    with app_.config["_db"].connect() as conn:
        sub_id = billing.latest_subscription(conn, billing.SCOPE_ACCOUNT,
                                             _account_id(app_))["subscription_id"]
    c.get(f"/billing/paypal/return?checkout={sub_id}")

    resp = c.post("/account/billing/cancel")
    assert resp.headers["Location"] == "/account/billing?ok=cancelled"
    assert gateway.cancelled == ["I-0001"]
    with app_.config["_db"].connect() as conn:
        sub = billing.get_subscription(conn, sub_id)
        assert sub["status"] == "cancelled"
        # Access is retained until the paid period ends (terms).
        assert billing.subscription_entitled(conn, billing.SCOPE_ACCOUNT,
                                             _account_id(app_))


def test_pending_checkout_can_be_discarded_and_retried(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_ACCOUNT)
    c = _account_client(app_)
    c.post("/account/billing/checkout", data={"plan_id": plan_id})
    with app_.config["_db"].connect() as conn:
        sub_id = billing.latest_subscription(conn, billing.SCOPE_ACCOUNT,
                                             _account_id(app_))["subscription_id"]

    # The pending row blocks a fresh checkout until it is discarded.
    resp = c.post("/account/billing/checkout", data={"plan_id": plan_id})
    assert "already have" in unquote(resp.headers["Location"])
    assert b"Discard checkout" in c.get("/account/billing").data

    resp = c.post("/account/billing/cancel")
    assert resp.headers["Location"] == "/account/billing?ok=discarded"
    assert gateway.cancelled == ["I-0001"]   # provider approval dropped too
    with app_.config["_db"].connect() as conn:
        assert billing.get_subscription(conn, sub_id) is None
        assert billing.live_subscription(conn, billing.SCOPE_ACCOUNT,
                                         _account_id(app_)) is None

    # A fresh checkout starts cleanly with a new provider id.
    c.post("/account/billing/checkout", data={"plan_id": plan_id})
    with app_.config["_db"].connect() as conn:
        sub = billing.latest_subscription(conn, billing.SCOPE_ACCOUNT,
                                          _account_id(app_))
    assert sub["provider_subscription_id"] == "I-0002"
    assert sub["status"] == "pending"


def test_cancelling_a_never_activated_subscription_grants_nothing(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_ACCOUNT)
    c = _account_client(app_)
    c.post("/account/billing/checkout", data={"plan_id": plan_id})
    with app_.config["_db"].connect() as conn:
        sub_id = billing.latest_subscription(conn, billing.SCOPE_ACCOUNT,
                                             _account_id(app_))["subscription_id"]
        # A webhook can cancel a row that never reached activation; grace
        # must not hand out free access for it.
        billing.mark_subscription(conn, sub_id, "cancelled")
        assert not billing.subscription_entitled(
            conn, billing.SCOPE_ACCOUNT, _account_id(app_))


def test_account_page_and_nav_expose_billing(app_, gateway):
    c = _account_client(app_)
    page = c.get("/account/billing")
    assert page.status_code == 200
    assert b'href="/account/billing"' in page.data
    assert b"Prepaid balance" in page.data


# ---- Webhooks -------------------------------------------------------------

def _sub_event(event_id, etype, provider_id="I-0001", **extra):
    return {"id": event_id, "event_type": etype,
            "resource": {"id": provider_id,
                         "billing_info": {"next_billing_time":
                                          "2026-12-01T00:00:00Z"},
                         **extra}}


def test_webhook_activates_and_duplicates_are_noops(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_ACCOUNT)
    c = _account_client(app_)
    c.post("/account/billing/checkout", data={"plan_id": plan_id})

    event = _sub_event("evt-1", "BILLING.SUBSCRIPTION.ACTIVATED")
    first = app_.test_client().post("/api/billing/webhook", json=event)
    assert first.status_code == 200
    assert first.get_json()["ok"] is True

    dup = app_.test_client().post("/api/billing/webhook", json=event)
    assert dup.status_code == 200
    assert dup.get_json()["duplicate"] is True

    with app_.config["_db"].connect() as conn:
        sub = billing.latest_subscription(conn, billing.SCOPE_ACCOUNT,
                                          _account_id(app_))
        assert sub["status"] == "active"
        assert sub["current_period_end"] == _epoch("2026-12-01T00:00:00Z")
        assert conn.execute("SELECT status FROM billing_webhook_events"
                            " WHERE event_id = 'evt-1'").fetchone()[0] == "processed"


def test_webhook_suspension_and_expiry(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_ACCOUNT)
    c = _account_client(app_)
    c.post("/account/billing/checkout", data={"plan_id": plan_id})
    with app_.config["_db"].connect() as conn:
        sub_id = billing.latest_subscription(conn, billing.SCOPE_ACCOUNT,
                                             _account_id(app_))["subscription_id"]
        provider = billing.get_subscription(conn, sub_id)[
            "provider_subscription_id"]
    client = app_.test_client()
    client.post("/api/billing/webhook",
                json=_sub_event("evt-a", "BILLING.SUBSCRIPTION.ACTIVATED",
                                provider))
    client.post("/api/billing/webhook",
                json=_sub_event("evt-b", "BILLING.SUBSCRIPTION.PAYMENT.FAILED",
                                provider))
    with app_.config["_db"].connect() as conn:
        sub = billing.get_subscription(conn, sub_id)
        assert sub["status"] == "suspended"
        # Dunning keeps service up for the grace window.
        assert billing.subscription_entitled(conn, billing.SCOPE_ACCOUNT,
                                             _account_id(app_))
    client.post("/api/billing/webhook",
                json=_sub_event("evt-c", "BILLING.SUBSCRIPTION.EXPIRED",
                                provider))
    with app_.config["_db"].connect() as conn:
        sub = billing.get_subscription(conn, sub_id)
        assert sub["status"] == "expired"
        # The subscription-owned plan override is dropped on expiry.
        assert billing.effective_plan(conn, billing.SCOPE_ACCOUNT,
                                      _account_id(app_)) is None
        assert not billing.subscription_entitled(conn, billing.SCOPE_ACCOUNT,
                                                 _account_id(app_))


def test_webhook_signature_gate(app_verify, gateway):
    webhook = app_verify.test_client()
    event = _sub_event("evt-sig", "BILLING.SUBSCRIPTION.ACTIVATED")

    gateway.verify_result = False
    assert webhook.post("/api/billing/webhook", json=event).status_code == 400

    gateway.verify_result = True
    ok = webhook.post("/api/billing/webhook", json=event)
    assert ok.status_code == 200
    # The event id was claimed by the rejected attempt? No: rejection happens
    # before the claim, so this delivery processes normally - and an unknown
    # provider id is tolerated rather than failing the whole webhook.
    assert ok.get_json()["detail"].startswith("unknown subscription")


def test_webhook_rejects_garbage(app_, gateway):
    resp = app_.test_client().post("/api/billing/webhook", data="not json",
                                   content_type="application/json")
    assert resp.status_code == 400


# ---- Server (slave) plans -------------------------------------------------

def _enrol_slave(app, server_id="srv-1"):
    priv, pub = crypto.generate_rsa_keypair()
    priv_pem = crypto.private_to_pem(priv).decode()
    pub_b64 = base64.b64encode(crypto.public_to_spki_der(pub)).decode()
    code = consent_code(app, server_id=server_id, hostname="slave",
                        base_url="https://slave", public_key=pub_b64)
    with mock.patch.object(federation, "verify_slave_callback",
                           lambda base_url, challenge, timeout=10:
                           federation.sign_bytes(priv_pem, challenge.encode())):
        resp = app.test_client().post("/api/federation/enrol", json={
            "server_id": server_id, "hostname": "slave",
            "base_url": "https://slave", "public_key": pub_b64, "code": code})
    assert resp.status_code == 200, resp.data
    return priv_pem, f"{server_id}.{resp.get_json()['server_secret']}"


def _assign_server_plan(app, server_id, plan_id):
    with app.config["_db"].connect() as conn:
        conn.execute("UPDATE federated_servers SET plan_id = ? WHERE server_id = ?",
                     (plan_id, server_id))
        conn.commit()


def test_admin_creates_a_shareable_slave_checkout(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_SLAVE, price=2000, name="Host")
    _enrol_slave(app_)
    _assign_server_plan(app_, "srv-1", plan_id)
    admin = _admin_client(app_)

    resp = admin.post("/federation/srv-1/checkout")
    assert resp.status_code == 303
    assert resp.headers["Location"].startswith("/federation?paypal_url=")
    assert gateway.approve in unquote(resp.headers["Location"])

    with app_.config["_db"].connect() as conn:
        sub = billing.latest_subscription(conn, billing.SCOPE_SLAVE, "srv-1")
    assert sub["status"] == "pending" and sub["plan_id"] == plan_id

    # The Federation page surfaces the link and the billing column.
    page = admin.get(resp.headers["Location"]).data
    assert b"PayPal checkout created" in page
    assert b"awaiting payment" in page

    # The operator completes the link; the return lands them on /federation.
    done = admin.get(f"/billing/paypal/return?checkout={sub['subscription_id']}")
    assert done.headers["Location"] == "/federation?paypal_ok=1"
    with app_.config["_db"].connect() as conn:
        assert billing.get_subscription(conn, sub["subscription_id"])["status"] == \
            "active"
    assert b"relay is paid up" in admin.get(done.headers["Location"]).data
    assert b"active" in admin.get("/federation").data


def test_slave_checkout_refuses_free_plans_and_duplicates(app_, gateway):
    _enrol_slave(app_, "srv-1")
    admin = _admin_client(app_)
    # No plan at all -> free -> refused.
    assert "free" in unquote(admin.post("/federation/srv-1/checkout")
                             .headers["Location"])

    plan_id = _paid_plan(app_, billing.SCOPE_SLAVE, price=2000, name="Host")
    _assign_server_plan(app_, "srv-1", plan_id)
    assert admin.post("/federation/srv-1/checkout").status_code == 303
    # A second checkout while one is pending replaces it (no stacking).
    admin.post("/federation/srv-1/checkout")
    with app_.config["_db"].connect() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM billing_subscriptions"
                            " WHERE account_type = 'slave'").fetchone()[0]
    assert rows == 1


class _FakeFcm:
    def __init__(self):
        self.sent = []

    def send(self, data, token, **kwargs):
        self.sent.append((data, token))


def test_doorbell_is_gated_behind_a_paid_subscription(app_, gateway):
    app_.config["_cfg"].BILLING_ENFORCEMENT = True
    plan_id = _paid_plan(app_, billing.SCOPE_SLAVE, price=2000, name="Host")
    priv_pem, token = _enrol_slave(app_)
    _assign_server_plan(app_, "srv-1", plan_id)
    fake = _FakeFcm()
    app_.config["_fcm"] = fake
    with app_.config["_db"].connect() as conn:
        accounts.upsert_device(conn, account_id="acct-1", device_id="dev-1",
                               server_id="srv-1", fcm_token="tok-1")

    def ring():
        body = json.dumps({"account_id": "acct-1", "device_id": "dev-1",
                           "sealed": {"c": "C", "i": "IV"}}).encode()
        headers = federation.sign_request(priv_pem, "POST",
                                          "/api/federation/doorbell", body)
        headers["Authorization"] = f"Bearer {token}"
        return app_.test_client().post(
            "/api/federation/doorbell", data=body,
            content_type="application/json", headers=headers)

    unpaid = ring()
    assert unpaid.status_code == 402
    assert unpaid.get_json()["error"] == "subscription required"
    assert fake.sent == []

    # whoami reports the same state to the slave.
    headers = federation.sign_request(priv_pem, "GET", "/api/federation/whoami", b"")
    headers["Authorization"] = f"Bearer {token}"
    who = app_.test_client().get("/api/federation/whoami", headers=headers)
    billing_block = who.get_json()["billing"]
    assert billing_block["required"] is True
    assert billing_block["entitled"] is False

    # Subscribe -> the same call relays.
    with app_.config["_db"].connect() as conn:
        sub = billing.create_subscription(conn, billing.SCOPE_SLAVE, "srv-1",
                                          plan_id)
        billing.activate_subscription(
            conn, sub["subscription_id"], provider_subscription_id="I-9000",
            period_start=int(time.time()),
            period_end=int(time.time()) + 30 * 86400)
    assert ring().status_code == 200
    assert fake.sent == [({"p": "C", "i": "IV"}, "tok-1")]

    headers = federation.sign_request(priv_pem, "GET", "/api/federation/whoami", b"")
    headers["Authorization"] = f"Bearer {token}"
    who = app_.test_client().get("/api/federation/whoami", headers=headers)
    assert who.get_json()["billing"]["entitled"] is True


def test_free_slave_plans_are_never_gated(app_, gateway):
    app_.config["_cfg"].BILLING_ENFORCEMENT = True
    priv_pem, token = _enrol_slave(app_)   # no plan assigned, no default
    fake = _FakeFcm()
    app_.config["_fcm"] = fake
    with app_.config["_db"].connect() as conn:
        accounts.upsert_device(conn, account_id="acct-1", device_id="dev-1",
                               server_id="srv-1", fcm_token="tok-1")
    body = json.dumps({"account_id": "acct-1", "device_id": "dev-1",
                       "sealed": {"c": "C", "i": "IV"}}).encode()
    headers = federation.sign_request(priv_pem, "POST", "/api/federation/doorbell",
                                      body)
    headers["Authorization"] = f"Bearer {token}"
    resp = app_.test_client().post("/api/federation/doorbell", data=body,
                                   content_type="application/json",
                                   headers=headers)
    assert resp.status_code == 200


# ---- Admin UI ---------------------------------------------------------------

def test_admin_provision_endpoint_links_a_plan(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_SLAVE, price=2000, name="Host")
    admin = _admin_client(app_)
    resp = admin.post(f"/billing/plans/{plan_id}/provision")
    assert resp.status_code == 303
    assert "paypal=provisioned" in resp.headers["Location"]
    with app_.config["_db"].connect() as conn:
        pp_id = billing.get_plan(conn, plan_id)["paypal_plan_id"]
    assert pp_id.startswith("PP-PLAN-")
    assert gateway.activated_plans == [pp_id]
    # The plans table shows it as linked.
    assert b"linked" in admin.get("/billing?tab=plans").data


def test_payments_tab_lists_subscriptions(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_ACCOUNT)
    c = _account_client(app_)
    c.post("/account/billing/checkout", data={"plan_id": plan_id})
    admin = _admin_client(app_)
    page = admin.get("/billing?tab=payments")
    assert page.status_code == 200
    assert b"PayPal gateway" in page.data
    assert b"I-0001" in page.data          # provider id listed
    assert b"pending" in page.data


def test_unconfigured_gateway_shows_setup_hint(config, db_path):
    app = _make_app(config, db_path)
    app.config["_cfg"].PAYPAL_CLIENT_ID = ""
    app.config["_cfg"].PAYPAL_CLIENT_SECRET = ""
    page = _admin_client(app).get("/billing?tab=payments")
    assert b"Not configured" in page.data
    assert b"paypal-integration-setup.md" in page.data


# ---- Worker sweep ----------------------------------------------------------

def test_sweep_expires_lapsed_subscriptions(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_ACCOUNT)
    c = _account_client(app_)
    c.post("/account/billing/checkout", data={"plan_id": plan_id})
    account_id = _account_id(app_)
    with app_.config["_db"].connect() as conn:
        sub = billing.latest_subscription(conn, billing.SCOPE_ACCOUNT, account_id)
        billing.activate_subscription(
            conn, sub["subscription_id"], provider_subscription_id="I-0001",
            period_start=int(time.time()) - 40 * 86400,
            period_end=int(time.time()) - 10 * 86400)  # lapsed 10 days ago
        assert billing.subscription_entitled(conn, billing.SCOPE_ACCOUNT,
                                             account_id) is False
        expired = billing.sweep_subscriptions(conn, grace_days=3)
        assert expired == 1
        row = billing.get_subscription(conn, sub["subscription_id"])
        assert row["status"] == "expired"
        # The plan override bought with it is gone.
        assert billing.effective_plan(conn, billing.SCOPE_ACCOUNT,
                                      account_id) is None
