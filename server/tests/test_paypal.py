# server/tests/test_paypal.py
"""PayPal client unit tests. The transport is faked at `requests`, so no
network is touched: status mapping, provisioning payloads, webhook verify."""
import json

import pytest

from server.app import billing, paypal
from server.app.db import Database


class _Resp:
    def __init__(self, status=200, payload=None, raw=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = (json.dumps(self._payload) if raw is None else raw)
        self.content = self.text.encode() if isinstance(self.text, str) else self.text

    def json(self):
        if isinstance(self._payload, dict) and self._payload:
            return self._payload
        return json.loads(self.text)


def _client(**overrides):
    from server.app.config import load_config
    cfg = load_config(overrides={"PAYPAL_CLIENT_ID": "cid",
                                 "PAYPAL_CLIENT_SECRET": "sec",
                                 **overrides})
    return paypal.PaypalClient(cfg)


def test_status_mapping_covers_lifecycle():
    assert paypal.local_status("ACTIVE") == "active"
    assert paypal.local_status("APPROVAL_PENDING") == "pending"
    assert paypal.local_status("SUSPENDED") == "suspended"
    assert paypal.local_status("CANCELLED") == "cancelled"
    assert paypal.local_status("EXPIRED") == "expired"
    assert paypal.local_status("SOMETHING_NEW") == "expired"


def test_parse_time_and_money():
    import datetime
    expected = int(datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc)
                   .timestamp())
    assert paypal.parse_time("2026-11-01T00:00:00Z") == expected
    assert paypal.parse_time("") is None
    assert paypal.parse_time("not a date") is None
    assert paypal.money(200) == "2.00"
    assert paypal.money(5) == "0.05"
    assert paypal.money(1234) == "12.34"


def test_enabled_and_base_url():
    from server.app.config import load_config
    off = load_config()
    assert not paypal.enabled(off)
    on = load_config(overrides={"PAYPAL_CLIENT_ID": "a",
                                "PAYPAL_CLIENT_SECRET": "b"})
    assert paypal.enabled(on)
    assert paypal.base_url(on) == paypal.SANDBOX_BASE
    live = load_config(overrides={"PAYPAL_CLIENT_ID": "a",
                                  "PAYPAL_CLIENT_SECRET": "b",
                                  "PAYPAL_MODE": "live"})
    assert paypal.base_url(live) == paypal.LIVE_BASE


def test_token_then_subscription_payload(monkeypatch):
    seen = {}

    def fake_post(url, **kw):
        seen["token_url"] = url
        seen["token_auth"] = kw["headers"]["Authorization"]
        return _Resp(200, {"access_token": "tok-1", "expires_in": 32400})

    def fake_request(method, url, **kw):
        seen["method"], seen["url"], seen["body"] = method, url, kw["json"]
        seen["bearer"] = kw["headers"]["Authorization"]
        return _Resp(201, {"id": "I-123",
                           "links": [{"rel": "approve", "href": "https://pp/approve"}]})

    monkeypatch.setattr(paypal.requests, "post", fake_post)
    monkeypatch.setattr(paypal.requests, "request", fake_request)
    client = _client()
    payload = client.create_subscription(
        plan_id="PP-1", custom_id="sub-local",
        return_url="https://push.example.com/return",
        cancel_url="https://push.example.com/cancel")

    assert seen["token_url"].endswith("/v1/oauth2/token")
    assert seen["token_auth"].startswith("Basic ")
    assert seen["method"] == "POST" and seen["url"].endswith("/v1/billing/subscriptions")
    assert seen["bearer"] == "Bearer tok-1"
    assert seen["body"]["plan_id"] == "PP-1"
    assert seen["body"]["custom_id"] == "sub-local"
    assert seen["body"]["application_context"]["return_url"] == \
        "https://push.example.com/return"
    assert paypal.PaypalClient.approve_link(payload) == "https://pp/approve"
    # The token is cached: a second call must not re-authenticate.
    posts = []
    monkeypatch.setattr(paypal.requests, "post",
                        lambda *a, **kw: posts.append(1) or _Resp(500))
    client.request("GET", "/v1/billing/subscriptions/I-123")
    assert posts == []


def test_api_errors_raise_with_status(monkeypatch):
    monkeypatch.setattr(paypal.requests, "post",
                        lambda *a, **kw: _Resp(200, {"access_token": "t",
                                                     "expires_in": 100}))
    monkeypatch.setattr(paypal.requests, "request",
                        lambda *a, **kw: _Resp(404, {"details": [
                            {"issue": "SUBSCRIPTION_NOT_FOUND"}]}))
    client = _client()
    with pytest.raises(paypal.PaypalError) as exc:
        client.get_subscription("I-nope")
    assert exc.value.status == 404
    assert "SUBSCRIPTION_NOT_FOUND" in str(exc.value)


