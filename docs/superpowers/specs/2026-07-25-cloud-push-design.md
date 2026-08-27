# Cloud Push — Design

## Goal

Push files (markdown, images, MP3s) from a Linux desktop to an Android phone off-LAN,
triggered by an AI agent via SSH. FCM is the doorbell: it tells the phone a file is
available, and the phone connects to the self-hosted server over HTTPS to pull it.

The push server is **self-hosted** — each user runs their own instance (Docker) on their
own machine. The Android app pairs with that server via a **QR code**, exchanging
**public keys**. Every FCM doorbell message is **envelope-encrypted** (server→phone) with
the recipient's public key, so a **single shared Firebase project** can serve all instances
of the app: FCM is a dumb doorbell that only sees ciphertext.

The **LocalSend device name** is the cross-LAN identity. It is registered with the push
server, so the existing `localsend-send` tool can address a phone by its familiar device
name: if the name is found on the LAN it sends over LocalSend as today; if it is **not**
found on the LAN and the tool is configured with a push server, it falls back to pushing
the files through that server, which delivers them to the target via the GCP/FCM
notification.

## Privacy & trust model

- **One Firebase project for every install of the app.** FCM is shared infrastructure.
  It routes a data message to a specific device token and nothing more.
- **FCM is only a doorbell.** The FCM message is a server→phone envelope saying a file is
  available and where to fetch it; the app then connects to the server over HTTPS to
  download the bytes. The envelope is encrypted with the phone's public key (and signed
  with the server's private key), so Google/Firebase, a network observer, and any other
  app instance see only ciphertext — never file names, paths, or retrieval keys.
- **Only registered devices receive messages.** A device is only in the registry after
  a successful QR pairing + key-possession proof. The server sends to a device's exact
  FCM token — it never broadcasts.
- **The LocalSend device name is a public routing address.** It sits in the registry so
  a sender can address a phone by name. Names are not secrets; content is still protected
  by the E2E envelope. A name collision is a routing problem, not a confidentiality one
  (see Security).
- **Trust anchor is the QR code** (trust-on-first-use). The server's public key comes
  from the QR the operator scans off their own server's screen, out-of-band from the
  network.

## Overview

```
Desktop (agent/SSH)      Self-hosted Push Server         Android Phone
       │                          (Docker, per-user)          │
       │  ────────── pairing ────────────────────────────────►│
       │     (operator scans QR off server's screen)          │
       │     server_url + server public key + pairing token   │
       │◄──────── register (device_secret, fcm_token, pub) ───│
       │                          │                           │
       │ POST /api/push           │                           │
       │ (Bearer: …)              │                           │
       │── file.md ──────────────►│                           │
       │                          │ stores file +             │
       │                          │ file-linked retrieval key │
       │                          │                           │
       │                          │ builds E2E envelope:      │
       │                          │   RSA-OAEP(phone pub) +   │
       │                          │   AES-GCM(payload) +      │
       │                          │   RSA-SHA256(server sig)  │
       │                          │                           │
       │                          │ FCM data message {env}    │
       │                          │ ─────────────────────────►│
       │                          │    (shared Firebase,      │
       │                          │     sees ciphertext only) │
       │                          │                           │
       │                          │  POST /api/push/{id}/     │
       │                          │  download {key}           │
       │                          │◄──────────────────────────│
       │                          │ ── file bytes (streamed) ►│
       │                          │                           │
       │                          │ importFileFromTemp()      │
       │                          │ → encrypted storage       │
```

## Push Server (Python, Docker, self-hosted)

### Deployment

- Runs where the operator chooses: a Linux desktop, a home server/NAS, a VPS.
  Single Docker image, `docker run` / compose.
- **On-LAN**: phone reaches it directly at `<host>:8080` (the QR encodes the LAN URL).
- **Off-LAN**: expose via Cloudflare Tunnel (recommended, gives TLS + a stable URL) or
  a port-forward. `PUSH_PUBLIC_URL` is what ends up in the QR.
- First run generates a **server RSA keypair**, stored in the SQLite DB (`DB_PATH`). The
  public key is embedded in the pairing QR.
- **State & backup**: everything (server keypair, client registry, device registry,
  pushes, retrieval keys) lives in one SQLite DB at `DB_PATH`. Back up by mounting the
  storage folder on the host and copying it (`sqlite3 .backup` or a file copy). If the
  server is reset and the DB is lost, every device must re-register — the app detects this
  and prompts (see Registration).
- **TLS**: served over TLS when off-LAN (tunnel). On plain LAN the FCM token and device
  secrets travel in the clear, so prefer a self-signed cert or accept the LAN as trusted;
  the encrypted envelope protects message content either way.

### Setup automation (maintainer, one-time)

Because the design uses **one shared Firebase project**, the Firebase setup is paid exactly
once — by the maintainer — and every self-hosted operator inherits it from the repo and the
server image. This section provides the script and the detailed walkthrough behind it. The
only step that cannot be automated is the Google browser OAuth login.

**`tools/fcm/setup-fcm.sh`** — idempotent, safe to re-run:

```bash
#!/usr/bin/env bash
set -euo pipefail
PROJECT_ID="${FIREBASE_PROJECT_ID:-mdrender-push}"
SA="fcm-pusher@$PROJECT_ID.iam.gserviceaccount.com"

# 1. Human step (once): Google OAuth browser login.
firebase login --no-localhost

# 2. Create the shared project if it does not exist yet.
firebase projects:create "$PROJECT_ID" --display-name "MDRender Cloud Push" || true

# 3. Register the Android app; write google-services.json into the app repo.
APP_ID="$(firebase apps:create android com.a42r.mdrender --project "$PROJECT_ID" --json \
            | jq -r '.appId')"
firebase apps:sdkconfig android "$APP_ID" --project "$PROJECT_ID" \
    > app/google-services.json

# 4. Enable the FCM v1 send API and create the service-account credential.
gcloud services enable firebasemessaging.googleapis.com --project "$PROJECT_ID"
gcloud iam service-accounts create fcm-pusher --project "$PROJECT_ID" || true
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:$SA" --role roles/firebasecloudmessaging.admin
gcloud iam service-accounts keys create fcm-service-account.json \
    --iam-account "$SA" --project "$PROJECT_ID"

echo "DONE. Commit app/google-services.json; mount fcm-service-account.json"
echo "into the server image as FCM_SERVER_KEY."
```

