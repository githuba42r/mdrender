# server/app/paypal.py
"""PayPal REST client for one-time top-up orders and webhook verification.

Talks to the PayPal Orders and Notifications APIs directly over HTTPS - no
SDK, just `requests`. Only provider identifiers ever touch this server:
card/bank details stay inside PayPal (design §11, terms).

The single billing flow: a buyer approves a one-time order, the capture is
credited to the local ledger (idempotent via the provider reference), and the
browser return URL reconciles the just-captured order so the happy path works
even before a webhook can reach the server. Subscription APIs are gone -
recurring plans were dropped in favour of prepaid credit.

The module is deliberately free of database access: callers in `billing` and
`app` own the rows, this file owns the wire.
"""
import base64
import datetime
import time

import requests

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
LIVE_BASE = "https://api-m.paypal.com"

TIMEOUT = 15


class PaypalError(Exception):
    """A PayPal API call failed (or returned something unparseable)."""

    def __init__(self, message, *, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


def enabled(config) -> bool:
    """True when the gateway is configured (client id + secret both set)."""
    return bool(getattr(config, "PAYPAL_CLIENT_ID", "")
                and getattr(config, "PAYPAL_CLIENT_SECRET", ""))


def base_url(config) -> str:
    return LIVE_BASE if str(getattr(config, "PAYPAL_MODE", "sandbox")).lower() == "live" \
        else SANDBOX_BASE


def parse_time(value):
    """PayPal RFC3339 timestamp -> epoch seconds, or None."""
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(datetime.timezone.utc)
        return int(parsed.timestamp())
    except (TypeError, ValueError):
        return None


def money(cents: int) -> str:
    """Cents -> the fixed 2-decimal string PayPal expects."""
    return f"{int(cents) / 100:.2f}"


class PaypalClient:
    """Thin authenticated wrapper over the PayPal REST API."""

    def __init__(self, config):
        self.config = config
        self.client_id = getattr(config, "PAYPAL_CLIENT_ID", "")
        self.client_secret = getattr(config, "PAYPAL_CLIENT_SECRET", "")
        self.base = base_url(config)
        self.currency = getattr(config, "PAYPAL_CURRENCY", "AUD") or "AUD"
        self.brand = getattr(config, "PAYPAL_BRAND_NAME", "MDRender") or "MDRender"
        self._token = None
        self._token_expires = 0.0

    # ---- transport --------------------------------------------------------

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires - 30:
            return self._token
        basic = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()).decode()
        try:
            resp = requests.post(
                f"{self.base}/v1/oauth2/token",
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {basic}",
                         "Accept": "application/json"},
                timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise PaypalError(f"token request failed: {exc}") from exc
        if resp.status_code != 200:
            raise PaypalError("PayPal rejected the client credentials",
                              status=resp.status_code, body=resp.text[:500])
        payload = resp.json()
        self._token = payload.get("access_token", "")
        self._token_expires = time.time() + float(payload.get("expires_in", 32400))
        if not self._token:
            raise PaypalError("PayPal returned no access token")
        return self._token

    def request(self, method, path, *, json_body=None, ok=(200, 201, 204)):
        """Call a PayPal endpoint. Raises PaypalError on transport/API failure."""
        token = self._access_token()
        try:
            resp = requests.request(
                method, f"{self.base}{path}",
                json=json_body,
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/json",
                         "Content-Type": "application/json"},
                timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise PaypalError(f"{method} {path} failed: {exc}") from exc
        if resp.status_code not in ok:
            detail = ""
            try:
                detail = (resp.json().get("details") or [{}])[0].get("issue", "")
            except ValueError:
                detail = resp.text[:300]
            raise PaypalError(f"{method} {path} -> {resp.status_code}"
                              f"{': ' + detail if detail else ''}",
                              status=resp.status_code, body=resp.text[:500])
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise PaypalError(f"{method} {path} returned non-JSON") from exc

    # ---- one-time top-up orders -------------------------------------------

    def create_order(self, *, amount_cents, currency, custom_id,
                     return_url, cancel_url, description):
        data = self.request("POST", "/v2/checkout/orders", json_body={
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {"currency_code": currency,
                           "value": money(amount_cents)},
                "custom_id": custom_id,
                "description": description[:120],
            }],
            "application_context": {
                "brand_name": self.brand,
                "return_url": return_url,
                "cancel_url": cancel_url,
                "user_action": "PAY_NOW",
                "shipping_preference": "NO_SHIPPING",
            },
        })
        return data

    def get_order(self, order_id: str) -> dict:
        return self.request("GET", f"/v2/checkout/orders/{order_id}")

    def capture_order(self, order_id: str) -> dict:
        return self.request("POST", f"/v2/checkout/orders/{order_id}/capture",
                            json_body={})

    @staticmethod
    def approve_link_for_order(payload: dict) -> str:
        for link in payload.get("links", []) or []:
            if link.get("rel") == "approve":
                return link.get("href", "")
        return ""

    # ---- webhooks -----------------------------------------------------------

    def verify_webhook(self, headers, raw_body: bytes) -> bool:
        """Signature-check a webhook delivery against PayPal's transmission.

        *headers* is the incoming request's headers (paypal-auth-algo,
        paypal-cert-url, paypal-transmission-id, paypal-transmission-sig,
        paypal-transmission-time). Requires PAYPAL_WEBHOOK_ID to be set.
        """
        webhook_id = getattr(self.config, "PAYPAL_WEBHOOK_ID", "")
        if not webhook_id:
            raise PaypalError("PAYPAL_WEBHOOK_ID is not configured")
        body_text = raw_body.decode("utf-8", "replace") if isinstance(
            raw_body, (bytes, bytearray)) else str(raw_body)
        return bool(self.request("POST", "/v1/notifications/verify-webhook-signature",
                                 json_body={
                                     "auth_algo": headers.get("paypal-auth-algo", ""),
                                     "cert_url": headers.get("paypal-cert-url", ""),
                                     "transmission_id": headers.get(
                                         "paypal-transmission-id", ""),
                                     "transmission_sig": headers.get(
                                         "paypal-transmission-sig", ""),
                                     "transmission_time": headers.get(
                                         "paypal-transmission-time", ""),
                                     "webhook_id": webhook_id,
                                     "webhook_event": body_text,
                                 }).get("verification_status") == "SUCCESS")


def client(config) -> PaypalClient:
    return PaypalClient(config)
