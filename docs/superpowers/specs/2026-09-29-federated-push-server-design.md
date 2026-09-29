# Federated Push Server — design

**Status:** Draft (branch `feature/federated-push`). Supersedes the relay-only
plan in [`docs/superpowers/plans/2026-09-29-push-federation.md`](../plans/2026-09-29-push-federation.md)
by widening it from a dumb relay into a multi-tenant, billable service with
master/slave deployment modes and email client accounts.

**Branch:** `feature/federated-push`.

---

## 1. Purpose

Turn the single self-hosted push server into a **federated service**:

- A **master** owns the app's Firebase project and the public service surface.
- **Slave** push servers (self-hosted or managed) register with the master and
  relay doorbells through it.
- **Clients** (people/businesses) sign up with an email account, upload files
  into per-client storage, and have them delivered to their registered devices.
- The **master is also a direct client push server**: clients can register
  against the master itself and push files through it, with no slave involved.
- Access is **prepaid**: slave servers pay a flat fee; clients pay for storage
  and messages.

The end-user app is unchanged: it still receives an E2E doorbell and pulls from
its paired server.

## 2. Roles and terminology

| Term | Meaning |
|------|---------|
| **Master** | The operator-run server that owns the FCM project and the account/billing/admin surface. It **also hosts clients directly** and doorbells their devices itself, in addition to relaying for slaves. |
| **Slave** | A push server that has no FCM credentials of its own and relays through a master. |
| **Standalone** | A push server with its own FCM project (today's mode) — not federated. |
| **Host** | The server a client is registered on: the **master** (direct) or a **slave**. |
| **Client** | A billable tenant hosted on the master or a slave, identified by an email account, that uploads files and owns devices. |
| **Device** | A paired MDRender app instance (FCM token + `push_key`). |
| **Admin** | A signed-in operator account. First admin is created at first run; the master supports multiple admins. |
| **Server ID** | A unique UUID minted at first run that identifies this server to a master. |

## 3. Deployment modes

Determined at run time, overridable by config:

1. **If Google/FCM service registration is available** on this deployment
   (a service-account file is present/valid), the server can act as a **master**
   (or a standalone server).
2. **If Google services are not available**, the server runs as a **slave**:
   it has no FCM credentials and must relay through a master.
3. A **default master URL is baked into the image**; the operator can override
   it (env/config, and via the admin UI).
4. A slave must, on first run, **complete local admin setup** and then
   **register with the master** before it can deliver pushes.

Config: `FCM_SERVER_KEY` present ⇒ master-capable; `FEDERATION_URL` set ⇒ slave
(default baked in, overridable). `ROLE=master|slave|standalone` forces a mode.
Detection is advisory: an explicit `ROLE` always wins.

**Startup detection (D1, resolved).** On startup the server first checks the
FCM service-account **file is present**, then runs a **live probe** (mints an
OAuth token from the service account and authenticates against Google). If the
file is absent, or the probe fails, the server still starts but marks FCM
**unavailable**: it does not act as a master-capable sender, logs the failure,
surfaces it in the admin UI, and **re-probes periodically** so a later-valid
credential recovers without a restart.

A master-capable server plays **both roles at once**: it hosts clients directly
(registration, uploads, devices, own FCM doorbells) **and** relays doorbells for
enrolled slaves.

## 4. Accounts and authentication

- **Replace the single `SERVER_PASSWORD` login with username + password.**
- **First run with no admin account**: the login page offers a *set up admin
  account* form (choose username + password). This is the only unauthenticated
  write path, and only while zero admins exist; once an admin exists, it is
  closed (a second admin can only be invited/created from the admin UI).
- Passwords hashed with the existing PBKDF2 helper (`hash_secret`/`verify_secret`);
  sessions use the existing signed server-side session store.
- **The master supports multiple accounts** (multiple people registering
  devices/clients), with roles (`admin`, `operator`).
- Login rate-limiting stays (reuse `LoginGate`), keyed per user+IP.

### 4a. Identity provider (Auth0 / AWS Cognito / Firebase / local)

Social login and passwordless should come from a managed identity provider, not
hand-rolled. Put every provider behind one **`IdentityProvider` interface**
(OIDC/JWT: verify the ID/access token, map to a local `user`, then mint our own
session) so the concrete choice is swappable and a local fallback always works.

| Option | Social | Passwordless | Trade-offs |
|--------|--------|--------------|-----------|
| **Firebase Authentication** (recommended default) | Google, Apple, GitHub, etc. | **Email link** (magic link), phone OTP, passkeys | Reuses the **existing Firebase project** (already used for FCM) — no new vendor. Web SDK on the frontend + Admin SDK on Flask to verify tokens. Email sending is handled by Firebase. |
| **Auth0** | Broad social set | Email magic link, SMS OTP, passkeys/WebAuthn | Best passwordless/MFA DX and rules/actions; MAU-based pricing; another vendor + lock-in. |
| **AWS Cognito** | Google/Facebook/Apple/SAML/OIDC | **No built-in email magic link** — needs `CUSTOM_AUTH` Lambda + SES (or phone OTP) | AWS-native and cheap at scale; passwordless is DIY and the hosted UI is less polished. |
| **Self-hosted local** | — | magic link (own SMTP) | No per-MAU cost, full control; must own email deliverability, MFA, passkey/WebAuthn, and account security. |

**Decision (D3):** use a **low-cost hosted provider** that supports **social
login and magic-link** email. Run a short spike comparing **Auth0**, **AWS
Cognito**, and **Firebase Auth** (which reuses the FCM project) on price,
passwordless fit, and email deliverability, behind the `IdentityProvider`
interface. **Local username/password** stays as the break-glass admin path and
for deployments with no external IdP. (Supersedes the earlier
Firebase-as-default recommendation.)

- **Clients** (tenants) get social + passwordless (lower friction, self-serve).
- **Admins/operators** stay on **local accounts or enterprise SSO with MFA** —
  do not expose privileged accounts to consumer social login.
- Account linking is by **verified email**; strict email verification is
  required so an IdP email cannot impersonate an existing account.
- Email-domain bans (§9) and IP/CIDR/ASN bans (§14) are applied after token
  verification, before a session is issued.
- The Android app may adopt the same IdP later (e.g. Firebase Auth), but the
  app is out of scope for this effort.

## 5. Server identity and federation registration

- On first run the server mints a **UUID `server_id`** and an **RSA keypair**
  (a federation keypair, distinct from the manifest-signing key), stored in the
  DB.
- A slave registers with the master via a **server-enrolment handshake**
  (short-code/browser approve, mirroring the CLI-enrolment pattern):
  - slave `POST /api/federation/enrol/start` → master returns an enrolment;
  - operator approves on the master (or types the code);
  -   credentials exchanged; the slave submits its **hostname**, **server_id**,
    and **public key**, and the master records them. **Enrolment is automatic
    (D2)** — no per-slave operator approval; bans/revocation still apply.
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
Every slave→master request carries:
`X-Federation-Server` (server_id), `X-Federation-Timestamp`,
`X-Federation-Nonce`, and `X-Federation-Signature` =
`sign(slave_priv, canonical(method, path, timestamp, nonce, sha256(body)))`.
The master verifies the signature against the registered public key and rejects
**stale timestamps** (outside a small window) and **replayed nonces**.

### Liveness (bidirectional)
- **Master → slave probe:** the master periodically calls the slave's
  `/api/federation/probe` with a challenge; the slave returns a **signed**
  challenge plus status. After N consecutive failures/timeouts the master marks
  the slave **down** (`status=down`, records `down_since`); the master is
  authoritative for registry status.
- **Slave → master heartbeat:** the slave sends a signed periodic heartbeat, and
  may **ping "back online"** at any time. On **restart**, a registered slave
  **pings the master** so it is immediately re-marked up.
- **Return to online:** on a successful heartbeat/ping/probe the master clears
  `down`, refreshes `last_seen`, and flushes any queued messages.

### Delivery queue (master → slave)
Messages the master must deliver to a slave (config/plan changes, suspension or
admin notices, revocations) go to a per-slave **outbox** while the slave is
**down**, and are delivered (with retry/backoff, signed) when it returns. The
queue has a retention/TTL. In the other direction, slave→master sends
(doorbells, device updates) are retried by the slave's existing retry worker if
the master is briefly unavailable.
- Server-to-server pub/priv keys allow the master to verify that a registration
  or a client/device update genuinely came from that slave.

## 6. Devices and clients

- A client is **hosted on exactly one server** — the master directly, or a
  slave — and its devices, files, quotas, and plan belong to that host.
- Devices are paired to their host exactly as today (`push_key`, FCM token,
  manifest signing key per server). Master-hosted clients are served by the
  master's own push stack; nothing is relayed.
- **Adding/registering a client or device on a slave pushes an update to the
  master** for each device's FCM registration (token, device id, owning client
  account), so the master can route/authorize doorbells without trusting a raw
  token supplied per request.