**What each artifact becomes:**
- `app/google-services.json` → committed to the Android repo; the app's FCM token comes from
  the SDK using it. `FCM_SERVER_KEY` needs no value on the phone — that's server-side only.
- `fcm-service-account.json` → baked into the server Docker image (or mounted), pointed to by
  `FCM_SERVER_KEY`. Server-only; never ships in the app.

**Maintenance:**
- **Re-run safety**: every step is `|| true` / idempotent — re-running repairs a missing
  project, app, or key without duplicating resources.
- **Key rotation**: delete the old key and create a new one
  (`gcloud iam service-accounts keys list/delete` then `... keys create`), then rebuild the
  image. Old key stops working immediately; no client change needed.
- **App config change**: if the Android package or app changes, re-run step 3 to regenerate
  `google-services.json`.
- **CLI refresh**: `firebase login --no-localhost` again when the token expires (usually
  every few hours of CLI use; refresh is automatic while a session is valid).

### Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/login` | none | Browser password form (`SERVER_PASSWORD`) |
| POST | `/login` | `SERVER_PASSWORD` | Validate password, set a short-lived session cookie (rate-limited + lockout, see Access control) |
| GET | `/pair` | session cookie | Android pairing page: renders the QR after login |
| GET | `/enrol/<id>` | session cookie | Tool enrolment page: shows the one-time key after login |
| POST | `/api/enrol/start` | none | Begin tool enrolment → `{ enrolment_id, verification_uri }` |
| POST | `/api/enrol` | one-time enrolment key | Exchange `{ enrolment_id, key }` for `client_id` + `client_secret` |
| POST | `/oauth/token` | client_id + client_secret (OAuth2 client-credentials grant) | Issue a short-lived `access_token` for tool/agent pushes |
| POST | `/api/push` | `Authorization: Bearer` (client-credentials) | Upload files for push; `target_device` (LocalSend name) is **required** — 400 if missing/unknown |
| POST | `/api/register-device` | pairing token + key-possession proof | Register FCM token + public key |
| POST | `/api/register-device` (update) | JSON body `{ device_secret, device_auth }` | Rotate FCM token / device name (no pairing token needed) |
| POST | `/api/push/{file_id}/download` | JSON body `{ key }` | Phone downloads a file (returns bytes over HTTPS) |
| POST | `/api/push/{file_id}/received` | JSON body `{ key }` | Ack: file downloaded and recorded → server deletes its copy |
| POST | `/api/device/status` | JSON body `{ device_secret, device_auth }` | Registration check → 200 ok / 404 needs re-registration |
| GET | `/api/push/{push_id}/status` | `Authorization: Bearer` | Agent checks delivery status |
| GET | `/api/health` | none | Health check |
| GET | `/devices` | session cookie | Device management: list registered devices, remove any |
| DELETE | `/devices/<device_id>` | session cookie | Remove a device (registry entry deleted; its `device_auth` becomes invalid) |
| GET | `/pushes` | session cookie | Pushed-file list (name, path, size, date, status) — operator view |
| GET | `/pending` | session cookie | Pending (unclaimed) push list — operator view |
| POST | `/api/push/{push_id}/retry` | session cookie | Operator re-push: send FCM for a push's still-pending files |

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SERVER_PASSWORD` | — | The server password. Gates **all** access: pairing, enrolment, device management, file listing. Required. |
| `LOGIN_MAX_ATTEMPTS` | `5` | Failed `/login` attempts before the source IP is locked out |
| `LOGIN_LOCKOUT_SECONDS` | `300` | Lockout duration after `LOGIN_MAX_ATTEMPTS` failures |
| `ENROL_TOKEN_TTL_HOURS` | `1` | How long a pending enrolment (link + key) stays valid |
| `ENROL_SESSION_TTL_MINUTES` | `15` | Browser session cookie lifetime after login |
| `ACCESS_TOKEN_TTL_SECONDS` | `3600` | OAuth2 access-token lifetime (client-credentials grant) |
| `PUSH_STORAGE_DIR` | `/data/push` | Directory for staged file bytes + the SQLite DB (`DB_PATH`) |
| `DB_PATH` | `/data/push/server.db` | SQLite DB: server keypair, client registry, device registry, pushes, retrieval keys |
| `PUSH_FILE_TTL_HOURS` | `24` | Purge unacked file **bytes** after N hours (send records are retained) |
| `PUSH_RETRY_COUNT` | `5` | FCM re-push attempts for a file not acked within the retry window |
| `PUSH_RETRY_INTERVAL_MINUTES` | `30` | Wait between re-push attempts |
| `DEVICE_TTL_DAYS` | `90` | Sweep registry entries for devices that made no device-auth call in N days (orphan cleanup) |
| `FCM_SERVER_KEY` | — | Path to the **shared** Firebase **service-account** JSON (FCM HTTP v1 credential, required for FCM path) |
| `PUSH_PUBLIC_URL` | — | Reachable URL of this server; sent in the QR + used as `push_url` |
| `LISTEN_ADDR` | `:8080` | Server listen address |

`FCM_SERVER_KEY` is the same credential in every self-hosted instance — it belongs to the
one shared Firebase project (see Security for the trade-off this implies). Legacy FCM
**server keys are fully deprecated** (decommissioned 2024-06-20), so FCM HTTP v1 is the only
path: the server mints a short-lived OAuth2 Bearer token from this service account
(JWT RS256 → `https://oauth2.googleapis.com/token` →
`POST https://fcm.googleapis.com/v1/projects/<PROJECT_ID>/messages:send`). The credential
is generated once by the maintainer (see Setup automation below) and baked into the server
image, so operators configure nothing.

