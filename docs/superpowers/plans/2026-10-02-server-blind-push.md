# Server-blind cloud push (§7a end-to-end)

**Date:** 2026-10-02 · **Status:** implementing · **Design:** `docs/superpowers/specs/2026-09-29-federated-push-server-design.md` §7a/§7b/§7c, D13

Makes `mdrender-send` cloud pushes work against an `ENCRYPTION_MODE=on`
server (federated.z42z.com). Today the client gets `422 encryption
required` — the §7b gate we added; this plan implements the loop behind it.

**End state (operator's words):** in encrypted mode the client sends one
opaque blob per file; the server never sees the folder or the file, never
decrypts and can never decrypt — all it knows is that a blob of data is
destined for a device attached to the account.

Decisions agreed with the operator: **full slice** (server + client + app in
one batch), **TOFU key pinning** with a printed fingerprint for v1.

## Wire formats (the contract between the three components)

### Sealed CEK
`sealed_cek = base64(RSA-OAEP-SHA256/MGF1-SHA256(device_content_pub, CEK))`
— same parameters the app already unwraps (`CloudPushKeyStore.decryptOaep`).

### Content blob
`blob = nonce(12) || AES-256-GCM(CEK, plaintext).encrypt_and_sign()` —
nonce prefixed, matching the app's existing `PushCrypto.decryptFile`
contract. The `nonce` form field is base64 of the same 12 bytes
(informational; the server never needs it).

### Plaintext envelope (what `plaintext` is)
```
u32be(len(header)) || header_json || file_bytes
header_json = {"name": "<original filename>", "path": "<destination folder chain>"}
```
- `path` carries the folder (empty = app default). The server rejects
  `target_folder` as form data, so this is the folder's only carrier.
- The server stores the blob verbatim under an opaque `file_id` (in the DB
  *and* on disk) — no name, no path, no metadata (D13).

### CEK derivation
`CEK = HKDF-SHA256(salt="", IKM=AMS, info="mdrender-content", L=32)` (RFC 5869).
AMS = 32 random bytes per client machine, stored `~/.config/mdrender/content-key`
(0600), never uploaded (spec §7a "one key per account" — machines share by
copying the file).

### Key trust (§7c, v1 = TOFU)
1. Client fetches `{content_pubkey, content_proof, device_public_key}`.
2. Verifies `content_proof = SHA256withRSA(pairing_pub, "content:" + content_pubkey)`.
3. Pins `sha256(content_pubkey DER)` in `~/.config/mdrender/pins.json`
   keyed by `server|device`, printing the fingerprint on first pin.
   Any later mismatch → hard error (re-pair or delete the pin entry).

## Component changes

### 1. Server (`server/app/app.py`, tests)
- **New route** `GET /api/push/content-key?device=<name>` (Bearer):
  returns `{content_pubkey, content_proof, device_public_key}` — same
  field names as the account route; 404 `no content key` when the device
  has none (client prints "re-pair the device with a current app").
- **`/api/push`** when encryption on:
  - accepts optional form field `sealed_cek` → upserts it for the target
    device (spec §7a step 2: "any client … uploading the sealed blob");
  - **fail-fast**: after the existing 422/400 gates, if the device still
    has no sealed CEK → `428 {"error": "seal required"}` *before* any row
    is written — no more silent zombie pushes;
  - stores `alg`/`nonce`? No — nonce lives in the blob; the gate fields
    stay informational (documented contract).
- **`retry.py`**: `_ring()` gains the same sealed-CEK check as
  `_ring_doorbell` (today it bypasses the gate).
- Tests (`test_encryption_policy.py`): content-key route (200/404/401),
  push+`sealed_cek` → row stored + doorbell rings, push without → 428 and
  no push row, existing 422/400 tests unchanged.

### 2. Client (`tools/localsend-send/localsend-send.py` + packaging)
- Lazy `cryptography` import on the encrypted path only; missing →
  `error: this server requires encryption — install python-cryptography`.
- AMS load/create (0600), HKDF, CEK.
- Policy probe `GET /api/server/policy`: `off` → current plaintext path
  unchanged; `on` → encrypted path.
- Encrypted path per push: fetch + verify + TOFU-pin content key →
  seal CEK → build envelope → AES-GCM → POST `/api/push` with
  `alg`, `nonce`, `sealed_cek`, `conflict` and **no** `target_folder`.
- Friendly messages for 422/428.
- Version **1.0.16** in all five manifest spots; packaging gains the
  crypto dependency: PKGBUILD `depends=('python' 'python-cryptography')`,
  nfpm `python3-cryptography`; README updated.

### 3. App (`app/src/main/java/com/a42r/mdrender/cloudpush/`)
- `PushCrypto`: `parseEnvelope(bytes) -> {name, path, fileBytes}` (strict:
  length bounds, JSON parse) alongside `decryptFile`.
- `CloudPushDownloader`: when `encryptionMode == "on"`, decrypted bytes
  must parse as an envelope — import name = `header.name`, folder chain =
  `header.path` (manifest `name`/`path` are opaque ids from here on);
  malformed envelope → task failure with a clear message (never import
  raw uuid-named bytes).
- Tests (`PushCryptoTest`, `CloudPushDownloaderTest`): envelope round-trip,
  truncated/garbage header, import-by-envelope-name, manifest-id tolerance.
- *(stretch, only if it stays small)* re-fetch policy on registration
  check so a server flipped to `on` later cannot cause silent failures.

### 4. One-off ops
- **Purge the zombie push** `7906ce1f635f482eb035bc27373f5f74` on the
  federated slave (plaintext content at rest, pending forever). Required
  before the first encrypted push: once a sealed CEK exists the retry
  sweeper would try to doorbell it and the app would fail-decrypt forever.
  *(destructive — operator confirmation required)*
- Rebuild both containers (`DOCKER_BUILDKIT=0`), rebuild+install the Arch
  package, build + `build-deploy.sh` the APK (phone needs adb).

## Verification
1. `cd server && .venv/bin/python -m pytest -q` — full suite green.
2. `./gradlew test` — app unit tests green.
3. Live against federated: `mdrender-send --conflict=replace --name
   "Clever Juniper" ./review9.md` → 200; DB shows `sealed_cek` row,
   `target_folder=''`, `file_name=file_id`, on-disk blob named by id;
   content-key route returns the phone's key; 428 path proven by test.
4. Phone receives the file named `review9.md` in the chosen/default
   folder (operator confirms on device).
5. Mode-off master unaffected: plaintext push still works, no envelope
   fields sent.

## Out of scope (follow-ups)
- AMS rotation UI (§7d), out-of-band fingerprint confirm (§7c full),
  streaming decrypt, `push-to-phone.sh` encryption, account-portal upload
  path parity beyond what already exists.
