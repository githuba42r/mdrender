# Federated Push Server — design

**Status:** Draft (branch `feature/federated-push`). Supersedes the relay-only
plan in [`docs/superpowers/plans/2026-09-29-push-federation.md`](../plans/2026-09-29-push-federation.md)
(now marked superseded) by widening it from a dumb relay into a multi-tenant,
billable service with master/slave deployment modes and email accounts.

**Branch:** `feature/federated-push`.

**Terminology note.** A **Client** was renamed to an **Account** (the billable
customer / tenant) to avoid colliding with the existing OAuth **`clients`**
table, which is renamed **`oauth_clients`** (the enrolled CLI credentials).

---

## 1. Purpose

Turn the single self-hosted push server into a **federated service**:

- A **master** owns the app's Firebase project and the public service surface.
- **Slave** push servers (self-hosted or managed) register with the master and
  relay doorbells through it.
- **Accounts** (people/businesses) sign up with an email, upload files into
  per-account storage, and have them delivered to their registered devices.
- The **master is also a direct account push server**: accounts can register
  against the master itself and push files through it, with no slave involved.
- Access is **prepaid**: slave servers pay the master a **flat monthly fee**;
  accounts hosted directly on a server are metered for storage and messages.

The end-user app is unchanged for the core push path; the file-encryption
feature (§7a) requires an **app change, built after the server infrastructure**.

## 2. Roles and terminology

| Term | Meaning |
|------|---------|
| **Master** | The operator-run server that owns the FCM project and the account/billing/admin surface. It **also hosts accounts directly** and doorbells their devices itself, in addition to relaying for slaves. |
| **Slave** | A push server that has no FCM credentials of its own and relays through a master. |
| **Standalone** | A push server with its own FCM project (today's mode) — not federated. |
| **Host** | The server an account is registered on: the **master** (direct) or a **slave**. |
| **Account** | A billable customer (tenant) hosted on the master or a slave, identified by an email, that uploads files and owns devices. |
| **Device** | A paired MDRender app instance (FCM token + `push_key`). |
| **Admin** | A signed-in operator login (username/password or SSO). Not a billing account. |
| **OAuth client** | An enrolled CLI credential (`oauth_clients`), used by `mdrender-send` and associated with an Account. |
| **Server ID** | A unique UUID minted at first run that identifies this server to a master. |

## 3. Deployment modes

Determined at run time, overridable by config:

1. **If Google/FCM service registration is available** (a service-account file
   is present/valid), the server can act as a **master** (or standalone).
2. **If Google services are not available**, the server runs as a **slave**:
   no FCM credentials, must relay through a master.
3. A **default master URL is baked into the image**; the operator can override
   it (env/config and the admin UI).
4. A slave must, on first run, **complete local admin setup** and then
   **register with the master** before it can deliver pushes.

Config: `FCM_SERVER_KEY` present ⇒ master-capable; `FEDERATION_URL` set ⇒ slave
(default baked in, overridable). `ROLE=master|slave|standalone` forces a mode.
Detection is advisory: an explicit `ROLE` always wins.

**Startup detection (D1, resolved).** On startup the server checks the FCM
service-account **file is present**, then runs a **live probe** (mints an OAuth
token). If absent or the probe fails, the server still starts but marks FCM
**unavailable** — it does not send as a master, logs the failure, warns in the
admin UI, and **re-probes periodically** so a later-valid credential recovers
without a restart.

A master-capable server plays **both roles at once**: it hosts accounts directly
and relays doorbells for enrolled slaves.

## 4. Accounts, logins, and authentication

- **Replace the single `SERVER_PASSWORD` login with username + password.**
  There are no existing installs to migrate.
- **First run with no admin**: the login page offers a *set up admin* form
  (username + password). This is the only unauthenticated write path and only
  while zero admins exist; once an admin exists it is closed (further admins are
  created from the admin UI).
- Passwords hashed with the existing PBKDF2 helper; sessions use the existing
  signed server-side session store.
- The master supports **multiple admins** (multiple operators), with roles.
- Login rate-limiting stays (reuse `LoginGate`), per user+IP.

### 4a. Identity: local user DB and optional hosted IdP

Two supported modes per host, behind one **`IdentityProvider` interface**
(verify a token/credentials, map to a local user, mint an MDRender session):

1. **Local user DB (default, always available).** A local `users` table with
   username/email + password (PBKDF2), optional email verification. No external
   dependency — works fully offline.
2. **Hosted identity provider (optional).** Social login + passwordless
   (magic-link) via a low-cost provider. Because the project already uses
   Firebase, the operator can **bring their own Firebase account/project** and
   the server documents the setup; Auth0 / AWS Cognito are alternatives.

**Decision (D3/D11):** use **Firebase Authentication** for account login —
email + **magic link** (passwordless), social providers, and email
verification — since the project already owns a Firebase project for FCM.
**Firebase Auth handles account email delivery and magic links** (and
verification), so no separate SMTP is needed for accounts. A **local user DB**
remains the always-available default/fallback (offline, bring-your-own), and
Auth0 / Cognito remain alternatives behind the interface. **Deliverable:** an
**IdP setup guide** for the operator's own Firebase account (project, providers,
API keys, token verification).

