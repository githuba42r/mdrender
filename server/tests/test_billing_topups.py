# server/tests/test_billing_topups.py
"""PayPal top-ups and prepaid-credit gating end-to-end.

Subscriptions are gone: the only way onto the service is a one-time top-up,
and (with BILLING_ENFORCEMENT on) uploads and push doorbells are refused
while an account is not in credit, per file for batch operations.
"""
import base64
import io
import json
import os
import unittest.mock as mock
from urllib.parse import unquote

import pytest

from server.app import accounts, billing, crypto, federation, paypal
from server.app.app import create_app
from conftest import consent_code


class FakeGateway:
    """Stands in for the PayPal API at `paypal.client(config)`."""

    def __init__(self):
        self.approve = "https://www.sandbox.paypal.com/checkoutnow?token=EC-FAKE"
        self.orders = {}
        self.verify_result = True
        self._order_n = 0

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


# ---- Helpers ----------------------------------------------------------------

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


def _seed_account_device(app, account_id, name="Sunny Falcon"):
    push_key = base64.b64encode(os.urandom(32)).decode()
    with app.config["_db"].connect() as conn:
        conn.execute(
            "INSERT INTO devices (device_secret, device_auth, device_name,"
            " device_model, fcm_token, public_key, push_key, registered_at,"
            " last_seen, account_id, approved_at) VALUES (?, ?, ?, 'Pixel', 'tok',"
            " 'PUB', ?, 1, 1, ?, 1)",
            ("sec-push", "auth-push", name, push_key, account_id))
        conn.commit()
    return name


def _push_client(app):
    """A push client with a bearer token, enrolled like a real one."""
    c = app.test_client()
    enrol = c.post("/api/enrol/start", json={}).json
    eid = enrol["enrolment_id"]
    code = app.config["_enrol_keys"][eid]["code"]
    creds = c.post("/api/enrol", json={"enrolment_id": eid, "code": code}).json
    tok = c.post("/oauth/token", data={
        "grant_type": "client_credentials",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    }).json["access_token"]
    return c, tok


class _FakeFcm:
    def __init__(self):
        self.sent = []

    def send(self, data, token, **kwargs):
        self.sent.append((data, token))


# ---- Account (user) top-ups ------------------------------------------------

def test_account_page_renders_and_checkout_redirects_to_paypal(app_, gateway):
    c = _account_client(app_)
    page = c.get("/account/billing")
    assert page.status_code == 200
    assert b"Prepaid balance" in page.data
    assert b"PayPal" in page.data

    resp = c.post("/account/billing/checkout", data={"amount": "10"})
    assert resp.status_code == 302
    assert resp.headers["Location"] == gateway.approve

    with app_.config["_db"].connect() as conn:
        order = conn.execute("SELECT * FROM billing_orders").fetchone()
    assert order["amount_cents"] == 1000 and order["status"] == "pending"
    assert order["account_type"] == billing.SCOPE_ACCOUNT
    assert order["account_id"] == _account_id(app_)


def test_topup_credits_the_ledger_exactly_once(app_, gateway):
    c = _account_client(app_)
    resp = c.post("/account/billing/checkout", data={"amount": "10"})
    assert resp.status_code == 302 and resp.headers["Location"] == gateway.approve
    with app_.config["_db"].connect() as conn:
        order = conn.execute("SELECT * FROM billing_orders").fetchone()
        assert order["amount_cents"] == 1000 and order["status"] == "pending"
        order_id = order["order_id"]

    for _ in range(2):
        got = c.get(f"/billing/paypal/return?checkout={order_id}")
        assert got.status_code == 303
        assert got.headers["Location"] == "/account/billing?ok=topup"
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
    resp = app.test_client().post("/account/billing/checkout", data={"amount": "10"})
    assert resp.status_code == 303
    assert resp.headers["Location"].startswith("/account/login")


def test_cancelled_checkout_is_abandoned(app_, gateway):
    c = _account_client(app_)
    c.post("/account/billing/checkout", data={"amount": "10"})
    with app_.config["_db"].connect() as conn:
        order_id = conn.execute("SELECT order_id FROM billing_orders"
                                ).fetchone()["order_id"]
    resp = c.get(f"/billing/paypal/cancel?checkout={order_id}")
    assert resp.headers["Location"] == "/account/billing?cancelled=1"
    with app_.config["_db"].connect() as conn:
        assert billing.get_order(conn, order_id) is None
        assert billing.balance(conn, billing.SCOPE_ACCOUNT,
                               _account_id(app_)) == 0


