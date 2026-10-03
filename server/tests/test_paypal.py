# server/tests/test_paypal.py
"""PayPal client unit tests. The transport is faked at `requests`, so no
network is touched: order payloads, error mapping, webhook verify."""
import json

import pytest

from server.app import paypal


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


def test_token_then_order_payload(monkeypatch):
    seen = {}

    def fake_post(url, **kw):
        seen["token_url"] = url
        seen["token_auth"] = kw["headers"]["Authorization"]
        return _Resp(200, {"access_token": "tok-1", "expires_in": 32400})

    def fake_request(method, url, **kw):
        seen["method"], seen["url"], seen["body"] = method, url, kw["json"]
        seen["bearer"] = kw["headers"]["Authorization"]
        return _Resp(201, {"id": "ORDER-1",
                           "links": [{"rel": "approve", "href": "https://pp/approve"}]})

    monkeypatch.setattr(paypal.requests, "post", fake_post)
    monkeypatch.setattr(paypal.requests, "request", fake_request)
    client = _client()
    payload = client.create_order(
        amount_cents=1500, currency="AUD", custom_id="order-local",
        return_url="https://push.example.com/return",
        cancel_url="https://push.example.com/cancel",
        description="MDRender prepaid credit")

    assert seen["token_url"].endswith("/v1/oauth2/token")
    assert seen["token_auth"].startswith("Basic ")
    assert seen["method"] == "POST" and seen["url"].endswith("/v2/checkout/orders")
    assert seen["bearer"] == "Bearer tok-1"
    assert seen["body"]["intent"] == "CAPTURE"
    unit = seen["body"]["purchase_units"][0]
    assert unit["amount"] == {"currency_code": "AUD", "value": "15.00"}
    assert unit["custom_id"] == "order-local"
    assert seen["body"]["application_context"]["return_url"] == \
        "https://push.example.com/return"
    assert paypal.PaypalClient.approve_link_for_order(payload) == \
        "https://pp/approve"
    # The token is cached: a second call must not re-authenticate.
    posts = []
    monkeypatch.setattr(paypal.requests, "post",
                        lambda *a, **kw: posts.append(1) or _Resp(500))
    client.get_order("ORDER-1")
    assert posts == []


def test_api_errors_raise_with_status(monkeypatch):
    monkeypatch.setattr(paypal.requests, "post",
                        lambda *a, **kw: _Resp(200, {"access_token": "t",
                                                     "expires_in": 100}))
    monkeypatch.setattr(paypal.requests, "request",
                        lambda *a, **kw: _Resp(404, {"details": [
                            {"issue": "ORDER_NOT_FOUND"}]}))
    client = _client()
    with pytest.raises(paypal.PaypalError) as exc:
        client.get_order("ORD-nope")
    assert exc.value.status == 404
    assert "ORDER_NOT_FOUND" in str(exc.value)


def test_transport_failure_raises(monkeypatch):
    import requests as requests_lib

    monkeypatch.setattr(paypal.requests, "post",
                        lambda *a, **kw: _Resp(200, {"access_token": "t",
                                                     "expires_in": 100}))

    def boom(*a, **kw):
        raise requests_lib.ConnectionError("no route to host")

    monkeypatch.setattr(paypal.requests, "request", boom)
    with pytest.raises(paypal.PaypalError):
        _client().get_order("ORDER-1")


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