- **Accounts** get social + passwordless when an IdP is configured, else local.
- **Admins/operators** stay on **local accounts or enterprise SSO with MFA** —
  no consumer social on privileged accounts.
- Account linking is by **verified email**; email verification is required.
- Email-domain bans (§9) and IP/CIDR/ASN bans (§14) apply after verification,
  before a session is issued.
- The Android app may adopt the same IdP later; that is out of scope here.

## 5. Server identity and federation registration

- On first run the server mints a **UUID `server_id`** and an **RSA keypair**
  (a federation keypair, distinct from the manifest-signing key), stored in the
  DB.
- A slave registers with the master via a **server-enrolment handshake**:
  - slave `POST /api/federation/enrol/start` → master returns an enrolment;
  - the slave submits its **hostname**, **server_id**, **public key**, and the
    **base URL** the master should call. **Enrolment is automatic (D2)** — no
    per-slave operator approval; bans/revocation still apply.
- **Mutual authentication:** the slave's admin configures the **expected master
  hostname**, and the slave verifies the master's hostname before enrolling
  (D5-review). The master verifies the slave by the signed callback (§5a).
- Thereafter, slave↔master API calls are **Bearer-authenticated *and signed***
  with the slave keypair (§5a); the master verifies the signature against the
  registered public key, so an intercepted token alone is not enough.

## 5a. Registration callback, signed messages, and liveness

### Registration callback (prove it exists, is alive and functional)
1. The slave submits its **hostname**, **server_id**, **public key**, and the
   **base URL** the master should call.
2. The master performs a **callback** to the slave's verify endpoint
   (`POST <slave_url>/api/federation/verify`) with a random challenge.
3. The slave **signs the challenge** with its private key and returns it; the
   master checks the signature against the submitted public key. This proves the
   slave controls the key, is reachable, and is functional — only then is it
   activated. A callback that cannot be reached or verified leaves the slave
   **pending**, not active.

### Signed messages (slave → master)
Every slave→master request carries `X-Federation-Server` (server_id),
`X-Federation-Timestamp`, `X-Federation-Nonce`, and `X-Federation-Signature` =
`sign(slave_priv, canonical(method, path, timestamp, nonce, sha256(body)))`.
The master verifies the signature against the registered public key and rejects
**stale timestamps** (outside a small window) and **replayed nonces**.

### Liveness (bidirectional)
- **Master → slave probe:** the master periodically calls the slave's
  `/api/federation/probe` with a challenge; the slave returns a **signed**
  challenge plus status. After N consecutive failures/timeouts the master marks
  the slave **down** (records `down_since`); the master is authoritative.
- **Slave → master heartbeat:** the slave sends a signed periodic heartbeat and
  may **ping "back online"** at any time. On **restart**, a registered slave
  **pings the master** so it is immediately re-marked up.