def test_account_page_and_nav_expose_billing(app_, gateway):
    c = _account_client(app_)
    page = c.get("/account/billing")
    assert page.status_code == 200
    assert b'href="/account/billing"' in page.data
    assert b"Prepaid balance" in page.data


# ---- Webhooks -------------------------------------------------------------

def _capture_event(event_id, order_id, provider_id="CAP-7777"):
    return {"id": event_id, "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": {"id": provider_id, "custom_id": f"order:{order_id}"}}


def test_webhook_captures_a_topup_and_duplicates_are_noops(app_, gateway):
    c = _account_client(app_)
    c.post("/account/billing/checkout", data={"amount": "10"})
    with app_.config["_db"].connect() as conn:
        order_id = conn.execute("SELECT order_id FROM billing_orders"
                                ).fetchone()["order_id"]

    webhook = app_.test_client()
    event = _capture_event("evt-1", order_id)
    first = webhook.post("/api/billing/webhook", json=event)
    assert first.status_code == 200
    assert first.get_json()["detail"] == "topup credited"

    dup = webhook.post("/api/billing/webhook", json=event)
    assert dup.status_code == 200
    assert dup.get_json()["duplicate"] is True

    with app_.config["_db"].connect() as conn:
        assert billing.balance(conn, billing.SCOPE_ACCOUNT,
                               _account_id(app_)) == 1000
        assert billing.get_order(conn, order_id)["status"] == "captured"
        assert conn.execute("SELECT status FROM billing_webhook_events"
                            " WHERE event_id = 'evt-1'"
                            ).fetchone()[0] == "processed"

    # Subscription lifecycle events are relics: accepted, ignored, no error.
    sub = webhook.post("/api/billing/webhook", json={
        "id": "evt-2", "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
        "resource": {"id": "I-0001"}})
    assert sub.status_code == 200
    assert sub.get_json()["detail"] == "ignored"
    assert sub.get_json()["ok"] is True


def test_webhook_signature_gate(app_verify, gateway):
    webhook = app_verify.test_client()
    event = _capture_event("evt-sig", "order-does-not-exist")

    gateway.verify_result = False
    assert webhook.post("/api/billing/webhook", json=event).status_code == 400

    gateway.verify_result = True
    ok = webhook.post("/api/billing/webhook", json=event)
    assert ok.status_code == 200
    # Rejection happens before the event id is claimed, so this delivery
    # processes normally - and an unknown order id is tolerated.
    assert ok.get_json()["detail"] == "unknown order"


def test_webhook_rejects_garbage(app_, gateway):
    resp = app_.test_client().post("/api/billing/webhook", data="not json",
                                   content_type="application/json")
    assert resp.status_code == 400


# ---- Credit gating: pushes -------------------------------------------------

def test_push_requires_credit_and_each_file_must_fit(app_, gateway):
    app_.config["_cfg"].BILLING_ENFORCEMENT = True
    _account_client(app_)
    account_id = _account_id(app_)
    device = _seed_account_device(app_, account_id)
    app_.config["_fcm"] = _FakeFcm()
    client, tok = _push_client(app_)

    def push(files):
        return client.post("/api/push",
                           headers={"Authorization": f"Bearer {tok}"},
                           data={"target_device": device, "file": files},
                           content_type="multipart/form-data")

    # Assign a plan that meters both storage and messages, still unfunded.
    with app_.config["_db"].connect() as conn:
        plan = billing.create_plan(conn, "Metered", billing.SCOPE_ACCOUNT,
                                   price_cents=500, storage_cents_per_mb=100,
                                   message_cents_per_1000=100)
        billing.set_account_plan(conn, billing.SCOPE_ACCOUNT, account_id, plan)

    # Out of credit: no doorbell at all.
    r = push([(io.BytesIO(b"x"), "a.txt")])
    assert r.status_code == 402
    assert r.get_json()["error"] == "payment required"

    with app_.config["_db"].connect() as conn:
        billing.add_credit(conn, billing.SCOPE_ACCOUNT, account_id, 250,
                           reason="topup")

    # Each file costs 100c (1 MB rounded up); both fit in 250c individually.
    ok = push([(io.BytesIO(b"x"), "a.txt"), (io.BytesIO(b"y"), "b.txt")])
    assert ok.status_code == 200, ok.data
    assert ok.json["files_sent"] == 2
    assert app_.config["_fcm"].sent

    # A 3 MB file costs 300c > the 250c credit: refused, naming the file.
    big = push([(io.BytesIO(b"x" * (3 * 1024 * 1024)), "big.bin")])
    assert big.status_code == 402
    detail = big.get_json()["detail"]
    assert "big.bin" in detail and "300" in detail and "250" in detail

    # Spending the balance back to zero blocks the next push again.
    with app_.config["_db"].connect() as conn:
        billing.debit(conn, billing.SCOPE_ACCOUNT, account_id, 250,
                      reason="usage")
    assert push([(io.BytesIO(b"x"), "c.txt")]).status_code == 402