### Access control

First access to the server is a **password authorization**. `SERVER_PASSWORD` gates every
human-facing operation; nothing is reachable without one of:

- **Browser session** — password login (`POST /login`) → short-lived session cookie. Required
  for pairing (`/pair`), enrolment confirmation (`/enrol/<id>`), device management
  (`/devices`), and the pushed/pending file lists (`/pushes`, `/pending`).
- **OAuth2 bearer** — client-credentials token for machine pushes (`POST /api/push`,
  `GET /api/push/{id}/status`). The credential is only obtainable through password-gated
  enrolment.
- **Phone device auth** — the `device_auth` secret for the phone's own endpoints (token/name
  rotation, registration check, download ack), plus file-linked retrieval keys for downloads.
  Device↔server calls use **POST/PUT JSON bodies**, never query strings, so nothing leaks
  into proxy/TLS-terminator logs. Received during password-gated pairing.

No anonymous access to registration, device management, file lists, or push endpoints.
`POST /login` is rate-limited and locks out a source IP after `LOGIN_MAX_ATTEMPTS` failures
for `LOGIN_LOCKOUT_SECONDS`.

### Server keypair & pairing (QR)

1. First run: generate RSA-3072 keypair, persist private key in the SQLite DB (`DB_PATH`).
2. The operator opens `/pair` in a browser. Without a session it shows the password form
   (`SERVER_PASSWORD`); after login it renders the pairing QR, whose payload is:
   ```json
   { "v": 1, "server_url": "<PUSH_PUBLIC_URL>", "pk": "<base64 DER SPKI>",
     "token": "<one-time pairing token>", "expires": "2026-08-27T12:00:00Z" }
   ```
   The pairing token is single-use, short-TTL (15 min), and authorizes exactly one device
   registration. The QR is shown **only after** the password is entered, so finding the
   URL is not enough to pair a device.
3. The operator scans the QR with the phone. The QR is the trust anchor — the phone stores
   the server public key from it.

### Tool enrolment (OAuth2 client credentials)