- **Return to online:** on a successful heartbeat/ping/probe the master clears
  `down`, refreshes `last_seen`, and flushes any queued messages.

### Delivery queue (master → slave)
Messages the master must deliver to a slave (config/plan changes, suspension or
admin notices, revocations) go to a per-slave **outbox** while the slave is
**down**, delivered (signed, retry/backoff) when it returns, with a retention
TTL. Slave→master sends (doorbells, device sync) are retried by the slave's
existing retry worker if the master is briefly unavailable.

All tunables in this section (probe interval/timeout, down threshold, heartbeat
cadence, nonce window, outbox TTL/backoff) are **server settings from
environment config** with sane defaults (D14).

## 6. Devices and accounts

- An account is **hosted on exactly one server** — the master directly, or a
  slave — and its devices, files, quotas, and plan belong to that host.
- Devices are paired to their host as today (`push_key`, FCM token, manifest
  signing key per server).
- **Device → account binding requires approval.** Pairing is initiated for a
  specific account (the account or an admin generates the pairing token/QR) and
  the **binding is approved** before the device is attached to the account. A
  device belongs to exactly one account.
- **Slave → master sync carries no PII (D-review #7).** On registering a device
  the slave pushes only the minimum needed to route a doorbell — opaque
  `account_id`, `device_id`, the FCM token, and the slave's `server_id`. **No
  email, name, or other PII reaches the master**; the master stores only the
  routing tuple `(server_id, account_id, device_id) → fcm_token` with lifecycle
  (upsert on register/rotate, delete on removal).
- Master-hosted accounts are served by the master's own stack; nothing is
  relayed.

## 7. Push flow

Two paths, depending on where the account is hosted.

**A. Account hosted on the master (direct, no relay)**
1. The account uploads files to the master (per-account pending storage, §10).
2. The master seals the trigger with the device's `push_key` and sends the FCM
   doorbell with its own project.
3. The app decrypts, fetches the signed manifest from the master, verifies, and
   downloads.

**B. Account hosted on a slave (relay)**
1. The account uploads files to the slave (per-account pending storage, §10).
2. The slave seals the trigger with the device's `push_key`.
3. The slave sends the sealed trigger to the master
   (`POST /api/federation/doorbell`), naming the device (opaque ids only).
4. The master resolves the device's FCM token, enforces that the requesting
   slave owns it (and that the slave is entitled and not banned), and sends the
   FCM doorbell via its project.
5. The app decrypts, fetches the signed manifest from the **slave**, verifies,
   and downloads.

Security invariants: for relayed pushes the master cannot read the doorbell (no
`push_key`) or forge a manifest (no server signing key); only the holder of a
device's `push_key` can produce an effective doorbell.

## 7a. File content encryption (server-blind) — requires an app change

**Goal:** an account's files are encrypted **before upload**, so no server —
slave or master — can read them, and the operator is not responsible for the
content. **The server infrastructure is built first; the Android app change
(the decryption key) follows.**

The key is shared **only** between the push client and the app. The server
relays **public keys and an opaque sealed blob** — it never sees the private
key or the content key.

### Key material
- The **push client originates the key**: a **content keypair** (`Cpriv`/`Cpub`);
  `Cpriv` is stored on the client (0600) and **never uploaded**.
- A random symmetric **Content Encryption Key (CEK)** encrypts files
  (AES-256-GCM per file, random nonce). The CEK is what client and app share.
- The **app** holds a **content decryption keypair** in its Keystore and
  registers its **content public key** with the server. This is a **new app
  key**: the app's existing pairing key is **sign-only** (`2863d0d`) and cannot
  decrypt.

### Key exchange at device registration
1. On pairing, the app generates its content decryption keypair and registers
   the **content public key** with the server.
2. The client fetches the app's content public key and **seals the CEK to it**
   (`sealed_cek = wrap(app_content_pub, CEK)`), uploading the sealed blob.
3. The server stores `sealed_cek` as an **opaque blob** — it cannot unwrap it.
   Only the app can open it.

### Push / fetch
1. The client encrypts each file with the CEK — **including a mangled/encrypted
   filename inside the payload** — and uploads the ciphertext + nonce
   (optionally signing with `Cpriv` so the app can verify origin).
2. The server stores **only an opaque binary blob** (no filename, no metadata) —
   just a random id, the bytes, and the nonce. The manifest carries opaque ids.
3. The app fetches `sealed_cek` once, unwraps it with its Keystore key, and
   decrypts downloads (recovering the real filename from the payload) with the
   CEK.

### Rotation
- **Changing the client content key** requires re-sealing to every app's
  content public key, so each app must **re-fetch** it (re-registration).
- The app may rotate its content keypair at re-pairing; the client re-seals.

### Properties and trade-offs
- Servers see only ciphertext and opaque wraps → **cannot read content**.
- Consequently servers **cannot scan, deduplicate, or moderate** content;
  abuse handling is policy/report-driven (§8a).
- The doorbell `push_key` (§7) is separate and unchanged.
- **Filenames/metadata are encrypted in the payload** and not stored (D13);
  storage is an opaque binary blob.

### 7b. Server-enforced encryption policy

Encryption is a **server setting, not a client choice**. The operator configures
`ENCRYPTION_MODE` (admin-set; default `off`): `off` · `optional` · `required`.

When it is **`required`**, the client and app are **forced to negotiate**
encryption before anything is delivered:

- The server **advertises the policy** (`GET /api/server/policy`, and in the
  pairing handshake) so both sides know encryption is mandatory.
- The app **must register its content public key** and the client **must upload
  the sealed CEK**; these are prerequisites for delivery.
- Uploads **must carry encryption metadata** (algorithm + nonce); plaintext
  uploads are rejected.
- A **doorbell is refused** for a device with no sealed CEK, forcing the
  client/app to finish negotiation first.

This is a deliberate operator control: it lets the admin guarantee that content
at rest on the push server (awaiting collection) is unreadable to them, and
therefore that they are not responsible for it. `off`/`optional` exist for
deployments that choose otherwise; the mode is never chosen by the client.

## 8. Master administration of slave servers

- **List** slaves (hostname, server_id, status, last seen, counts).
- **Revoke / delete / deactivate / ban** a slave (deactivate = suspend without
  deleting; revoke = invalidate credentials; delete = remove).
- **Ban** a slave by **IP, CIDR range, AS number, hostname, or domain**; bans
  block enrolment and all federation calls from the banned source. The same
  vocabulary applies globally (§14).
- A slave's admin UI lists **its own accounts and devices**.

## 8a. Policy and compliance

Paid multi-tenant service, so deliver **Policy and Terms & Conditions
documents**: acceptable use, content/abuse reporting (given server-blind
content, report-driven), privacy (data minimisation, no PII at the master),
billing/refund terms, and IP/CIDR/ASN/email ban policy. These ship with the
admin UI (linked) and are configurable per operator.

## 9. Account (tenant) signup

- Accounts register with an **email as the login** (local password, or IdP
  magic-link/social when configured), and include their **content public key**
  `Cpub` (§7a) so devices can seal content keys to them.
- Admin can **delete, block, or ban** an email.
- **Banned email domains:** a configurable blocklist of free/consumer and
  temporary/disposable domains is rejected at signup (operator-editable, with an
  allowlist override).
- **Banned network sources:** signup is refused for source IPs matching an IP
  ban, a CIDR range, or an AS number — enforced at signup and every request
  (§14).
- Account ↔ devices ↔ pending files are scoped per account.

## 10. Per-account storage and quotas

- Each account has a **pending collection**: uploaded files live in per-account
  storage on its **host** until collected. Tenants are isolated by **per-account
  directories** (D7). With client-side encryption on (§7a), the server stores
  **ciphertext + nonce as an opaque blob**, never plaintext and **never a real
  filename** (opaque ids only).
- An admin configures, per account (with global defaults):
  - **max pending bytes / file count** (quota), and
  - **max age of pending files** before automatic **purge**.
- A background sweeper enforces age and quota; the account UI shows usage.

## 11. Billing foundation (prepaid)

Build the foundation now; wire real providers later.

- **Two billing models:**
  - **Slave servers**: the master bills each slave a **flat monthly fee** for
    federation access. The master does **not** meter accounts hosted on a slave.
  - **Accounts hosted directly on the master**: metered on **storage** and
    **messages**, prepaid via a balance.
  - Accounts hosted on a slave are the slave's own billing concern (the slave
    may run the same metering for its accounts; it is not reported to the
    master).