def test_push_gate_skips_unmetered_plans(app_, gateway):
    app_.config["_cfg"].BILLING_ENFORCEMENT = True
    _account_client(app_)
    account_id = _account_id(app_)
    device = _seed_account_device(app_, account_id)
    app_.config["_fcm"] = _FakeFcm()
    client, tok = _push_client(app_)

    def push():
        return client.post("/api/push",
                           headers={"Authorization": f"Bearer {tok}"},
                           data={"target_device": device,
                                 "file": [(io.BytesIO(b"x"), "a.txt")]},
                           content_type="multipart/form-data")

    # No plan: nothing meters messages, so zero credit still rings.
    assert push().status_code == 200

    # A storage-metered plan does not gate pushes (messages are free).
    with app_.config["_db"].connect() as conn:
        storage_only = billing.create_plan(conn, "StorageOnly",
                                           billing.SCOPE_ACCOUNT,
                                           storage_cents_per_mb=100)
        billing.set_account_plan(conn, billing.SCOPE_ACCOUNT, account_id,
                                 storage_only)
    assert push().status_code == 200

    # The moment messages are metered, the credit gate binds.
    with app_.config["_db"].connect() as conn:
        metered = billing.create_plan(conn, "Metered", billing.SCOPE_ACCOUNT,
                                      storage_cents_per_mb=100,
                                      message_cents_per_1000=100)
        billing.set_account_plan(conn, billing.SCOPE_ACCOUNT, account_id,
                                 metered)
    assert push().status_code == 402


# ---- User pages ------------------------------------------------------------

def test_account_home_shows_balance_and_topup_link(app_, gateway):
    c = _account_client(app_)
    account_id = _account_id(app_)
    with app_.config["_db"].connect() as conn:
        billing.add_credit(conn, billing.SCOPE_ACCOUNT, account_id, 750,
                           reason="topup")
    page = c.get("/account")
    assert page.status_code == 200
    assert b"$7.50" in page.data
    assert b"credit" in page.data
    assert b'href="/account/billing"' in page.data


def test_account_home_shows_debit_balance(app_, gateway):
    c = _account_client(app_)
    account_id = _account_id(app_)
    with app_.config["_db"].connect() as conn:
        billing.debit(conn, billing.SCOPE_ACCOUNT, account_id, 250,
                      reason="messages")
    page = c.get("/account")
    assert b"-$2.50" in page.data
    assert b"debit" in page.data


def test_admin_user_detail_shows_balance_in_debit(app_, gateway):
    _account_client(app_)
    account_id = _account_id(app_)
    with app_.config["_db"].connect() as conn:
        billing.debit(conn, billing.SCOPE_ACCOUNT, account_id, 250,
                      reason="messages")
    page = _admin_client(app_).get(f"/accounts/{account_id}")
    assert page.status_code == 200
    assert b"-$2.50" in page.data
    assert b"debit" in page.data
    # The subscription UI is gone for good.
    assert b"Subscription" not in page.data
    assert b"Cancel subscription" not in page.data


# ---- Server (slave) top-ups ------------------------------------------------

def test_admin_creates_a_shareable_slave_topup(app_, gateway):
    plan_id = _paid_plan(app_, billing.SCOPE_SLAVE, price=2000, name="Host")
    _enrol_slave(app_)
    _assign_server_plan(app_, "srv-1", plan_id)
    admin = _admin_client(app_)

    resp = admin.post("/federation/srv-1/checkout", data={"amount": "20"})
    assert resp.status_code == 303
    assert resp.headers["Location"].startswith("/federation?paypal_url=")
    assert gateway.approve in unquote(resp.headers["Location"])

    with app_.config["_db"].connect() as conn:
        order = conn.execute("SELECT * FROM billing_orders").fetchone()
    assert order["amount_cents"] == 2000
    assert order["account_type"] == billing.SCOPE_SLAVE
    assert order["account_id"] == "srv-1"

    # The Federation page surfaces the link and the server's credit state.
    page = admin.get(resp.headers["Location"]).data
    assert b"PayPal checkout created" in page
    assert b"out of credit" in page

    # The operator completes the link; the return lands them on /federation.
    done = admin.get(f"/billing/paypal/return?checkout={order['order_id']}")
    assert done.headers["Location"] == "/federation?ok=topup"
    with app_.config["_db"].connect() as conn:
        assert billing.balance(conn, billing.SCOPE_SLAVE, "srv-1") == 2000
    fed = admin.get(done.headers["Location"]).data
    assert b"Top-up complete" in fed
    assert b"$20 in credit" in fed