- The master stores `(server_id, client_id, device_id) → fcm_token` with
  lifecycle (upsert on register/rotate, delete on removal).

## 7. Push flow

Two paths, depending on where the client is hosted.

**A. Client hosted on the master (direct, no relay)**
1. The client uploads files to the master (per-client pending storage, §10).
2. The master seals the trigger with the device's `push_key` and sends the FCM
   doorbell with its own project.
3. The app decrypts, fetches the signed manifest from the master, verifies, and
   downloads.

**B. Client hosted on a slave (relay)**
1. The client uploads files to the slave (per-client pending storage, §10).
2. The slave seals the trigger with the device's `push_key`.
3. The slave sends the sealed trigger to the master
   (`POST /api/federation/doorbell`), naming the device.
4. The master resolves the device's FCM token, enforces that the requesting
   slave owns it (and that the slave/client is entitled and not banned), and
   sends the FCM doorbell via its project.
5. The app decrypts, fetches the signed manifest from the **slave**, verifies,
   and downloads.

Security invariants (carried over): for relayed pushes the master cannot read
the doorbell (no `push_key`) or forge a manifest (no server signing key); only
the holder of a device's `push_key` can produce an effective doorbell.

## 7a. File content encryption (server-blind)

**Goal:** a client's files are encrypted **before upload**, so no server — slave
or master — can read them, and the operator is not responsible for the content.