- **Metering (D5):** example rates — **$5 per MB per month** once a file is
  **pending > 1 hour**, and **$0.50 per 1000 messages** (doorbells). Rates and
  thresholds are per-plan configuration.
- **Billing cycles are anniversary-based (D12):** each account's period runs
  from **its own start date**, not a shared calendar month; plan changes prorate
  to that account's dates.
- **Plans** are first-class and there can be **many**, one per account scope
  (`slave` = flat, `account` = metered), each with a price, currency, interval,
  included allowances, and overage rates.
- **Groups**: an account belongs to **exactly one** group; a plan can be applied
  to a group, and an account may carry its own plan. **Effective plan: account
  plan (override) → its group's plan.**
- A **default group** holds accounts with no explicit group (cannot be deleted);
  its plan is the baseline. No group precedence exists.
- **Entitlement layer**: every billable action checks an entitlement
  (`active`, `grace`, `suspended`) derived from a prepaid balance/period, so the
  provider is swappable.
- **Account states**: **free** (requires **admin approval**), **trial**
  (requires a **card on file**, once a gateway is integrated), **paid**; each
  plan has a **grace period**.
- **Provider abstraction**: a `PaymentProvider` interface with a **manual
  implementation first** — admins **add credits**, and that path always works;
  **Stripe / PayPal / crypto** later. No card data is stored before a gateway.
