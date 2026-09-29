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
- Access is **prepaid**: slave servers pay a flat fee; clients pay for storage
  and messages.

The end-user app is unchanged: it still receives an E2E doorbell and pulls from
its paired server.

## 2. Roles and terminology

| Term | Meaning |
|------|---------|
| **Master** | The operator-run server that owns the FCM project and the account/billing/admin surface. |
| **Slave** | A push server that has no FCM credentials of its own and relays through a master. |
| **Standalone** | A push server with its own FCM project (today's mode) — not federated. |
| **Client** | A billable tenant on a (slave) server, identified by an email account, that uploads files and owns devices. |
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

**Recommendation:** default to **Firebase Authentication** (it reuses the FCM
project already owned by the master) with **Auth0** as the drop-in alternative;
keep **local username/password** as the break-glass admin path and for
deployments with no external IdP.

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
  - credentials exchanged; the slave submits its **hostname**, **server_id**,
    and **public key**, and the master records them.
- Thereafter, slave↔master API calls are **Bearer-authenticated** with the
  issued secret; the master may additionally verify a **signed request**
  (RSA) so an intercepted token alone is not enough.
- Server-to-server pub/priv keys allow the master to verify that a registration
  or a client/device update genuinely came from that slave.

## 6. Devices and clients

- Devices are paired to a slave exactly as today (`push_key`, FCM token,
  manifest signing key per server).
- **Adding/registering a client or device on a slave pushes an update to the
  master** for each device's FCM registration (token, device id, owning client
  account), so the master can route/authorize doorbells without trusting a raw
  token supplied per request.
- The master stores `(server_id, client_id, device_id) → fcm_token` with
  lifecycle (upsert on register/rotate, delete on removal).

## 7. Push flow

1. A client uploads files to a slave (per-client pending storage, §10).
2. The slave seals the trigger with the device's `push_key` (unchanged).
3. The slave sends the sealed trigger to the master
   (`POST /api/federation/doorbell`), naming the device.
4. The master resolves the device's FCM token, enforces that the requesting
   slave owns it (and that the slave/client is entitled and not banned), and
   sends the FCM doorbell via its project.
5. The app decrypts, fetches the signed manifest from the slave, verifies, and
   downloads the files.

Security invariants (carried over): the master cannot read the doorbell (no
`push_key`) or forge a manifest (no server signing key); only the holder of a
device's `push_key` can produce an effective doorbell.

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
  email-link auth).
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
  per-client storage until the client is paired/collects them.
- An admin configures, per client (with global defaults):
  - **max pending bytes / file count** (quota), and
  - **max age of pending files** before automatic **purge**.
- A background sweeper enforces age and quota; the client UI shows usage.

## 11. Billing foundation (prepaid)

Build the foundation now; wire real providers later.

- **Two billing models:**
  - **Slave servers**: a **flat access fee** (prepaid period) for federation.
  - **Clients**: metered on **storage** (pending usage) and **messages**
    (doorbells/files delivered), prepaid via a balance.
- **Plans** are first-class and there can be **many**: admins create any number
  of plans per account scope (`slave` = flat access, `client` = metered), each
  with a price, currency, interval, included allowances, and overage rates.
- **Groups**: accounts can be grouped and a plan applied to a group. Every
  account also carries an optional individual plan assignment.
  **Effective-plan precedence: account plan → group plan → default-group plan.**
- A **default group** exists that **every account is automatically a member of**
  (it cannot be deleted); its plan is the baseline used when there is no more
  specific assignment. Membership is additive — an account can be in several
  groups; where groups disagree, the **highest-priority group's plan** wins
  (the default group is lowest priority).
- **Entitlement layer**: every billable action checks an entitlement
  (`active`, `grace`, `suspended`) derived from a prepaid balance/period, so the
  payment provider is swappable.
- **Provider abstraction**: a `PaymentProvider` interface with a no-op/manual
  implementation first; **Stripe / PayPal / crypto** added later. Manual credit
  adjustment by admins must always work.
- Ledger of charges/credits; admin UI to view/adjust; client/slave self-serve
  top-up page (stubbed initially).
- No card data is ever stored by this codebase.

## 12. Data model (new/changed)

| Table | Purpose |
|-------|---------|
| `admins` | username, password hash (nullable when SSO), role, created, disabled/blocked/banned |
| `identities` | provider (firebase/auth0/cognito/local), subject, email, `user_type` + `user_id`, email_verified, linked_at |
| `server_identity` | `server_id` (uuid), federation keypair, hostname, role |
| `federated_servers` | master's record of slaves: server_id, hostname, pubkey, secret hash, status, plan, period, last_seen |
| `bans` | `kind = ip \| cidr \| asn \| hostname \| domain`; `scope = global \| server \| client`; reason, created_by, expires |
| `clients` | email, password hash (nullable), status (active/blocked/banned), balance |
| `client_devices` | device_id, client_id, server_id, fcm_token, name |
| `client_files` | pending files: client_id, size, created, stored_path, status |
| `client_quotas` | per-client overrides (max bytes/count, max age) |
| `email_domain_rules` | allow/deny list for signup domains |
| `billing_ledger` | charges/credits, reason, period, provider ref |
| `billing_plans` | id, name, `scope` (slave\|client), price, currency, interval, included storage/messages, overage rates, active |
| `billing_groups` | id, name, `plan_id` (nullable), `priority`, `is_default` |
| `billing_group_members` | `group_id`, `account_type` (admin/slave/client), `account_id` |
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
`POST /api/federation/heartbeat`, `GET /api/federation/whoami`,
`PUT/DELETE /api/federation/clients/{client_id}`,
`PUT/DELETE /api/federation/clients/{client_id}/devices/{device_id}`,
`POST /api/federation/doorbell`.