The key is shared **only** between the push client and the app. The server
relays **public keys and an opaque sealed blob** — it never sees the private
key or the content key.

### Key material
- The **push client originates the key**. It creates a **content keypair**
  (`Cpriv`/`Cpub`); `Cpriv` is stored on the client (0600) and is **never
  uploaded**.
- A random symmetric **Content Encryption Key (CEK)** encrypts files
  (AES-256-GCM per file, random nonce). The CEK is what client and app share.
- The **app** holds a **content decryption keypair** in its Keystore and
  registers its **content public key** with the server. This is a *new* key:
  the app's existing pairing key is **sign-only** (`2863d0d`) and cannot
  decrypt, so the app must add a decryption-capable content key.

### Key exchange at device registration
1. On pairing, the app generates its content decryption keypair and registers
   the **content public key** with the server.
2. The client fetches the app's content public key and **seals the CEK to it**
   (`sealed_cek = wrap(app_content_pub, CEK)`), uploading the sealed blob.
3. The server stores `sealed_cek` as an **opaque blob** — it cannot unwrap it,
   having neither the app's private key nor the CEK. Only the app can open it.

### Push / fetch
1. The client encrypts each file with the CEK and uploads ciphertext + nonce
   (optionally signing with `Cpriv` so the app can verify the client origin).
2. The server stores **ciphertext only**.
3. The app fetches `sealed_cek` once, unwraps it with its Keystore key, and
   decrypts downloads with the CEK.

### Rotation
- **Changing the client content key** (a new CEK) requires re-sealing to every
  app's content public key, so each app must **re-fetch** it — the
  re-registration the requirements note.
- The app may also rotate its content keypair at re-pairing, after which the
  client re-seals.