- Ledger of charges/credits; admin UI to view/adjust; self-serve top-up page
  (stubbed). No card data is ever stored by this codebase.

## 12. Data model (new/changed)

| Table | Purpose |
|-------|---------|
| `admins` | username, password hash (nullable when SSO), role, created, disabled/blocked/banned |
| `users` | local user DB: email/username, password hash, verified, status (for local auth) |
| `identities` | IdP link: provider, subject, email, `user_type` + `user_id`, email_verified, linked_at |
| `oauth_clients` | **renamed from `clients`** — the enrolled CLI credentials, associated with an account |
| `server_identity` | `server_id` (uuid), federation keypair, hostname, role |
| `federated_servers` | server_id, hostname, `base_url`, pubkey, secret hash, status (pending/active/down/deactivated/revoked/banned), plan, period, last_seen, `down_since`, `last_probe` |
| `federated_nonces` | seen signed-request nonces (replay window) |
| `federated_outbox` | queued master→slave messages: server_id, payload, created, attempts, next_retry_at, acked_at |
| `bans` | `kind = ip \| cidr \| asn \| hostname \| domain`; `scope = global \| server \| account`; reason, created_by, expires |
| `accounts` | email, password hash (nullable), `host` (master \| federated server_id), status (active/blocked/banned), balance — the tenant/customer |
| `account_devices` | routing only, **no PII**: `account_id` (opaque), `device_id`, `server_id`, `fcm_token`, name(optional local) |
| `account_files` | pending **opaque blobs**: account_id, size (ciphertext), created, `stored_path` (random id), `alg` + `nonce`, status — **no filename/metadata** |
| `account_keys` | account content public key `Cpub`: account_id, pubkey, created, retired_at |
| `device_content_keys` | per-device sealed CEK: device_id, `sealed_cek` (opaque), alg, created, retired_at |
| `devices` (existing) | gains `content_pubkey`, `account_id` |
| `account_quotas` | per-account overrides (max bytes/count, max age) |
| `email_domain_rules` | allow/deny list for signup domains |
| `billing_ledger` | charges/credits, reason, period, provider ref |
| `billing_plans` | id, name, `scope` (slave\|account), price, currency, interval, included storage/messages, overage rates, active |
| `billing_groups` | id, name, `plan_id` (nullable), `is_default` |
| `account_groups` | one row per billable account: `account_type` (slave/account), `account_id` (unique), `group_id` |
| `account_plans` | per-account plan override: `account_type`, `account_id`, `plan_id` |

