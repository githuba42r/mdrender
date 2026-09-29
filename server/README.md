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
| `PUSH_PUBLIC_URL` | *(empty)* | Public base URL. Embedded in the pairing QR payload and the tool-enrolment verification URI. |
| `FCM_SERVER_KEY` | *(empty)* | Path to a Firebase service-account JSON (FCM HTTP v1). Empty ⇒ pushes are stored but not delivered. |
| `LISTEN_ADDR` | `:8080` | Bind address `host:port` (empty host = all interfaces). |
| `DB_PATH` | `/data/push/server.db` | SQLite database file. Holds the server keypair, OAuth clients, and devices. |
| `PUSH_STORAGE_DIR` | `/data/push` | Directory for uploaded file trees (`<push_id>/<file_id>/…`). |
| `LOGIN_MAX_ATTEMPTS` | `5` | Failed `/login` attempts before the client IP is locked out. |
| `LOGIN_LOCKOUT_SECONDS` | `300` | Lockout duration after too many failed logins. |
| `ENROL_TOKEN_TTL_HOURS` | `1` | Tool-enrolment key lifetime (the key shown at `/enrol/<eid>`). |
| `ENROL_SESSION_TTL_MINUTES` | `15` | Pairing-token lifetime; drives QR expiry. |
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

The browser UI also exposes `/devices` (list/delete), `/pushes` (all pushes),
and `/pending` (un-acked files), all behind the login session.

## Tool enrolment (CLI)

`tools/localsend-send/localsend-send.py` gains cloud-push subcommands in a
later task. The intended flow, which the server already implements:

```bash
# One-time: enrol this machine → prints client_id / client_secret
tools/localsend-send/localsend-send.py --enrol --server <url>

# Get an OAuth access token, then push files to a device by name
tools/localsend-send/localsend-send.py --oauth
tools/localsend-send/localsend-send.py --push <device-name> file.pdf
```

Server-side, enrolment is:
`POST /api/enrol/start` → open the returned `verification_uri`
(`/enrol/<enrolment_id>`) in a signed-in browser and read the key →
`POST /api/enrol` with the key → `{client_id, client_secret}` →
`POST /oauth/token` (client-credentials grant) → a Bearer `access_token` →
`POST /api/push` with that token to push files.

Enrolment keys expire after `ENROL_TOKEN_TTL_HOURS`; access tokens after
`ACCESS_TOKEN_TTL_SECONDS`.

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