### Properties and trade-offs
- The server (master or slave) sees only ciphertext and opaque wraps, so it
  **cannot read file content** — the "we are not responsible for the content"
  posture.
- Consequently the server **cannot scan, deduplicate, or moderate** content;
  abuse handling must be policy/report-driven.
- The doorbell `push_key` (§7) is **separate** and unchanged: it protects the
  trigger, not file content.
- Encryption is per client and may be opted out (plaintext); the server records
  the mode per client/device.
- Whether **file names/metadata** are also encrypted is open (D13).

## 8. Master administration of slave servers

Admin UI + API to:

- **List** slave servers (hostname, server_id, owner, status, last seen, counts).
- **Revoke / delete / deactivate** a slave (deactivate = suspend without
  deleting; revoke = invalidate credentials; delete = remove).
- **Ban** a slave by **IP address, IP block range (CIDR), AS number, hostname,
  or domain name**; bans block enrolment and all federation calls from the
  banned source. The same ban vocabulary applies globally (§14).
- A slave's admin UI lists **its own clients and devices**.

## 9. Client (tenant) accounts

- Clients register with an **email address as the login** (password or
  email-link auth), and include their **content public key** `Cpub` (§7a) so
  devices can wrap content keys to them.
- Master/admin can **delete, block, or ban** an email address.
- **Banned email domains**: a configurable blocklist of free/consumer and
  temporary/disposable email domains is rejected at signup (operator-editable,
  with an allowlist override for exceptions).
- **Banned network sources**: signup is refused for source IPs matching an IP
  ban, an IP block range (CIDR), or an AS number — enforced at signup and on
  every subsequent request, not just registration (§14).
- Client ↔ devices ↔ pending files are scoped per client.

## 10. Per-client storage and quotas

- Each client has a **pending collection**: their uploaded files live in
  per-client storage on their **host** (the master or a slave) until the client
  collects them. Tenants are isolated by **per-client directories** (D7). When
  client-side encryption is on (§7a), the server stores **ciphertext + nonce**,
  never plaintext; only server-hosted clients store files at all.
- An admin configures, per client (with global defaults):
  - **max pending bytes / file count** (quota), and
  - **max age of pending files** before automatic **purge**.
- A background sweeper enforces age and quota; the client UI shows usage.

## 11. Billing foundation (prepaid)

Build the foundation now; wire real providers later.

- **Two billing models:**
  - **Slave servers**: a **flat access fee** (prepaid period) for federation.
  - **Clients**: metered on **storage** (pending usage) and **messages**
    (doorbells/files delivered), prepaid via a balance — the same whether the
    client is **hosted on the master directly** or on a slave. Only slaves pay
    the flat access fee.
- **Plans** are first-class and there can be **many**: admins create any number
  of plans per account scope (`slave` = flat access, `client` = metered), each
  with a price, currency, interval, included allowances, and overage rates.
- **Groups**: an account belongs to **exactly one** group. A plan can be
  applied to a group, and every account may also carry an individual plan.
  **Effective plan: account plan (override) → the account's group plan.**
- A **default group** exists for accounts with no explicit group (it cannot be
  deleted); its plan is the baseline. Because an account is in only one group,
  there is **no group precedence** to resolve.
- **Entitlement layer**: every billable action checks an entitlement
  (`active`, `grace`, `suspended`) derived from a prepaid balance/period, so the
  payment provider is swappable.
- **Account states**: **free** (requires **admin approval**), **trial**
  (requires a **card on file**, once a payment provider is integrated), and
  **paid**; each plan carries a **grace period** before suspension.
- **Provider abstraction**: a `PaymentProvider` interface with a **manual
  implementation first** — admins **add credits** to an account, and that path
  always works; **Stripe / PayPal / crypto** are added later behind the same
  interface. No card data is stored before a real gateway exists.
- **Metering (D5)**: clients are charged **$X per KB stored** once files are
  retained beyond the plan's threshold **Y**, and **$Z per message**
  (doorbell/file) sent; rates and thresholds are per-plan configuration.
- Ledger of charges/credits; admin UI to view/adjust; client/slave self-serve
  top-up page (stubbed initially).