**Client (tenant) API:** signup by email, upload to pending
(`POST /api/client/upload`), list/quota, collect/ack.

**Master admin:** list/revoke/deactivate/delete/ban slaves;
list/block/ban clients and email domains; **network bans (ip, cidr, asn,
hostname, domain)**; quotas; billing/ledger; **plan CRUD (many plans);
group CRUD + membership; assign a plan to an account or a group** (the default
group auto-includes every account).

**Ban management (shared vocabulary):** `GET/POST/DELETE /api/admin/bans` with a
`kind` of `ip | cidr | asn | hostname | domain`, a `scope`
(`global | server | client`), and optional expiry.

## 14. Security and trust model

- Reuse the E2E doorbell + signed-manifest invariants (§7); the master stays
  content-blind.
- Server-to-server: Bearer + request signature; per-slave keypair.
- Bans enforced at the edge of **every** endpoint — signup, federation, client,
  and admin — not just registration. Matching is by exact IP, **CIDR block
  range**, **AS number**, hostname, or domain.
- The client's source IP is read from the proxy-aware forwarded header (the
  master sits behind Cloudflare); the AS number is resolved from a local
  IP→ASN database rather than an external call.
- First-run admin setup closes permanently after the first admin exists.
- Rate-limit login, signup, upload, and doorbell endpoints per identity/IP.
- No secrets in URLs; no card data stored.

## 15. Phases (tracer bullets)

- **A — Auth foundation.** username/password admins, first-run `/setup`,
  multiple admins, roles, rate limits (migrate existing `SERVER_PASSWORD`), the
  `IdentityProvider` abstraction, and Firebase Auth for clients (email-link
  passwordless + social) with local accounts for admins.
- **B — Deployment modes.** Google/FCM detection, `ROLE` override, baked
  default master URL, server identity (uuid + keypair), config + UI surfacing.
- **C — Federation enrolment.** slave enrol handshake, master registry,
  heartbeat, `whoami`, signed requests.
- **D — Client + device sync.** client accounts (email), device update push to
  master, `(server_id, client_id, device_id) → token` registry.
- **E — Doorbell relay.** `POST /api/federation/doorbell`, entitlement + ban
  checks, FCM send on the master; slave `_ring_doorbell` federation branch.
- **F — Client storage + quotas.** per-client pending storage, upload API,
  quota/age config, sweeper, usage UI.
- **G — Master admin.** slave list/revoke/deactivate/delete/ban (ip/host/domain);
  client list/block/ban; email-domain rules.
- **H — Billing foundation.** multiple plans, groups + membership with
  account → group → default-group precedence, the default group, entitlements,
  ledger, provider interface (manual first), top-up page stub.

Each phase lands independently with tests; the app is untouched.

## 16. Open decisions

- **D1** FCM-availability detection: file presence vs a live probe; failure
  behaviour when the credential is present but invalid.
- **D2** Slave registration approval: auto (master URL known) vs
  operator-approved.
- **D3** Client auth: password vs magic-link email; email verification
  requirement.
- **D4** Slave billing period vs metered; trial length; grace window.
- **D5** Client metering units: per byte stored, per file, per doorbell —
  and how prepaid balance is debited.
- **D6** Free/temp email domain list source and update cadence.
- **D7** Multi-tenant data isolation model (per-client encryption or directory
  scoping).
- **D8** Whether a slave may also be a standalone (own FCM) for some tenants.
- **D12** Plan resolution details: group priority ordering when an account is in
  several groups, proration/effective-date on plan changes, whether the default
  group's plan is free, and whether clients and slaves share one plan space or
  separate ones.
- **D10** Identity provider: Firebase Auth (reuses the FCM project) vs Auth0 vs
  Cognito vs self-hosted; which social providers; and whether passwordless is
  email-link, passkey, or phone OTP.
- **D11** Email deliverability for passwordless/verification (Firebase vs
  SES/SMTP), plus signup abuse controls (rate limits, CAPTCHA, verified-email
  requirement).
- **D9** ASN/CIDR source and matching: a bundled IP→ASN database
  (e.g. MaxMind GeoLite2 ASN, self-updated) vs an external lookup; how to trust
  the forwarded client IP through Cloudflare; IPv4 vs IPv6 CIDR handling; and
  whether ASN bans also cover hosting/VPN ranges.

## 17. Non-goals (this effort)

- Moving file bytes through the master (files stay on the slave, fetched by the
  app).
- Rewriting the Android app.
- Real payment-provider integration (foundation only).
- Cross-master federation.
