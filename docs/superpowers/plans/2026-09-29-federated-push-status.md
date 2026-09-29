# Federated Push — implementation status

_Last updated: 2026-09-29. Branch `feature/federated-push`. Design authority:
`docs/superpowers/specs/2026-09-29-federated-push-server-design.md`._

This tracks progress implementing the phases A–J from the design. **All
decisions D1–D14 are resolved.** `server/.venv/bin/python -m pytest server/tests`
is green (**94 tests**).

## Done (committed)

| Phase | Commit | Scope |
|---|---|---|
| A | `6e64d2e` | username/password admins, first-run `/setup`, `/admins`, `IdentityProvider` + local user DB, per-user+IP rate limiting |
| B | `38f17fd` | role detection (`master/slave/standalone`, `ROLE`/`MASTER_URL`), live FCM probe, stable `server_identity`, `/status` |
| C | `3e298cf`, `183d18d` | federation registry, **signed requests** + nonce replay guard, bearer tokens, auto enrolment via signed callback, `heartbeat`/`ping` flushing the **outbox**, slave `/verify` + `/probe`, probe sweep + down marking, `/federation` admin page |
| D | accounts commit | `accounts` (email/status/host/balance), `account_devices` (no-PII), public `/signup`, signed `PUT/DELETE /api/federation/accounts/{id}/devices/{id}` |
| E | doorbell commit | master `POST /api/federation/doorbell` (ownership + FCM send, content-blind) |
| F/G/H/J | foundations commit | `storage.py` (quotas + age purge), `bans.py` (ip/cidr/asn/hostname/domain), `billing.py` (plans, one group per account, default group, ledger), `encryption.py` (account pubkey + sealed CEK) |
| I | policy docs | `docs/policies/{terms,privacy,acceptable-use}.md` |

## Remaining (the integration/UI layer)

- **C — outbound slave client**: enrol with the master, periodic heartbeat, send
  `ping` on restart, and a background thread running `sweep_liveness` on the
  master. (The master side and all responders exist and are tested.)
- **E — slave `_ring_doorbell` federation branch**: when no local FCM, seal the
  trigger and forward to the master via the outbound client.
- **D — account portal**: account login/session (distinct from admin), device
  binding with **approval**, and wiring the existing `/pair` + `/api/register-device`
  flow to an account.
- **F — upload API** (`POST /api/account/upload`), quota enforcement on upload,
  `purge_expired` in the retry worker, usage UI.
- **G — ban enforcement at endpoint edges**, admin ban UI (`/api/admin/bans`),
  and **DB-IP Lite** integration + `CF-IPCountry`.
- **H — entitlement enforcement**, admin plan/group UI, manual-credit UI, and
  the self-serve top-up stub.
- **J — key-exchange endpoints** (`GET .../content-pubkey`, `PUT .../sealed-cek`,
  `GET /api/device/{id}/sealed-cek`) and the **Android app** decryption key
  (explicitly sequenced after the server, D10).
- **Auth — Firebase Auth** wiring behind `IdentityProvider` (local is done).

## Notes

- The app is untouched; content encryption needs the app change (Phase J).
- Total tests: 94. No commits pushed; all local on `feature/federated-push`.
