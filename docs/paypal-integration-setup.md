# PayPal billing setup

This server bills two kinds of customer through PayPal:

- **Accounts** (the `/account/billing` page): monthly/yearly plan
  subscriptions plus one-time prepaid top-ups.
- **Federated slave servers** (`/federation`): a paid server plan is paid as
  a subscription; the admin creates a shareable checkout link and the server
  operator completes it.

Everything runs through PayPal's REST APIs (`server/app/paypal.py`) — no
SDK, no third-party processor. This guide walks a developer account from
zero to a tested sandbox integration, then on to live.

## 1. Create a developer account and a REST app

1. Sign up at <https://developer.paypal.com> (free).
2. Go to **Apps & Credentials** and switch the toggle to **Sandbox**.
3. **Create App** — give it any name (e.g. `MDRender dev`).
4. Copy the **Client ID** and **Secret** shown for the new app.

Keep the toggle on **Sandbox** while developing. A second REST app will be
needed later on the **Live** toggle for production (step 7).

## 2. Configure the server

The gateway is "configured" only when both credentials are set — nothing
else activates it.

| Variable | Default | Meaning |
|----------|---------|---------|
| `PAYPAL_MODE` | `sandbox` | `sandbox` or `live`. Chooses the API host (`api-m.sandbox.paypal.com` vs `api-m.paypal.com`). |
| `PAYPAL_CLIENT_ID` | *(empty)* | REST app client id. |
| `PAYPAL_CLIENT_SECRET` | *(empty)* | REST app secret. |
| `PAYPAL_WEBHOOK_ID` | *(empty)* | Id of the webhook registered with this app (step 5). Required to verify event signatures. |
| `PAYPAL_VERIFY_WEBHOOKS` | `true` | Signature verification gate. Set `false` **only** in local development to accept unsignalled test events. |
| `PAYPAL_CURRENCY` | `AUD` | Default currency for plans/top-ups that don't carry their own. |
| `PAYPAL_BRAND_NAME` | `MDRender Cloud Push` | Name shown on the PayPal checkout page. |
| `PAYPAL_GRACE_DAYS` | `3` | Days past period end (or suspension) that access continues before entitlement lapses. |
| `PAYPAL_TOPUP_MIN_CENTS` | `500` | Minimum one-time top-up (5.00). |
| `PAYPAL_TOPUP_MAX_CENTS` | `50000` | Maximum one-time top-up (500.00). |
| `BILLING_ENFORCEMENT` | `false` | When `true`, a paid server plan with no live subscription is refused at the doorbell (`402`). Leave `false` until payments are proven end-to-end. |

```sh
export PAYPAL_MODE=sandbox
export PAYPAL_CLIENT_ID=AeEf...apps.paypal.com
export PAYPAL_CLIENT_SECRET=ELx...
export PAYPAL_WEBHOOK_ID=4J0...   # after step 5
```

Restart the server. The **Billing → Payments** tab now shows *Connected in
sandbox mode* instead of the "not configured" callout.

## 3. Sandbox test accounts

Sandbox checkouts are paid with **sandbox buyer accounts**, not real cards.

1. In the Developer Dashboard: **Testing Tools → Sandbox Accounts**.
2. PayPal usually pre-creates a pair. If not, **Create Account** twice:
   - one **Business** account (the seller — this is what your REST app acts
     as), and
   - one **Personal** account (the buyer).
3. Log in as the buyer at <https://sandbox.paypal.com> (not `www`) and keep
   the password somewhere handy — every checkout will ask for it.
4. Fund the buyer account (Sandbox account → **View/edit account** →
   **Update account** → funding source), or use PayPal's [sandbox test
   card numbers](https://developer.paypal.com/tools/sandbox-funding/)
   during checkout.

## 4. Billing plans

Local plans (created on **Billing → Plans**) are linked to PayPal plans lazily:

- **At first checkout** — a plan with no PayPal id yet is provisioned
  automatically (product + billing plan, then activated).
- **On demand** — the *Provision* button on **Billing → Plans** creates the
  PayPal plan ahead of time. The `linked` chip confirms the id is stored on
  the plan row.

Notes:

- Free plans (`price_cents = 0`) are never provisioned — entitlement doesn't
  need PayPal.
- The plan's billing period maps to PayPal's cycle: anything starting with
  `year` becomes a yearly cycle, everything else monthly.
- After changing a plan's **price**, press *Provision* again (it offers
  "force") to create a fresh PayPal plan. Existing subscribers stay on the
  old plan — PayPal does not re-price them.

## 5. Webhooks

Renewals, cancellations, failed payments and top-up captures arrive as
webhook events at `POST /api/billing/webhook`.

1. **Expose your server over HTTPS.** PayPal only delivers to `https` URLs.
   For local development:

   ```sh
   ngrok http 8080
   # -> Forwarding  https://xxxx.ngrok-free.app -> http://localhost:8080
   ```

