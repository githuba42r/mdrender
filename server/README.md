# Cloud Push Server

A small self-hosted push relay for the MDRender Android app. The server stores
incoming files and "rings the doorbell" on the target device via FCM (Firebase
Cloud Messaging); the app then pulls a signed manifest and the files straight
from the server. The FCM message is a constant-size encrypted trigger naming
only where to look — no file names, paths, or download keys — so FCM never sees
plaintext and a push can never overflow the 4 KB message limit.

See `docs/superpowers/specs/2026-07-25-cloud-push-design.md` for the full
endpoint and security tables.

## Quick start

### Docker (recommended)

```bash
cp docker-compose.example.yml docker-compose.yml
```

Edit `docker-compose.yml` and set three values:

| Variable | Set to |
|----------|--------|
| `SERVER_PASSWORD` | a strong password for the browser UI |
| `PUSH_PUBLIC_URL` | the public URL your server is reachable at, e.g. `https://push.example.com` |
| `FCM_SERVER_KEY` | path to your Firebase service-account JSON (see [FCM setup](#fcm-setup)) |

Then:

```bash
docker compose up
```

The container listens on `:8080` and keeps all state under the `/data/push`
volume (declared in the `Dockerfile`). Uploaded files, the SQLite DB, and the
server keypair all live there.

### Without Docker

From the repository root, with a Python venv (requires `server/requirements.txt`):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt

SERVER_PASSWORD=secret \
PUSH_PUBLIC_URL=http://localhost:8080 \
python -m server.run
```

`server/run.py` is the entrypoint: it builds the app, starts the retry/sweep
worker thread, and serves with waitress on `LISTEN_ADDR`. (The Docker image
uses the same `python -m server.run` as its `CMD`.)

## Configuration

All configuration is via environment variables. Defaults come from
`server/app/config.py`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `SERVER_PASSWORD` | *(none — required)* | Password for the browser UI (`/login`). |
| `PUSH_PUBLIC_URL` | *(empty)* | Public base URL. Embedded in the pairing QR payload and the tool-enrolment verification URI. On a slave it is also the URL the master calls back on. |
| `ROLE` | *(auto)* | `master` / `slave` / `standalone`. Empty ⇒ auto-detect from FCM availability. |
| `MASTER_URL` | *(empty)* | Federation master a slave enrols with (see [Example: federated slave](#example-federated-slave)). |
| `FEDERATION_ENABLED` | `true` | Master-side env gate for the federation endpoints; the Settings page then toggles `accept_new_slaves`. |
| `FCM_SERVER_KEY` | *(empty)* | Path to a Firebase service-account JSON (FCM HTTP v1). Empty ⇒ pushes are stored but not delivered. |
| `LISTEN_ADDR` | `:8080` | Bind address `host:port` (empty host = all interfaces). |
| `DB_PATH` | `/data/push/server.db` | SQLite database file. Holds the server keypair, OAuth clients, and devices. |
| `PUSH_STORAGE_DIR` | `/data/push` | Directory for uploaded file trees (`<push_id>/<file_id>/…`). |
| `LOGIN_MAX_ATTEMPTS` | `5` | Failed `/login` attempts before the client IP is locked out. |
| `LOGIN_LOCKOUT_SECONDS` | `300` | Lockout duration after too many failed logins. |
| `ENROL_TOKEN_TTL_HOURS` | `1` | Tool-enrolment lifetime (the whole `/enrol/<eid>` session). |
| `ENROL_SESSION_TTL_MINUTES` | `15` | Pairing-token lifetime; drives QR expiry. |
| `ENROL_CODE_TTL_SECONDS` | `60` | Lifetime of the short manual-entry enrolment code. |
| `ENROL_CODE_MAX_ATTEMPTS` | `5` | Wrong code guesses before the short code burns out. |
| `ACCESS_TOKEN_TTL_SECONDS` | `3600` | OAuth access-token lifetime for the CLI. |
| `PUSH_FILE_TTL_HOURS` | `24` | Uploaded files are purged after this many hours. |
| `PUSH_RETRY_COUNT` | `5` | FCM re-push attempts per file not acked within the retry window. |
| `PUSH_RETRY_INTERVAL_MINUTES` | `30` | Delay between FCM retries. |
| `DEVICE_TTL_DAYS` | `90` | Devices not seen in this long are swept. |

## Pairing a device

1. Open `http://<server>/login` in a browser and sign in with `SERVER_PASSWORD`.
   Failed logins are rate-limited and locked out (see `LOGIN_MAX_ATTEMPTS`).
2. Open `/pair`. The page shows an inline SVG QR code with the server URL and
   the token expiry.
3. Scan the QR with the MDRender Android app (Cloud Push → pair).

The QR encodes a JSON payload — `{v:1, server_url, pk, token, expires}` (see
`server/app/pairing.py`). `pk` is the server's public key; the token is
**single-use** and expires after `ENROL_SESSION_TTL_MINUTES` (default 15
minutes). The app registers with the server via `/api/register-device` and
receives a `device_auth` secret in return, which it keeps for future requests.

At registration the app also sends a 32-byte `push_key` it generated itself and
signs a proof binding that key to its device secret and public key. That key
becomes the doorbell key: the FCM trigger is AES-256-GCM encrypted to it, so a
captured doorbell reveals nothing and cannot be replayed to another device.

Fetching a manifest (`POST /api/push/<push_id>/manifest` with
`{"challenge_key": …}`) returns the **signed manifest as the response body**,
with the base64 RSA-SHA256 signature in the `X-Push-Manifest-Signature` header.
The body is byte-for-byte what was signed, so the app verifies exactly what it
received rather than re-encoding a parsed object.

The browser UI also exposes `/devices` (list/delete), `/clients` (enrolled CLI
clients, with revoke and registration instructions), `/pushes` (all pushes),
and `/pending` (un-acked files), all behind the login session.

Push clients can list the targets they may send to with
`GET /api/devices` (Bearer-token gated, like `POST /api/push`); it returns
`{"devices": [{name, registered_at, last_seen}]}` and never the device secret.
`localsend-send.py --list` uses it.

## Tool enrolment (CLI)

The browser `/clients` page lists the enrolled clients and shows the steps
below; it can also mint an enrolment for you with **Register a new client**.
The flow, which the server already implements:

```bash
# One-time: enrol this machine → writes ~/.config/mdrender/push-credentials.json
tools/localsend-send/localsend-send.py --enrol --server <url>

# Push files to a paired device by name (LAN first, cloud fallback)
tools/localsend-send/localsend-send.py --name <device-name> file.pdf
```

Server-side, enrolment is:
`POST /api/enrol/start` → open the returned `verification_uri`
(`/enrol/<enrolment_id>`) in a signed-in browser, then either:

- click **Complete registration** (session-gated `POST /enrol/<id>/approve`,
  which mints the OAuth client) and the CLI collects the credentials by polling
  `POST /api/enrol/complete`; or
- for a headless/remote machine, read the short **6-character code** shown on
  the page and type it into the CLI, which exchanges it at `POST /api/enrol`
  (case-insensitive; expires after `ENROL_CODE_TTL_SECONDS`; burns out after
  `ENROL_CODE_MAX_ATTEMPTS` wrong guesses; **New code** reissues it).

Then `POST /oauth/token` (client-credentials grant) → a Bearer `access_token` →
`POST /api/push` with that token to push files.

Enrolment sessions expire after `ENROL_TOKEN_TTL_HOURS`; access tokens after
`ACCESS_TOKEN_TTL_SECONDS`.

## Example: federated slave

A **slave** is a second instance of this same image that registers with a
**master** (see `federation.py` / `federation_client.py` / `federation_worker.py`).
Pushes landing on the slave are relayed to the master's outbox on every
heartbeat, and the master probes the slave at its `PUSH_PUBLIC_URL`, so devices
paired with either server end up under one FCM configuration.

```bash
cd server
cp docker-compose.federated.example.yml docker-compose.federated.yml
MASTER_URL=https://md.z42z.com \
PUSH_PUBLIC_URL=https://federated.z42z.com \
docker compose -f docker-compose.federated.yml up -d --build
```

The example keeps its own compose project name (`mdrender-federated`), its own
image tag, and its own volume (`./data-federated:/data/push`), so it runs beside
the master container (`mdrender-push`) without reconciling it. `IDENTITY_PROVIDER`,
`ENCRYPTION_MODE`, and `ROLE` come from the example file.

First boot and joining a master (design §5):

1. A fresh slave has no admin: the browser lands on **`/setup`**, where the
   first admin chooses the username and password.
2. Setup signs them in and lands them on **`/federation`**, which shows the
   master URL (editable) and a **Connect to a master** button
   (`POST /federation/connect`).
3. Connect signs a `connect` request with the slave's federation key and
   redirects the admin's browser to the master's **consent page**
   (`GET /federation/connect?…`): who is asking, the server/host plans that
   apply, and the terms - no login needed on the master for that page.
4. **Approve** mints a single-use code and sends the browser back to the
   slave's `/federation/callback?code=…&state=…`; **Decline** stops there.
5. The callback enrolls the slave (the code is exchanged over
   `POST /api/federation/enrol`) and lands the admin on the push list. The
   master's `/federation` then lists the slave (`active` / `down` /
   `deactivated` / `revoked`).

Nothing enrolls on its own: the worker only heartbeats a slave that is already
enrolled, and an enrolment without an operator-approved code is answered
`403 consent required` (a slave the master already consented to, unchanged and
unrevoked, may re-enrol code-less so a wiped DB recovers).

Two URL notes:

- The **master must be able to reach `PUSH_PUBLIC_URL`** — it calls
  `/api/federation/verify` during enrolment and probes it afterwards. A hostname
  behind a Cloudflare Tunnel therefore has to be **proxied** (orange cloud); a
  grey-clouded tunnel record only yields the tunnel CNAME and the callback fails.
  Registration failure is shown on the slave's `/federation` page, not swallowed.
- Changing `PUSH_PUBLIC_URL` or `MASTER_URL` after registration means the master
  still has the old `base_url`. Press **Connect to a master** again (or clear
  the slave's `federation_client` row first) so the master stores the new URL -
  approval covers the exact `(server_id, base_url, public_key)` triple.

## FCM setup

`FCM_SERVER_KEY` points at a Google **service-account** JSON file for the
shared Firebase project (FCM HTTP v1). The server mints a short-lived OAuth
token from the service account and calls the FCM v1 send API for every
delivery and retry.

Without it the server is still fully usable as a store-and-hold relay: it
accepts and stores pushes and exposes the download/status endpoints, but
nothing is delivered — the retry worker is a no-op when no FCM client is
configured (`server/run.py`, `server/app/retry.py`).

Use `tools/fcm/setup-fcm.sh` (see the spec) to create the Firebase project,
the `fcm-pusher` service account, and `fcm-service-account.json`. Mount that
file into the container and point `FCM_SERVER_KEY` at it.

## Backup

Everything that matters lives under `/data/push`:

- `server.db` — the SQLite DB holding the **server keypair**, OAuth clients,
  and device registrations.
- the push file tree under `PUSH_STORAGE_DIR` — uploaded files awaiting (or
  already) delivery.

Back up the whole `/data/push` tree (in Docker, the named volume or the
`./data` bind mount). **Losing `server.db` invalidates device trust**: devices
hold the server public key and their own `device_auth`, so a wiped DB forces
every device to re-pair.

## Reset / re-registration

If a device loses its `device_auth` (e.g. app data cleared, or the server DB
was restored from a backup made before the device registered), the device's
next status check hits `POST /api/device/status` and the server answers
`{error: "re-register"}` (HTTP 404) because the stored credential no longer
matches (see `server/app/app.py`).

Fix it by re-pairing through the normal QR flow (`/login` → `/pair`). If a
new device registers under the **same device name** with a different
`device_secret`, the server **displaces** the old registration — the 
previous device is deleted and the new one takes the name
(`server/app/pairing.py`, name-collision replace).

## Reference

- Full endpoint and security tables:
  `docs/superpowers/specs/2026-07-25-cloud-push-design.md`
- Server source: `server/app/` — `config.py`, `app.py` (routes),
  `pairing.py`, `retry.py`, `store.py`, `push_store.py`, `fcm.py`, `trigger.py`
  (doorbell + manifest builder/signing).
- Android app: consumes the same spec (pairing QR, doorbell decryption, signed
  manifest verification, download/ack endpoints).