def test_slave_checkout_refuses_free_plans_and_bad_amounts(app_, gateway):
    _enrol_slave(app_, "srv-1")
    admin = _admin_client(app_)
    # No plan at all -> free -> refused.
    assert "free" in unquote(admin.post("/federation/srv-1/checkout",
                                        data={"amount": "10"})
                             .headers["Location"])

    plan_id = _paid_plan(app_, billing.SCOPE_SLAVE, price=2000, name="Host")
    _assign_server_plan(app_, "srv-1", plan_id)
    missing = admin.post("/federation/srv-1/checkout")
    assert "Enter a top-up amount" in unquote(missing.headers["Location"])
    low = admin.post("/federation/srv-1/checkout", data={"amount": "1"})
    assert "Minimum" in unquote(low.headers["Location"])
    high = admin.post("/federation/srv-1/checkout", data={"amount": "9999"})
    assert "Maximum" in unquote(high.headers["Location"])
    with app_.config["_db"].connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM billing_orders").fetchone()[0] == 0


def test_doorbell_is_gated_behind_server_credit(app_, gateway):
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

    def whoami():
        headers = federation.sign_request(priv_pem, "GET",
                                          "/api/federation/whoami", b"")
        headers["Authorization"] = f"Bearer {token}"
        return app_.test_client().get("/api/federation/whoami",
                                      headers=headers).get_json()

    unpaid = ring()
    assert unpaid.status_code == 402
    assert unpaid.get_json()["error"] == "payment required"
    assert fake.sent == []
    who = whoami()
    assert who["billing"]["required"] is True
    assert who["billing"]["entitled"] is False
    assert who["billing"]["status"] == "out of credit"

    # A top-up raises the credit; the same doorbell now relays.
    with app_.config["_db"].connect() as conn:
        billing.add_credit(conn, billing.SCOPE_SLAVE, "srv-1", 5000,
                           reason="topup")
    assert ring().status_code == 200
    assert fake.sent == [({"p": "C", "i": "IV"}, "tok-1")]
    who = whoami()
    assert who["billing"]["entitled"] is True
    assert who["billing"]["status"] == "in credit"


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


# ---- Credit gate controls (plan checkbox + account override) ---------------

def test_plan_require_credit_checkbox_gates_zero_rate_plans(app_, gateway):
    app_.config["_cfg"].BILLING_ENFORCEMENT = True
    c = _account_client(app_)
    account_id = _account_id(app_)
    admin = _admin_client(app_)

    # The admin creates a zero-rate plan with the checkbox ticked.
    resp = admin.post("/billing/plans", data={
        "name": "Strict", "scope": "account", "price": "0",
        "require_credit": "1"})
    assert resp.status_code == 303
    with app_.config["_db"].connect() as conn:
        plan = conn.execute("SELECT * FROM billing_plans WHERE name = 'Strict'"
                            ).fetchone()
    assert plan["require_credit"] == 1
    assert b"requires credit" in admin.get("/billing?tab=plans").data
    with app_.config["_db"].connect() as conn:
        billing.set_account_plan(conn, billing.SCOPE_ACCOUNT, account_id,
                                 plan["plan_id"])

    # Zero usage rates, but the plan says always-in-credit: gate binds.
    blocked = c.post("/api/account/upload",
                     data={"file": (io.BytesIO(b"x"), "x.txt")},
                     content_type="multipart/form-data")
    assert blocked.status_code == 402
    assert blocked.get_json()["error"] == "payment required"

    with app_.config["_db"].connect() as conn:
        billing.add_credit(conn, billing.SCOPE_ACCOUNT, account_id, 100,
                           reason="topup")
    assert c.post("/api/account/upload",
                  data={"file": (io.BytesIO(b"x"), "x.txt")},
                  content_type="multipart/form-data").status_code == 200

    # Untick the checkbox (no require_credit field posted) and the
    # zero-rate plan is exempt again: no credit needed.
    resp = admin.post(f"/billing/plans/{plan['plan_id']}", data={
        "name": "Strict", "scope": "account", "price": "0"})
    assert resp.status_code == 303
    with app_.config["_db"].connect() as conn:
        assert billing.get_plan(conn, plan["plan_id"])["require_credit"] == 0
        billing.debit(conn, billing.SCOPE_ACCOUNT, account_id, 100,
                      reason="storage")
    assert b"requires credit" not in admin.get("/billing?tab=plans").data
    assert c.post("/api/account/upload",
                  data={"file": (io.BytesIO(b"x"), "x.txt")},
                  content_type="multipart/form-data").status_code == 200

    # The JSON snapshot reports both controls.
    snap = c.get("/api/billing/subscription")
    assert snap.status_code == 200
    assert snap.get_json()["plan"]["require_credit"] is False
    assert snap.get_json()["credit_gate_exempt"] is False


