# server/app/paypal.py
"""PayPal REST client for subscriptions (recurring plans) and orders (top-ups).

Talks to the PayPal Subscriptions, Catalog, Orders and Notifications APIs
directly over HTTPS - no SDK, just `requests`. Only provider identifiers ever
touch this server: card/bank details stay inside PayPal (design §11, terms).

Two flows are supported:

* **Subscriptions** - a local `billing_plans` row (scope `slave` or `account`)
  is provisioned into a PayPal billing plan once (``ensure_paypal_plan``), then
  buyers approve a subscription against it. Lifecycle arrives over webhooks;
  the browser return URL also reconciles the just-approved subscription so the
  happy path works even before a webhook can reach the server.

* **Orders** - one-time captures for prepaid top-ups, credited to the ledger
  on capture (idempotent via the provider reference).

The module is deliberately free of database access: callers in `billing` and
`app` own the rows, this file owns the wire.
"""
import base64
import datetime
import time

import requests

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
LIVE_BASE = "https://api-m.paypal.com"

# PayPal subscription statuses -> our local vocabulary.
STATUS_MAP = {
    "APPROVAL_PENDING": "pending",
    "APPROVAL_IN_PROGRESS": "pending",
    "ACTIVE": "active",
    "SUSPENDED": "suspended",
    "CANCELLED": "cancelled",
    "EXPIRED": "expired",
}

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


def local_status(paypal_status: str) -> str:
    """Map a PayPal subscription status onto ours (unknown => expired)."""
    return STATUS_MAP.get((paypal_status or "").upper(), "expired")


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

    # ---- catalog / plan provisioning --------------------------------------

    def create_product(self, name: str) -> str:
        """One catalog product representing this deployment's paid plans."""
        data = self.request("POST", "/v1/catalogs/products", json_body={
            "name": name[:120],
            "description": f"{name} subscription",
            "type": "SERVICE",
            "category": "SOFTWARE",
        })
        return data.get("id", "")

    def create_billing_plan(self, *, name, price_cents, currency, interval) -> str:
        """Create (DRAFT) a PayPal billing plan for a local plan row.

        *interval* is 'month' or 'year'; price 0 never reaches here - free
        plans are entitled without a gateway.
        """
        frequency = "YEAR" if str(interval).lower().startswith("year") else "MONTH"
        data = self.request("POST", "/v1/billing/plans", json_body={
            "name": name[:120],
            "description": name[:240],
            "billing_info": {
                "billing_cycles": [{
                    "frequency": frequency,
                    "tenure": "REGULAR",
                    "interval_count": 1,
                    "total_cycles": 0,
                    "pricing_scheme": {
                        "fixed_price": {"value": money(price_cents),
                                        "currency_code": currency},
                    },
                }],
                "payment_preferences": {
                    "auto_bill_outstanding": True,
                    "setup_fee": {"value": "0.00", "currency_code": currency},
                    "payment_failure_threshold": 3,
                },
                "taxes": {"percentage": "0", "inclusive": False},
            },
        })
        return data.get("id", "")

    def activate_plan(self, plan_id: str) -> None:
        self.request("POST", f"/v1/billing/plans/{plan_id}/activate",
                     json_body={})

    def ensure_product(self, cache) -> str:
        """Return the catalog product id held in *cache*, creating it once.

        *cache* is a zero-arg callable pair (get, set) so the caller can pin
        the id in server_settings without this module knowing about them.
        """
        product_id = cache[0]()
        if product_id:
            return product_id
        product_id = self.create_product(self.brand)
        if not product_id:
            raise PaypalError("PayPal product creation returned no id")
        cache[1](product_id)
        return product_id

    # ---- subscriptions -----------------------------------------------------

    def create_subscription(self, *, plan_id, custom_id, return_url, cancel_url):
        """Start a subscription; returns the PayPal payload (id + links)."""
        return self.request("POST", "/v1/billing/subscriptions", json_body={
            "plan_id": plan_id,
            "custom_id": custom_id,
            "application_context": {
                "brand_name": self.brand,
                "return_url": return_url,
                "cancel_url": cancel_url,
                "user_action": "SUBSCRIBE_NOW",
                "shipping_preference": "NO_SHIPPING",
            },
        })

    def get_subscription(self, subscription_id: str) -> dict:
        return self.request("GET", f"/v1/billing/subscriptions/{subscription_id}")

    def cancel_subscription(self, subscription_id: str, reason: str = "") -> None:
        self.request("POST", f"/v1/billing/subscriptions/{subscription_id}/cancel",
                     json_body={"reason": reason[:240] or "Cancelled by customer"})

    @staticmethod
    def approve_link(payload: dict) -> str:
        """The rel=approve link a browser must be sent to."""
        for link in payload.get("links", []) or []:
            if link.get("rel") == "approve":
                return link.get("href", "")
        return ""

    @staticmethod
    def subscription_period(payload: dict):
        """(start, end) epoch seconds for an ACTIVE PayPal subscription."""
        billing = payload.get("billing_info") or {}
        end = parse_time(billing.get("next_billing_time"))
        created = parse_time(payload.get("create_time"))
        start = created if created is not None else int(time.time())
        return start, end

    # ---- orders (one-time top-ups) ------------------------------------------

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
