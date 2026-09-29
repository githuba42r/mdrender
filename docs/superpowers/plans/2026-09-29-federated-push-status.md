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

## Remaining

- **Device → account binding (approval)** and wiring the existing `/pair` +
  `/api/register-device` flow to an account (`devices.account_id`).
- **Slave `_ring_doorbell` federation branch**: when a slave has no FCM, seal and
  forward to the master (needs device→account binding).
- **DB-IP Lite** ASN lookup + `CF-IPCountry` for ASN/country bans (D9).
- **Entitlement enforcement**: debit on upload/doorbell per the plan; grace and
  suspension.
- **Hosted IdP**: implement `/auth/oidc` verification (Firebase, D3/D11).
- **Android app**: content decryption keypair (Phase J app change, D10).
- Ban management on the *slave* side and per-account credit UI ergonomics.

## Notes

- No commits pushed; all local on `feature/federated-push`. The Android app is
  otherwise unchanged.