2. Developer Dashboard → **Apps & Credentials** → your app → **Webhooks**
   → **Add Webhook**:
   - **Webhook URL**: `https://<your-public-host>/api/billing/webhook`
   - **Event types** — subscribe to all of:

     ```
     BILLING.SUBSCRIPTION.ACTIVATED
     BILLING.SUBSCRIPTION.RE-ACTIVATED
     BILLING.SUBSCRIPTION.UPDATED
     BILLING.SUBSCRIPTION.SUSPENDED
     BILLING.SUBSCRIPTION.PAYMENT.FAILED
     BILLING.SUBSCRIPTION.CANCELLED
     BILLING.SUBSCRIPTION.EXPIRED
     PAYMENT.CAPTURE.COMPLETED
     ```

3. Copy the **Webhook ID** from the created webhook's details and set
   `PAYPAL_WEBHOOK_ID` to it. Restart.

Signature verification sends the event headers + body back to PayPal
(`/v1/notifications/verify-webhook-signature`); a mismatch is rejected with
`400 bad signature`. If `PAYPAL_WEBHOOK_ID` is empty, verification fails
closed (reject everything) — set `PAYPAL_VERIFY_WEBHOOKS=false` only as a
local-dev escape hatch.

Duplicates are safe: every processed event id is recorded in
`billing_webhook_events`, and a repeat delivery answers `200` without
re-running. A processing error answers `500` so PayPal retries.

## 6. Testing in the sandbox

### Account subscription

1. Sign up / sign in to the account portal, open **Billing**
   (`/account/billing`).
2. **Subscribe** on a paid plan → you are redirected to the PayPal checkout.
3. Approve the payment with the sandbox buyer credentials.
4. PayPal sends you back to `/account/billing?ok=subscribed` — the plan row
   shows `active` with a renewal date, and the account's effective plan is
   now the paid one.

### Prepaid top-up

On **Billing**, enter an amount within the top-up bounds and **Top up**.
Approve the order in sandbox; the capture webhook (or the synchronous
return path) credits the ledger exactly once — replaying the return URL
must not double-credit.

### Federated server plan

1. On the master's **Federation** page, assign a paid plan to a server and
   press **Pay**.
2. The page shows a shareable PayPal link — open it (or hand it to the
   server operator) and approve with the sandbox buyer.
3. Return to `/federation?paypal_ok=1`: the server's Billing column shows
   `active`.

### Webhook events

Approvals exercise the synchronous return path; **webhooks carry the rest of
the lifecycle** (renewals, dunning, cancellations). To test them without
waiting weeks:

- Developer Dashboard → your app → **Webhooks** → select the webhook →
  **Send test event** (or the *Event logs* view's test action) and choose e.g.
  `BILLING.SUBSCRIPTION.SUSPENDED` or `BILLING.SUBSCRIPTION.CANCELLED`,
  with the resource id set to a real `provider_subscription_id` (listed on
  the **Payments** tab).
- Alternatively set `PAYPAL_VERIFY_WEBHOOKS=false` locally and POST a
  hand-crafted event JSON to `/api/billing/webhook`.

What each event should do:

| Event | Local effect |
|-------|--------------|
| `ACTIVATED` / `RE-ACTIVATED` / `UPDATED` | subscription → `active`, period end from `next_billing_time`, account plan pinned |
| `SUSPENDED` / `PAYMENT.FAILED` | subscription → `suspended` (grace applies) |
| `CANCELLED` | subscription → `cancelled` (access runs to the paid period + grace) |
| `EXPIRED` | subscription → `expired`, account plan override cleared |
| `PAYMENT.CAPTURE.COMPLETED` | top-up credited once (matched by `custom_id = order:<id>`) |

### Enforcement

With `BILLING_ENFORCEMENT=true`, a paid server plan with no live
subscription gets `402 subscription required` at the doorbell (after the
signature check — free plans and dead plans are unaffected). Turn this on
only after the flows above pass.

## 7. Going live

1. Create a **second REST app** on the **Live** toggle of *Apps &
   Credentials* and copy its client id/secret.
2. Register a **production webhook** on that live app pointing at your real
   `https://<host>/api/billing/webhook`, and collect its webhook id.
3. Set `PAYPAL_MODE=live`, swap in the live credentials and
   `PAYPAL_WEBHOOK_ID`, restart, and re-check the **Payments** tab shows
   *Connected in live mode*.
4. Re-provision paid plans (force) so they get live PayPal plan ids — the
   sandbox plan ids stored on the rows are invalid in live mode. Cancel any
   sandbox subscriptions so `BILLING_ENFORCEMENT` doesn't strand anyone.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| *Online payments are not configured* on any billing page | `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` not both set (or server not restarted). |
| *Webhook ID not set* banner on **Payments** | `PAYPAL_WEBHOOK_ID` empty — renewal events will not arrive. |
| `400 bad signature` on `/api/billing/webhook` | Wrong `PAYPAL_WEBHOOK_ID` for this app, or `PAYPAL_MODE` doesn't match the app the webhook belongs to. |
| Webhook URL unreachable | PayPal requires HTTPS and a publicly routable host — use ngrok (or similar) locally. |
| *PayPal plan creation returned no id* | Check the server log for the API error; usually a bad price/currency on the local plan. |
| Checkout approval works but status stays `pending` | Return path only succeeds if PayPal reports the subscription `active` — check the **Payments** tab and PayPal's sandbox API logs; then rely on the `ACTIVATED` webhook. |
| Double-counted top-up | Should be impossible — `billing_orders` completes once per order id. If seen, check for two *different* order rows (two checkouts), not one order credited twice. |