Existing `devices`, `pushes`, `push_files`, `sessions`, `server_keys` remain;
`clients` is renamed `oauth_clients`.

## 13. API surface (proposed)

**Auth (all deployments):** `GET/POST /setup` (first-run admin), `POST /login`,
`POST /logout`, admin user CRUD. Local user DB plus optional IdP:
`GET /auth/providers` and `POST /auth/oidc` (verify via `IdentityProvider`, mint
a session); local login always available.

**Master ↔ slave (Bearer + signature):**
`POST /api/federation/enrol/start`, `POST /api/federation/enrol`,
`GET/POST /api/federation/verify`, `POST /api/federation/probe`,
`POST /api/federation/ping`, `POST /api/federation/heartbeat`,
`GET /api/federation/whoami`,
`PUT/DELETE /api/federation/accounts/{account_id}`,
`PUT/DELETE /api/federation/accounts/{account_id}/devices/{device_id}`,
`POST /api/federation/doorbell`.

**Account (tenant) API** (master and each slave): signup by email, device
pairing (with approval), upload to pending (`POST /api/account/upload`),
list/quota, collect/ack.

**Content key exchange (§7a):** device registration publishes the app's
**content public key**; `GET /api/account/devices/{id}/content-pubkey`; the
client `PUT`s the **sealed CEK**; the app fetches
`GET /api/device/{id}/sealed-cek`; uploads carry the `nonce`. `Cpriv` and the
CEK never reach the server.

**Master admin:** list/revoke/deactivate/delete/ban slaves; list/block/ban
accounts and email domains; network bans (ip, cidr, asn, hostname, domain);
quotas; billing/ledger; plan CRUD; group CRUD; assign an account to one group;
assign a plan to an account or group.

**Ban management:** `GET/POST/DELETE /api/admin/bans` with `kind`
(`ip | cidr | asn | hostname | domain`), `scope` (`global | server | account`),
optional expiry.

## 14. Security and trust model

- E2E doorbell + signed-manifest invariants (§7); the master stays
  content-blind.
- Server-to-server: Bearer + **per-slave keypair signature** on every
  slave→master message (§5a); stale timestamps and replayed nonces rejected.
  Slaves are activated only after the signing callback; the slave verifies the
  expected **master hostname** before enrolling (mutual auth).
- **No PII at the master:** slave→master sync carries opaque ids + FCM token
  only (§6).
- Bans enforced at the edge of **every** endpoint — signup, federation, account,
  admin — by exact IP, **CIDR**, **AS number**, hostname, or domain.
- Source IP from the proxy-aware forwarded header; **country** from
  `CF-IPCountry` (or the local DB for direct calls) and **ASN** from a bundled
  local **DB-IP Lite** IP→ASN DB — no external lookup (D9).
- First-run admin setup closes permanently after the first admin.
- Rate-limit login, signup, upload, and doorbell endpoints per identity/IP.
- No secrets in URLs; no card data stored.
- **Server-blind content (§7a):** only ciphertext + opaque key wraps reach a
  server; the operator holds no decryption key, so servers cannot scan or
  moderate content (hence §8a).

## 15. Phases (tracer bullets)

- **A — Auth foundation.** username/password admins, first-run `/setup`,
  multiple admins, `IdentityProvider` interface + local user DB, documented IdP
  setup (bring-your-own Firebase), rate limits.
- **B — Deployment modes.** FCM detection + probe, `ROLE` override, baked
  default master URL, server identity (uuid + keypair), config/UI surfacing.
- **C — Federation enrolment + liveness.** enrol handshake with signed callback;
  slave verifies master hostname; signed slave→master requests; master probe +
  down detection; slave heartbeat/restart ping; master→slave outbox; `whoami`.
- **D — Accounts + device sync.** account signup (email), device binding with
  approval, no-PII slave→master device sync, `(host, account_id, device_id) →
  token` registry.
