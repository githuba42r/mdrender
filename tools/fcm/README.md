# FCM one-time setup (maintainer)

The Cloud Push feature routes a doorbell message through **one shared Firebase project**,
paid exactly once — by the maintainer. Every self-hosted operator inherits it from the
repo (the Android `google-services.json`) and the server image (the FCM service-account
credential), so **operators configure nothing**.

This directory contains the script that produces both credentials, and the walkthrough
behind it. **The script requires a Google browser login and can only be run once by the
maintainer.** It is not run by CI, operators, or the phone.

- `setup-fcm.sh` — one run produces both artifacts.
- This README — prereqs, the one browser step, what each artifact is, key rotation,
  re-run safety, and the JSON-only shortcut.

## What the script produces

| Artifact | Created by | Destination | Who uses it |
|---|---|---|---|
| `app/google-services.json` | `firebase apps:create` + `apps:sdkconfig` | committed to the Android repo | The Android FCM SDK, at build time — it registers the app in the shared project and lets the phone obtain an FCM token |
| `server/fcm-service-account.json` | `gcloud iam service-accounts keys create` | mounted into the server image | The server's FCM HTTP v1 credential (the `FCM_SERVER_KEY` env var points at it) |

Both files are written **relative to the current working directory**, so run the script
from the repo root:

```bash
cd <repo root>
./tools/fcm/setup-fcm.sh
```

The project id defaults to `mdrender-push`; override it with the `FIREBASE_PROJECT_ID`
environment variable if you already have a Firebase project you want to reuse:

```bash
FIREBASE_PROJECT_ID=my-existing-project ./tools/fcm/setup-fcm.sh
```

## Prereqs

All three are checked by the script up front (`command -v ...`), which exits with a
message if any is missing.

- **Firebase CLI** — the npm package `firebase-tools`:
  ```bash
  npm i -g firebase-tools
  ```
- **gcloud (Google Cloud SDK)** — install the Cloud SDK for your platform, e.g. on
  Debian/Ubuntu via the apt repository or the standalone installer; on macOS/Windows via
  the bundled installer. See https://cloud.google.com/sdk/docs/install.
- **jq** — JSON processor, from your package manager:
  ```bash
  apt install jq        # Debian/Ubuntu
  brew install jq       # macOS
  pacman -S jq          # Arch
  ```

## The one browser step (the only non-automatable part)

```bash
firebase login --no-localhost
```

The script stops here the first time. `firebase login --no-localhost` prints a URL and
opens Google's OAuth page in your browser (over SSH / headless you copy the URL into a
browser on the same Google account). Approve the app, then **paste the authorization code
back into the terminal**. This is the only step that requires a human — everything else in
the script is automatable.

**Token expiry / re-login.** The Firebase CLI token expires after a few hours of CLI use.
While a session is still valid the CLI refreshes it automatically on subsequent commands, so
you normally only notice at the next `firebase` invocation after a gap. When it does expire,
just re-run `firebase login --no-localhost`. (This is why the script calls it before the
Firebase commands.)

> Note: the script's `gcloud` commands run under your default gcloud credentials. If gcloud
> is not authenticated yet on this machine, authenticate it once with the same Google
> account first: `gcloud auth login`.

## What each artifact is

### `app/google-services.json` — the Android side (committed)

This file is **committed to the Android repo** (like any `google-services.json`). The
Android FCM SDK reads it at build time via the `com.google.gms.google-services` Gradle
plugin, registers the app in the shared Firebase project, and from then on the phone can
obtain an **FCM token**. That token is what the phone sends to the push server during QR
pairing so the server can reach it as a doorbell.

`FCM_SERVER_KEY` **needs no value on the phone** — sending is server-side only. The phone
only ever holds the Firebase SDK token.

### `server/fcm-service-account.json` — the server side (NEVER ships in the app)

This is the shared Firebase **service-account** credential the server uses to send FCM v1
messages (JWT RS256 → OAuth2 bearer → `messages:send`). It is **server-only**:

- Mounted into the server image. `server/docker-compose.example.yml` shows the wiring:
  ```yaml
  environment:
    FCM_SERVER_KEY: "/srv/fcm-service-account.json"
  volumes:
    - ./fcm-service-account.json:/srv/fcm-service-account.json:ro
  ```