def test_transport_failure_raises(monkeypatch):
    import requests as requests_lib

    monkeypatch.setattr(paypal.requests, "post",
                        lambda *a, **kw: _Resp(200, {"access_token": "t",
                                                     "expires_in": 100}))

    def boom(*a, **kw):
        raise requests_lib.ConnectionError("no route to host")

    monkeypatch.setattr(paypal.requests, "request", boom)
    with pytest.raises(paypal.PaypalError):
        _client().get_subscription("I-1")


def test_verify_webhook_sends_the_transmission_headers(monkeypatch):
    seen = {}

    def fake_request(method, url, **kw):
        seen["url"], seen["body"] = url, kw["json"]
        return _Resp(200, {"verification_status": "SUCCESS"})

    monkeypatch.setattr(paypal.requests, "post",
                        lambda *a, **kw: _Resp(200, {"access_token": "t",
                                                     "expires_in": 100}))
    monkeypatch.setattr(paypal.requests, "request", fake_request)
    headers = {"paypal-auth-algo": "SHA256withRSA",
               "paypal-cert-url": "https://api.paypal.com/cert.pem",
               "paypal-transmission-id": "tx-1",
               "paypal-transmission-sig": "sig",
               "paypal-transmission-time": "2026-10-01T00:00:00Z"}
    client = _client(PAYPAL_WEBHOOK_ID="WH-1")
    assert client.verify_webhook(headers, b'{"id":"evt-1"}') is True
    assert seen["url"].endswith("/v1/notifications/verify-webhook-signature")
    assert seen["body"]["webhook_id"] == "WH-1"
    assert seen["body"]["transmission_id"] == "tx-1"
    assert seen["body"]["webhook_event"] == '{"id":"evt-1"}'


def test_verify_webhook_without_id_is_an_error():
    client = _client()
    with pytest.raises(paypal.PaypalError):
        client.verify_webhook({}, b"{}")


def test_provision_plan_creates_product_and_plan_once(db_path, monkeypatch):
    from server.app.config import load_config

    cfg = load_config(overrides={"DB_PATH": db_path,
                                 "PAYPAL_CLIENT_ID": "cid",
                                 "PAYPAL_CLIENT_SECRET": "sec"})
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        plan_id = billing.create_plan(conn, "Host", billing.SCOPE_SLAVE,
                                      price_cents=2000, interval="month")

        calls = []

        def fake_request(method, url, json=None, **kw):
            calls.append((method, url))
            if url.endswith("/v1/catalogs/products"):
                return _Resp(201, {"id": "PROD-1"})
            if url.endswith("/v1/billing/plans"):
                assert json["billing_info"]["billing_cycles"][0][
                    "pricing_scheme"]["fixed_price"] == {"value": "20.00",
                                                         "currency_code": "AUD"}
                return _Resp(201, {"id": "PP-PLAN-1"})
            if url.endswith("/activate"):
                return _Resp(204, {}, raw="")
            raise AssertionError(f"unexpected call {method} {url}")

        monkeypatch.setattr(paypal.requests, "post",
                            lambda *a, **kw: _Resp(200, {"access_token": "t",
                                                         "expires_in": 100}))
        monkeypatch.setattr(paypal.requests, "request", fake_request)
        got = billing.provision_plan(conn, cfg, plan_id)
        assert got == "PP-PLAN-1"
        assert billing.get_plan(conn, plan_id)["paypal_plan_id"] == "PP-PLAN-1"
        # Product id pinned in server_settings; second run provisions nothing.
        assert any(u.endswith("/v1/catalogs/products") for _, u in calls)
        before = len(calls)
        assert billing.provision_plan(conn, cfg, plan_id) == "PP-PLAN-1"
        assert len(calls) == before


def test_provision_rejects_free_and_unconfigured(db_path):
    from server.app.config import load_config

    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        free = billing.create_plan(conn, "Free", billing.SCOPE_ACCOUNT)
        paid = billing.create_plan(conn, "Pro", billing.SCOPE_ACCOUNT,
                                   price_cents=500)
        with pytest.raises(ValueError):
            billing.provision_plan(conn, load_config(), free)
        with pytest.raises(ValueError):
            billing.provision_plan(conn, load_config(), paid)