- No card data is ever stored by this codebase.

## 12. Data model (new/changed)

| Table | Purpose |
|-------|---------|
| `admins` | username, password hash (nullable when SSO), role, created, disabled/blocked/banned |
| `identities` | provider (firebase/auth0/cognito/local), subject, email, `user_type` + `user_id`, email_verified, linked_at |
| `server_identity` | `server_id` (uuid), federation keypair, hostname, role |
| `federated_servers` | server_id, hostname, `base_url`, pubkey, secret hash, status (pending/active/down/deactivated/banned), plan, period, last_seen, `down_since`, `last_probe` |
| `federated_nonces` | seen signed-request nonces (replay window) |
| `federated_outbox` | queued master→slave messages: server_id, payload, created, attempts, next_retry_at, acked_at |
| `bans` | `kind = ip \| cidr \| asn \| hostname \| domain`; `scope = global \| server \| client`; reason, created_by, expires |
| `clients` | email, password hash (nullable), `host` (master \| federated server_id), status (active/blocked/banned), balance |
| `client_devices` | device_id, client_id, server_id, fcm_token, name |
| `client_files` | pending files: client_id, size, created, stored_path, `encryption` (alg, nonce), status |
| `client_keys` | client content public key `Cpub`: client_id, pubkey, created, retired_at |
| `device_content_keys` | per-device sealed CEK: device_id, `sealed_cek` (opaque, client-sealed to the app), alg, created, retired_at |
| `devices` (existing) | gains `content_pubkey` — the app's content decryption public key |
| `client_quotas` | per-client overrides (max bytes/count, max age) |
| `email_domain_rules` | allow/deny list for signup domains |
| `billing_ledger` | charges/credits, reason, period, provider ref |
| `billing_plans` | id, name, `scope` (slave\|client), price, currency, interval, included storage/messages, overage rates, active |
| `billing_groups` | id, name, `plan_id` (nullable), `is_default` |
| `account_groups` | one row per account: `account_type` (admin/slave/client), `account_id` (unique), `group_id` (defaults to the default group) |
| `account_plans` | per-account plan override: `account_type`, `account_id`, `plan_id` |

Existing `devices`, `pushes`, `push_files`, `sessions`, `oauth clients`
remain; the client/tenant layer wraps them.

## 13. API surface (proposed)

**Auth (all deployments):** `GET/POST /setup` (first-run admin), `POST /login`
(username+password), `POST /logout`, admin user CRUD. External IdP:
`GET /auth/providers` (enabled social/passwordless options) and
`POST /auth/oidc` (verify the provider token via the `IdentityProvider`, then
mint an MDRender session); local login always remains available.

