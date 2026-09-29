# Push Federation Implementation Plan (planning guide)

> **Status: PLANNING ONLY — not started.** This is a guide for a future
> implementation effort, not a binding spec. Before coding, promote the
> decisions in [Open decisions](#open-decisions-ratify-before-coding) into a
> spec under `docs/superpowers/specs/` and then a task-by-task plan.
>
> **For agentic workers:** once ratified, execute with
> superpowers:subagent-driven-development or superpowers:executing-plans, one
> task at a time, checkbox by checkbox (`- [ ]`).

**Goal:** Let the single, store-distributed MDRender Android app receive push
doorbells from *any* self-hosted push server, via a central **federated master**
that owns the app's Firebase project. The master resolves each doorbell to one
device token and relays it, without being able to read or forge anything.

**Why:** FCM registration tokens are scoped to the Firebase project baked into
the APK (`app/google-services.json`, project `mdrender-push`). A self-hoster's
server uses its own project and therefore **cannot send** to the store app's
tokens. Today the only ways around this are (a) build your own APK with your own
`google-services.json`, or (b) share the app project's service account —
unacceptable. Federation keeps the FCM credential central and lets servers ask
the master to ring a device.

## Business model (this feature)

The master is operated by the project owner and costs real money to run (VM,
TLS, monitoring, the app's Firebase project overhead, support). Access to the
**federation relay + registry** is sold as a subscription that covers those
costs:

- **$2/month** or **$20/year** per self-hosted push server (annual ≈ two months
  free).
- Billing covers the relay only — file transfer stays on the self-hoster's own
  server and is never billed.
- Federation (device sync + doorbell forwarding) requires an **active
  subscription**, with a short grace period after a failed renewal.
- Operators who don't want to pay can still self-host fully by building their
  own APK with their own Firebase project (no master involvement).

**Tech stack (planned):** Python 3.11 + Flask + `cryptography` + SQLite for the
master (reuse the existing `server/` codebase as a deployment mode); the
existing self-hosted server gains a federation client path; the Android app is
**unchanged for the MVP**.

**Related:** [Cloud Push design](../specs/2026-07-25-cloud-push-design.md),
[Cloud Push plan](2026-08-28-cloud-push.md).

---

## Roles

| Role | Owns | Trust |
|------|------|-------|
| **App** | Its FCM token, `push_key`, pinned server public key | Does not trust the master; verifies manifests against the paired server key |
| **Self-hosted push server** | Files, devices, push records, its RSA signing key, each device's `push_key` | Full trust of its own data; authenticates to the master |
| **Federated master** | The app-project FCM service account; server registry; `(server_id, device_id) → fcm_token` map | Relays only; **must not** be able to read doorbells or forge pushes |

The master is a **dumb, semi-trusted relay**. It is never in the file data path:
the phone still downloads from its paired self-hosted server over HTTPS.

---

## Current building blocks (reuse)

- `server/app/trigger.py`
  - `trigger_plaintext(server_url, push_id, challenge_key)` — the doorbell
    payload; already contains `server_url`, so the phone knows **which** server
    to pull from.
  - `seal_trigger(push_key, ...)` → `{"i": iv_b64, "c": ct||tag_b64}`;
    AES-256-GCM under the per-device `push_key`.
- `server/app/app.py::_ring_doorbell(device, push_row)` — the single place that
  sends FCM (`fcm.send({"p": ..., "i": ...}, device["fcm_token"])`).
- `server/app/fcm.py::FcmClient` — service-account JWT → OAuth → FCM v1 send.
- `app/.../cloudpush/PushFcmService.kt` — receives `{"p","i"}`, calls the handler.
- `app/.../cloudpush/CloudPushMessageHandler.kt` — decrypts with `pushKey`,
  fetches the manifest from `server_url`, verifies with the pinned
  `serverPublicKeyPem`. Undecryptable/unverifiable messages are dropped.
- `app/.../cloudpush/PushFcmService.onNewToken` → `PushClient.rotateToken(...)` —
  the app already reports token rotation to its **paired server**.
- Server auth patterns to mirror: OAuth client + short-code enrolment
  (`/api/enrol*`), Bearer validation (`bearer_client_id`), session-gated admin
  pages.

---

## Trust model and invariants

1. **Only the holder of a device's `push_key` can produce an effective
   doorbell.** GCM authenticates the sealed trigger, so a doorbell not sealed to
   the target device fails to open and is dropped. This is the security
   boundary, not the master's registry.
2. **Only the paired server can author a push the app accepts.** The manifest is
   RSA-SHA256-signed and the app verifies against the key pinned at pairing, so
   neither the master nor a rogue server can inject file names or retrieval
   keys.
3. **The master cannot read** the doorbell (no `push_key`) and **cannot forge**
   a manifest (no server signing key). It can only deliver, delay, drop, or
   replay.
4. **No file bytes transit the master.** It only forwards a constant-size FCM
   data message.
5. **The FCM service account never leaves the master.** No shared credential is
   distributed to self-hosters.

### Master compromise — blast radius

| Can | Cannot |
|-----|--------|
| See metadata: `server_id`, `device_id`, token, timing, volume | Read doorbell contents |
| Suppress/delay doorbells (DoS) | Forge a valid manifest (`serverPublicKeyPem`) |
| Send garbage FCM data to any known token | Forge an effective doorbell (needs `push_key`) |
| Enumerate device tokens in its project | Move/replace file contents |

Mitigations: keep stored metadata minimal, rate-limit, sign server requests,
and treat the master as replaceable (operators can self-build an APK or use a
manual pull as fallback).

---

## Architecture

### Registration (once per self-hosted server)

```
self-hosted server ──POST /api/federation/enrol/start──▶ master
        │                                                  │
        │◀── {enrolment_id, verification_uri} ─────────────┘
   operator opens verification_uri (signed-in), clicks approve OR reads short code
        │                                                  │
        ├─POST /api/federation/enrol {enrolment_id, code}─▶│
        │◀── {server_id, server_secret} ───────────────────┘
   server stores creds (0600), uses client_credentials for a Bearer token
```

Mirror the existing tool-enrolment flow (`/api/enrol/start`, `/enrol/<id>`
approve page, short 6-char code with TTL + attempt cap, `/oauth/token`).
See the current implementation in `server/app/app.py`.

### Device registry sync (server-authenticated)

```
device registers/rotates on its server ──▶ server PUT /api/federation/devices/{device_id}
                                              {name, fcm_token}         (Bearer)
device removed on server             ──▶ server DELETE /api/federation/devices/{device_id}
```

The self-hosted server is authoritative for its own devices and tokens; the
app still talks only to it.

### Doorbell (per push)

```
push stored on server ──▶ seal_trigger(push_key, server_url, push_id, challenge_key)
                       ──▶ POST /api/federation/doorbell {device_id, p, i, request_id}
                              (Bearer server creds)
master: look up (server_id, device_id) → fcm_token; send FCM {"p","i"}
app: decrypt → fetch manifest from server_url → verify sig → enqueue downloads
```

The master never sees `push_id`/`server_url` (they are inside the ciphertext);
`request_id` is an opaque server-generated id used only for idempotent retries.

---

## Registry design

**Table `federated_devices`** (master):

| Column | Notes |
|--------|-------|
| `server_id` | FK to federated server |
| `device_id` | Opaque stable id from the self-hosted server (see decision D3) |
| `fcm_token` | Current token |
| `name` | Optional display name for operator UI; may be blank |
| `updated_at` | Last sync |
| `last_doorbell_at` | Rate-limit / observability |
| PK | `(server_id, device_id)` |

**Table `federated_servers`** (master):

| Column | Notes |
|--------|-------|
| `server_id` | PK |
| `secret_hash` | Hashed federation secret |
| `name` | Operator-visible label |
| `created_at`, `revoked_at`, `last_seen_at` | Lifecycle / ops |
| `plan` | `monthly` \| `yearly` (billing) |
| `status` | `trialing` \| `active` \| `past_due` \| `canceled` \| `suspended` |
| `current_period_end` | Entitlement horizon |
| `stripe_customer_id`, `stripe_subscription_id` | Provider ids (no card data) |

**Lifecycle rules:**
- Upsert on `PUT`; `DELETE` on removal.
- Token rotation replaces `fcm_token` and invalidates the old one for routing.
- Revoking a server blocks its token and removes (or archives) its devices.
- Prune devices FCM reports as unregistered, and devices not synced within a TTL.
- Uniqueness policy: a given `fcm_token` should map to at most one
  `(server_id, device_id)`; reject/flag duplicates.

---

## Master API (proposed)

| Method | Path | Auth | Body → Response |
|--------|------|------|-----------------|
| POST | `/api/federation/enrol/start` | none | `{}` → `{enrolment_id, verification_uri}` |
| POST | `/api/federation/enrol` | code | `{enrolment_id, code}` → `{server_id, server_secret}` |
| POST | `/oauth/token` | client creds | `{grant_type, client_id, server_secret}` → `{access_token}` |
| PUT | `/api/federation/devices/{device_id}` | Bearer | `{name, fcm_token}` → `{ok:true}` |
| DELETE | `/api/federation/devices/{device_id}` | Bearer | `{}` → `{ok:true}` |
| POST | `/api/federation/doorbell` | Bearer | `{device_id, p, i, request_id}` → `{ok:true}` |
| GET | `/api/federation/whoami` | Bearer | `{}` → `{server_id, name, status, current_period_end}` |
| GET | `/api/health` | none | `{}` → `{ok:true}` |
| POST | `/api/billing/checkout` | Bearer | `{plan}` → `{checkout_url}` (Stripe-hosted) |
| POST | `/api/billing/portal` | Bearer | `{}` → `{portal_url}` (manage/cancel) |
| POST | `/api/billing/webhook` | Stripe sig | provider events → entitlement updates |

Admin (session-gated, master only): list/revoke federated servers, view device
counts.

Error contract: `401 unauthorized` (bad/revoked creds), `403 forbidden`
(server not allowed to address that device), `404 unknown device`,
`409 duplicate token` (policy), `429 rate limited`.

---

## Self-hosted server changes

- **Config** (`server/app/config.py`): add `FEDERATION_URL` (empty = disabled),
  `FEDERATION_SERVER_ID`, `FEDERATION_SERVER_SECRET`, retry/backoff knobs.
- **`_ring_doorbell`** (`server/app/app.py`): keep building the sealed trigger
  locally; choose transport:
  1. local FCM if `FCM_SERVER_KEY` configured (existing behaviour), else
  2. federation: `POST {FEDERATION_URL}/api/federation/doorbell` with
     `{device_id, p, i, request_id}`.
  Returns the same bool contract; failures feed the existing retry worker.
- **Device sync**: hook `register_device`, `update_device_token`, `delete_device`
  (and the sweep) to `PUT`/`DELETE` the master registry. Best-effort with retry;
  never block the local operation.
- **Token acquisition**: client-credentials grant against the master; cache the
  access token; re-enrol/`whoami` on 401.
- **`localsend-send`/ops tooling**: optional `--federation-status` to show the
  master-registered devices (parallel to existing `--list`).

**Backwards compatibility:** unchanged when `FEDERATION_URL` is unset. The
existing "bring your own Firebase project + build your own APK" path remains
supported.

---

## Android app changes

**MVP: none.** The app already drops doorbells it cannot decrypt or verify, and
already reports token rotation to its paired server (which forwards to the
master). The trigger's `server_url` is the paired server, so pulls are directed
correctly.

Optional hardening (separate task, not required for MVP):
- **Verify `server_url`** in the decrypted trigger equals the configured server
  before fetching (defence in depth against a malicious relay that somehow
  obtained a doorbell).
- **Replay/staleness**: extend the sealed trigger to v2 with `iat`/`nonce` and
  reject stale/duplicate doorbells, to blunt replayed deliveries.
- Consider not being a `FirebaseMessagingService`-only receiver when unpaired
  (already cheap: it no-ops).

---

## Security considerations

- **Cross-tenant ringing is inherently blocked** by `push_key` (invariant 1): a
  rogue server cannot craft an effective doorbell for a device it isn't paired
  with. A spoofed registry entry yields only dropped FCM.
- **Garbage/spam FCM** to a known token is possible for anyone with the token or
  a server credential → rate-limit per `server_id` and per token; alert on
  spikes.
- **Token concentration**: the master holds all tokens for its project. Store
  the minimum (token + opaque ids); treat the DB as sensitive; back it up.
- **Replay**: a captured `{p,i}` can be replayed until the push is acked/purged;
  `challenge_key` is per push and bytes are deleted on ack. Consider v2 trigger
  staleness (above) if replay matters.
- **Enrolment abuse**: master enrolment should be operator-approved (not
  open self-serve) or gated by code + rate limits, mirroring tool enrolment.
- **Revocation**: revoke server at master (immediate Bearer rejection, drop its
  registry rows); rotate device `push_key` at the server to kill leaked
  doorbells (existing lever).
- **Single point of failure / censorship**: master downtime or unlisting stops
  doorbells. Document fallbacks (self-built APK with own Firebase; manual pull;
  operator re-push).
- **Legal/abuse**: the master's Firebase project can be rate-limited/flagged;
  monitor and cap.

---

## Service billing (subscription access)

The relay is a paid service; the self-hoster's own server and file transfer are
not. Billing is designed to be low-touch and to keep card data out of this
codebase.

**Pricing:** `$2/month` or `$20/year` per federated server. Treat prices/currency
as configuration, not hard-coded constants.

**Provider:** Stripe Billing + Checkout (hosted), web only (no Play Billing).
- `POST /api/billing/checkout` → a Checkout Session for a plan, carrying
  `server_id` in metadata / `client_reference_id`.
- `POST /api/billing/portal` → Stripe Billing Portal (card change, cancel).
- `POST /api/billing/webhook` → signature-verified, **idempotent** entitlement
  updates for `checkout.session.completed`,
  `customer.subscription.updated/deleted`, `invoice.payment_failed`.

**Entitlement model:** subscription state lives on the `federated_servers` row
(see registry). Device-sync and doorbell endpoints require `status in
{trialing, active}`, or within a grace window past `current_period_end`.
Otherwise `402 {"error":"subscription required"}`.
- **Sign-up order:** create the server enrolment first (get `server_id`) → pay →
  webhook attaches the subscription to that row. One subscription covers one
  server.
- **Failed renewal:** Stripe dunning; keep a short grace period, then suspend the
  relay. Device data stays local; nothing is lost, and paying resumes instantly.
- **Cancellation:** access continues to `current_period_end`, then the relay is
  cut (the self-hosted server keeps working locally without doorbells).

**Compliance / ops:**
- **PCI:** Stripe-hosted fields only; we store only provider ids + status.
- **Tax:** decide currency and whether prices are tax-inclusive (AU GST 10%);
  use Stripe Tax or a merchant-of-record to avoid handling VAT/GST ourselves.
- **Policies:** Terms, Privacy, and Refund pages; clear cancellation terms.
- **Fraud/abuse:** requiring payment is itself an abuse control; handle
  chargebacks by revoking the server.
- **Reporting/alerting:** active servers, MRR, failed payments, webhook failures.

## Open decisions (ratify before coding)

- **D1 — Registry population:** server-driven (recommended; app unchanged) vs
  app-driven binding (app also registers with the master using a server-issued
  proof). Server-driven is simpler; the security boundary does not depend on it.
- **D2 — Server enrolment:** operator-approved console vs self-serve with code +
  rate limits vs invitation tokens.
- **D3 — `device_id` scheme:** `sha256(device_secret)` (stable, non-reversible,
  already available server-side) vs a random UUID minted at first sync.
  Recommend the hash.
- **D4 — Master deployment:** a mode of the existing `server/` app
  (`ROLE=master`) vs a separate service. Reusing the codebase shares auth, FCM,
  and templates; recommend a mode/flags first.
- **D5 — Replay protection:** acceptable for MVP, or require trigger v2 from the
  start?
- **D6 — Fallback transport:** keep FCM-only, or add optional polling for
  self-hosters who refuse federation? (The Cloud Push spec's R1 notes polling is
  currently structurally dead.)
- **D7 — Token metadata:** store device display names at the master (nicer ops
  UI, more metadata) vs ids only.
- **D8 — Billing provider:** Stripe (recommended; Checkout + Billing Portal)
  vs a merchant-of-record (Paddle/Lemon Squeezy — offloads global tax) vs manual
  invoicing.
- **D9 — Price/currency:** AUD vs USD; keep both monthly and annual, and whether
  prices are tax-inclusive (AU GST 10%).
- **D10 — Free tier / trial:** none vs a 14-day trial vs a small free allowance
  (e.g. one device). Affects abuse and conversion.
- **D11 — Enforcement:** hard block immediately on non-payment vs a grace window
  (recommended: ~7 days) before suspending the relay.

---

## Implementation phases (tracer bullets)

Each task is independently and end-to-end verifiable. No task touches the
Android app.

### Phase F1 — Master skeleton + registry

- [ ] **F1.1** Add a `master` deployment mode (config flag) to `server/`,
      mounting no file storage and disabling push/pairing routes.
- [ ] **F1.2** Federated-server enrolment: reuse the OAuth-client + short-code
      flow; store `federated_servers` (incl. subscription columns); Bearer
      validation. Leave an **entitlement hook** (permissive stub until F7).
- [ ] **F1.3** `federated_devices` schema + `PUT`/`DELETE /api/federation/devices/{id}`
      with `(server_id, device_id)` scoping and token-uniqueness policy.
- [ ] **F1.4** `GET /api/federation/whoami`; admin page listing servers/devices.
      **Accept:** pytest covers enrol → token → upsert → list → revoke.

### Phase F2 — Server federation client

- [ ] **F2.1** Config knobs + credential persistence (0600) + token cache.
- [ ] **F2.2** Sync device upsert/rotation/deletion to the master, retrying on
      failure without blocking local operations.
      **Accept:** unit tests with a fake master; offline master does not break
      device registration.

### Phase F3 — Federated doorbell (happy path)

- [ ] **F3.1** `POST /api/federation/doorbell` on the master: check entitlement
      (permissive stub until F7), resolve token, enforce server scope, call
      `FcmClient.send`, honour `request_id` idempotency, rate-limit.
- [ ] **F3.2** `_ring_doorbell` federation branch on the self-hosted server.
      **Accept:** end-to-end with a fake FCM on the master — a push to a synced
      device produces exactly one targeted FCM send; unknown/foreign
      `device_id` → 403/404; duplicate `request_id` → single send.

### Phase F4 — Lifecycle & resilience

- [ ] **F4.1** Token rotation, device removal, server revocation cascade, TTL
      sweeps, FCM `UNREGISTERED` cleanup.
- [ ] **F4.2** Retry/backoff integration with the existing retry worker.
      **Accept:** revocation immediately blocks; rotation re-routes; stale rows
      pruned.

### Phase F5 — Hardening & ops

- [ ] **F5.1** Rate limits + metrics/logging (per server, per token, failures).
- [ ] **F5.2** Backup/restore + runbook for the master; secret handling review.
- [ ] **F5.3** (Optional) app-side hardening: verify `server_url`; trigger v2
      staleness/nonce.
      **Accept:** documented runbook; abuse simulation stays within limits.

### Phase F6 — Packaging & docs

- [ ] **F6.1** Docker/compose profile for the master (its own
      `fcm-service-account.json`, no storage volume needed beyond SQLite).
- [ ] **F6.2** Docs: self-hoster "connect to the public master" guide; master
      operator guide; update `server/README.md` and the tools README.
- [ ] **F6.3** Promote the ratified decisions into a spec and link it here.

### Phase F7 — Billing & entitlement

- [ ] **F7.1** Stripe products/prices (config); `POST /api/billing/checkout`
      (plan + `server_id` metadata) and `POST /api/billing/portal`.
- [ ] **F7.2** `POST /api/billing/webhook`: signature verification, event-id
      idempotency, and entitlement updates on
      complete/update/delete/payment_failed; attach subscription to the server.
- [ ] **F7.3** Enforce entitlement in `doorbell` + device-sync endpoints (`402`
      when inactive) with a grace window; admin view of status; suspend/resume.
      **Accept:** a server with no active subscription/grace cannot ring; a
      cancellation revokes relay at period end; duplicate webhook delivery is a
      no-op; pytest drives fake Stripe events.
- [ ] **F7.4** Compliance/ops: Stripe Tax (or merchant-of-record), receipts,
      Terms/Privacy/Refund pages, webhook-failure alerting, MRR/failed-payment
      reporting.

---

## Testing strategy

- **Master**: pytest like `server/tests/`; fake `FcmClient`/`make_fcm_client`
  monkeypatch (existing pattern). Cover auth, scoping, idempotency, rate limits,
  revocation.
- **Self-hosted server**: pytest with a fake master HTTP server (like
  `tools/localsend-send/test_localsend_send.py` does for the push server);
  offline/error paths.
- **End-to-end**: two fakes — master (captures FCM sends) + self-hosted server —
  driving a real `localsend-send` push; assert one targeted FCM per push and no
  cross-tenant delivery.
- **App**: no new tests for MVP; optional hardening adds `CloudPushMessageHandler`
  tests for `server_url` mismatch and stale trigger.

## Risks

| Risk | Mitigation |
|------|------------|
| Master becomes an abuse magnet | Approval-gated enrolment, rate limits, monitoring, easy revocation |
| Master outage stops all doorbells | Document fallbacks (own APK/Firebase, manual pull); retries; status page |
| Metadata/privacy concerns | Minimal stored fields; opaque ids; documented trust model |
| Cross-tenant attempts | `push_key` invariant means they are dropped; still rate-limit |
| Scope creep into app changes | Keep MVP app-free; hardening behind a separate task |
| Two code paths in `_ring_doorbell` | Small, single branch; covered by tests both ways |
| PCI/tax burden of taking payments | Stripe-hosted checkout only; Stripe Tax or a merchant-of-record; store no card data |
| Non-payment / revenue leakage | Dunning + grace window + suspend; require active entitlement on every relay call |
| One subscription covering many servers | Bind subscription to `server_id` metadata; enforce one sub per server |
| Billing outage blocks paying servers | Grace period + admin manual override |
| Chargebacks/abuse | Payment requirement deters spam; revoke on dispute |

## References

- `server/app/trigger.py`, `server/app/fcm.py`, `server/app/app.py` (`_ring_doorbell`, enrol/OAuth)
- `app/src/main/java/com/a42r/mdrender/cloudpush/{PushFcmService,CloudPushMessageHandler,PushClient}.kt`
- `docs/superpowers/specs/2026-07-25-cloud-push-design.md` (R1 polling-fallback note)
- `docs/superpowers/plans/2026-08-28-cloud-push.md`
- Stripe Billing + Checkout + Billing Portal docs (billing provider, decision D8)
