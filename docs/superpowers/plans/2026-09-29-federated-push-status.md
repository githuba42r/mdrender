# Federated Push — implementation status

_Last updated: 2026-09-29. Branch `feature/federated-push`. Design authority:
`docs/superpowers/specs/2026-09-29-federated-push-server-design.md`._

All decisions **D1–D14 are resolved**. `server/.venv/bin/python -m pytest
server/tests` is green (**112 tests**).

## Complete

- **A — auth**: username/password admins, first-run `/setup`, `/admins`,
  `IdentityProvider` + local user DB, per-user+IP rate limiting.
- **B — deployment**: role detection (`master/slave/standalone`, `ROLE`,
  `MASTER_URL`), live FCM probe, stable `server_identity`, `/status`.
- **C — federation**: registry, **signed requests + nonce/staleness rejection**,
  bearer tokens, auto enrolment via signed callback, `heartbeat`/`ping` flushing
  the **outbox**, slave `/verify` + `/probe`, probe sweep + down marking,
  `/federation` admin page, **outbound slave client** + maintenance **worker**
  wired into `run.py`.
- **D — accounts**: `accounts` + no-PII `account_devices`, public `/signup`,
  signed slave→master device sync, **account session/login + `/account` portal**
  with admin/account separation.
- **E — push**: master `POST /api/federation/doorbell` (ownership-enforced,
  content-blind, FCM send).
- **F — storage**: `POST /api/account/upload` with quota enforcement, usage in
  the portal, `purge_expired` in the worker.
- **G — bans**: `bans.py` (ip/cidr/asn/hostname/domain), edge enforcement via
  `before_request`, `/bans` admin UI.
- **H — billing**: plans/groups/ledger data layer + `/billing` admin UI (manual
  credits).
- **I — policy**: terms, privacy, acceptable-use templates.
- **J — encryption (server side)**: account/device content public keys + opaque
  sealed CEK endpoints; `encryption.py`.
- **Auth surface**: `IDENTITY_PROVIDER`, `/auth/providers`, `/auth/oidc` stub.

## Also complete (second pass)

- **C tail**: outbound slave client + maintenance worker (enrol/heartbeat/restart
  ping; master liveness sweep).
- **D**: account session/login + `/account` portal; **account-bound pairing with
  approval**; device publishing (local registry on the master, signed sync to
  the master from a slave).
- **E**: slave `_ring_doorbell` federation branch (seals and forwards to the
  master when there is no local FCM).
- **F**: `POST /api/account/upload` + quota enforcement + purge in the worker.
- **G**: `/bans` UI, edge enforcement, `country` ban kind, DB-IP Lite ASN/country
  lookups + `CF-IPCountry`.
- **H**: `/billing` UI (plans, groups, manual credits) + entitlement
  primitives (`BILLING_ENFORCEMENT` flags upload 402 when unentitled).
- **J**: content key-exchange endpoints; **auth surface**: `/auth/providers`,
  `/auth/oidc` stub.

## Remaining

- **Hosted IdP implementation**: verify a Firebase/Auth0 ID token at `/auth/oidc`
  and mint a session (D3/D11). Local accounts are fully wired.
- **Metered charging**: automatically debit storage/messages on the plan's
  schedule (primitives exist; policy wiring remains).
- **Android app**: content decryption keypair (Phase J app change, D10).
- Optional polish: per-account credit/suspend UI, slave-side ban display.

## Notes

- No commits pushed; all local on `feature/federated-push`. The Android app is
  otherwise unchanged.