- **Never ships in the app.** It is already gitignored (`*service-account*.json` in
  `.gitignore`), and the operator is warned here too: **do not commit this file, do not put
  it in the Android app, do not paste it into issues or chat.** Anyone holding it can send
  FCM messages in the shared project (worst case: a spoofed notification — message content
  is E2E-encrypted, see the design's Security section — but treat it as a credential).

## Key rotation

Rotate the server credential when you suspect it leaked or as routine hygiene:

1. List the current keys for the service account:
   ```bash
   gcloud iam service-accounts keys list \
     --iam-account "fcm-pusher@$PROJECT_ID.iam.gserviceaccount.com" \
     --project "$PROJECT_ID"
   ```
2. Delete the old key(s):
   ```bash
   gcloud iam service-accounts keys delete <KEY_ID> \
     --iam-account "fcm-pusher@$PROJECT_ID.iam.gserviceaccount.com" \
     --project "$PROJECT_ID"
   ```
3. Mint a replacement — either re-run the whole script (it overwrites
   `server/fcm-service-account.json` with a fresh key), or just:
   ```bash
   gcloud iam service-accounts keys create server/fcm-service-account.json \
     --iam-account "fcm-pusher@$PROJECT_ID.iam.gserviceaccount.com" \
     --project "$PROJECT_ID"
   ```
4. **Rebuild the server image** so the new credential is baked/mounted.

The old key stops working immediately; **no app change is needed** — the phone never holds
the service-account credential, only its own FCM token.

## Re-run safety (read this before re-running)

**Run from the repo root** — the script writes `app/google-services.json` and
`server/fcm-service-account.json` relative to your CWD, so a re-run from elsewhere writes
them to the wrong place.

The script is **mostly** safe to re-run, but **not fully idempotent**. Honestly:

- Idempotent by design: `firebase projects:create` and
  `gcloud iam service-accounts create` both carry `|| true` (they no-op if the resource
  already exists), and `gcloud services enable` is idempotent (it's a no-op once the API is
  enabled). `gcloud iam service-accounts keys create` is safe too, but it **mints a fresh
  key every run** — each re-run produces a new credential file, and the earlier key stays
  valid in GCP until you delete it (see Key rotation). Re-running does **not** duplicate
  projects, apps, or service accounts.
- **The exception:** `firebase apps:create android com.a42r.mdrender ...` has **no
  `|| true` and no exists-check**. The Android app is registered once; a re-run after that
  errors out ("app already exists") and, because of `set -e`, the script stops. This is
  expected. It is not a failure of your project — the app is already there.

  **One-line repair** — fetch the existing app id and re-run just the config step:
  ```bash
  APP_ID="$(firebase apps:list android --project "$PROJECT_ID" --json | jq -r '.results[0].appId')"
  firebase apps:sdkconfig android "$APP_ID" --project "$PROJECT_ID" > app/google-services.json
  ```
  That rewrites `app/google-services.json` from the existing app. Everything after the
  `apps:create` step is unaffected.

### JSON-only shortcut (no server credential)

The script produces **both** artifacts in one run. If you only need the Android side —
`app/google-services.json` — and don't yet need the server credential, you can **stop after
the `firebase apps:sdkconfig` step**. The `gcloud` steps that follow are server-side only
(enable the FCM v1 API and mint `server/fcm-service-account.json`); skip them for a
JSON-only run. You can come back and run the gcloud steps (or the whole script with the
repair one-liner above) later.

## Shared-project note

This is the one shared Firebase project. The maintainer pays for it exactly once, and every
self-hosted operator inherits it:

- the Android repo already carries `app/google-services.json` → every app install
  registers in the shared project automatically;
- the server image carries the service-account credential → operators mount it (or it is
  baked in) and point `FCM_SERVER_KEY` at it; they configure nothing.

Operators never run this script. The trade-off of sharing one credential across all
instances is acknowledged in the design: the FCM payload is an AES-GCM doorbell under a
key negotiated during QR pairing, so even another holder of the service-account credential
sees only ciphertext — and, crucially, no file names, paths, or download keys at all, since
those travel in a separately signed manifest fetched over HTTPS. The worst case is a spoofed
doorbell, never a content leak.