def test_account_override_beats_the_plan_regardless_of_balance(app_, gateway):
    app_.config["_cfg"].BILLING_ENFORCEMENT = True
    c = _account_client(app_)
    account_id = _account_id(app_)
    device = _seed_account_device(app_, account_id)
    app_.config["_fcm"] = _FakeFcm()
    push_client, tok = _push_client(app_)
    admin = _admin_client(app_)

    def upload():
        return c.post("/api/account/upload",
                      data={"file": (io.BytesIO(b"x"), "x.txt")},
                      content_type="multipart/form-data")

    def push():
        return push_client.post("/api/push",
                                headers={"Authorization": f"Bearer {tok}"},
                                data={"target_device": device,
                                      "file": [(io.BytesIO(b"x"), "a.txt")]},
                                content_type="multipart/form-data")

    with app_.config["_db"].connect() as conn:
        plan = billing.create_plan(conn, "Metered", billing.SCOPE_ACCOUNT,
                                   storage_cents_per_mb=100,
                                   message_cents_per_1000=100)
        billing.set_account_plan(conn, billing.SCOPE_ACCOUNT, account_id, plan)

    # Metered plan, no credit: both gates bind.
    assert upload().status_code == 402
    assert push().status_code == 402

    # Admin ticks the override on the User page.
    resp = admin.post(f"/accounts/{account_id}/credit-gate-exempt",
                      data={"exempt": "1", "next": f"/accounts/{account_id}"})
    assert resp.status_code == 303
    detail = admin.get(f"/accounts/{account_id}").data
    assert b"credit-gate exempt" in detail

    # Even deep in debit, uploads and pushes go through.
    with app_.config["_db"].connect() as conn:
        billing.debit(conn, billing.SCOPE_ACCOUNT, account_id, 500,
                      reason="messages")
    assert c.get("/account/billing").data.count(b"gate exempt") >= 1
    assert upload().status_code == 200
    assert push().status_code == 200

    # Exemption skips the gate, not the metering: the doorbell still bills.
    with app_.config["_db"].connect() as conn:
        charged = billing.bill_messages(conn, app_.config["_cfg"])
    assert charged == [account_id]
    with app_.config["_db"].connect() as conn:
        assert billing.balance(conn, billing.SCOPE_ACCOUNT, account_id) == -600

    # Untick: the plan's gates apply again immediately.
    admin.post(f"/accounts/{account_id}/credit-gate-exempt",
               data={"exempt": "0", "next": f"/accounts/{account_id}"})
    assert upload().status_code == 402
    assert push().status_code == 402


# ---- Admin UI --------------------------------------------------------------

def test_payments_tab_lists_topup_orders(app_, gateway):
    c = _account_client(app_)
    c.post("/account/billing/checkout", data={"amount": "10"})
    admin = _admin_client(app_)
    page = admin.get("/billing?tab=payments")
    assert page.status_code == 200
    assert b"PayPal gateway" in page.data
    assert b"Top-up orders" in page.data
    assert b"ORDER-1" in page.data             # provider id listed
    assert b"pending" in page.data
    # Subscriptions are gone from the page too.
    assert b"Subscriptions" not in page.data


def test_plans_tab_has_no_paypal_provisioning(app_, gateway):
    _paid_plan(app_, billing.SCOPE_ACCOUNT)
    admin = _admin_client(app_)
    page = admin.get("/billing?tab=plans")
    assert b"Plan fee (paid marker)" in page.data
    assert b"/provision" not in page.data


def test_unconfigured_gateway_shows_setup_hint(config, db_path):
    app = _make_app(config, db_path)
    app.config["_cfg"].PAYPAL_CLIENT_ID = ""
    app.config["_cfg"].PAYPAL_CLIENT_SECRET = ""
    page = _admin_client(app).get("/billing?tab=payments")
    assert b"Not configured" in page.data
    assert b"paypal-integration-setup.md" in page.data