- **E — Push delivery.** master direct path (seal + own FCM + local storage) and
  relay path (`/api/federation/doorbell`, entitlement + ban checks, master FCM);
  slave `_ring_doorbell` federation branch.
- **F — Account storage + quotas.** per-account pending storage, upload API,
  quota/age config, sweeper, usage UI.
- **G — Master admin.** slave list/revoke/deactivate/delete/ban (ip/cidr/asn/
  host/domain); account list/block/ban; email-domain rules; **DB-IP Lite**
  lookup integration.
- **H — Billing foundation.** plans, groups (one per account, default fallback,
  account override), metering ($5/MB/month pending >1h, $0.50/1000 messages),
  entitlements, ledger, manual provider, top-up stub.
- **I — Policy & T&C documents (§8a).**
- **J — Content encryption (server infra first; app change follows).** account
  content keypair, device content public key + sealed CEK exchange, ciphertext
  upload/fetch, key change ⇒ device re-registration, per-account toggle; then
  the Android app adds the decryption keypair.

Each phase lands independently with tests.

## 16. Decisions

- **D1 (resolved)** FCM availability: file presence + a **live probe** at
  startup; failure disables FCM (no master sending), warns in the UI, and
  re-probes periodically.
- **D2 (resolved)** Slave registration is **automatic** after the signing
  callback; no per-slave operator approval.
- **D3 (resolved direction)** Support **local user DB + optional hosted IdP**
  (social + magic-link); spike Auth0 / Cognito / Firebase; document
  bring-your-own-Firebase setup.
- **D4 (resolved)** Account states **free** (admin-approved), **trial** (card on
  file, once a gateway exists), **paid**; per-plan **grace period**; **manual
  billing** (admins add credits) first.
- **D5 (resolved)** Metering: e.g. **$5 per MB per month** once pending **> 1
  hour**, and **$0.50 per 1000 messages**; per-plan config.
- **D6 (resolved)** Vendor open, regularly-updated free/temporary-domain lists;
  refresh on a schedule; admin allow/deny override.
- **D7 (resolved)** Tenant isolation by **per-account directory scoping**;
  encryption optional (§7a).
- **D8 (resolved — no)** A slave never runs its own FCM (custom APK + namespace
  collision); always relay-only.
- **D9 (decided)** **DB-IP Lite** (`IP to Country` + `IP to ASN`), read locally
  via `maxminddb` (free, no account, monthly, CC BY 4.0); `CF-IPCountry` when
  proxied; IPv4+IPv6; attribution + monthly refresh.
- **D10 (resolved)** The Android app change for content encryption is
  **required** but **built after** the server infrastructure (Phase J).
- **D11 (resolved)** Account email, **magic-link delivery**, and verification
  are handled by **Firebase Auth** (D3); no separate SMTP for accounts. Signup
  abuse controls remain (rate limits, optional CAPTCHA, verified-email required).
- **D12 (resolved)** Billing cycles are **anniversary-based** per account; plan
  changes prorate to the account's own dates.
- **D13 (resolved)** **Filenames are mangled/encrypted into the payload** and the
  server stores **only an opaque binary string** (no name, no metadata).
  **Encryption is a server setting (`ENCRYPTION_MODE` = off/optional/required),
  enforced server-side (§7b)** — when required, clients and apps are forced to
  negotiate; the client never decides. The sealing algorithm (RSA-OAEP vs ECIES)
  is a small implementation detail.
- **D14 (resolved)** All liveness/queue tunables (probe interval, timeouts, down
  threshold, heartbeat cadence, nonce window, outbox TTL/backoff) are **server
  settings from environment config** with sane defaults.

## 17. Non-goals (this effort)

- Moving file bytes through the master **on behalf of slaves** (a slave-hosted
  account's files stay on that slave; a master-hosted account's files live on
  the master as its host).
- Rewriting the Android app **now** — the content-encryption app change is
  required but sequenced after the server infrastructure (D10 / Phase J).
- Real payment-provider integration (foundation only).
- Cross-master federation.
