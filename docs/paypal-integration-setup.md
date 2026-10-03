# PayPal billing setup

This server bills two kinds of customer through PayPal — always as **one-time
prepaid credit**, never as a recurring subscription:

- **Accounts** (the `/account/billing` page): the buyer tops up a balance;
  metered usage (messages, storage) draws it down. With enforcement on, an
  account that is not in credit cannot upload or push.
- **Federated slave servers** (`/federation`): a paid server plan requires a
  prepaid balance too. The admin creates a shareable checkout link and the
  server operator (or the admin) completes it.

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
| `PAYPAL_CURRENCY` | `AUD` | Default currency for top-ups that don't carry their own. |
| `PAYPAL_BRAND_NAME` | `MDRender Cloud Push` | Name shown on the PayPal checkout page. |
| `PAYPAL_TOPUP_MIN_CENTS` | `500` | Minimum one-time top-up (5.00). |
| `PAYPAL_TOPUP_MAX_CENTS` | `50000` | Maximum one-time top-up (500.00). |
| `BILLING_ENFORCEMENT` | `false` | When `true`, **metered** usage requires prepaid credit: an account whose plan charges for storage (`storage_cents_per_mb > 0`) is refused uploads at zero credit, one whose plan charges per message (`message_cents_per_1000 > 0`) is refused pushes, and each file in a batch must also fit inside the remaining credit. Zero-rate plans — and accounts with no plan — are exempt, because billing can never charge them; a **paid** server plan with no credit is refused at the doorbell (`402`). Leave `false` until payments are proven end-to-end. |

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

Local plans (created on **Billing → Plans**) are just that — local. They are
never provisioned into PayPal: a plan's `price` is only the *paid marker*
for a federation server (non-zero ⇒ that server's doorbell requires prepaid
credit), while its message/storage rates meter usage into the ledger.

- Free plans (`price_cents = 0`) and zero-rate account plans simply charge
  nothing; entitlement is decided purely by the credit balance.
- Changing a plan's price or rates takes effect immediately — there is no
  provider-side plan to re-create.

## 5. Webhooks

Top-up captures arrive as webhook events at `POST /api/billing/webhook`.
(The synchronous browser return path credits the ledger too — the webhook is
the belt to that suspenders.)

1. **Expose your server over HTTPS.** PayPal only delivers to `https` URLs.
   For local development:

   ```sh
   ngrok http 8080
   # -> Forwarding  https://xxxx.ngrok-free.app -> http://localhost:8080
   ```

2. Developer Dashboard → **Apps & Credentials** → your app → **Webhooks**
   → **Add Webhook**:
   - **Webhook URL**: `https://<your-public-host>/api/billing/webhook`
   - **Event types** — subscribe to:

     ```
     PAYMENT.CAPTURE.COMPLETED
     ```

     (Legacy `BILLING.SUBSCRIPTION.*` subscriptions are accepted and
     ignored — they log `detail: "ignored"` and change nothing. Any other
     event type behaves the same way.)

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

### Account top-up

1. Sign up / sign in to the account portal, open **Billing**
   (`/account/billing`).
2. Enter an amount within the top-up bounds and **Top up** → you are
   redirected to the PayPal checkout.
3. Approve the order with the sandbox buyer credentials.
4. PayPal sends you back to `/account/billing?ok=topup` — the balance tile
   shows the new credit with an *in credit* chip, and the payment history
   lists the order.
5. Replaying the return URL must not double-credit: the ledger has one
   `topup` row per order.

### Federated server top-up

1. On the master's **Federation** page, assign a paid plan to a server,
   enter an amount next to **Pay** and press it.
2. The page shows a shareable PayPal link — open it (or hand it to the
   server operator) and approve with the sandbox buyer.
3. Return to `/federation?ok=topup`: the server's Billing column shows
   `$<amount> in credit`.

### Webhook events

Approvals exercise the synchronous return path; the webhook carries the
asynchronous case (e.g. the buyer closes the tab before the return hits).
To test it without a second checkout:

- Developer Dashboard → your app → **Webhooks** → select the webhook →
  **Send test event** with `PAYMENT.CAPTURE.COMPLETED` and the resource's
  `custom_id` set to `order:<order_id>` (order ids are listed on the
  **Payments** tab).
- Alternatively set `PAYPAL_VERIFY_WEBHOOKS=false` locally and POST a
  hand-crafted event JSON to `/api/billing/webhook`.

What each event should do:

| Event | Local effect |
|-------|--------------|
| `PAYMENT.CAPTURE.COMPLETED` with `custom_id = order:<id>` | top-up credited once, order → `captured` |
| `PAYMENT.CAPTURE.COMPLETED` for an unknown order | `detail: "unknown order"` — accepted, no change |
| `BILLING.SUBSCRIPTION.*` (legacy) / anything else | `detail: "ignored"` — accepted, no change |

### Enforcement

With `BILLING_ENFORCEMENT=true` (after the flows above pass), a gate
applies only where the account's effective plan can actually charge —
**zero-rate plans are exempt**, as is an account with no plan at all:

- **Account uploads** (`POST /api/account/upload`): gated only when
  `storage_cents_per_mb > 0`. Then `402 payment required` while
  `balance <= 0`; additionally every file in the batch is costed against
  the credit (`ceil(bytes / 1 MiB) × storage_cents_per_mb`) and a file
  that costs more than the balance is refused by name.
- **Push doorbells** (`POST /api/push`): gated only when
  `message_cents_per_1000 > 0` (pushes are metered as messages, not
  storage). Same credit gate and per-file check, so a device cannot be
  woken without credit behind it.
- **Federation doorbell** (`POST /api/federation/doorbell`): refused with
  `402 payment required` only when the server's effective plan is paid and
  its prepaid balance is not positive. Free plans are never gated.

To run a free tier with enforcement on, assign a zero-rate plan (both
rates 0) as the default group's plan: metered plans gate, free plans
don't.

## 7. Going live

1. Create a **second REST app** on the **Live** toggle of *Apps &
   Credentials* and copy its client id/secret.
2. Register a **production webhook** on that live app pointing at your real
   `https://<host>/api/billing/webhook` (event type
   `PAYMENT.CAPTURE.COMPLETED`), and collect its webhook id.
3. Set `PAYPAL_MODE=live`, swap in the live credentials and
   `PAYPAL_WEBHOOK_ID`, restart, and re-check the **Payments** tab shows
   *Connected in live mode*.
4. There are no provider-side plans to migrate — balances are local ledger
   rows, so a sandbox balance simply does not carry over (top up again in
   live mode).

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| *Online payments are not configured* on any billing page | `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` not both set (or server not restarted). |
| *Webhook ID not set* banner on **Payments** | `PAYPAL_WEBHOOK_ID` empty — capture events will not arrive. |
| `400 bad signature` on `/api/billing/webhook` | Wrong `PAYPAL_WEBHOOK_ID` for this app, or `PAYPAL_MODE` doesn't match the app the webhook belongs to. |
| Webhook URL unreachable | PayPal requires HTTPS and a publicly routable host — use ngrok (or similar) locally. |
| `402 payment required` on upload/push/doorbell with enforcement on | The account (or, for the doorbell, the slave server) has no credit — top up on **Billing** / **Federation**, or turn enforcement off while developing. |
| Upload refused with *"… costs N cents … only has M cents"* | That single file is more expensive than the remaining credit; top up or send a smaller file. |
| Checkout approval works but the balance stays put | Check the **Payments** tab: the order row should read `captured`. If it is still `pending`, PayPal's capture call failed — check the server log and PayPal's sandbox API logs; the `PAYMENT.CAPTURE.COMPLETED` webhook will finish it when it arrives. |
| Double-counted top-up | Should be impossible — `billing_orders` completes once per order id. If seen, check for two *different* order rows (two checkouts), not one order credited twice. |
