# Privacy Policy

_Last updated: 2026-09-29. Template; complete with entity, jurisdiction and
contact before launch._

The MDRender Cloud Push service is built to minimise data, and file content is
end-to-end encrypted so the operator cannot read it.

## What is collected

- **Admins**: username, password hash, optional email, role, timestamps.
- **Accounts**: email, password hash (or identity-provider subject), host,
  status, prepaid balance.
- **Devices**: a device secret, FCM token, public keys, and a presence
  timestamp.
- **Files**: stored as **ciphertext with an opaque id** and a nonce. **No
  filename or content metadata is stored** when encryption is on.
- **Network data**: the source IP of requests, used for security, rate limiting,
  and bans; **country** (from `CF-IPCountry`) and **AS number** (from a bundled
  DB-IP Lite database) for ban enforcement.

## The master holds no PII for slave-hosted accounts

When an account is hosted on a slave, the slave sends the master only the
**routing tuple** — opaque account/device ids and the FCM token — needed to ring
the device. No email, name, or other personal data reaches the master.

## What is never collected

- File contents or filenames (encrypted end-to-end; the operator holds no key).
- Card data — no payment gateway is integrated initially; when one is, card
  data is handled entirely by the provider and never stored here.

## Retention

- Pending files are purged after the account's configured maximum age and are
  subject to its quota.
- Bans and the billing ledger are retained for enforcement and accounting.
- Sessions expire; expired rows are pruned.

## Third parties

- **Firebase Cloud Messaging** (Google) delivers doorbell notifications and
  Authentication handles account email/magic-links.
- **DB-IP Lite** supplies the ASN/country database (bundled; attribution in the
  about page).
- No analytics, advertising, or tracking SDKs are used.

## Your choices

- Delete or block your account via the operator.
- Report abuse; reports are reviewed against the server-blind design.