**Master ↔ slave (Bearer + optional signature):**
`POST /api/federation/enrol/start`, `POST /api/federation/enrol`,
`GET/POST /api/federation/verify` (slave signs the master's callback challenge),
`POST /api/federation/probe` (slave answers a signed liveness challenge),
`POST /api/federation/ping` (slave declares "back online", incl. on restart),
`POST /api/federation/heartbeat` (periodic, also flushes the master's outbox),
`GET /api/federation/whoami`,
`PUT/DELETE /api/federation/clients/{client_id}`,
`PUT/DELETE /api/federation/clients/{client_id}/devices/{device_id}`,
`POST /api/federation/doorbell`.

**Client (tenant) API** (exposed by **both** the master directly and each
slave): signup by email, device pairing, upload to pending
(`POST /api/client/upload`), list/quota, collect/ack.

**Content key exchange (§7a):** device registration publishes the app's
**content public key**; `GET /api/client/devices/{id}/content-pubkey` returns it
to the client, which `PUT`s the **sealed CEK**; the app fetches
`GET /api/device/{id}/sealed-cek`; uploads carry the `nonce` alongside
ciphertext. `Cpriv` and the CEK never reach the server.

**Master admin:** list/revoke/deactivate/delete/ban slaves;
list/block/ban clients and email domains; **network bans (ip, cidr, asn,
hostname, domain)**; quotas; billing/ledger; **plan CRUD (many plans);
group CRUD; assign an account to exactly one group (default group when unset);
assign a plan to an account or a group.**

**Ban management (shared vocabulary):** `GET/POST/DELETE /api/admin/bans` with a
`kind` of `ip | cidr | asn | hostname | domain`, a `scope`
(`global | server | client`), and optional expiry.

## 14. Security and trust model

- Reuse the E2E doorbell + signed-manifest invariants (§7); the master stays
  content-blind.
- Server-to-server: Bearer + **per-slave keypair signature** on every
  slave→master message (§5a), verified against the registered public key;
  stale timestamps and replayed nonces rejected. Slaves are activated only after
  the master's callback challenge is signed back correctly.
- Bans enforced at the edge of **every** endpoint — signup, federation, client,
  and admin — not just registration. Matching is by exact IP, **CIDR block
  range**, **AS number**, hostname, or domain.
- The client's source IP is read from the proxy-aware forwarded header (the
  master sits behind Cloudflare); **country** comes from the `CF-IPCountry`
  header (or the local DB for direct calls) and the **AS number** from a bundled
  local IP→ASN database (DB-IP Lite) — no external lookup (D9).
- First-run admin setup closes permanently after the first admin exists.
- Rate-limit login, signup, upload, and doorbell endpoints per identity/IP.
- No secrets in URLs; no card data stored.
- **Server-blind content (§7a):** file bytes are encrypted on the client and
  only ciphertext + opaque key wraps reach a server; the operator holds no
  decryption key. This also means servers cannot scan or moderate content.

## 15. Phases (tracer bullets)

- **A — Auth foundation.** username/password admins, first-run `/setup`,
  multiple admins, roles, rate limits (migrate existing `SERVER_PASSWORD`), the
  `IdentityProvider` abstraction, and Firebase Auth for clients (email-link
  passwordless + social) with local accounts for admins.
- **B — Deployment modes.** Google/FCM detection, `ROLE` override, baked
  default master URL, server identity (uuid + keypair), config + UI surfacing.
- **C — Federation enrolment + liveness.** slave enrol handshake with the
  master's **signed callback verification**; signed slave→master requests
  (nonce/timestamp replay guard); master **probe** loop with down detection;
  slave heartbeat + restart **ping**; master→slave **outbox** queueing and
  flush-on-return; `whoami`.
- **D — Client + device sync.** client accounts (email) **hosted on the master
  directly or on a slave**; device update push to master for slave-hosted
  clients; `(host, client_id, device_id) → token` registry.
- **E — Push delivery.** master **direct** path (seal + own FCM + local
  storage) and relay path (`POST /api/federation/doorbell`, entitlement + ban
  checks, master FCM); slave `_ring_doorbell` federation branch.
- **F — Client storage + quotas.** per-client pending storage, upload API,
  quota/age config, sweeper, usage UI.
- **F2 — Client-side content encryption.** client content keypair, device CEK
  wrap at registration, server-blind ciphertext upload/fetch, key change ⇒
  device re-registration, per-client encryption toggle.
- **G — Master admin.** slave list/revoke/deactivate/delete/ban (ip/host/domain);
  client list/block/ban; email-domain rules.
- **H — Billing foundation.** multiple plans, groups (one per account, default
  group fallback, account plan override), entitlements, ledger, provider
  interface (manual first), top-up page stub.

Each phase lands independently with tests; the app is untouched.

## 16. Open decisions

- **D1 (resolved)** FCM availability: require the service-account file's
  **presence**, then run a **live probe** at startup; a failed/absent probe
  disables FCM (no master sending), warns in the admin UI, and re-probes
  periodically rather than blocking startup.
- **D2 (resolved)** Slave registration is **automatic**: a slave that completes
  the enrolment handshake is accepted without per-slave operator approval.
  Bans/revocation (§8) still apply, and the master may require a signed request.
- **D3 (resolved direction)** Use a **low-cost hosted identity provider** for
  clients supporting **social login and magic-link** email. Evaluate **Auth0**,
  **AWS Cognito**, and other low-cost options (e.g. Firebase Auth) on price,
  passwordless fit, and email deliverability; the concrete choice is a short
  spike, behind the `IdentityProvider` interface. Admins stay on local/SSO.
- **D4 (resolved)** Account types: **free** (requires **admin approval**),
  **trial** (requires a **card on file** — available once a payment provider is
  integrated), and **paid**; each plan carries a **grace period**. **No payment
  gateway initially**: a **manual billing provider** where admins **add credits**
  to an account. The provider interface allows Stripe/PayPal/crypto later.
- **D5 (resolved)** Client pricing is metered: **$X per KB stored** once files
  are retained beyond **Y** (per-plan threshold), and **$Z per message**
  (doorbell/file) sent. Exact values are per-plan configuration.
- **D6 (resolved — suggestion)** Source the free/temporary-domain list from
  open, regularly-updated community lists (e.g. the `disposable-email-domains`
  project and free-provider lists), **vendored and refreshed on a schedule**,
  with an admin allow/deny override.
- **D7 (resolved)** Tenant isolation is by **directory scoping** (per-client
  directories). Only server-hosted ("local") clients store files on the server;
  if a client opts into content encryption, those files are stored **encrypted**
  and decrypted on the device (§7a).
- **D8 (resolved — no)** A slave will **not** also run its own FCM: it would
  require a custom APK and the Android namespace would collide with the Play
  Store app. A slave is always relay-only.
- **D12 (open)** Plan resolution details: proration/effective-date on plan
  changes, whether the default group's plan is free, and whether clients and
  slaves share one plan space or separate ones. (Group precedence is moot — an
  account is in exactly one group.)
- **D10 (folded into D3)** Provider selection is a short spike across Auth0 /
  Cognito / Firebase Auth (and other low-cost options) covering social set,
  magic-link/passkey support, and price.
- **D11 (open)** Email deliverability for magic-link/verification follows the
  chosen provider (D3); decide the fallback SMTP/SES path and signup abuse
  controls (rate limits, CAPTCHA, verified-email requirement).
- **D13 (open)** Content-encryption details (§7a): the client-originated CEK is
  **sealed to the app's content public key** (algorithm: RSA-OAEP vs X25519 +
  HKDF / ECIES); the app needs a **new decryption-capable Keystore key** (its
  pairing key is sign-only); decide whether file names/metadata are also
  encrypted, CEK scope (per-device vs per-client), and streaming AEAD for large
  files.
