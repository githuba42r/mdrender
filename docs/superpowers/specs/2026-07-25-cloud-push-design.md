# Cloud Push — Design

## Goal

Push files (markdown, images, MP3s) from a Linux desktop to an Android phone off-LAN,
triggered by an AI agent via SSH. The phone receives files via a call-home pull model
with FCM as the doorbell.

## Overview

```
Desktop (agent/SSH)         Push Server (Docker)          Android Phone
       │                          │                            │
       │ POST /api/push           │                            │
       │ (X-API-Key: …)           │                            │
       │── file.md ──────────────►│                            │
       │                          │   stores file +            │
       │                          │   generates one-time token │
       │                          │                            │
       │                          │   FCM data message         │
       │                          │   { push_url,              │
       │                          │     downloads: {           │
       │                          │       "id": "token" } }    │
       │                          │ ──────────────────────────►│
       │                          │                            │
       │                          │   GET /api/push/{id}/      │
       │                          │   download?token=<token>   │
       │                          │◄───────────────────────────│
       │                          │ ── file bytes (streamed) ─►│
       │                          │                            │
       │                          │   importFileFromTemp()     │
       │                          │   → encrypted storage      │
```

## Push Server (Python, Docker)

### Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/push` | `X-API-Key` header | Upload files for push |
| POST | `/api/register-device` | none (trusted) | Register FCM token + device secret |
| GET | `/api/push/{file_id}/download` | `?token=<one-time>` | Phone downloads a file |
| GET | `/api/pending` | `?device_secret=<secret>` | Phone polls for pending pushes |
| GET | `/api/push/{push_id}/status` | `X-API-Key` header | Agent checks delivery status |
| DELETE | `/api/push/{file_id}` | `?token=<one-time>` | Acknowledge file received |
| GET | `/api/health` | none | Health check |

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PUSH_API_KEY` | — | Secret for authenticating file push requests (required) |
| `PUSH_STORAGE_DIR` | `/data/push` | Directory for staged files |
| `PUSH_FILE_TTL_HOURS` | `24` | Auto-purge unclaimed files after N hours |
| `FCM_SERVER_KEY` | — | Firebase server key for sending FCM messages (optional) |
| `PUSH_PUBLIC_URL` | — | Hostname sent in FCM payloads so phone knows where to download from |
| `LISTEN_ADDR` | `:8080` | Server listen address |

### Push flow (detail)

1. Agent calls `POST /api/push` with multipart file(s) and optional `target_folder`
   - Server validates `X-API-Key`
   - Stores each file to `<storage_dir>/<push_id>/<file_id>/<filename>`
   - Generates one-time download tokens per file (random UUIDs)
   - If `FCM_SERVER_KEY` is set and a device is registered, sends FCM data message:
     ```json
     {
       "push_url": "<PUSH_PUBLIC_URL>",
       "push_id": "uuid",
       "downloads": { "<file_id>": "<one-time-token>", ... },
       "file_count": 3,
       "total_bytes": 52428800
     }
     ```
   - Returns `{ push_id, file_ids[], total_bytes }`

2. File auto-purge:
   - After first successful download, file is deleted
   - After `PUSH_FILE_TTL_HOURS`, file is deleted regardless
   - Token is consumed on first use (replay rejects)

### Device registration

- Phone generates a **device secret** (random UUID) on first launch
- `POST /api/register-device` sends `{ device_secret, fcm_token }`
- Server stores the mapping. The device secret acts as the polling identity.
- Multiple registration attempts update the FCM token (tokens rotate)

### Polling

- `GET /api/pending?device_secret=<secret>` returns list of pushes with their metadata but without download tokens
- Download tokens are only delivered via FCM (or via a separate auth'd flow)

### Implementation notes

- Python with Flask or FastAPI, single Docker image
- File-backed state: `state.json` in storage dir with token/device/push mappings
- Graceful SIGTERM, logs to stdout
- Optional Cloudflare Tunnel config: `tunnel: <name>` in a `cloudflared.yaml`

## Android Changes

### New package: `cloudpush/`

| Class | Purpose |
|-------|---------|
| `PushServerConfig` | Holds hostname (optional) + device secret, persisted in preferences |
| `PushClient` | HTTP client using `java.net.HttpURLConnection` (no new dep) |
| `PushFcmService` | `FirebaseMessagingService` — always registered, handles data messages |
| `CloudPushManager` | Orchestrator — receives trigger, manages download queue with progress |
| `CloudPushWorker` | WorkManager periodic worker for polling (only if hostname configured) |

### Data flow

**FCM path (always on):**
- `PushFcmService.onMessageReceived()` receives data payload containing `push_url`, `downloads` mapping
- For each entry in `downloads`, calls `PushClient.downloadFile(push_url, file_id, token)` which streams to a temp file, then calls `FileRepository.importFileFromTemp()`
- Shows notification: "3 files received in Cloud Push"
- Files stored in a "Cloud Push" folder (via `FolderRepository.findOrCreateFolder`)

**Polling path (optional):**
- `PushClient.fetchPending(hostname, device_secret)` → list of push summaries
- For each pending push, downloads files individually
- `CloudPushWorker` schedules periodic checks (every 2h, WiFi-only, battery-not-low)
- Polling is disabled when hostname is empty in Settings

### Settings additions

New section "Cloud Push" in Settings:

- **Server URL** — text field, empty = polling disabled
- **Device ID** — auto-generated, read-only
- **Check Now** — button triggers immediate poll
- Status label showing last check time and files received

### Dependencies

```kotlin
// build.gradle.kts
implementation(platform("com.google.firebase:firebase-bom:33.0.0"))
implementation("com.google.firebase:firebase-messaging")

// top-level build.gradle.kts
plugins {
    id("com.google.gms.google-services") version "4.4.2"
}
```

### PushFcmService (`onMessageReceived`)

```kotlin
class PushFcmService : FirebaseMessagingService() {
    override fun onMessageReceived(message: RemoteMessage) {
        val data = message.data
        val pushUrl = data["push_url"] ?: return
        // push_url tells the phone what server to download from
        // — allows FCM payload to carry the correct host
        val downloadsJson = data["downloads"] ?: return
        // Parse file_id → download_token mapping
        // Launch CloudPushManager.download(pushUrl, downloads)
    }

    override fun onNewToken(token: String) {
        // Register with push server if hostname configured
    }
}
```

## Agent Integration

### `push-to-phone` script

```bash
#!/bin/bash
# Usage: push-to-phone file1 [file2 ...]
PUSH_URL="${PUSH_SERVER_URL:-https://push.example.com}"
curl -H "X-API-Key: $PUSH_API_KEY" \
     -F "file=@$1" \
     "$PUSH_URL/api/push"
```

The agent calls this from an SSH session. The script is a thin wrapper around `curl`.

## Security

| Layer | Mechanism |
|-------|-----------|
| Upload | `X-API-Key` header, env-configured on server |
| Download | One-time tokens, single-use, delivered via FCM only |
| Transport | TLS via Cloudflare Tunnel or direct (server-configured) |
| Rest storage | AES-256 encrypted on phone via existing `CryptoEngine` |
| File cleanup | Files purged post-download or after TTL |
| Device identity | Auto-generated UUID device secret |

## Open Questions (deferred)

- Multiple devices: single push server could support several phones (one per `device_secret`)
- Push from any folder: currently files land in a "Cloud Push" folder; the `target_folder` param could route to subfolders