Machine tools (`localsend-send`, the agent's `push-to-phone`) get their own OAuth2
**client credential** from the server. This gives each tool a revocable identity, and it
works off-LAN because both enrolment and every push go to `PUSH_SERVER_URL`.

Enrolment is a **browser flow gated by the site password** (`SERVER_PASSWORD`):

```bash
localsend-send.py --enrol --server <PUSH_SERVER_URL>
# Open in your browser:  https://push.example.com/enrol/8f3c1a...
#   → enter the site password, then copy the key shown back here:
# Enter enrolment key: ████
# → wrote ~/.config/mdrender/push-credentials.json (0600)
```

1. The CLI calls `POST /api/enrol/start` → `{ enrolment_id, verification_uri }` and prints
   the link (auto-opening the browser via `xdg-open` when a display is present).
2. The operator opens the link in a browser. The server asks for the site password
   (`SERVER_PASSWORD`, set as an env var); after login it shows a **one-time key** for that
   enrolment.
3. The operator re-enters the key into the CLI on the console.
4. The CLI calls `POST /api/enrol` with `{ enrolment_id, key }`; the server verifies the
   key (single-use, valid `ENROL_TOKEN_TTL_HOURS`) and returns
   `{ client_id, client_secret }`.
5. The CLI writes them to a credentials file
   (`$XDG_CONFIG_HOME/mdrender/push-credentials.json`, default
   `~/.config/mdrender/push-credentials.json`, perms 0600).

The key is exchanged server-side for the credential, so `client_secret` is never shown in
the browser. A browser session is short-lived (`ENROL_SESSION_TTL_MINUTES`).

**Authenticate a push** (OAuth2 client-credentials grant, RFC 6749 §4.4):

1. The tool reads the credentials file; if it holds a cached `access_token` that is still
   valid, it uses it.
2. Otherwise it `POST /oauth/token` with `grant_type=client_credentials`, `client_id`,
   `client_secret` → `{ "access_token": "...", "token_type": "Bearer",
   "expires_in": 3600, "scope": "push" }` and caches the token in the file.
3. It calls `POST /api/push` (and `/api/push/{id}/status`) with
   `Authorization: Bearer <access_token>`.

The server keeps a **client registry** in the SQLite DB: `client_id` → hashed
`client_secret`, `name`, `scopes`, `created_at`, `revoked_at`. A client can be revoked by
the operator without touching other tools. The default scope is `push` (upload files,
check status); it cannot enrol new clients or register devices.

### Registration (device registry)

The phone registers by calling `POST /api/register-device`:

```json
{
  "device_secret": "<random UUID, phone-generated>",
  "device_name": "<LocalSend device name, e.g. LocalSendPrefs.alias>",
  "fcm_token": "<Firebase instance token>",
  "public_key": "<base64 DER SPKI of phone public key>",
  "pairing_token": "<from QR, first registration only>",
  "sig": "<base64: RSA-SHA256(phone private key, sha256(device_secret||device_name||fcm_token||public_key))>"
}
```

- `device_name` is the phone's **LocalSend name** (`LocalSendPrefs.alias`) — the routing
  identity a sender uses to address this device (see Agent Integration).
- Server verifies the pairing token (one-time, unexpired), then verifies `sig` against
  the presented `public_key` — proof the caller holds the phone's private key.
- A `device_name` already bound to a *different* `device_secret` **replaces** the old
  registration (last-writer-wins): the new device takes over the name, the previous
  registry entry is deleted and its `device_auth` invalidated — the likely case is a
  reinstall / re-pair with a fresh keypair. The displaced device is sent an FCM notice so
  the app can prompt "re-pair to keep receiving". See Security.
- On success it stores a registry entry and returns a `device_auth` secret:
  ```json
  { "ok": true, "device_auth": "<random secret>" }
  ```
- The phone persists `device_secret`, `device_auth`, `server_url`, and the server public
  key in `PushServerConfig`.
- **FCM token rotation** (`onNewToken`): the phone re-POSTs with `device_secret` +
  `device_auth` + the new `fcm_token` (no pairing token). The server updates the token
  only if `device_auth` matches — an attacker who only has `device_secret` can't hijack
  the device.
- **Registration check** (`POST /api/device/status`): the phone can verify its registration
  at any time — on app foreground and when a push/ack fails. If the server was reset (DB
  lost) and the entry no longer exists it returns 404; the app shows "device needs
  re-registration" and offers to re-pair.

Registry entry: `device_secret` (id) → `device_auth`, `device_name`, `fcm_token`,
`public_key`, `registered_at`. Name-addressed pushes resolve `device_name` →
`device_secret` → `fcm_token` + `public_key`.

### Push flow (detail)

1. A sender calls `POST /api/push` with multipart file(s), `target_device` (a LocalSend
   device name — **required**), and optional `target_folder`.
   - Server validates the OAuth2 bearer token.
   - `target_device` is required: missing or unknown → `400 { "error": "device not found" }`.
     No broadcast — a push always names exactly one device.
   - Stores each file to `<storage_dir>/<push_id>/<file_id>/<filename>`.
   - Generates a file-linked retrieval key per file (random UUID). The key is **not**
     single-use: it stays valid until the file is acked or purged, so a phone can retry a
     partial or failed download.
   - `target_folder` (e.g. `Docs/Reports`) routes files into that subfolder of the phone's
     "Cloud Push" folder on import; nested paths are supported. Empty = Cloud Push root.
2. For each target device, the server builds the **E2E envelope** (encrypted with that
   device's public key) and sends an FCM data message to that device's token only:
   ```json
   {
     "v": 1,
     "alg": "RSA-OAEP-256",
     "enc": "A256GCM",
     "kid": "<phone key id / rotation>",
     "ek": "<base64: RSA-OAEP(phone public key, random 32-byte content key)>",
     "iv": "<base64: 12-byte IV>",
     "tag": "<base64: 16-byte GCM tag>",
     "ct": "<base64: AES-256-GCM(content key, payload JSON)>",
     "sig": "<base64: RSA-SHA256(server private key, ek||iv||ct)>"
   }
   ```
   where the payload is the plaintext the current design sends today:
   ```json
   { "server_url": "<PUSH_PUBLIC_URL>", "push_id": "uuid",
     "date": "2026-08-28T12:34:56Z",
     "files": [ { "file_id": "<file_id>", "name": "notes.md",
                  "path": "Docs/Reports",
                  "retrieval_key": "<file-linked key>" } ] }
   ```
   The envelope is a **doorbell** — it says a file is available and where to fetch it
   (server URL + file id + file-linked retrieval key). It carries only server URL, push id,
   date/time, and per file its id, name, target path, and retrieval key. File names, paths,
   and keys exist **only** inside the encrypted envelope; the phone then connects to the
   server over HTTPS to download the actual bytes.
   - **Batching (envelope ≤ 3.5 KB).** A push's files are chunked into contiguous slices
     (~32 files per message at 256 bytes per file entry); each slice is a separate FCM
     message with the same `push_id`. The phone merges slices sharing a `push_id` into one
     download queue. Retry/re-push applies per file as before.
   - `ek` (RSA-3072 OAEP-SHA256) is 384 bytes; with the 3.5 KB payload cap the full base64
     envelope stays safely under FCM's 4 KB data-message limit.
   - Firebase sees only the envelope. It cannot read file names, paths, or retrieval keys.
   - The phone verifies `sig` with the server public key (authenticity), unwraps the
     content key with its Keystore private key, and AES-GCM decrypts `ct`.
3. **Ack & purge:**
   - The phone downloads each file over HTTPS, imports it into app storage, then acks via
     `POST /api/push/{file_id}/received`. The ack deletes the file **bytes** on the server;
     the send record (name, path, size, date, target, status) is retained and shown in the
     operator view (`GET /pushes`).
   - Unacked files are purged after `PUSH_FILE_TTL_HOURS`; the record still remains.
   - The retrieval key is file-linked, not one-time — valid until ack or purge, so a partial
     or failed download can be retried.
4. **Delivery retry:** if a file is not acked within `PUSH_RETRY_INTERVAL_MINUTES`, the
   server re-sends the slice(s) containing the unacked files (stored envelope, or rebuilt),
   up to `PUSH_RETRY_COUNT` attempts, then marks those files exhausted (still listed in
   `/pending`). The operator can re-trigger a push of pending files from the UI via
   `POST /api/push/{push_id}/retry`.

### Implementation notes

- Python with Flask or FastAPI, single Docker image.
- **SQLite** (`DB_PATH`): tables for the server keypair, pairing tokens, client registry,
  device registry, pushes, files, and retrieval keys. SQLite transactions give atomic
  updates (no read-modify-write races). Backup = mount/copy the DB file (or
  `sqlite3 .backup`).
- `cryptography` library for RSA-OAEP (SHA-256), AES-256-GCM, and RSA-SHA256 signing.
- Graceful SIGTERM, logs to stdout.
- Optional Cloudflare Tunnel config: `tunnel: <name>` in a `cloudflared.yaml`.

## Android Changes

### New package: `cloudpush/`

| Class | Purpose |
|-------|---------|
| `PushServerConfig` | Holds server URL + server public key + device secret + device auth + device name, persisted in preferences |
| `CloudPushKeyStore` | Generates and holds the phone RSA-3072 keypair in Android Keystore (`mdrender_cloudpush_keypair`), non-exportable private key |
| `PushCrypto` | Builds/verifies the E2E envelope: RSA-OAEP unwrap, AES-GCM decrypt, server sig verify |
| `QrPairingScanner` | ML Kit barcode scanner for the pairing QR |
| `PushClient` | HTTP client using `java.net.HttpURLConnection` (no new dep) |
| `PushFcmService` | `FirebaseMessagingService` — always registered, decrypts and handles envelopes |
| `CloudPushManager` | Orchestrator — receives decrypted trigger, manages the download queue with progress |
| `CloudPushDownloadService` | `dataSync` foreground service — processes the download queue in the background with a progress notification |

`PushCrypto` is separate from `CryptoEngine` (symmetric, at-rest) — this is public-key
E2E for the transport. The phone private key never leaves the Keystore.

### Pairing & registration flow (app)

- Settings → **Cloud Push → Pair with server**: opens the QR scanner (new CAMERA
  permission), scans the server's QR.
- Parses `server_url`, server public key, pairing token; stores them in
  `PushServerConfig`.
- Reads `device_name` from `LocalSendPrefs.alias` (the phone's LocalSend name).
- Generates (or reuses) the Keystore keypair, then registers via `PushClient.registerDevice`
  (with key-possession `sig`), receives `device_auth`.
- On success: status shows "Paired with <server>" and the registered device name.
- If the user renames the LocalSend alias in Settings, the phone re-registers with the
  same `device_secret`/`device_auth` and the new `device_name` so the server keeps routing
  by the current name.

### Data flow — FCM path (always on)

- `PushFcmService.onMessageReceived()` reads the envelope (`data["p"]`).
- Verifies the server signature, unwraps the content key, decrypts the payload
  (`server_url`, `push_id`, `files[]` with name/path/retrieval_key) — all off the main
  thread.
- Enqueues the files on `CloudPushManager`, which merges slices sharing a `push_id` into
  one download queue, and starts `CloudPushDownloadService` (a `dataSync` **foreground
  service** — downloads run in the background because the app is usually not foreground
  when FCM arrives). The service posts a progress notification: "Downloading notes.md from
  Cloud Push…".
- For each entry in `files`, `PushClient.downloadFile(server_url, file_id, retrieval_key)`
  POSTs `{ "key": … }` to `/api/push/{file_id}/download`, streams the response to a temp
  file, then calls `FileRepository.importFileFromTemp()`.
- Files stored under the "Cloud Push" folder; `path` (from `target_folder`) resolves to a
  subfolder via `FolderRepository.findOrCreateFolder`.
- After a file is imported, the phone acks via `POST /api/push/{file_id}/received`
  (`{ "key": … }`) — the server then deletes its copy.
- The notification updates as each file completes; when done: "3 files received in Cloud
  Push". Each import is transactional (temp → import only on success); a failed download
  leaves the file on the server for the retry path.
- If signature verification or decryption fails, drop the message (no partial imports)
  and log a warning.

### Data flow — registration monitoring (no polling)

- There is **no polling**: delivery is FCM-push only (no battery drain).
- On app foreground — and when a push/ack fails with 401/404 — `PushClient.checkRegistration()`
  POSTs `{ device_secret, device_auth }` to `/api/device/status`. If the server returns 404
  (server reset, DB lost), the app shows "Device needs re-registration" and offers to re-pair.

### Settings additions

New section "Cloud Push" in Settings:

- **Pair with server** — button; opens the QR scanner (first-time setup)
- **Device name** — read-only, mirrors the LocalSend alias (this is what senders address)
- **Server URL** — text field (pre-filled from QR)
- **Device ID** — auto-generated, read-only
- **Re-pair / rotate keys** — wipes pairing + keys and restarts registration
- **Check registration** — button; calls `/api/device/status` now (shows "paired" or
  "needs re-registration")
- **Received pushes** — list of files received via Cloud Push (name, path, date), each
  opening the file.
- **Pending / active downloads** — list of downloads not yet imported (in progress, queued,
  or failed), each with **Cancel**. Cancel discards the local temp copy and acks the server
  as delivered (`POST /api/push/{file_id}/received`) so the server removes its copy.
- Status label showing pairing state and files received; if the server no longer recognises
  the device, it shows a re-registration prompt.

### Dependencies

```kotlin
// app/build.gradle.kts
implementation(platform("com.google.firebase:firebase-bom:33.0.0"))
implementation("com.google.firebase:firebase-messaging")
implementation("com.google.mlkit:barcode-scanning")   // QR pairing scanner

// top-level build.gradle.kts
plugins {
    id("com.google.gms.google-services") version "4.4.2"
}
```

- The app ships the **one shared** `google-services.json` (single Firebase project for
  all installs). Firebase is used only for messaging transport.
- Add `<uses-permission android:name="android.permission.CAMERA"/>` for QR scanning.
- Declare `CloudPushDownloadService` in the manifest with
  `android:foregroundServiceType="dataSync"` (the `FOREGROUND_SERVICE` /
  `FOREGROUND_SERVICE_DATA_SYNC` permissions are already present).

### PushFcmService (`onMessageReceived`)

```kotlin
class PushFcmService : FirebaseMessagingService() {
    override fun onMessageReceived(message: RemoteMessage) {
        val envelope = message.data["p"] ?: return
        // (off main thread)
        val payload = PushCrypto.decryptEnvelope(envelope) ?: run {
            Log.w(TAG, "CloudPush: drop message — bad sig or decrypt"); return
        }
        // payload: server_url, push_id, date, files [{file_id, name, path, retrieval_key}]
        CloudPushManager.download(payload)
    }

    override fun onNewToken(token: String) {
        // Re-register with the paired server (device_secret + device_auth + token)
    }
}
```

## Agent Integration

Two tools deliver files; both authenticate to the push server with OAuth2 client
credentials from the shared credential file.

### `localsend-send` — LAN-first, cloud fallback

`tools/localsend-send/localsend-send.py` today sends files to a LocalSend receiver by IP
(`--host` only; there is no `--name` yet). It gains a **device-name target** so the same
command works when the phone is off-LAN. **LAN name discovery is new implementation work**
(LocalSend v2 UDP multicast, same-subnet only — see R8); the cloud fallback is the primary
path when the phone is off-LAN:

```bash
# One-time: enrol this machine with the server (writes ~/.config/mdrender/push-credentials.json)
localsend-send.py --enrol --server https://push.example.com

# LAN: resolve "Sunny Falcon" on the LAN and send via LocalSend v2 (today's flow)
localsend-send.py --name "Sunny Falcon" notes.md photo.jpg

# Off-LAN / not on LAN: falls back to the push server, delivered via FCM
localsend-send.py --name "Sunny Falcon" notes.md photo.jpg
```

Behavior with `--name`:

1. Discover on the LAN (LocalSend v2 UDP multicast — the same protocol the app's
   `LocalSendDiscovery` speaks) and resolve the name to an IP.
2. **Found on LAN** → send via the existing LocalSend protocol path (PIN, folder,
   conflict strategy unchanged).
3. **Not found** → fall back to `POST /api/push` with `target_device=<name>` and the files
   as multipart. The tool authenticates with the OAuth2 bearer token from its credential
   file (enrolling or fetching a token first if needed). The server resolves the name in
   its registry and delivers via the E2E FCM envelope.
4. **Not found** and no enrolled server → exit with "device not found".

`--host <ip>` stays direct with no fallback (scripted/agent use). The server URL comes
from the credential file (or `--server` / `PUSH_SERVER_URL` as an override); auth is the
OAuth2 bearer from the credential file, no static API key.

### `push-to-phone` script

A thin `curl` wrapper for the agent to deliver files to a named device without the
LocalSend handshake. It reuses the same OAuth2 credential file as `localsend-send` — the
operator enrols the machine once, and both tools share it:

```bash
#!/bin/bash
# Usage: push-to-phone --target NAME file1 [file2 ...]
CREDS="${MDREnder_PUSH_CREDS:-$HOME/.config/mdrender/push-credentials.json}"
TOKEN="$(python3 - "$CREDS" <<'PY'
import json, sys, urllib.request, urllib.parse
c = json.load(open(sys.argv[1]))
body = urllib.parse.urlencode({
    "grant_type": "client_credentials",
    "client_id": c["client_id"],
    "client_secret": c["client_secret"]}).encode()
r = urllib.request.urlopen(c["server_url"] + "/oauth/token", data=body)
print(json.load(r)["access_token"])
PY
)"
flags=("-F" "target_device=$2")  # --target NAME is required (server returns 400 otherwise)
if [[ "$1" == "--target" ]]; then shift 2; fi
for f in "$@"; do flags+=("-F" "file=@$f"); done
curl -H "Authorization: Bearer $TOKEN" "${flags[@]}" "${PUSH_URL:-$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["server_url"])' "$CREDS")}/api/push"
```

`--target <name>` addresses exactly one device by its LocalSend name and is required — the
server returns 400 if omitted.

## Security

| Layer | Mechanism |
|-------|-----------|
| Tool auth (push) | OAuth2 client-credentials: `Authorization: Bearer` from a short-lived access token; per-tool credential, revocable independently |
| Tool credential at rest | `client_id`/`client_secret` stored in `~/.config/mdrender/push-credentials.json`, perms 0600 |
| All server access | Gated by `SERVER_PASSWORD` (env var) + short-lived session cookie: pairing, enrolment, device management, file listing. Machine pushes use OAuth2 bearer; phone ops use `device_auth` |
| Login brute force | Rate limit + lockout on `POST /login` (`LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS`), keyed per source IP |
| Transport to server | TLS via Cloudflare Tunnel off-LAN; plain LAN is accepted (see Deployment) |
| FCM payload | Envelope-encrypted (server→phone): RSA-OAEP-256 wraps a random AES-256-GCM key; server signs; phone decrypts. Shared Firebase sees ciphertext only |
| Registration | QR is the trust anchor (TOFU) + one-time pairing token + key-possession proof |
| FCM token rotation | `device_auth` secret required; `device_secret` alone can't hijack |
| Device↔server auth | `device_auth` and retrieval keys travel in **POST/PUT JSON bodies**, never query strings, so nothing leaks into proxy/TLS-terminator logs |
| Download | File-linked retrieval keys (not single-use), delivered only inside the encrypted envelope; the phone acks via `/received` to trigger deletion |
| Rest storage | AES-256 encrypted on phone via existing `CryptoEngine` |
| Device identity | Auto-generated UUID device secret + device auth secret |
| Device name routing | Names are public addresses, not secrets; registration still gated by pairing + key proof |

**Password needs transport protection.** `SERVER_PASSWORD` travels to the server over the
browser; on a plain-HTTP LAN it is visible to anyone sniffing the LAN. Prefer a TLS
terminator (Cloudflare Tunnel, or a self-signed cert for LAN) whenever password-gated
access is used. Login is additionally protected by rate limiting and lockout
(`LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS`).

**Name collision policy.** Names are last-writer-wins. A new registration claiming a name
already bound to another `device_secret` **replaces** the old registration: its registry
entry and `device_auth` are invalidated, and the displaced device is sent an FCM notice.
The common case is a reinstall / re-pair with a fresh keypair. An attacker who completes a
password-gated pairing could displace a device this way — bounded by the password gate.
Names remain a routing concern, never a confidentiality one — content is protected by the
E2E envelope regardless of who holds the name.

### Shared-Firebase trade-off (acknowledged)

A single Firebase project means every self-hosted operator holds the same FCM service-account
credential (`FCM_SERVER_KEY`).
In principle any operator could send an FCM data message to any device token in that
project. This is safe-by-design for confidentiality — payloads are E2E encrypted with the
recipient's phone public key, which the attacker doesn't have — so the worst case is a
spoofed notification/spam, never a content leak. If that worst case is unacceptable, the
alternative is per-user Firebase projects (contradicts the "one project" goal) — noted as
an open question.

## Open Questions & Decisions

**Decided (2026-08-28):**

- **Multiple devices** — the server has a device-management console after login:
  `GET /devices` lists every registered device (name, registered_at, device id) with a
  remove action (`DELETE /devices/<id>`). Removing deletes the registry entry; its
  `device_auth` becomes invalid and name-addressed pushes to it return 404.
- **Name collision** — replace, don't reject: a new registration claiming a name takes it
  over (last-writer-wins). See Security.
- **Orphan cleanup** — required. Replacement/removal deletes the superseded registry entry;
  a periodic sweep (`DEVICE_TTL_DAYS`, default 90) drops devices that make no device-auth
  call within the window. Unacked file bytes are purged by `PUSH_FILE_TTL_HOURS`; send
  records are retained.
- **Push from any folder** — approved: `target_folder` routes to subfolders of the "Cloud
  Push" folder.
- **Admin surface** — the server password (`SERVER_PASSWORD`) gates all access: pairing,
  enrolment, device management, and pushed/pending file lists.
- **No polling (R1)** — delivery is FCM-push only. No phone polling: short intervals drain
  battery, long intervals defeat the push purpose. Missed pushes are recovered server-side
  by retries (`PUSH_RETRY_COUNT`, `PUSH_RETRY_INTERVAL_MINUTES`) and operator-triggered
  re-push from the UI (`POST /api/push/{push_id}/retry`).
- **Doorbell + HTTPS pull (R2)** — FCM carries only the envelope (server URL, date, file
  name/path, retrieval key); the app pulls the bytes over HTTPS.
- **File-linked keys + ack-delete (R3/R5)** — retrieval keys are not one-time; the ack
  (`POST /api/push/{file_id}/received`) triggers server deletion of the bytes; the send
  record remains.
- **Login rate limit + lockout (R4)** — `LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS`.
- **Explicit target (R7)** — `POST /api/push` requires `target_device`; 400 if absent. No
  broadcast.
- **`--name` LAN discovery (R8)** — new implementation work in `localsend-send.py`
  (not present today); best-effort, same-subnet only.
- **SQLite + backup + POST-body auth (R9)** — all server state (keypair, clients, devices,
  pushes, keys) in one SQLite DB; backup by mounting/copying the DB; device↔server calls
  use POST/PUT bodies (no query-string secrets); a lost DB means re-registration, which the
  app detects via `POST /api/device/status`.
- **Envelope size** — bounded by batching. The envelope carries only server URL, push id,
  date/time, and per file name/path + retrieval key — never file bytes. The server splits a
  push into multiple FCM messages when the payload would exceed **3.5 KB** (headroom under
  FCM's 4 KB cap): at ~256 bytes per file entry that is ~32 files per message. Each message
  is the same push (`push_id`) carrying a contiguous slice of the file list; the phone
  merges them into one download queue. See §Push flow.
- **OAuth2 scopes** — enrolment is a registration process whose only purpose is to allow
  pushing. Scope stays `push` (upload + status); the operator/management surface is
  password-session-gated, not OAuth2, so no extra scopes are needed.
- **Background downloads** — all downloads happen in the background (the app is usually not
  foreground when FCM arrives). They run in a `dataSync` foreground service with a progress
  notification; the manifest already declares `FOREGROUND_SERVICE_DATA_SYNC`.

## Design Review — Open Issues

Raised during design review, 2026-08-27. Each issue needs a decision before implementation.
Respond inline under **Resolution** (or append a response in a new subsection).

### R1. Polling fallback is structurally dead

- **Problem:** Download tokens are delivered *only* inside the FCM envelope (§Push flow,
  §Polling), yet polling is specified as the fallback "for when FCM is unavailable" — and
  `GET /api/pending` returns metadata **without** download tokens.
- **Why it matters:** When FCM is genuinely unavailable (offline, Doze, force-stop, non-GMS
  device), the phone can see that a push exists but has no way to download it. The fallback
  path can never complete, so off-LAN push silently breaks in the exact situations it exists
  for.
- **Suggested fix:** Let polling fetch tokens over an authenticated channel (`device_auth`),
  or persist the envelope server-side and let the phone retrieve it with `device_auth`.
- **Decision (2026-08-28):** polling is removed entirely — delivery is FCM-push only. No
  battery-vs-latency trade-off. Recovery is server-side: retries + operator re-push.
- **Resolution:**

### R2. "End-to-end encrypted" is narrower than stated

- **Problem:** The E2E envelope protects only the FCM doorbell message. Files are uploaded
  in the clear and stored/served as plaintext by the server (§Push flow); the phone's
  `device_secret` and FCM token also sit on the server.
- **Why it matters:** The Goal says "messages encrypted and thus private" and the privacy
  section says FCM/network observers "never file names, URLs, or download tokens." The
  accurate claim is *encrypted against Firebase and network observers, plaintext to the
  server operator*. Fine for a single-user self-hosted server, but the Privacy & trust model
  should say so explicitly rather than implying true E2E.
- **Suggested fix:** Rename to "server-side envelope encryption" and state plainly that the
  server sees file content.
- **Decision (2026-08-28):** scope accepted — self-hosted server sees plaintext. The
  envelope now carries file names/paths/retrieval keys encrypted (see §Push flow), so
  "opaque to Firebase" is the precise claim. Rename Goal/privacy wording to "server-side
  envelope encryption".
- **Decision (2026-08-28):** doorbell-only confirmed — FCM carries just the envelope; the
  app pulls the bytes over HTTPS from the server. Wording updated.
- **Resolution:**

### R3. No delivery guarantee — pushes can vanish silently

- **Problem:** FCM data messages are best-effort. Combined with one-time download tokens
  (consumed on first use), auto-purge after TTL, and no ack/reconciliation loop
  (`DELETE /api/push/{file_id}` exists but nothing re-sends on failure), an
  offline/force-stopped phone permanently loses pushes.
- **Why it matters:** An AI-agent-driven "push report, confirm delivered" workflow needs
  delivery confirmation; today a lost FCM message is invisible and unrecoverable.
- **Suggested fix:** A delivery state machine: persist the envelope server-side, phone acks
  via the DELETE endpoint, server re-pushes or surfaces stale entries.
- **Decision (2026-08-28):** implemented as retries + operator re-push: unacked files are
  re-pushed after `PUSH_RETRY_INTERVAL_MINUTES` up to `PUSH_RETRY_COUNT`, then exhausted but
  listed in `/pending`; the UI can re-trigger via `POST /api/push/{push_id}/retry`.
- **Resolution:**

### R4. Enrolment/pairing funnels through one unguarded password

- **Problem:** `SERVER_PASSWORD` gates both tool enrolment and device pairing — the two
  highest-privilege operations (a client credential can push to *every* device; pairing adds
  a device). There is no rate-limiting or lockout on `POST /login`.
- **Why it matters:** The admin gate of the whole system is a dictionary-attack target on any
  LAN the server is reachable from.
- **Suggested fix:** Fail-closed rate limit + backoff on `/login`; consider a separate pairing
  passphrase (or two env vars) so enrolment and pairing are not both behind a single secret.
- **Decision (2026-08-28):** single `SERVER_PASSWORD` gates all access — accepted as the
  model (see Access control). Rate-limiting/lockout on `/login` now implemented
  (`LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS`).
- **Resolution:**

### R5. Purge semantics are ambiguous

- **Problem:** "After the **first successful download**, the file is deleted" (server-side
  "served"?) vs the `DELETE` ack endpoint (client-side "imported"?). Unclear which is
  authoritative, and whether an interrupted download burns the one-time token.
- **Why it matters:** If "download" means "stream started," an interrupted download destroys
  the file; if client ack is authoritative, the spec must say so.
- **Suggested fix:** Make client ack (`DELETE`) the purge trigger; tokens are consumed only on
  a completed, acked download.
- **Decision (2026-08-28):** ack = the phone has downloaded AND recorded the file. The ack
  endpoint (`POST /api/push/{file_id}/received`) deletes the file bytes; the send record
  remains. Keys are file-linked (not single-use) so interrupted downloads can retry.
- **Resolution:**

### R6. `--enrol` invocation is inconsistent with the flow

- **Problem:** §Agent Integration shows `localsend-send.py --enrol <token> --server <URL>`,
  but §Tool enrolment defines a flow where the CLI never holds a token upfront:
  `--enrol --server <URL>` → prints link → browser password → typed one-time key.
- **Why it matters:** The stale positional `<token>` example would mislead an operator
  following the doc.
- **Suggested fix:** Align §Agent Integration to `--enrol --server <URL>` + interactive key
  entry.
- **Decision (2026-08-28):** confirmed — §Agent Integration updated to
  `--enrol --server <URL>` → link → browser password → typed one-time key.
- **Resolution:**

### R7. Broadcast default is a footgun

- **Problem:** `POST /api/push` with no `target_device` delivers to **every registered
  device**.
- **Why it matters:** An agent forgetting `--target` (or a name typo returning 404 rather than
  failing loudly) silently sends files to all phones.
- **Suggested fix:** Require an explicit target, or make broadcast an explicit opt-in flag.
- **Decision (2026-08-28):** explicit target required — `POST /api/push` without
  `target_device` returns 400. No broadcast.
- **Resolution:**

### R8. Name→IP discovery is new work and subnet-bound

- **Problem:** The CLI today is `--host`-only; `--name` requires implementing LocalSend v2
  UDP multicast discovery, which does not cross subnets/VLANs. `LocalSendPrefs.alias` is also
  auto-generated and user-renamable, and a rename mid-flight can orphan in-flight pushes.
- **Why it matters:** In practice "not found on LAN" will be the common path (Wi-Fi-isolated
  phones, guest networks), silently routing to the server. The spec should treat LAN discovery
  as best-effort and the cloud fallback as the real path.
- **Suggested fix:** Acknowledge discovery is best-effort; consider surfacing when a push took
  the cloud path.
- **Decision (2026-08-28):** `--name` LAN discovery is **not yet implemented** in
  `localsend-send.py` (today it is `--host` only) — add it to the implementation plan.
  Best-effort, same-subnet only; the cloud fallback is the primary path.
- **Resolution:**

### R9. Operational gaps

- **Problem:**
  - (a) `state.json` is a single read-modify-write file with no locking — concurrent
    registrations/pushes can lose updates.
  - (b) No backup story: losing `server_key.pem` or the registry orphans every paired device
    (server can't sign or route; all phones must re-pair).
  - (c) Secrets in query strings — `GET /api/pending?device_secret=…` and
    `download?token=…` are logged by reverse proxies/TLS terminators.
  - (d) "Always on" isn't — no foreground service is specified for background large-file
    downloads; Doze/force-stop can suspend it; non-GMS devices get no FCM at all.
  - (e) The Security table is broken in rendered markdown (the "Password needs transport
    protection" paragraph splits the table mid-row).
- **Why it matters:** Concurrency → lost state; no backup → hard re-pair event; log leakage →
  credentials in proxy logs; "always on" overreach → false reliability expectations.
- **Suggested fix:** File lock or append-only log + snapshot for `state.json`; document
  backup/export of the server key + registry; move tokens/device identity into headers or
  bodies; specify a `dataSync` foreground service for downloads; fix the table formatting.
- **Decision (2026-08-28):** admin surface resolved (password gates device mgmt + listing);
  (a) SQLite replaces `state.json` (atomic updates); (b) backup = mount/copy the DB; (c)
  device↔server calls use POST/PUT bodies — no query-string secrets; (e) Security table
  formatting fixed. (d) background-download foreground service still open.
- **Resolution:**