- **D14 (open)** Liveness/queue tuning (§5a): probe interval and timeout, number
  of failures before a slave is marked down, heartbeat cadence, signed-request
  time window and nonce retention, and the outbox retention/backoff policy.
- **D9 (resolved — recommendation)** Use **local lookups**, no per-request
  external call:
  - **Country:** prefer Cloudflare's **`CF-IPCountry`** header (the master sits
    behind Cloudflare — free, zero lookup); fall back to a local IP→Country DB
    for direct/non-proxied and internal calls.
  - **ASN:** bundle a local **IP→ASN MMDB** and read it with the pure-Python
    `maxminddb` library. Recommended source: **DB-IP Lite (IP to ASN / IP to
    Country)** — free, **no account**, monthly updates, **CC BY 4.0
    (attribution only)**. Alternatives: **IPinfo Lite** (daily updates, country
    + ASN, CC BY-SA 4.0) and **MaxMind GeoLite2** (free account, CC BY-SA 4.0).
    Avoid per-lookup APIs (rate limits, latency, privacy).
  - **Matching:** IP and **CIDR** bans need no lookup (parse + match); **ASN**
    and **country** bans use the DB/header. Handle **IPv4 and IPv6** CIDRs.
  - Honour attribution and each DB's license; refresh on a schedule
    (`geoipupdate` for GeoLite2, monthly download for DB-IP).

## 17. Non-goals (this effort)

- Moving file bytes through the master **on behalf of slaves** (a slave-hosted
  client's files stay on that slave). A **master-hosted** client's files do
  live on the master, because the master is that client's host.
- Rewriting the Android app.
- Real payment-provider integration (foundation only).
- Cross-master federation.
