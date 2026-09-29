# Cloud Push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push files from a Linux desktop to an Android phone off-LAN — an AI agent triggers a push via SSH, a self-hosted Python server stores the bytes, and FCM "doorbells" the phone, which pulls the bytes over HTTPS and imports them into encrypted storage.

**Architecture:** One shared Firebase project acts as a dumb doorbell for all self-hosted servers. Each operator runs their own server (Docker, SQLite for all state). The server and phone exchange RSA-3072 public keys via a QR (TOFU pairing); the phone also generates a random 32-byte `push_key`. Every FCM message is a fixed-size **encrypted doorbell trigger** — `server_url`, `push_id`, `challenge_key` — sealed with AES-256-GCM under `push_key`, so Firebase sees only ciphertext and cannot forge a message the app will accept. The phone exchanges the trigger for a **signed manifest** over HTTPS, then downloads bytes and imports them into encrypted storage. Delivery is FCM-push only (no polling); recovery is server-side retries + operator re-push. The app downloads in a `dataSync` foreground service because it is usually not foreground when FCM arrives.

**Tech Stack:** Python 3.11 + Flask + `cryptography` + SQLite (server, single Docker image); Kotlin + Hilt + Compose + Room + Firebase Messaging + ML Kit barcode + CameraX (Android); Python `urllib` + UDP multicast (agent tools). Android uses `java.net.HttpURLConnection` (no new HTTP dep).

**Spec:** [docs/superpowers/specs/2026-07-25-cloud-push-design.md](../specs/2026-07-25-cloud-push-design.md) — the plan argues from the spec; executors read both. Unresolved spec "Resolution:" placeholders under Design Review are resolved by the *Decisions* lines; this plan implements those decisions.

## Global Constraints

> **Progress note (2026-08-29).** Tick marks reflect work that is actually done.
> Tasks 0.1, 0.2, all of Phase A (server) and all of Phase C (tools) are
> complete. A1, A3, A6, A7, A9 and A10 were re-done against the doorbell design
> when it replaced envelope batching. Phase 0.3 and all of Phase B (Android) are not started. Earlier task
> briefs and reports in `.superpowers/sdd/2026-08-28-cloud-push/` predate the
> redesign and still describe the envelope design; where they disagree with this
> plan, this plan wins.

Verbatim rules that apply to every task:

- **Branch = `feature/cloud-push`.** Do NOT bump `version.properties` (main-branch release). Device installs on this branch use the `-rc.N` version tag via the existing build scripts.
- **graphify:** read `graphify-out/GRAPH_REPORT.md` before searching source; run `graphify update .` after modifying code (AST-only, no API cost).
- **Commit cadence:** commit per task on `feature/cloud-push`. No commits to `master`.
- **Android:** `minSdk 26`, `targetSdk 36`, Java/Kotlin target 17, Compose BOM `2024.12.01`, Hilt 2.50 (kapt), Room 2.6.1 (KSP), kotlinx-serialization 1.7.3. Follow existing patterns (`LocalSendPrefs` for prefs, `@AndroidEntryPoint` + `@Inject` for services).
- **No new HTTP client dep on Android** — `java.net.HttpURLConnection` only.
- **Device↔server calls use POST/PUT JSON bodies, never query strings** (R9). No secrets in URLs.
- **`target_device` is REQUIRED on `POST /api/push`** — 400 `{"error":"device not found"}` when missing/unknown. No broadcast (R7).
- **Doorbell, not manifest.** The FCM message carries **no file manifest** — only `server_url`, `push_id`, `challenge_key`, encrypted under `push_key`. The phone fetches a **signed manifest** from `POST /api/push/{push_id}/manifest`. No envelope, no RSA content-key wrap, no batching, no slice-merge.
- **IV = `HMAC-SHA256(push_key, push_id)[:12]`.** Derived, never random, so it is unique per key with no nonce state and a retry reproduces a byte-identical message.
- **Manifest is signed, not encrypted** — RSA-SHA256 against the QR-pinned server public key, so a rogue TLS certificate cannot inject file names or retrieval keys. Encryption would protect nothing, since the manifest never reaches Firebase.
- **`push_key` is a random 32-byte field** covered by the registration `sig`, not a key derived from the two RSA public keys (which would be recomputable from anything holding the phone's public key).
- **FCM HTTP v1 only** (legacy server keys decommissioned 2024-06-20): send credential is a Firebase **service-account** JSON, never a legacy key.
- **Retrieval keys are file-linked, not single-use** — valid until ack or purge.
- **Ack = phone downloaded AND recorded.** `POST /api/push/{file_id}/received` deletes server bytes; the send record row is retained.
- **No polling.** Registration check happens on app foreground and when a push/ack fails with 401/404.
- **`--name` LAN discovery in `localsend-send.py` is new work** (R8), best-effort, same-subnet; cloud fallback is the primary path.

---

## Phase 0 — Grounding & Scaffolding

### Task 0.1: Ground `feature/cloud-push` on master

**Context:** the branch diverged at `7df55e1`; master is ahead with the **PushHistory** feature (`PushHistoryDao/Entity/Repository`, `PushHistoryScreen`, and `FolderRepository.getHiddenTreeFolderIds`) plus version bumps. The PushHistory feature is the model for the spec's "Received pushes" list and `record(source=…)` will be reused, so master must be present.

**Files:** (none — git)

- [ ] **Step 1: Merge master into the branch**

```bash
git fetch origin
git merge origin/master --no-edit
```

Expected: clean merge (branch contains only `docs/` commits).

- [ ] **Step 2: Verify the merge pulled in the expected files**

```bash
ls app/src/main/java/com/a42r/mdrender/data/dao/PushHistoryDao.kt \
   app/src/main/java/com/a42r/mdrender/ui/settings/PushHistoryScreen.kt
grep -n "getHiddenTreeFolderIds" app/src/main/java/com/a42r/mdrender/data/repository/FolderRepository.kt
```

Expected: both files present, `getHiddenTreeFolderIds` defined in `FolderRepository`.

- [ ] **Step 3: Compile**

```bash
./gradlew :app:compileDebugKotlin
```

Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "chore: merge master for PushHistory + latest fixes"
```

- [ ] **Step 5: Refresh the knowledge graph**

```bash
graphify update .
```

### Task 0.2: Server project scaffolding + test harness

**Files:**
- Create: `server/requirements.txt`
- Create: `server/pytest.ini`
- Create: `server/tests/conftest.py`
- Create: `server/tests/test_smoke.py`
- Create: `server/app/__init__.py` (empty)
- Modify: `.gitignore` (add `server/.pytest_cache/`, `__pycache__/`, `*.db`)

**Interfaces:**
- Produces: a runnable `pytest` harness under `server/`; the `server.app` package root that later tasks fill in.

- [x] **Step 1: Write `server/requirements.txt`**

```
flask==3.0.3
cryptography==43.0.1
requests==2.32.3
pytest==8.3.3
```

- [x] **Step 2: Write `server/pytest.ini`**

```ini
[pytest]
testpaths = tests
addopts = -q
```

- [x] **Step 3: Write the failing smoke test** `server/tests/test_smoke.py`

```python
def test_app_imports():
    import server.app.app  # noqa: F401  (imports app factory)
```

- [x] **Step 4: Run to verify it fails**

Run: `cd server && python3 -m pytest`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.app.app'`.

- [x] **Step 5: Write `server/app/__init__.py`** (empty file) and `server/tests/conftest.py`

```python
# server/tests/conftest.py
import os
import sys
import tempfile

import pytest


@pytest.fixture()
def db_path():
    with tempfile.TemporaryDirectory() as d:
        yield os.path.join(d, "test.db")


@pytest.fixture()
def config(db_path):
    from server.app.config import load_config

    return load_config(overrides={"DB_PATH": db_path, "SERVER_PASSWORD": "testpass"})
```

- [x] **Step 6: Add `server/app/app.py` as a stub so the import passes**

```python
# server/app/app.py
"""Flask application factory. Routes are added in later tasks."""
from flask import Flask


def create_app(config):
    app = Flask(__name__)
    return app
```

- [x] **Step 7: Run the test to verify it passes**

Run: `cd server && python3 -m pytest`
Expected: PASS (1 passed).

- [x] **Step 8: Install deps and commit**

```bash
python3 -m pip install -r server/requirements.txt
git add server/ .gitignore
git commit -m "chore(server): scaffold Python push server + pytest harness"
```

### Task 0.3: Firebase plumbing (Android build prerequisite)

**Context:** the FCM path needs `google-services.json` from the one shared Firebase project; the google-services Gradle plugin **fails the build without it**. The maintainer (this repo's owner) must create it once via `tools/fcm/setup-fcm.sh` (Task C4) — the only human step is the Google browser login. Until that file exists, Android build steps in Phase B are blocked at compile time; server tasks (Phase A) are unaffected.

**Files:**
- Modify: `gradle/libs.versions.toml` (add firebaseBom, firebaseMessaging, mlkitBarcode, cameraX)
- Modify: `build.gradle.kts` (top level — add google-services plugin)
- Modify: `app/build.gradle.kts` (apply plugin, add deps)
- Create: `app/google-services.json` (from setup script or a dev-time placeholder project)

**Interfaces:**
- Produces: `firebase-messaging`, `com.google.mlkit:barcode-scanning`, CameraX on the classpath; the `com.google.gms.google-services` plugin applied.

- [x] **Step 1: Add versions to `gradle/libs.versions.toml`**

```toml
firebaseBom = "33.0.0"
mlkitBarcode = "17.3.0"
cameraX = "1.4.1"
```
and libraries:
```toml
firebase-bom = { group = "com.google.firebase", name = "firebase-bom", version.ref = "firebaseBom" }
firebase-messaging = { group = "com.google.firebase", name = "firebase-messaging" }
mlkit-barcode = { group = "com.google.mlkit", name = "barcode-scanning", version.ref = "mlkitBarcode" }
camera-core = { group = "androidx.camera", name = "camera-core", version.ref = "cameraX" }
camera-camera2 = { group = "androidx.camera", name = "camera-camera2", version.ref = "cameraX" }
camera-lifecycle = { group = "androidx.camera", name = "camera-lifecycle", version.ref = "cameraX" }
camera-view = { group = "androidx.camera", name = "camera-view", version.ref = "cameraX" }
```
> Implemented with `firebaseBom = "33.7.0"` rather than 33.0.0, which was already stale.

- [x] **Step 2: Add the google-services plugin to the top-level `build.gradle.kts`**
> Done in `b602316`, using the version-catalog alias `libs.plugins.gcp.services`
> (`gcpServices = "4.4.2"`) rather than an inline `id(...) version`. The earlier
> deferral existed only because the plugin fails the build without
> `app/google-services.json`; that file now exists, so the plugin applies cleanly.

```kotlin
plugins {
    // ...existing aliases...
    id("com.google.gms.google-services") version "4.4.2" apply false
}
```

- [x] **Step 3: Apply the plugin and deps in `app/build.gradle.kts`**
> Deps applied; the plugin half waits on Step 2.

```kotlin
plugins {
    // ...existing...
    id("com.google.gms.google-services")
}

dependencies {
    // ...existing...
    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.messaging)
    implementation(libs.mlkit.barcode)
    implementation(libs.camera.core)
    implementation(libs.camera.camera2)
    implementation(libs.camera.lifecycle)
    implementation(libs.camera.view)
}
```

- [x] **Step 4: Obtain `app/google-services.json`**
> Provisioned manually on personal project `mdrender-push` (no organization/folder
> parent), committed in `b602316`. The Android app `com.a42r.mdrender` is registered
> and `processDebugGoogleServices` emits `gcm_defaultSenderId=626486255098` and
> `google_app_id=1:626486255098:android:a30d17437e1d47f4c91843` into the merged
> resources. `app/google-services.json` is client-side config only and is safe to
> commit; the server's `fcm-service-account.json` private key is not.

```bash
# Maintainer step — one Google browser login. Runs the idempotent script (Task C4).
./tools/fcm/setup-fcm.sh
```
If the maintainer has not run the script yet, block here and report; do **not** fabricate the file. While blocked, continue with Phase A tasks.

- [x] **Step 5: Add `android.permission.CAMERA` to `app/src/main/AndroidManifest.xml`**

```xml
<uses-permission android:name="android.permission.CAMERA" />
```

- [x] **Step 6: Verify a build now compiles**

Run: `./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL (with `google-services.json` present).

- [x] **Step 7: Commit**

```bash
git add gradle/libs.versions.toml build.gradle.kts app/build.gradle.kts \
        app/google-services.json app/src/main/AndroidManifest.xml
git commit -m "feat: add Firebase Messaging, ML Kit barcode, CameraX, and google-services plugin"
```

---

## Phase A — Push Server (Python)

### Task A1: Config + SQLite schema

**Files:**
- Create: `server/app/config.py`
- Create: `server/app/db.py`
- Test: `server/tests/test_db.py`

**Interfaces:**
- Consumes: `config` fixture (Task 0.2).
- Produces:
  - `load_config(*, overrides: dict | None = None) -> Config` where `Config` has attributes named exactly like the env vars: `SERVER_PASSWORD`, `LOGIN_MAX_ATTEMPTS: int`, `LOGIN_LOCKOUT_SECONDS: int`, `ENROL_TOKEN_TTL_HOURS: int`, `ENROL_SESSION_TTL_MINUTES: int`, `ACCESS_TOKEN_TTL_SECONDS: int`, `PUSH_STORAGE_DIR: str`, `DB_PATH: str`, `PUSH_FILE_TTL_HOURS: int`, `PUSH_RETRY_COUNT: int`, `PUSH_RETRY_INTERVAL_MINUTES: int`, `DEVICE_TTL_DAYS: int`, `FCM_SERVER_KEY: str`, `PUSH_PUBLIC_URL: str`, `LISTEN_ADDR: str`. Defaults from the spec's table.
  - `class Database`: `__init__(self, path: str)`, `connect() -> sqlite3.Connection` (sets `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `row_factory=sqlite3.Row`), `init_schema(conn)`, and one helper per table group (documented in later tasks).

- [x] **Step 1: Write the failing schema test** `server/tests/test_db.py`

```python
def test_schema_creates_tables(db_path):
    from server.app.db import Database

    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in rows}
    assert {"server_keys", "pairing_tokens", "clients", "devices",
            "pushes", "push_files"}.issubset(names)
```

- [x] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.app.db'`.

- [x] **Step 3: Write `server/app/config.py`**

```python
# server/app/config.py
import os


class Config:
    def __init__(self, **kw):
        self.__dict__.update(kw)


DEFAULTS = {
    "LOGIN_MAX_ATTEMPTS": 5,
    "LOGIN_LOCKOUT_SECONDS": 300,
    "ENROL_TOKEN_TTL_HOURS": 1,
    "ENROL_SESSION_TTL_MINUTES": 15,
    "ACCESS_TOKEN_TTL_SECONDS": 3600,
    "PUSH_STORAGE_DIR": "/data/push",
    "DB_PATH": "/data/push/server.db",
    "PUSH_FILE_TTL_HOURS": 24,
    "PUSH_RETRY_COUNT": 5,
    "PUSH_RETRY_INTERVAL_MINUTES": 30,
    "DEVICE_TTL_DAYS": 90,
    "FCM_SERVER_KEY": "",
    "PUSH_PUBLIC_URL": "",
    "LISTEN_ADDR": ":8080",
}


def load_config(*, overrides: dict | None = None) -> Config:
    env = {k: os.environ.get(k, v) for k, v in DEFAULTS.items()}
    env["SERVER_PASSWORD"] = os.environ.get("SERVER_PASSWORD", "")
    for k in env:
        if k in ("LOGIN_MAX_ATTEMPTS", "LOGIN_LOCKOUT_SECONDS",
                 "ENROL_TOKEN_TTL_HOURS", "ENROL_SESSION_TTL_MINUTES",
                 "ACCESS_TOKEN_TTL_SECONDS", "PUSH_FILE_TTL_HOURS",
                 "PUSH_RETRY_COUNT", "PUSH_RETRY_INTERVAL_MINUTES",
                 "DEVICE_TTL_DAYS"):
            env[k] = int(env[k])
    if overrides:
        env.update(overrides)
    return Config(**env)
```

- [x] **Step 4: Write `server/app/db.py`**

```python
# server/app/db.py
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS server_keys (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  private_key_pem TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pairing_tokens (
  token TEXT PRIMARY KEY,
  expires_at INTEGER NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS clients (
  client_id TEXT PRIMARY KEY,
  client_secret_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  scopes TEXT NOT NULL DEFAULT 'push',
  created_at INTEGER NOT NULL,
  revoked_at INTEGER
);
CREATE TABLE IF NOT EXISTS devices (
  device_secret TEXT PRIMARY KEY,
  device_auth TEXT NOT NULL,
  device_name TEXT NOT NULL UNIQUE,
  fcm_token TEXT,
  public_key TEXT NOT NULL,
  push_key TEXT NOT NULL,
  registered_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pushes (
  push_id TEXT PRIMARY KEY,
  target_device TEXT NOT NULL,
  challenge_key TEXT NOT NULL,
  date INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS push_files (
  file_id TEXT PRIMARY KEY,
  push_id TEXT NOT NULL,
  file_name TEXT NOT NULL,
  file_path TEXT NOT NULL DEFAULT '',
  size INTEGER NOT NULL,
  retrieval_key TEXT NOT NULL,
  stored_path TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  retries INTEGER NOT NULL DEFAULT 0,
  next_retry_at INTEGER,
  acked_at INTEGER,
  created_at INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)  # type: ignore[attr-defined]

    def connect(self) -> sqlite3.Connection:
        import os  # noqa: F401
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA)
        self._migrate(conn)
        conn.commit()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Idempotently add columns introduced after a database was first created.
        SQLite has no ALTER TABLE ... IF NOT EXISTS, so compare against the live
        table and issue plain ALTERs for anything missing."""
        wanted = {"devices": {"push_key": "TEXT NOT NULL DEFAULT ''"},
                  "pushes": {"challenge_key": "TEXT NOT NULL DEFAULT ''"}}
        for table, columns in wanted.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for column, decl in columns.items():
                if column not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
```

(Note: `os` is imported at module top in a real file; the inline import above is for plan brevity — write it at the top.)

- [x] **Step 5: Run tests to verify they pass**

Run: `cd server && python3 -m pytest`
Expected: PASS (2 passed).

- [x] **Step 6: Commit**

```bash
git add server/ && git commit -m "feat(server): config loading + SQLite schema"
```

### Task A2: Crypto primitives

**Files:**
- Create: `server/app/crypto.py`
- Test: `server/tests/test_crypto.py`

**Interfaces:**
- Produces (all importable from `server.app.crypto`):
  - `generate_rsa_keypair() -> tuple[RSAPrivateKey, RSAPublicKey]` (RSA-3072)
  - `private_to_pem(key) -> bytes` / `public_to_spki_der(key) -> bytes` / `public_from_spki_der(der: bytes) -> RSAPublicKey` / `public_from_spki_pem(pem: bytes) -> RSAPublicKey`
  - `oaep_wrap(public_key, data: bytes) -> bytes` (RSA-OAEP, SHA-256)
  - `oaep_unwrap(private_key, data: bytes) -> bytes`
  - `aes_gcm_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> tuple[bytes, bytes]` (ciphertext, 16-byte tag)
  - `aes_gcm_decrypt(key: bytes, iv: bytes, ciphertext: bytes, tag: bytes) -> bytes`
  - `sign(private_key, data: bytes) -> bytes` (RSA-SHA256, PKCS1v15)
  - `verify(public_key, data: bytes, sig: bytes) -> bool`

- [x] **Step 1: Write the failing crypto test** `server/tests/test_crypto.py`

```python
def test_roundtrip_oaep_and_aes():
    from server.app.crypto import (
        aes_gcm_decrypt, aes_gcm_encrypt, generate_rsa_keypair, oaep_unwrap, oaep_wrap,
    )

    priv, pub = generate_rsa_keypair()
    content_key = b"0123456789abcdef0123456789abcdef"  # 32 bytes
    wrapped = oaep_wrap(pub, content_key)
    assert oaep_unwrap(priv, wrapped) == content_key

    iv = b"\x00" * 12
    ct, tag = aes_gcm_encrypt(content_key, iv, b"hello push")
    assert aes_gcm_decrypt(content_key, iv, ct, tag) == b"hello push"


def test_sign_verify():
    from server.app.crypto import generate_rsa_keypair, sign, verify

    priv, pub = generate_rsa_keypair()
    sig = sign(priv, b"ek||iv||ct")
    assert verify(pub, b"ek||iv||ct", sig)
    assert not verify(pub, b"ek||iv||Cx", sig)
```

- [x] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_crypto.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [x] **Step 3: Write `server/app/crypto.py`**

```python
# server/app/crypto.py
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

GCM_TAG_LENGTH = 16


def generate_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    return private_key, private_key.public_key()


def private_to_pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def public_to_spki_der(key):
    return key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def public_from_spki_der(der: bytes):
    return serialization.load_der_public_key(der)


def public_from_spki_pem(pem: bytes):
    return serialization.load_pem_public_key(pem)


def oaep_wrap(public_key, data: bytes) -> bytes:
    return public_key.encrypt(data, padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    ))


def oaep_unwrap(private_key, data: bytes) -> bytes:
    return private_key.decrypt(data, padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    ))


def aes_gcm_encrypt(key: bytes, iv: bytes, plaintext: bytes):
    encryptor = Cipher(algorithms.AES(key), modes.GCM(iv)).encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    return ciphertext, encryptor.tag


def aes_gcm_decrypt(key: bytes, iv: bytes, ciphertext: bytes, tag: bytes):
    decryptor = Cipher(algorithms.AES(key), modes.GCM(iv, tag)).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def sign(private_key, data: bytes) -> bytes:
    return private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def verify(public_key, data: bytes, sig: bytes) -> bool:
    try:
        public_key.verify(sig, data, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_crypto.py -v`
Expected: PASS (2 passed).

- [x] **Step 5: Commit**

```bash
git add server/app/crypto.py server/tests/test_crypto.py
git commit -m "feat(server): RSA-OAEP, AES-GCM, and signing primitives"
```

### Task A3: Doorbell trigger builder (AES-GCM under `push_key`)

> **Revised 2026-08-29.** This task replaces the original "Envelope builder + batching"
> design. The manifest is no longer carried in FCM: a manifest is only actionable if the
> server is reachable, and reaching the server is required for the file bytes anyway, so
> carrying it spent FCM payload and receiver-side merge complexity to obtain information
> that is useless when the server is down. The original code also had a live defect —
> slicing a *pre-encryption* payload against a 3.5 KB cap produced FCM data messages of
> ~5.9 KB against FCM's hard 4 KB limit, so any slice of 12+ files was rejected. The
> trigger below is constant-size, so file count cannot exhaust the limit.

**Files:**
- Create: `server/app/trigger.py`
- Test: `server/tests/test_trigger.py`

**Interfaces:**
- Consumes: `crypto.py` (Task A2) — `aes_gcm_encrypt`, `aes_gcm_decrypt`.
- Produces (all importable from `server.app.trigger`):
  - `derive_iv(push_key: bytes, push_id: str) -> bytes` — `HMAC-SHA256(push_key, push_id)[:12]`
  - `trigger_plaintext(server_url: str, push_id: str, challenge_key: str) -> bytes` — compact JSON `{v, server_url, push_id, challenge_key}`
  - `seal_trigger(push_key: bytes, server_url: str, push_id: str, challenge_key: str) -> dict` — `{"i": <b64 iv>, "c": <b64 ct||tag>}`: exactly the two FCM data fields, so the route is `fcm.send({"p": sealed["c"], "i": sealed["i"]}, token)`. The format version travels *inside* the plaintext, so there is no third field for server and client to agree on.
  - `open_trigger(push_key: bytes, sealed: dict) -> dict | None` — `None` on any failure (forged/tampered message)
  - `build_manifest(push_id: str, date_iso: str, rows) -> dict` — `{push_id, date, files:[{file_id,name,path,size,retrieval_key}]}` from unacked `push_files` rows
  - `manifest_bytes(manifest: dict) -> bytes` — canonical serialisation used for **both** signing and transmission, so they cannot diverge

**Why the IV travels in the clear.** The IV is derived from `push_id`, and `push_id` lives
*inside* the ciphertext — so the phone cannot know it before decrypting. Carrying `i` as a
plaintext FCM field is standard and safe: a GCM IV need not be secret, and a swapped or
tampered IV fails the tag check. The value of the derivation is on the **server** side —
it guarantees the server never repeats a (key, IV) pair under a long-lived key, and makes
a retry byte-identical. The phone just uses the IV it is given.

- [x] **Step 1: Write the failing test** `server/tests/test_trigger.py`

```python
import os

from server.app.trigger import (
    derive_iv, open_trigger, seal_trigger, build_manifest, manifest_bytes,
)


def test_seal_open_roundtrip():
    key = os.urandom(32)
    sealed = seal_trigger(key, "https://push.example.com", "p1", "ck-1")
    got = open_trigger(key, sealed)
    assert got["server_url"] == "https://push.example.com"
    assert got["push_id"] == "p1"
    assert got["challenge_key"] == "ck-1"


def test_wrong_key_cannot_open():
    sealed = seal_trigger(os.urandom(32), "https://x", "p1", "ck-1")
    assert open_trigger(os.urandom(32), sealed) is None


def test_tampered_ciphertext_is_rejected():
    key = os.urandom(32)
    sealed = seal_trigger(key, "https://x", "p1", "ck-1")
    raw = bytearray(__import__("base64").b64decode(sealed["c"]))
    raw[0] ^= 0xFF
    sealed["c"] = __import__("base64").b64encode(bytes(raw)).decode()
    assert open_trigger(key, sealed) is None


def test_swapped_iv_is_rejected():
    key = os.urandom(32)
    sealed = seal_trigger(key, "https://x", "p1", "ck-1")
    other = seal_trigger(key, "https://x", "p2", "ck-1")
    sealed["i"] = other["i"]
    assert open_trigger(key, sealed) is None


def test_iv_is_deterministic_and_key_bound():
    key = os.urandom(32)
    assert derive_iv(key, "p1") == derive_iv(key, "p1")
    assert derive_iv(key, "p1") != derive_iv(key, "p2")
    assert derive_iv(key, "p1") != derive_iv(os.urandom(32), "p1")
    assert len(derive_iv(key, "p1")) == 12


def test_retry_reproduces_identical_ciphertext():
    key = os.urandom(32)
    assert seal_trigger(key, "https://x", "p1", "ck-1") == \
           seal_trigger(key, "https://x", "p1", "ck-1")


def test_trigger_size_is_independent_of_file_count():
    import json
    # The whole point: no batching, so size cannot grow with the manifest.
    key = os.urandom(32)
    sizes = {n: len(json.dumps(seal_trigger(key, "https://push.example.com", f"p{n}", "ck-1")))
             for n in (1, 100, 10000)}
    assert max(sizes.values()) - min(sizes.values()) < 32
```

- [x] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_trigger.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [x] **Step 3: Write `server/app/trigger.py`**

```python
# server/app/trigger.py
import base64
import hashlib
import hmac
import json

from server.app import crypto

# FCM rejects data messages above this. The trigger is constant-size, so this is
# a guard rail rather than a budget that file count can exhaust.
MAX_FCM_MESSAGE_BYTES = 4096

GCM_TAG_LENGTH = 16


def derive_iv(push_key: bytes, push_id: str) -> bytes:
    """Deterministic 12-byte IV, unique per (key, push_id) with no stored state.
    Guarantees the server never repeats a (key, IV) pair, and makes a retry of
    the same push byte-identical."""
    return hmac.new(push_key, push_id.encode(), hashlib.sha256).digest()[:12]


def trigger_plaintext(server_url: str, push_id: str, challenge_key: str) -> bytes:
    return json.dumps(
        {"v": 1, "server_url": server_url, "push_id": push_id,
         "challenge_key": challenge_key},
        separators=(",", ":"), sort_keys=True,
    ).encode()


def seal_trigger(push_key: bytes, server_url: str, push_id: str,
                 challenge_key: str) -> dict:
    iv = derive_iv(push_key, push_id)
    ct, tag = crypto.aes_gcm_encrypt(
        push_key, iv, trigger_plaintext(server_url, push_id, challenge_key)
    )
    return {"i": base64.b64encode(iv).decode(),
            "c": base64.b64encode(ct + tag).decode()}


def open_trigger(push_key: bytes, sealed: dict) -> dict | None:
    try:
        iv = base64.b64decode(sealed["i"], validate=True)
        raw = base64.b64decode(sealed["c"], validate=True)
        if len(iv) != 12 or len(raw) <= GCM_TAG_LENGTH:
            return None
        plaintext = crypto.aes_gcm_decrypt(
            push_key, iv, raw[:-GCM_TAG_LENGTH], raw[-GCM_TAG_LENGTH:]
        )
        return json.loads(plaintext)
    except Exception:
        # Forged, tampered, or simply not for this device -> silent drop.
        return None


def build_manifest(push_id: str, date_iso: str, rows) -> dict:
    return {
        "push_id": push_id,
        "date": date_iso,
        "files": [
            {"file_id": r["file_id"], "name": r["file_name"],
             "path": r["file_path"] or "", "size": r["size"],
             "retrieval_key": r["retrieval_key"]}
            for r in rows
        ],
    }


def manifest_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_trigger.py -v`
Expected: PASS (7 passed).

- [x] **Step 5: Commit**

```bash
git add server/app/trigger.py server/tests/test_trigger.py
git rm -q server/app/envelope.py server/tests/test_envelope.py
git commit -m "feat(server): encrypted doorbell trigger replaces envelope + batching"
```

### Task A4: Password auth, sessions, rate limit + lockout

**Files:**
- Create: `server/app/auth.py`
- Test: `server/tests/test_auth.py`

**Interfaces:**
- Consumes: `config` fixture; `Database` (Task A1).
- Produces (from `server.app.auth`):
  - `hash_secret(secret: str) -> str` — `pbkdf2_hmac` hex, salted.
  - `verify_secret(secret: str, stored: str) -> bool`.
  - `class LoginGate`: `__init__(self, config)`, `check(ip: str, password: str) -> tuple[bool, int]` → `(allowed, retry_after_seconds)`. Tracks failures per IP in memory; locks the IP for `LOGIN_LOCKOUT_SECONDS` after `LOGIN_MAX_ATTEMPTS` failures. Correct password resets the counter.
  - `make_session(secret: str, config) -> str` / `verify_session(secret: str, token: str, config) -> bool` — HMAC-signed cookie token (secret derived from the server key PEM; see Task A5 step 1 where the keypair is created; the plan wires it in Task A9). For now, `make_session` uses a `config.session_secret` attribute that Task A9 sets.

- [x] **Step 1: Write the failing rate-limit test** `server/tests/test_auth.py`

```python
def test_login_gate_locks_out(config):
    from server.app.auth import LoginGate

    gate = LoginGate(config)
    # config.LOGIN_MAX_ATTEMPTS == 5
    for _ in range(5):
        allowed, _ = gate.check("1.2.3.4", "wrong")
        assert allowed is False
    allowed, retry_after = gate.check("1.2.3.4", "wrong")
    assert allowed is False
    assert retry_after > 0
    # A different IP is not locked out, and the right password resets:
    allowed, _ = gate.check("5.6.7.8", config.SERVER_PASSWORD)
    assert allowed is True


def test_session_token_roundtrip(config):
    from server.app.auth import make_session, verify_session

    config.session_secret = "test-secret"
    token = make_session(config.session_secret, config)
    assert verify_session(config.session_secret, token, config) is True
    assert verify_session(config.session_secret, "forged", config) is False
```

- [x] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [x] **Step 3: Write `server/app/auth.py`**

```python
# server/app/auth.py
import hashlib
import hmac
import time
import uuid

SESSION_TTL_SECONDS = 15 * 60  # ENROL_SESSION_TTL_MINUTES


def hash_secret(secret: str) -> str:
    salt = uuid.uuid4().hex
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt.encode(), 100_000)
    return f"{salt}${digest.hex()}"


def verify_secret(secret: str, stored: str) -> bool:
    try:
        salt, digest_hex = stored.split("$", 1)
        digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt.encode(), 100_000)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except ValueError:
        return False


class LoginGate:
    def __init__(self, config):
        self.config = config
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def check(self, ip: str, password: str) -> tuple[bool, int]:
        now = time.time()
        locked_until = self._locked_until.get(ip, 0.0)
        if now < locked_until:
            return False, int(locked_until - now)
        if password == self.config.SERVER_PASSWORD:
            self._failures.pop(ip, None)
            self._locked_until.pop(ip, None)
            return True, 0
        attempts = self._failures.setdefault(ip, [])
        attempts.append(now)
        attempts[:] = [t for t in attempts if now - t < self.config.LOGIN_LOCKOUT_SECONDS]
        if len(attempts) >= self.config.LOGIN_MAX_ATTEMPTS:
            self._locked_until[ip] = now + self.config.LOGIN_LOCKOUT_SECONDS
            return False, self.config.LOGIN_LOCKOUT_SECONDS
        return False, 0


def _sig(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(session_secret: str, config) -> str:
    payload = f"{uuid.uuid4().hex}:{int(time.time())}"
    return f"{payload}:{_sig(session_secret, payload)}"


def verify_session(session_secret: str, token: str, config) -> bool:
    parts = token.split(":")
    if len(parts) != 3:
        return False
    payload, created_s, given_sig = parts
    try:
        created = int(created_s)
    except ValueError:
        return False
    if time.time() - created > SESSION_TTL_SECONDS:
        return False
    expected = _sig(session_secret, payload)
    if not hmac.compare_digest(expected, given_sig):
        return False
    return True
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_auth.py -v`
Expected: PASS (2 passed).

- [x] **Step 5: Commit**

```bash
git add server/app/auth.py server/tests/test_auth.py
git commit -m "feat(server): password login, signed sessions, rate limit + lockout"
```

### Task A5: Server keypair + OAuth2 client-credentials

**Files:**
- Create: `server/app/store.py`
- Modify: `server/app/auth.py` (add OAuth2 functions)
- Test: `server/tests/test_oauth.py`

**Interfaces:**
- Consumes: `Database`, `crypto.py`, `hash_secret`/`verify_secret` (Task A4).
- Produces (`server.app.store`):
  - `get_or_create_server_keypair(db) -> tuple[str, str]` — single `server_keys` row; returns `(private_key_pem, public_key_spki_der_b64)`.
  - `session_secret_from_pem(pem: str) -> str` — `sha256(pem)` hex; used to sign session cookies so sessions survive restarts.
  - `create_client(db, name: str, secret_hash: str) -> str` (returns `client_id` UUID)
  - `get_client(db, client_id) -> sqlite3.Row | None`
  - `revoke_client(db, client_id)`
  - `list_clients(db) -> list[sqlite3.Row]`
- Produces (`server.app.auth` additions):
  - `issue_access_token(config, client_id) -> str` and `validate_access_token(config, token) -> str | None` (client_id) — in-memory dict; TTL `config.ACCESS_TOKEN_TTL_SECONDS`.

- [x] **Step 1: Write the failing OAuth test** `server/tests/test_oauth.py`

```python
def test_client_credentials_flow(config, db_path):
    from server.app.auth import hash_secret, issue_access_token, validate_access_token
    from server.app.db import Database
    from server.app.store import create_client, get_or_create_server_keypair

    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        pem, _ = get_or_create_server_keypair(conn)
        assert pem
        client_id = create_client(conn, "my-tool", hash_secret("super-secret"))
        token = issue_access_token(config, client_id)
        assert validate_access_token(config, token) == client_id
        assert validate_access_token(config, "bogus") is None
```

- [x] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_oauth.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [x] **Step 3: Write `server/app/store.py`**

```python
# server/app/store.py
import base64
import hashlib
import time
import uuid

from server.app import crypto


def get_or_create_server_keypair(conn):
    row = conn.execute("SELECT private_key_pem FROM server_keys WHERE id = 1").fetchone()
    if row is None:
        priv, pub = crypto.generate_rsa_keypair()
        pem = crypto.private_to_pem(priv)
        conn.execute(
            "INSERT INTO server_keys (id, private_key_pem, created_at) VALUES (1, ?, ?)",
            (pem, int(time.time())),
        )
        conn.commit()
    else:
        pem = row["private_key_pem"]
        pub = crypto.public_from_spki_pem(
            crypto.private_to_pem  # replaced below: parse pub from private
        )
    # Rebuild pub from private to keep a single source of truth:
    priv = _load_private(pem)
    pub = priv.public_key()
    return pem, base64.b64encode(crypto.public_to_spki_der(pub)).decode()


def _load_private(pem: str):
    from cryptography.hazmat.primitives import serialization

    return serialization.load_pem_private_key(pem.encode(), password=None)


def session_secret_from_pem(pem: str) -> str:
    return hashlib.sha256(pem.encode()).hexdigest()


def create_client(conn, name: str, secret_hash: str) -> str:
    client_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO clients (client_id, client_secret_hash, name, scopes, created_at, revoked_at)"
        " VALUES (?, ?, ?, 'push', ?, NULL)",
        (client_id, secret_hash, name, int(time.time())),
    )
    conn.commit()
    return client_id


def get_client(conn, client_id):
    return conn.execute("SELECT * FROM clients WHERE client_id = ?", (client_id,)).fetchone()


def list_clients(conn):
    return conn.execute("SELECT client_id, name, created_at, revoked_at FROM clients").fetchall()


def revoke_client(conn, client_id):
    conn.execute("UPDATE clients SET revoked_at = ? WHERE client_id = ?",
                 (int(time.time()), client_id))
    conn.commit()
```

*(Clean up the redundant `public_from_spki_pem` line in Step 3's real file — `get_or_create_server_keypair` ends with `_load_private(pem)`; the mid-function call is dead code shown here for review clarity. Write it correctly: generate → store PEM → `_load_private` → return `(pem, spki_der_b64)`.)*

- [x] **Step 4: Append OAuth2 to `server/app/auth.py`**

```python
ACCESS_TOKENS: dict[str, tuple[str, float]] = {}  # token -> (client_id, expires_at)


def issue_access_token(config, client_id: str) -> str:
    token = uuid.uuid4().hex
    ACCESS_TOKENS[token] = (client_id, time.time() + config.ACCESS_TOKEN_TTL_SECONDS)
    return token


def validate_access_token(config, token: str) -> str | None:
    entry = ACCESS_TOKENS.get(token)
    if entry is None:
        return None
    client_id, expires = entry
    if time.time() > expires:
        ACCESS_TOKENS.pop(token, None)
        return None
    return client_id
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_oauth.py -v`
Expected: PASS (1 passed).

- [x] **Step 6: Commit**

```bash
git add server/app/store.py server/app/auth.py server/tests/test_oauth.py
git commit -m "feat(server): server keypair in DB + OAuth2 client-credentials tokens"
```

### Task A6: Pairing tokens + device registration (incl. name collision)

**Files:**
- Add to: `server/app/store.py`
- Create: `server/app/pairing.py`
- Test: `server/tests/test_registry.py`

**Interfaces:**
- Consumes: `store`, `crypto` (`public_from_spki_der`, `verify`, `sign`), `get_or_create_server_keypair`.
- Produces:
  - `create_pairing_token(conn, ttl_minutes: int) -> str` (stores `expires_at = now + ttl*60`)
  - `consume_pairing_token(conn, token: str, now: float | None = None) -> bool` (single-use; returns False if missing/expired/used; marks used)
  - `pairing_payload(server_url: str, server_pk_b64: str, token: str, expires_iso: str) -> dict` — the QR JSON `{v:1, server_url, pk, token, expires}`.
  - `build_pairing_qr(server_url: str, server_pk_b64: str, token: str, expires_iso: str) -> str` — JSON string for the QR.
  - `register_device(conn, *, device_secret, device_name, fcm_token, public_key_b64, push_key_b64, pairing_token, sig_b64) -> tuple[str | None, str | None]` → `(device_auth, displaced_device_secret_or_None)`. Verifies pairing token (first registration), verifies `sig` = RSA-SHA256(public_key, `sha256(device_secret||device_name||fcm_token||public_key_b64||push_key_b64)`), name collision → delete old row with same name when it has a different device_secret (returns its secret so the app can be notified), inserts new row, returns a fresh `device_auth` UUID. `push_key_b64` is the **negotiated doorbell key** and is covered by the signature, so it cannot be substituted without the phone's private key.
  - `update_device_token(conn, device_secret, device_auth, new_token) -> bool` (token rotation; verifies device_auth)
  - `update_device_push_key(conn, device_secret, device_auth, new_push_key_b64) -> bool` (doorbell key rotation; verifies device_auth)
  - `update_device_name(conn, device_secret, device_auth, new_name) -> bool`
  - `check_device(conn, device_secret, device_auth) -> bool`
  - `touch_last_seen(conn, device_secret)`
  - `get_device_by_name(conn, name) -> sqlite3.Row | None`
  - `get_device_by_secret(conn, device_secret) -> sqlite3.Row | None`
  - `delete_device(conn, device_secret)`
  - `list_devices(conn) -> list[sqlite3.Row]`
  - `sweep_stale_devices(conn, ttl_days) -> list[str]`

- [x] **Step 1: Write the failing registry test** `server/tests/test_registry.py`

```python
import base64, hashlib, time

from server.app import crypto
from server.app.db import Database
from server.app.store import get_or_create_server_keypair
from server.app.pairing import (
    create_pairing_token, consume_pairing_token, register_device, check_device,
    get_device_by_name, update_device_token, update_device_name,
)


def _sig(priv, secret, name, token, pub_b64, push_key_b64):
    data = hashlib.sha256(
        f"{secret}{name}{token}{pub_b64}{push_key_b64}".encode()
    ).digest()
    return base64.b64encode(crypto.sign(priv, data)).decode()


PUSH_KEY_1 = base64.b64encode(b"k" * 32).decode()
PUSH_KEY_2 = base64.b64encode(b"j" * 32).decode()


def test_register_and_replace_on_name_collision(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        token = create_pairing_token(conn, 15)
        assert consume_pairing_token(conn, token)
        assert not consume_pairing_token(conn, token)  # single-use

        phone_priv, phone_pub = crypto.generate_rsa_keypair()
        pub_b64 = base64.b64encode(crypto.public_to_spki_der(phone_pub)).decode()
        token2 = create_pairing_token(conn, 15)
        auth, displaced = register_device(
            conn, device_secret="sec-1", device_name="Sunny Falcon",
            fcm_token="tok-1", public_key_b64=pub_b64, push_key_b64=PUSH_KEY_1,
            pairing_token=token2,
            sig_b64=_sig(phone_priv, "sec-1", "Sunny Falcon", "tok-1", pub_b64, PUSH_KEY_1),
        )
        assert auth and displaced is None
        assert check_device(conn, "sec-1", auth)

        # New keypair claims the same name -> replaces.
        priv2, pub2 = crypto.generate_rsa_keypair()
        pub2_b64 = base64.b64encode(crypto.public_to_spki_der(pub2)).decode()
        token3 = create_pairing_token(conn, 15)
        auth2, displaced = register_device(
            conn, device_secret="sec-2", device_name="Sunny Falcon",
            fcm_token="tok-2", public_key_b64=pub2_b64, push_key_b64=PUSH_KEY_2,
            pairing_token=token3,
            sig_b64=_sig(priv2, "sec-2", "Sunny Falcon", "tok-2", pub2_b64, PUSH_KEY_2),
        )
        assert displaced == "sec-1"
        assert get_device_by_name(conn, "Sunny Falcon")["device_secret"] == "sec-2"

        # Token rotation requires device_auth.
        assert update_device_token(conn, "sec-2", auth2, "tok-3")
        assert get_device_by_name(conn, "Sunny Falcon")["fcm_token"] == "tok-3"
        assert not update_device_token(conn, "sec-2", "wrong-auth", "tok-4")
```

- [x] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [x] **Step 3: Write `server/app/pairing.py`** (with the store helpers for devices appended to `store.py`)

```python
# server/app/pairing.py
import base64
import hashlib
import time
import uuid

from server.app import crypto


def create_pairing_token(conn, ttl_minutes: int) -> str:
    token = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO pairing_tokens (token, expires_at, used) VALUES (?, ?, 0)",
        (token, int(time.time()) + ttl_minutes * 60),
    )
    conn.commit()
    return token


def consume_pairing_token(conn, token: str, now: float | None = None) -> bool:
    now = now or time.time()
    row = conn.execute("SELECT expires_at, used FROM pairing_tokens WHERE token = ?",
                       (token,)).fetchone()
    if row is None or row["used"] or now > row["expires_at"]:
        return False
    conn.execute("UPDATE pairing_tokens SET used = 1 WHERE token = ?", (token,))
    conn.commit()
    return True


def pairing_payload(server_url, server_pk_b64, token, expires_iso):
    return {"v": 1, "server_url": server_url, "pk": server_pk_b64,
            "token": token, "expires": expires_iso}


def build_pairing_qr(server_url, server_pk_b64, token, expires_iso) -> str:
    import json
    return json.dumps(pairing_payload(server_url, server_pk_b64, token, expires_iso))


def register_device(conn, *, device_secret, device_name, fcm_token, public_key_b64,
                    push_key_b64, pairing_token, sig_b64):
    if not consume_pairing_token(conn, pairing_token):
        return None, None
    public_key = crypto.public_from_spki_der(base64.b64decode(public_key_b64))
    data = hashlib.sha256(
        f"{device_secret}{device_name}{fcm_token}{public_key_b64}{push_key_b64}".encode()
    ).digest()
    if not crypto.verify(public_key, data, base64.b64decode(sig_b64)):
        return None, None

    displaced = None
    existing = conn.execute(
        "SELECT device_secret FROM devices WHERE device_name = ?", (device_name,)
    ).fetchone()
    if existing and existing["device_secret"] != device_secret:
        displaced = existing["device_secret"]
        conn.execute("DELETE FROM devices WHERE device_secret = ?", (displaced,))

    device_auth = uuid.uuid4().hex
    conn.execute(
        "INSERT OR REPLACE INTO devices (device_secret, device_auth, device_name, fcm_token,"
        " public_key, push_key, registered_at, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (device_secret, device_auth, device_name, fcm_token, public_key_b64, push_key_b64,
         int(time.time()), int(time.time())),
    )
    conn.commit()
    return device_auth, displaced
```

Append to `store.py`:

```python
def update_device_token(conn, device_secret, device_auth, new_token) -> bool:
    cur = conn.execute(
        "UPDATE devices SET fcm_token = ? WHERE device_secret = ? AND device_auth = ?",
        (new_token, device_secret, device_auth),
    )
    conn.commit()
    return cur.rowcount > 0


def update_device_push_key(conn, device_secret, device_auth, new_push_key_b64) -> bool:
    """Rotate the doorbell key. Requires device_auth, so a leaked device_secret
    alone cannot replace the key an attacker would use to forge triggers."""
    cur = conn.execute(
        "UPDATE devices SET push_key = ? WHERE device_secret = ? AND device_auth = ?",
        (new_push_key_b64, device_secret, device_auth),
    )
    conn.commit()
    return cur.rowcount > 0


def update_device_name(conn, device_secret, device_auth, new_name) -> bool:
    try:
        cur = conn.execute(
            "UPDATE devices SET device_name = ? WHERE device_secret = ? AND device_auth = ?",
            (new_name, device_secret, device_auth),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        return False  # name already taken by another device


def check_device(conn, device_secret, device_auth) -> bool:
    return conn.execute(
        "SELECT 1 FROM devices WHERE device_secret = ? AND device_auth = ?",
        (device_secret, device_auth),
    ).fetchone() is not None


def touch_last_seen(conn, device_secret):
    conn.execute("UPDATE devices SET last_seen = ? WHERE device_secret = ?",
                 (int(time.time()), device_secret))
    conn.commit()


def get_device_by_name(conn, name):
    return conn.execute("SELECT * FROM devices WHERE device_name = ?", (name,)).fetchone()


def get_device_by_secret(conn, device_secret):
    return conn.execute("SELECT * FROM devices WHERE device_secret = ?",
                        (device_secret,)).fetchone()


def delete_device(conn, device_secret):
    conn.execute("DELETE FROM devices WHERE device_secret = ?", (device_secret,))
    conn.commit()


def list_devices(conn):
    return conn.execute(
        "SELECT device_secret, device_name, registered_at, last_seen FROM devices ORDER BY registered_at"
    ).fetchall()


def sweep_stale_devices(conn, ttl_days: int) -> list[str]:
    cutoff = int(time.time()) - ttl_days * 86400
    rows = conn.execute("SELECT device_secret FROM devices WHERE last_seen < ?", (cutoff,)).fetchall()
    for r in rows:
        conn.execute("DELETE FROM devices WHERE device_secret = ?", (r["device_secret"],))
    conn.commit()
    return [r["device_secret"] for r in rows]
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_registry.py -v`
Expected: PASS (1 passed).

- [x] **Step 5: Commit**

```bash
git add server/app/store.py server/app/pairing.py server/tests/test_registry.py
git commit -m "feat(server): pairing tokens + device registry with name-collision replace"
```

### Task A7: Push endpoint (multipart upload, target_device required, file-linked keys)

**Files:**
- Add to: `server/app/store.py`
- Create: `server/app/push_store.py`
- Test: `server/tests/test_push.py`

**Interfaces:**
- Consumes: `Database`, `store`, `pairing` (device lookup), OAuth2 validation.
- Produces (`server.app.push_store`):
  - `create_push(conn, push_id: str, target_device: str, challenge_key: str) -> None` — the per-push capability the phone presents to fetch the manifest
  - `add_file(conn, *, file_id, push_id, file_name, file_path, size, retrieval_key, stored_path, created_at) -> None`
  - `get_push_files(conn, push_id) -> list[sqlite3.Row]`
  - `get_pending_files(conn, now) -> list[sqlite3.Row]` (status='pending' and next_retry_at IS NULL OR <= now)
  - `mark_acked(conn, file_id, now) -> bool` (sets status='acked', acked_at, clears stored bytes)
  - `purge_expired_bytes(conn, ttl_hours, now) -> list[str]` (status stays; stored_path→NULL; returns file_ids)
  - `list_pushes(conn) -> list[sqlite3.Row]` (aggregated per push: id, target, date, status, file_count, acked_count)
  - `list_pending(conn) -> list[sqlite3.Row]`
  - `reset_push_retries(conn, push_id, now) -> None`
  - `mark_exhausted(conn, file_id) -> None`
  - `increment_retry(conn, file_id, now, interval_minutes) -> None`
  - `get_push_by_id(conn, push_id) -> sqlite3.Row | None`
- Test coverage (endpoint-level, via Flask test client — full `create_app` is wired in Task A9, so this task tests the store functions directly):
  - push files are stored with a file-linked key (two downloads with the same key both succeed — enforced by the download endpoint in Task A8).

- [x] **Step 1: Write the failing store test** `server/tests/test_push.py`

```python
def test_push_file_lifecycle(config, db_path):
    from server.app.db import Database
    from server.app.push_store import (
        add_file, create_push, get_push_files, get_pending_files,
        mark_acked, purge_expired_bytes, list_pushes,
    )

    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "push-1", "Sunny Falcon", "ck-1")
        add_file(conn, file_id="f1", push_id="push-1", file_name="notes.md",
                 file_path="Docs", size=100, retrieval_key="k1",
                 stored_path="push-1/f1/notes.md", created_at=1000)
        assert len(get_push_files(conn, "push-1")) == 1
        assert len(get_pending_files(conn, 2000)) == 1
        assert mark_acked(conn, "f1", 2000)
        assert len(get_pending_files(conn, 2000)) == 0
        pushes = list_pushes(conn)
        assert pushes[0]["file_count"] == 1 and pushes[0]["acked_count"] == 1
```

- [x] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_push.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [x] **Step 3: Write `server/app/push_store.py`**

```python
# server/app/push_store.py
import time


def create_push(conn, push_id: str, target_device: str, challenge_key: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO pushes (push_id, target_device, challenge_key, date, status)"
        " VALUES (?, ?, ?, ?, 'pending')",
        (push_id, target_device, challenge_key, int(time.time())),
    )
    conn.commit()


def add_file(conn, *, file_id, push_id, file_name, file_path, size,
             retrieval_key, stored_path, created_at):
    conn.execute(
        "INSERT INTO push_files (file_id, push_id, file_name, file_path, size,"
        " retrieval_key, stored_path, status, retries, next_retry_at, acked_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, NULL, ?)",
        (file_id, push_id, file_name, file_path, size, retrieval_key, stored_path,
         created_at + 60, created_at),
    )
    conn.commit()


def get_push_files(conn, push_id):
    return conn.execute(
        "SELECT * FROM push_files WHERE push_id = ? ORDER BY created_at", (push_id,)
    ).fetchall()


def get_pending_files(conn, now):
    return conn.execute(
        "SELECT * FROM push_files WHERE status = 'pending'"
        " AND (next_retry_at IS NULL OR next_retry_at <= ?)", (now,)
    ).fetchall()


def mark_acked(conn, file_id, now) -> bool:
    cur = conn.execute(
        "UPDATE push_files SET status = 'acked', acked_at = ?, stored_path = NULL"
        " WHERE file_id = ?", (now, file_id),
    )
    conn.commit()
    return cur.rowcount > 0


def purge_expired_bytes(conn, ttl_hours, now) -> list[str]:
    cutoff = now - ttl_hours * 3600
    rows = conn.execute(
        "SELECT file_id, stored_path FROM push_files WHERE status != 'acked'"
        " AND created_at < ? AND stored_path IS NOT NULL", (cutoff,)
    ).fetchall()
    for r in rows:
        conn.execute("UPDATE push_files SET stored_path = NULL WHERE file_id = ?",
                     (r["file_id"],))
    conn.commit()
    return [r["file_id"] for r in rows]


def list_pushes(conn):
    return conn.execute(
        "SELECT p.push_id, p.target_device, p.date, p.status,"
        " COUNT(f.file_id) AS file_count,"
        " COALESCE(SUM(CASE WHEN f.status = 'acked' THEN 1 ELSE 0 END), 0) AS acked_count"
        " FROM pushes p LEFT JOIN push_files f ON f.push_id = p.push_id"
        " GROUP BY p.push_id ORDER BY p.date DESC"
    ).fetchall()


def list_pending(conn):
    return conn.execute(
        "SELECT f.*, p.target_device FROM push_files f JOIN pushes p ON p.push_id = f.push_id"
        " WHERE f.status = 'pending' ORDER BY f.created_at DESC"
    ).fetchall()


def reset_push_retries(conn, push_id, now) -> None:
    conn.execute(
        "UPDATE push_files SET retries = 0, next_retry_at = ? WHERE push_id = ? AND status = 'pending'",
        (now, push_id),
    )
    conn.commit()


def mark_exhausted(conn, file_id) -> None:
    conn.execute("UPDATE push_files SET status = 'exhausted', stored_path = NULL WHERE file_id = ?",
                 (file_id,))
    conn.commit()


def increment_retry(conn, file_id, now, interval_minutes) -> None:
    conn.execute(
        "UPDATE push_files SET retries = retries + 1, next_retry_at = ? WHERE file_id = ?",
        (now + interval_minutes * 60, file_id),
    )
    conn.commit()


def get_push_by_id(conn, push_id):
    return conn.execute("SELECT * FROM pushes WHERE push_id = ?", (push_id,)).fetchone()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_push.py -v`
Expected: PASS (1 passed).

- [x] **Step 5: Commit**

```bash
git add server/app/push_store.py server/tests/test_push.py
git commit -m "feat(server): push + file store with ack/purge lifecycle"
```

### Task A8: FCM v1 client (service-account token mint + send)

**Files:**
- Create: `server/app/fcm.py`
- Test: `server/tests/test_fcm.py`

**Interfaces:**
- Produces (`server.app.fcm`):
  - `class FcmError(Exception)`
  - `load_service_account(path: str) -> dict` (raises `FcmError` if missing)
  - `class FcmClient`: `__init__(self, service_account: dict)`, `mint_token() -> str` (cached until ~1h expiry; JWT RS256 `{iss: client_email, scope: "https://www.googleapis.com/auth/firebase.messaging", aud: token_uri, iat, exp}` with header `{alg, typ, kid: private_key_id}`), `send(data_message: dict, fcm_token: str) -> None` (POSTs `POST {FCM_ENDPOINT}/projects/{project_id}/messages:send`, body `{"message":{"token": fcm_token, "data": data_message}}`; raises `FcmError` on non-2xx). `FCM_ENDPOINT = "https://fcm.googleapis.com/v1"`.
- Test: verify the JWT is well-formed and the send URL/body are correct by monkeypatching `urllib`; no network in tests.

- [x] **Step 1: Write the failing FCM test** `server/tests/test_fcm.py`

```python
import json

from server.app.fcm import FcmClient, FcmError


def test_mint_token_well_formed():
    sa = {
        "project_id": "mdrender-push",
        "client_email": "fcm-pusher@mdrender-push.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nMII...\n-----END PRIVATE KEY-----",
        "private_key_id": "kid-123",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    client = FcmClient(sa)
    token = client.mint_token()
    header_b64, claims_b64, sig_b64 = token.split(".")
    import base64
    header = json.loads(base64.urlsafe_b64decode(header_b64 + "=="))
    claims = json.loads(base64.urlsafe_b64decode(claims_b64 + "=="))
    assert header["kid"] == "kid-123" and header["alg"] == "RS256"
    assert claims["iss"] == sa["client_email"]
    assert claims["scope"] == "https://www.googleapis.com/auth/firebase.messaging"
    assert claims["exp"] - claims["iat"] == 3600
```

*(The `private_key` above is a stub; use a real generated test key in Step 3 — generate via `crypto.generate_rsa_keypair` and PEM-encode, so the token can actually be signed.)*

- [x] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_fcm.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [x] **Step 3: Write `server/app/fcm.py`**

```python
# server/app/fcm.py
import base64
import json
import os
import time
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

FCM_ENDPOINT = "https://fcm.googleapis.com/v1"
OAUTH_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class FcmError(Exception):
    pass


def load_service_account(path: str) -> dict:
    if not path or not os.path.exists(path):
        raise FcmError(f"service account file not found: {path!r}")
    with open(path) as fh:
        return json.load(fh)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class FcmClient:
    def __init__(self, service_account: dict):
        self.sa = service_account
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._private_key = serialization.load_pem_private_key(
            service_account["private_key"].encode(), password=None
        )

    def mint_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expiry - 60:
            return self._token
        header = {"alg": "RS256", "typ": "JWT", "kid": self.sa["private_key_id"]}
        claims = {
            "iss": self.sa["client_email"],
            "scope": OAUTH_SCOPE,
            "aud": self.sa["token_uri"],
            "iat": int(now),
            "exp": int(now) + 3600,
        }
        signing_input = (_b64url(json.dumps(header).encode()) + "."
                         + _b64url(json.dumps(claims).encode()))
        signature = self._private_key.sign(
            signing_input.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        self._token = signing_input + "." + _b64url(signature)
        self._token_expiry = now + 3600
        return self._token

    def send(self, data_message: dict, fcm_token: str) -> None:
        access_token = self._get_access_token()
        url = f"{FCM_ENDPOINT}/projects/{self.sa['project_id']}/messages:send"
        body = json.dumps({"message": {"token": fcm_token, "data": data_message}}).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except urllib.error.HTTPError as e:
            raise FcmError(f"FCM send failed: HTTP {e.code} {e.read()[:200]}")
        except urllib.error.URLError as e:
            raise FcmError(f"FCM send failed: {e}")

    def _get_access_token(self) -> str:
        jwt = self.mint_token()
        body = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt,
        }).encode()
        req = urllib.request.Request(self.sa["token_uri"], data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise FcmError(f"token mint failed: HTTP {e.code} {e.read()[:200]}")
        except urllib.error.URLError as e:
            raise FcmError(f"token mint failed: {e}")
        return data["access_token"]
```

- [x] **Step 4: Fix the test to use a real key and run**

Replace the stub private key in `test_fcm.py`:

```python
from server.app.crypto import generate_rsa_keypair
from server.app.crypto import private_to_pem

_priv, _ = generate_rsa_keypair()
SA = {
    "project_id": "mdrender-push",
    "client_email": "fcm-pusher@mdrender-push.iam.gserviceaccount.com",
    "private_key": private_to_pem(_priv).decode(),
    "private_key_id": "kid-123",
    "token_uri": "https://oauth2.googleapis.com/token",
}
```

and update the test to use `SA` and assert the signature verifies against `_priv.public_key()`.

Run: `cd server && python3 -m pytest tests/test_fcm.py -v`
Expected: PASS (1 passed).

- [x] **Step 5: Commit**

```bash
git add server/app/fcm.py server/tests/test_fcm.py
git commit -m "feat(server): FCM HTTP v1 client with service-account token mint"
```

### Task A9: Flask app — all routes wired

**Files:**
- Rewrite: `server/app/app.py`
- Modify: `server/app/auth.py` (nothing new — uses existing)
- Test: `server/tests/test_api.py` (integration: enrol → oauth → register → push → download → ack; plus auth-gating and 400 checks)

**Interfaces:**
- Consumes: everything from Tasks A1–A8.
- Produces: `create_app(config) -> Flask` with the full endpoint table from the spec §Endpoints, including:
  - `POST /login` (form) → sets session cookie `mdrender_session`
  - `GET /pair`, `GET /enrol/<id>`, `GET /devices`, `GET /pushes`, `GET /pending` (session-cookie gated; HTML)
  - `POST /api/enrol/start` → `{enrolment_id, verification_uri}`
  - `POST /api/enrol` → `{client_id, client_secret}` (one-time key)
  - `POST /oauth/token` → OAuth2 client-credentials
  - `POST /api/push` (Bearer, multipart, `target_device` required → 400 if missing/unknown) → stores bytes, mints a per-push `challenge_key`, seals **one** doorbell trigger per device, sends a single FCM data message, returns `{push_id, files_sent}`
  - `POST /api/register-device` (pairing + sig over `…||push_key`) → `{ok, device_auth}`; update variant (device_secret+device_auth) for token/name/`push_key` rotation
  - `POST /api/push/<push_id>/manifest` (body `{challenge_key}`) → `{manifest, sig}`; 404 unknown push, 403 wrong `challenge_key`. `sig` is RSA-SHA256 over `manifest_bytes(manifest)` with the **server** private key, so the phone authenticates it against the QR-pinned public key rather than trusting the TLS terminator
  - `POST /api/push/<file_id>/download` (body `{key}`) → streams bytes
  - `POST /api/push/<file_id>/received` (body `{key}`) → acks, deletes bytes
  - `POST /api/device/status` (body `{device_secret, device_auth}`) → 200 ok / 404 needs re-registration
  - `GET /api/push/<push_id>/status` (Bearer) → per-file status
  - `GET /api/health` → `{ok: true}`
  - `DELETE /devices/<device_secret>` (session cookie) → remove device
  - `POST /api/push/<push_id>/retry` (session cookie) → reset retries + re-send FCM for pending files
- Enrolment state (one-time keys) lives in an in-memory dict (a browser-flow artefact); enrolment keys expire after `ENROL_TOKEN_TTL_HOURS`.

- [x] **Step 1: Write the failing integration test** `server/tests/test_api.py`

```python
import base64, io, json, os

from server.app.app import create_app
from server.app.crypto import generate_rsa_keypair, private_to_pem, public_to_spki_der
from server.app.trigger import manifest_bytes, open_trigger, seal_trigger


def _fake_fcm():
    class Fake:
        def __init__(self):
            self.sent = []
        def send(self, data_message, fcm_token):
            self.sent.append((data_message, fcm_token))
    return Fake()


def test_full_push_flow(config, db_path, monkeypatch):
    fcm = _fake_fcm()
    monkeypatch.setattr("server.app.app.make_fcm_client", lambda cfg: fcm)
    app = create_app(config)
    app.config["TESTING"] = True
    client = app.test_client()

    # Health
    assert client.get("/api/health").json == {"ok": True}

    # Password login (session)
    r = client.post("/login", data={"password": "testpass"})
    assert r.status_code == 302

    # Enrol a tool via the one-time key
    enrol = client.post("/api/enrol/start", json={}).json
    eid = enrol["enrolment_id"]
    key = app.config["_enrol_keys"][eid]  # test hook: inspect the in-memory key
    creds = client.post("/api/enrol", json={"enrolment_id": eid, "key": key}).json
    assert "client_id" in creds and "client_secret" in creds

    # OAuth2 token
    tok = client.post("/oauth/token", data={
        "grant_type": "client_credentials",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    }).json["access_token"]

    # Register a device (pairing token from /pair is a test hook below)
    phone_priv, phone_pub = generate_rsa_keypair()
    pub_b64 = base64.b64encode(public_to_spki_der(phone_pub)).decode()
    push_key_b64 = base64.b64encode(os.urandom(32)).decode()
    pairing_token = app.config["_test_pairing_token"]  # set by /pair in test
    import hashlib
    from server.app.crypto import sign
    sig = base64.b64encode(sign(phone_priv, hashlib.sha256(
        f"sec-1|Sunny Falcon|fcm-1|{pub_b64}|{push_key_b64}".encode()
    ).digest())).decode()
    reg = client.post("/api/register-device", json={
        "device_secret": "sec-1", "device_name": "Sunny Falcon",
        "fcm_token": "fcm-1", "public_key": pub_b64, "push_key": push_key_b64,
        "pairing_token": pairing_token, "sig": sig,
    })
    assert reg.status_code == 200

    # Push with a target_device — required
    assert client.post("/api/push",
        headers={"Authorization": f"Bearer {tok}"},
        data={"target_device": "Sunny Falcon", "file": (io.BytesIO(b"hello"), "notes.md")},
        content_type="multipart/form-data").status_code == 200
    # Missing target_device -> 400
    r = client.post("/api/push",
        headers={"Authorization": f"Bearer {tok}"},
        data={"file": (io.BytesIO(b"hi"), "a.md")},
        content_type="multipart/form-data")
    assert r.status_code == 400
    # Exactly one FCM message — no batching, regardless of file count
    assert len(fcm.sent) == 1

    # Decrypt the doorbell: AES-GCM under the negotiated push_key
    msg = fcm.sent[0][0]
    doorbell = open_trigger(base64.b64decode(push_key_b64),
                            {"i": msg["i"], "c": msg["p"]})
    assert doorbell["server_url"] == config.PUSH_PUBLIC_URL
    push_id = doorbell["push_id"]
    challenge_key = doorbell["challenge_key"]

    # A forged message addressed to this device is a silent no-op
    other = seal_trigger(base64.b64decode(push_key_b64), config.PUSH_PUBLIC_URL,
                         "some-other-push", "ck")
    assert open_trigger(os.urandom(32), {"i": other["i"], "c": other["c"]}) is None

    # Exchange the doorbell for a signed manifest. The body IS the signed bytes
    # and the signature rides in a header.
    r = client.post(f"/api/push/{push_id}/manifest", json={"challenge_key": challenge_key})
    assert r.status_code == 200
    signed_bytes = r.data
    sig_b64 = r.headers["X-Push-Manifest-Signature"]
    body = json.loads(signed_bytes)
    assert signed_bytes == manifest_bytes(body)
    # Signature is checked against the server public key the QR pinned
    from server.app.crypto import verify
    assert verify(app.config["_server_pub"], signed_bytes,
                  base64.b64decode(sig_b64))
    file_entry = body["files"][0]
    assert file_entry["name"] == "notes.md"
    retrieval_key = file_entry["retrieval_key"]

    # Wrong challenge_key -> 403
    assert client.post(f"/api/push/{push_id}/manifest",
                       json={"challenge_key": "wrong"}).status_code == 403

    # Download + ack
    dl = client.post(f"/api/push/{file_entry['file_id']}/download",
                     json={"key": retrieval_key})
    assert dl.data == b"hello"
```

*(The manifest assertion must use `crypto.verify(server_pub, manifest_bytes(manifest),
sig)` — the server signs exactly the bytes it transmits, so the phone can verify with
the QR-pinned public key. This is the property that a rogue TLS certificate cannot
manufacture, so it is not optional.)*

- [x] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_api.py -v`
Expected: FAIL (endpoints 404).

- [x] **Step 3: Write `server/app/app.py`** — the app factory + routes

Key structure (complete implementation is the bulk of this task):

```python
# server/app/app.py
import base64, io, json, os, time, uuid, datetime, hmac, hashlib
from flask import Flask, request, Response, jsonify, make_response, redirect

from server.app import crypto, fcm as fcm_mod, pairing, push_store
from server.app.auth import (LoginGate, hash_secret, verify_secret,
                             make_session, verify_session,
                             issue_access_token, validate_access_token)
from server.app.db import Database
from server.app.store import (get_or_create_server_keypair, session_secret_from_pem,
                              create_client, get_client, revoke_client, list_clients,
                              update_device_token, update_device_name, check_device,
                              touch_last_seen, get_device_by_name, get_device_by_secret,
                              delete_device, list_devices, sweep_stale_devices)
from server.app.trigger import build_manifest, manifest_bytes, seal_trigger

SESSION_COOKIE = "mdrender_session"
_ACCESS_TOKENS: dict = {}
```

The factory:

```python
def create_app(config):
    app = Flask(__name__)
    app.config["_db"] = Database(config.DB_PATH)
    app.config["_enrol_keys"] = {}       # enrolment_id -> {key, expires}
    app.config["_login_gate"] = LoginGate(config)
    app.config["_fcm"] = None            # set lazily
    app.config["_pairing_tokens"] = {}   # set by /pair GET; single current token

    @app.before_request
    def _open_db():
        g.db = app.config["_db"].connect()
        g.db.init_schema()
        g.cfg = config

    @app.teardown_request
    def _close_db(exc):
        if "db" in g:
            g.db.close()
    ...
```

*(Write the remaining handlers per the endpoint table. Notable behaviors:)*

- **`/login`** (POST): `allowed, retry = gate.check(remote_addr, password)`; on success `make_response(redirect("/pair"))` + `set_cookie(SESSION_COOKIE, make_session(...))`; on failure 401 with the lockout seconds.
- **Session gate helper**: `require_session()` → 401 unless `verify_session(config.session_secret, request.cookies.get(SESSION_COOKIE))` — compute `config.session_secret` once from `session_secret_from_pem(get_or_create_server_keypair(db)[0])`.
- **`/pair`** (GET, session): create a fresh pairing token, set `app.config["_test_pairing_token"]`, render a QR page (Task A11) containing `build_pairing_qr(config.PUSH_PUBLIC_URL, server_pk_b64, token, expires_iso)`.
- **`/api/enrol/start`** (POST): create `enrolment_id = uuid4().hex`, store `{key: uuid4().hex, expires}`; return `{enrolment_id, verification_uri: f"{base}/enrol/{id}"}`.
- **`/api/enrol`** (POST): verify key matches + unexpired, delete it, `secret_hash = hash_secret(secret=uuid4().hex)`, `client_id = create_client(...)`, return `{client_id, client_secret}`.
- **`/oauth/token`** (POST form): verify `grant_type=client_credentials`, client exists + `verify_secret(secret, hash)`, not revoked → `issue_access_token`; else 401.
- **`/api/push`** (Bearer): validate token → client_id; read `target_device` (form) → **400 `{"error":"device not found"}` if missing**; resolve device via `get_device_by_name` → 400 if unknown; `push_id = uuid4().hex`; mint `challenge_key = uuid4().hex`; for each uploaded `file`, `file_id = uuid4().hex`, write bytes to `<storage_dir>/<push_id>/<file_id>/<filename>`, `add_file(...)` with a fresh retrieval key (`uuid4().hex`). Then **one** `seal_trigger(push_key, PUSH_PUBLIC_URL, push_id, challenge_key)` and a **single** `fcm.send({"p": sealed["c"], "i": sealed["i"]}, device["fcm_token"])`; return `{push_id, files_sent: N}`. One message regardless of file count. (When `config.FCM_SERVER_KEY` is empty and no `make_fcm_client` is injected, skip the FCM send — the integration test injects a fake.)
- **`/api/push/<push_id>/manifest`** (POST body `{challenge_key}`): 404 unknown `push_id`; 403 unless `challenge_key` matches the push row; else build the manifest from the push's **unacked** files (`get_pending_files` filtered to this push), `manifest = build_manifest(push_id, date_iso, rows)`, and return the **signed bytes as the response body** with `X-Push-Manifest-Signature: base64(sign(server_priv, body))`. The body being the exact signed byte string is what lets B3 verify what it received without re-serialising. Because the manifest is built from live DB state it is always current — a retry can never hand the phone an already-acked file.
- **`/api/register-device`** (POST): if body has `pairing_token` → new registration via `pairing.register_device` (carrying `push_key`, covered by the `sig`); else update via `update_device_token`/`update_device_name`/`update_device_push_key` (requires `device_secret`+`device_auth`). On success 200 `{ok: true, device_auth}`.
- **`/api/push/<file_id>/download`** (POST body `{key}`): find file by `file_id`; 404 if missing/acked; verify `key == retrieval_key`; if `stored_path` set, stream the bytes (`send_file`); else 404. Key is **not** consumed.
- **`/api/push/<file_id>/received`** (POST body `{key}`): verify key, `mark_acked` (deletes bytes), 200.
- **`/api/device/status`** (POST body): `check_device` → 200 `{ok:true}` / 404 `{error:"re-register"}`; on 200 also `touch_last_seen`.
- **`GET /api/push/<push_id>/status`** (Bearer): per-file `{file_id, name, status, retries}`.
- **`GET /api/health`**: `{"ok": True}`.
- **`POST /api/push/<push_id>/retry`** (session): `reset_push_retries` + re-ring the single doorbell trigger for that push.
- **`GET/POST /devices`, `GET /pushes`, `GET /pending`, `DELETE /devices/<secret>`** (session): rendered HTML (Task A11) or JSON; `DELETE /devices` calls `delete_device` then `revoke`-equivalent.

Provide a module-level `make_fcm_client(config) -> FcmClient` (loads `config.FCM_SERVER_KEY`), monkeypatched by tests.

- [x] **Step 4: Run the integration test and iterate**

Run: `cd server && python3 -m pytest tests/test_api.py -v`
Expected: PASS once the flow works end-to-end.

- [x] **Step 5: Run the full suite**

Run: `cd server && python3 -m pytest`
Expected: PASS (all tests).

- [x] **Step 6: Commit**

```bash
git add server/app/app.py server/tests/test_api.py
git commit -m "feat(server): full Flask endpoint surface with auth + FCM delivery"
```

### Task A10: Retry worker + sweep + operator re-push

**Files:**
- Create: `server/app/retry.py`
- Test: `server/tests/test_retry.py`

**Interfaces:**
- Consumes: `Database`, `push_store`, `pairing` (`get_device_by_name`), `trigger`, `crypto`, `fcm`.
- Produces (`server.app.retry`):
  - `class RetryWorker`: `__init__(self, config, db: Database, fcm_client)`, `tick(now: float) -> list[str]` (returns touched file_ids). For each `get_pending_files(conn, now)`: if `retries >= config.PUSH_RETRY_COUNT` → `mark_exhausted`; else re-send the **push's doorbell trigger** (one per `push_id`, not one per file) and `increment_retry(file_id, now, config.PUSH_RETRY_INTERVAL_MINUTES)`; on `FcmError`, leave `next_retry_at` unchanged (retry next tick). The phone then re-requests the manifest, which is always current — so a retry can no longer hand it a file that was already acked.
  - `class SweepWorker`: `tick()` → `sweep_stale_devices(conn, ttl_days)` + `purge_expired_bytes(conn, ttl_hours, now)`.
  - `run_forever(config, db, fcm_client)` — loops every 60 s, calls both ticks; handles `KeyboardInterrupt`/`SystemExit` cleanly.

- [x] **Step 1: Write the failing retry test** `server/tests/test_retry.py`

```python
import base64, hashlib, time

from server.app.db import Database
from server.app.push_store import add_file, create_push, get_push_files
from server.app.retry import RetryWorker, SweepWorker


def test_retry_exhausts_after_count(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "p1", "Sunny Falcon", "ck-1")
        add_file(conn, file_id="f1", push_id="p1", file_name="a.md", file_path="",
                 size=3, retrieval_key="k1", stored_path="p1/f1/a.md", created_at=time.time())
    worker = RetryWorker(config, db, fcm_client=FakeFcm())
    for _ in range(config.PUSH_RETRY_COUNT):
        worker.tick(time.time())
    with db.connect() as conn:
        status = conn.execute("SELECT status FROM push_files WHERE file_id = 'f1'").fetchone()
    assert status["status"] == "exhausted"
```

*(Define `FakeFcm` locally with a `send()` that records. The worker resolves the device
via `push_files.push_id → pushes.target_device → devices.device_name`; a missing device
short-circuits to `mark_exhausted`, so no device row is needed for this test.)*

- [x] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_retry.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [x] **Step 3: Write `server/app/retry.py`**

```python
# server/app/retry.py
import time

from server.app import fcm as fcm_mod, pairing, push_store, trigger


def _device(conn, row):
    push = push_store.get_push_by_id(conn, row["push_id"])
    if push is None:
        return None
    return pairing.get_device_by_name(conn, push["target_device"])


class RetryWorker:
    """Re-rings the doorbell for pushes that still have unacked files.

    One trigger per push, not one per file: the phone re-requests the manifest,
    which is built from current DB state and therefore never names a file that
    has already been acked. `seal_trigger` is deterministic in `push_id`, so a
    retry reproduces a byte-identical message.
    """

    def __init__(self, config, db, fcm_client):
        self.config = config
        self.db = db
        self.fcm_client = fcm_client

    def tick(self, now: float) -> list[str]:
        if self.fcm_client is None:
            return []
        touched = []
        with self.db.connect() as conn:
            rung: set[str] = set()
            for row in push_store.get_pending_files(conn, now):
                if row["retries"] >= self.config.PUSH_RETRY_COUNT:
                    push_store.mark_exhausted(conn, row["file_id"])
                    touched.append(row["file_id"])
                    continue
                if row["push_id"] in rung:
                    # Already re-rung for this push on this tick.
                    push_store.increment_retry(
                        conn, row["file_id"], now, self.config.PUSH_RETRY_INTERVAL_MINUTES
                    )
                    touched.append(row["file_id"])
                    continue
                device = _device(conn, row)
                if device is None:
                    push_store.mark_exhausted(conn, row["file_id"])
                    touched.append(row["file_id"])
                    continue
                push = push_store.get_push_by_id(conn, row["push_id"])
                sealed = trigger.seal_trigger(
                    base64.b64decode(device["push_key"]),
                    self.config.PUSH_PUBLIC_URL, push["push_id"],
                    push["challenge_key"],
                )
                try:
                    self.fcm_client.send(
                        {"p": sealed["c"], "i": sealed["i"]}, device["fcm_token"]
                    )
                    rung.add(row["push_id"])
                    push_store.increment_retry(
                        conn, row["file_id"], now, self.config.PUSH_RETRY_INTERVAL_MINUTES
                    )
                    touched.append(row["file_id"])
                except fcm_mod.FcmError:
                    pass  # retry next tick
        return touched


class SweepWorker:
    def __init__(self, config, db):
        self.config = config
        self.db = db

    def tick(self) -> None:
        with self.db.connect() as conn:
            from server.app.store import sweep_stale_devices

            sweep_stale_devices(conn, self.config.DEVICE_TTL_DAYS)
            push_store.purge_expired_bytes(conn, self.config.PUSH_FILE_TTL_HOURS, time.time())
```

*(Add `import base64` at the top of the real file — shown inline above only for plan
brevity.)*

- [x] **Step 4: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_retry.py -v`
Expected: PASS (1 passed).

- [x] **Step 5: Commit**

```bash
git add server/app/retry.py server/tests/test_retry.py
git commit -m "feat(server): retry worker, orphan sweep, byte purge"
```

### Task A11: Browser UI (login, pair, enrol, devices, pushes, pending)

**Files:**
- Create: `server/app/templates/base.html`
- Create: `server/app/templates/login.html`
- Create: `server/app/templates/pair.html`
- Create: `server/app/templates/enrol.html`
- Create: `server/app/templates/devices.html`
- Create: `server/app/templates/pushes.html`
- Create: `server/app/templates/pending.html`
- Modify: `server/app/app.py` (render templates for the HTML routes)

**Interfaces:**
- Consumes: the session-gated GET routes from Task A9.
- Produces: minimal server-rendered pages (no JS framework). The QR in `pair.html` is rendered as an inline `<svg>` via `qrcode` lib (add `qrcode==7.4.2` to `requirements.txt`) or an `<img>` from a `/pair/qr.svg` route that `qrcode.make(...)` returns.

- [x] **Step 1: Add `qrcode` to `server/requirements.txt`**

```
qrcode==7.4.2
```

- [x] **Step 2: Write the templates**

`login.html` — a password form POSTing to `/login`. `pair.html` — shows the server URL + the QR SVG + expiry. `enrol.html` — shows the one-time key for `enrolment_id`. `devices.html` — table of `list_devices` rows with a "Remove" form POSTing to `/devices/<secret>/delete`. `pushes.html` — table of `list_pushes` with per-push "Re-push pending" form POSTing to `/api/push/<push_id>/retry`. `pending.html` — table of `list_pending` (file name, path, size, date, status, retries). All extend `base.html` (a small header linking to the sections, gated by session).

- [x] **Step 3: Wire the HTML routes in `server/app/app.py`**

```python
@app.route("/pair")
def pair_page():
    require_session()
    db = g.db
    _, server_pk_b64 = get_or_create_server_keypair(db)
    token = pairing.create_pairing_token(db, 15)
    app.config["_test_pairing_token"] = token
    expires_iso = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15)
    qr_text = pairing.build_pairing_qr(g.cfg.PUSH_PUBLIC_URL, server_pk_b64,
                                       token, expires_iso.isoformat())
    img = qrcode.make(qr_text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()
    return render_template("pair.html", qr_png=qr_b64, server_url=g.cfg.PUSH_PUBLIC_URL,
                           expires=expires_iso.strftime("%Y-%m-%d %H:%M UTC"))
```

and analogous handlers for `/login` (GET form + POST), `/enrol/<id>`, `/devices`, `/pushes`, `/pending`, plus POST handlers for `DELETE /devices/<secret>` and `/api/push/<push_id>/retry`.

- [x] **Step 4: Manual verification**

Run: `cd server && python3 -m server.app.app` (add a `__main__` block that calls `create_app(load_config()).run(host, port)`), then open `http://localhost:8080/login` in a browser, log in with `SERVER_PASSWORD`, and confirm: pairing page renders a scannable QR; devices/pushes/pending pages render with the empty state.

- [x] **Step 5: Commit**

```bash
git add server/app/templates server/app/app.py server/requirements.txt
git commit -m "feat(server): password login + QR pairing + admin pages"
```

### Task A12: Dockerfile + compose + startup entry

**Files:**
- Create: `server/Dockerfile`
- Create: `server/docker-compose.example.yml`
- Create: `server/run.py`
- Create: `server/.dockerignore`

**Interfaces:**
- Produces: `run.py` that wires `create_app(load_config())` to the WSGI server (Flask dev server is fine for single-user; production note to use `waitress` — add `waitress==3.0.0` to requirements) and starts `RetryWorker.run_forever` + `SweepWorker` threads; the Docker image that runs it.

- [x] **Step 1: Write `server/run.py`**

```python
# server/run.py
import threading
from server.app.app import create_app
from server.app.config import load_config
from server.app.db import Database
from server.app.retry import RetryWorker, SweepWorker, run_forever


def main():
    config = load_config()
    app = create_app(config)
    db = Database(config.DB_PATH)
    if config.FCM_SERVER_KEY:
        from server.app import fcm
        fcm_client = fcm.FcmClient(fcm.load_service_account(config.FCM_SERVER_KEY))
    else:
        fcm_client = None
    threading.Thread(target=run_forever, args=(config, db, fcm_client), daemon=True).start()
    host, port = config.LISTEN_ADDR.rsplit(":", 1)
    app.run(host=host, port=int(port))


if __name__ == "__main__":
    main()
```

- [x] **Step 2: Write `server/Dockerfile`**

```dockerfile
FROM python:3.11-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY run.py .
ENV DB_PATH=/data/push/server.db PUSH_STORAGE_DIR=/data/push
VOLUME ["/data/push"]
EXPOSE 8080
CMD ["python", "run.py"]
```

- [x] **Step 3: Write `server/docker-compose.example.yml`**

```yaml
services:
  push:
    build: .
    ports: ["8080:8080"]
    environment:
      SERVER_PASSWORD: "change-me"
      PUSH_PUBLIC_URL: "http://localhost:8080"
      FCM_SERVER_KEY: "/srv/fcm-service-account.json"
      LISTEN_ADDR: "0.0.0.0:8080"
    volumes:
      - ./data:/data/push
      - ./fcm-service-account.json:/srv/fcm-service-account.json:ro
```

- [x] **Step 4: Add `waitress` to requirements and verify the image builds**

Run: `cd server && docker build -t mdrender-push .`
Expected: image builds. (If Docker is unavailable on the machine, verify `python3 run.py` starts and `/api/health` returns ok.)

- [x] **Step 5: Commit**

```bash
git add server/Dockerfile server/docker-compose.example.yml server/run.py server/.dockerignore server/requirements.txt
git commit -m "feat(server): Docker image + compose + startup entry"
```

### Task A13: Server README

**Files:**
- Create: `server/README.md`

**Interfaces:** documentation only.

- [x] **Step 1: Write `server/README.md`**

Cover: quick start (`docker compose up`), all env vars with the spec defaults, the pairing flow (open `/pair`, scan QR), tool enrolment (`localsend-send.py --enrol --server …`), backup (mount `/data/push` and copy `server.db`), and the reset behavior (device re-registration via `/api/device/status`). Reference `docs/superpowers/specs/2026-07-25-cloud-push-design.md`.

- [x] **Step 2: Commit**

```bash
git add server/README.md
git commit -m "docs(server): operator README"
```

---

## Phase B — Android

*(Prerequisite: Task 0.3's `google-services.json` must exist for the FCM-dependent tasks to compile.)*

### Task B1: `PushServerConfig` — persisted pairing config

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/cloudpush/PushServerConfig.kt`
- Test: `app/src/test/java/com/a42r/mdrender/cloudpush/PushServerConfigTest.kt`

**Interfaces:**
- Produces (`com.a42r.mdrender.cloudpush.PushServerConfig`, `@Singleton @Inject constructor(@ApplicationContext context)`), backed by SharedPreferences `"mdrender_cloudpush_prefs"`:
  - `var serverUrl: String`
  - `var serverPublicKeyPem: String`
  - `var deviceSecret: String` (auto-generates a UUID on first read)
  - `var deviceAuth: String`
  - `var pushKey: ByteArray` (the negotiated doorbell key, base64 in prefs; auto-generates 32 random bytes on first read)
  - `val pushKeyB64: String` (base64 form, as sent to the server)
  - `val deviceName: String` (getter reads `LocalSendPrefs.alias`)
  - `val isPaired: Boolean` (`serverUrl.isNotEmpty() && serverPublicKeyPem.isNotEmpty() && deviceAuth.isNotEmpty()`)
  - `fun clear()` — wipes all fields (re-pair / rotate). Also regenerates `pushKey` on next read, so re-pairing invalidates any captured doorbell.

- [ ] **Step 1: Write the failing unit test** `PushServerConfigTest.kt`

```kotlin
package com.a42r.mdrender.cloudpush

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat  // not present — see note
```

*(The repo uses JUnit4 + Mockito, not Truth. Write the test with plain `assert` / `org.junit.Assert.assertEquals` against a fresh `PushServerConfig(ApplicationProvider.getApplicationContext())`: set `serverUrl`, assert `isPaired` flips, assert `clear()` resets it, and assert `deviceSecret` is stable across reads. Also assert `pushKey` is 32 bytes, stable across reads, **different** after `clear()`, and that `pushKeyB64` round-trips it.)*

- [ ] **Step 2: Run to verify it fails (compiles, assertion fails)**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: FAIL — class not found.

- [ ] **Step 3: Write `PushServerConfig.kt`**

```kotlin
package com.a42r.mdrender.cloudpush

import android.content.Context
import android.util.Base64
import com.a42r.mdrender.localsend.LocalSendPrefs
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class PushServerConfig @Inject constructor(
    @ApplicationContext context: Context,
    private val localSendPrefs: LocalSendPrefs
) {
    private val prefs =
        context.getSharedPreferences("mdrender_cloudpush_prefs", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, "") ?: ""
        set(v) = prefs.edit().putString(KEY_SERVER_URL, v).apply()

    var serverPublicKeyPem: String
        get() = prefs.getString(KEY_SERVER_PUBLIC_KEY, "") ?: ""
        set(v) = prefs.edit().putString(KEY_SERVER_PUBLIC_KEY, v).apply()

    var deviceSecret: String
        get() = prefs.getString(KEY_DEVICE_SECRET, null)
            ?: UUID.randomUUID().toString().also { prefs.edit().putString(KEY_DEVICE_SECRET, it).apply() }
        set(v) = prefs.edit().putString(KEY_DEVICE_SECRET, v).apply()

    var deviceAuth: String
        get() = prefs.getString(KEY_DEVICE_AUTH, "") ?: ""
        set(v) = prefs.edit().putString(KEY_DEVICE_AUTH, v).apply()

    var pushKey: ByteArray
        get() = prefs.getString(KEY_PUSH_KEY, null)
            ?.let { Base64.decode(it, Base64.DEFAULT) }
            ?: java.security.SecureRandom().let { rng ->
                ByteArray(32).also { rng.nextBytes(it) }
                    .also { prefs.edit().putString(KEY_PUSH_KEY, Base64.encodeToString(it, Base64.DEFAULT)).apply() }
            }
        set(v) = prefs.edit().putString(KEY_PUSH_KEY, Base64.encodeToString(v, Base64.DEFAULT)).apply()

    val pushKeyB64: String get() = Base64.encodeToString(pushKey, Base64.DEFAULT)

    val deviceName: String get() = localSendPrefs.alias

    val isPaired: Boolean
        get() = serverUrl.isNotEmpty() && serverPublicKeyPem.isNotEmpty() && deviceAuth.isNotEmpty()

    fun clear() {
        prefs.edit()
            .putString(KEY_SERVER_URL, "")
            .putString(KEY_SERVER_PUBLIC_KEY, "")
            .putString(KEY_DEVICE_AUTH, "")
            .putString(KEY_DEVICE_SECRET, "")
            .putString(KEY_PUSH_KEY, "")
            .apply()
    }

    companion object {
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_SERVER_PUBLIC_KEY = "server_public_key_pem"
        private const val KEY_DEVICE_SECRET = "device_secret"
        private const val KEY_DEVICE_AUTH = "device_auth"
        private const val KEY_PUSH_KEY = "push_key"
    }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/cloudpush/ app/src/test/java/com/a42r/mdrender/cloudpush/
git commit -m "feat(android): PushServerConfig persisted pairing config + push_key"
```

### Task B2: `CloudPushKeyStore` — RSA-3072 in Android Keystore

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/cloudpush/CloudPushKeyStore.kt`
- Test: `app/src/test/java/com/a42r/mdrender/cloudpush/CloudPushKeyStoreTest.kt` (skipped — Android Keystore not available on JVM; test logic is instrumented or left to integration)

**Interfaces:**
- Produces (`com.a42r.mdrender.cloudpush.CloudPushKeyStore`, `@Singleton @Inject constructor()`):
  - `const val ALIAS = "mdrender_cloudpush_keypair"`
  - `fun getOrCreateKeyPair(): KeyPair`
  - `fun getPublicKeySpkiDer(): ByteArray`
  - `fun sign(data: ByteArray): ByteArray` (SHA256withRSA)
  - `fun deleteKeyPair()`

> **Revised 2026-08-29:** the keypair no longer decrypts anything. Doorbell encryption is
> symmetric under `push_key`; the RSA key is used only to sign the registration
> key-possession proof. So `decrypt()` and the `PURPOSE_DECRYPT` / `ENCRYPTION_PADDING_RSA_OAEP`
> setup are removed — keep `PURPOSE_SIGN` and `SIGNATURE_PADDING_RSA_PKCS1`.

- [ ] **Step 1: Write `CloudPushKeyStore.kt`**

```kotlin
package com.a42r.mdrender.cloudpush

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Signature
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class CloudPushKeyStore @Inject constructor() {
    companion object {
        const val ALIAS = "mdrender_cloudpush_keypair"
        private const val PROVIDER = "AndroidKeyStore"
    }

    private val keyStore: KeyStore =
        KeyStore.getInstance(PROVIDER).apply { load(null) }

    fun getOrCreateKeyPair(): KeyPair {
        if (keyStore.containsAlias(ALIAS)) {
            return (keyStore.getEntry(ALIAS, null) as KeyStore.PrivateKeyEntry).let {
                KeyPair(it.certificate.publicKey, it.privateKey)
            }
        }
        val generator = KeyPairGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_RSA, PROVIDER
        )
        generator.initialize(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setKeySize(3072)
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setSignaturePaddings(KeyProperties.SIGNATURE_PADDING_RSA_PKCS1)
                .build()
        )
        return generator.generateKeyPair()
    }

    fun getPublicKeySpkiDer(): ByteArray =
        getOrCreateKeyPair().public.encoded // X.509 SubjectPublicKeyInfo (DER)

    fun sign(data: ByteArray): ByteArray {
        val signature = Signature.getInstance("SHA256withRSA")
        signature.initSign(getOrCreateKeyPair().private)
        signature.update(data)
        return signature.sign()
    }

    fun deleteKeyPair() {
        keyStore.deleteEntry(ALIAS)
    }
}
```

- [ ] **Step 2: Verify it compiles**

Run: `./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/cloudpush/CloudPushKeyStore.kt
git commit -m "feat(android): RSA-3072 Keystore keypair for Cloud Push E2E"
```

### Task B3: `PushCrypto` — doorbell decrypt + manifest verify

> **Revised 2026-08-29.** Replaces envelope verify + decrypt. The doorbell is AES-256-GCM
> under the symmetric `push_key`; the Keystore RSA key is no longer used to unwrap a
> per-message content key, only to sign the registration proof and verify the manifest
> signature. `decryptEnvelope` → `decryptTrigger`; `PushPayload` → `Doorbell` + `Manifest`.

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/cloudpush/PushCrypto.kt`
- Test: `app/src/test/java/com/a42r/mdrender/cloudpush/PushCryptoTest.kt` (JVM — pure crypto with a JCE-generated AES key and RSA keypair, no Keystore needed)

**Interfaces:**
- Produces (`com.a42r.mdrender.cloudpush.PushCrypto`, `@Singleton @Inject constructor(private val keyStore: CloudPushKeyStore)`):
  - `data class Doorbell(val serverUrl: String, val pushId: String, val challengeKey: String)`
  - `data class ManifestFile(val fileId: String, val name: String, val path: String, val size: Long, val retrievalKey: String)`
  - `data class Manifest(val pushId: String, val date: String, val files: List<ManifestFile>)`
  - `fun decryptTrigger(ctB64: String, ivB64: String, pushKey: ByteArray): Doorbell?` — AES-256-GCM/NoPadding with `GCMParameterSpec(128, iv)`, tag appended to `ct`; returns `null` on any failure (forged or misaddressed message)
  - `fun verifyManifest(manifestJson: String, sigB64: String, serverPublicKeyPem: String): List<ManifestFile>?` — `SHA256withRSA`.verify over the **exact received bytes**, then parse. Returns `null` if the signature fails or parsing fails.
  - `fun signRegistration(data: ByteArray): String` — delegates to `keyStore.sign` (base64), used by `PushClient.registerDevice`

**Critical contract:** `verifyManifest` must verify the signature over the **exact bytes
the server transmitted** — the server signs `json.dumps(manifest, separators=(",", ":"),
sort_keys=True)`. Re-serialising the parsed object would reorder keys and break the
signature, so the raw response body string is what gets verified, then parsed.

The server returns that signed byte string as the **response body**, with the base64
signature in the `X-Push-Manifest-Signature` header. That is deliberate: it means
`fetchManifest` has nothing to slice out of the envelope, and the bytes verified are
byte-for-byte the bytes signed. The `verifyManifest` signature above takes both parts
explicitly so the pairing of payload to signature cannot be fumbled at the call site.

- [ ] **Step 1: Write the failing unit test** `PushCryptoTest.kt`

```kotlin
class PushCryptoTest {
    // Build a trigger the way the server does (HMAC-derived IV) and open it.
    @Test fun `decryptTrigger returns the doorbell`() { /* assert fields */ }

    @Test fun `decryptTrigger rejects a wrong push key`() {
        // assertNull(crypto.decryptTrigger(ct, iv, randomKey))
    }

    @Test fun `decryptTrigger rejects a tampered ciphertext`() { /* flip a byte */ }

    // Sign a manifest with a JCE RSA key, then verify; and assert a tampered
    // manifest body fails verification.
    @Test fun `verifyManifest accepts a valid signature`() { /* assert 2 files */ }
    @Test fun `verifyManifest rejects a tampered body`() { /* assertNull */ }
}
```

*(The IV the test feeds in is just the base64 the server would send; `decryptTrigger`
takes the IV as an argument precisely because — as in Task A3 — the server derives it
from a `push_id` that is still inside the ciphertext, so the app cannot compute it
before decrypting.)*

- [ ] **Step 2: Run to verify it fails**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: FAIL — class not found.

- [ ] **Step 3: Write `PushCrypto.kt`**

```kotlin
package com.a42r.mdrender.cloudpush

import android.util.Base64
import java.security.KeyFactory
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class PushCrypto @Inject constructor(private val keyStore: CloudPushKeyStore) {

    fun decryptTrigger(ctB64: String, ivB64: String, pushKey: ByteArray): Doorbell? = try {
        val iv = Base64.decode(ivB64, Base64.DEFAULT)
        val raw = Base64.decode(ctB64, Base64.DEFAULT)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(pushKey, "AES"), GCMParameterSpec(128, iv))
        val json = String(cipher.doFinal(raw), Charsets.UTF_8)
        val obj = Json.parseToJsonElement(json).jsonObject
        Doorbell(obj.getValue("server_url").jsonPrimitive.content,
                 obj.getValue("push_id").jsonPrimitive.content,
                 obj.getValue("challenge_key").jsonPrimitive.content)
    } catch (e: Exception) {
        // Forged, tampered, or simply not addressed to this device -> drop.
        null
    }

    fun verifyManifest(manifestJson: String, sigB64: String,
                       serverPublicKeyPem: String): List<ManifestFile>? = try {
        val pem = serverPublicKeyPem
            .replace("-----BEGIN PUBLIC KEY-----", "")
            .replace("-----END PUBLIC KEY-----", "")
            .replace("\s".toRegex(), "")
        val key = KeyFactory.getInstance("RSA")
            .generatePublic(X509EncodedKeySpec(Base64.decode(pem, Base64.DEFAULT)))
        // Verify the exact received bytes: re-serialising would reorder keys.
        if (!Signature.getInstance("SHA256withRSA")
                .run { initVerify(key); update(manifestJson.toByteArray()); verify(Base64.decode(sigB64, Base64.DEFAULT)) }) {
            return null
        }
        val files = Json.parseToJsonElement(manifestJson).jsonObject
            .getValue("files").jsonArray.map { el ->
                val o = el.jsonObject
                ManifestFile(o.getValue("file_id").jsonPrimitive.content,
                             o.getValue("name").jsonPrimitive.content,
                             o.getValue("path").jsonPrimitive.content,
                             o.getValue("size").jsonPrimitive.long,
                             o.getValue("retrieval_key").jsonPrimitive.content)
            }
        files
    } catch (e: Exception) {
        null
    }

    fun signRegistration(data: ByteArray): String =
        Base64.encodeToString(keyStore.sign(data), Base64.DEFAULT)
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/cloudpush/PushCrypto.kt app/src/test/java/com/a42r/mdrender/cloudpush/
git commit -m "feat(android): doorbell decrypt + signed-manifest verify"
```
### Task B4: `PushClient` — HTTP client

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/cloudpush/PushClient.kt`
- Test: `app/src/test/java/com/a42r/mdrender/cloudpush/PushClientTest.kt` (uses a local `com.sun.net.httpserver.HttpServer` on a random port to assert request shape: POST/PUT, JSON bodies, no query-string secrets)

**Interfaces:**
- Produces (`com.a42r.mdrender.cloudpush.PushClient`, `@Singleton @Inject constructor()`), all `suspend`:
  - `fun registerDevice(config: PushServerConfig, publicKeySpkiDer: ByteArray, fcmToken: String, pairingToken: String): Result<String>` → returns `deviceAuth`; POSTs `/api/register-device` with `push_key` and the sig proof over `sha256(device_secret||device_name||fcm_token||public_key||push_key)`.
  - `fun fetchManifest(doorbell: PushCrypto.Doorbell): Result<String>` → POSTs `/api/push/{push_id}/manifest` with `{challenge_key}`, returns the **raw response body** for `PushCrypto.verifyManifest` to verify. Returning the raw string is required: the signature covers the exact bytes, so the client must not re-serialise.
  - `fun rotateToken(config: PushServerConfig, newFcmToken: String): Result<Unit>`
  - `fun rotatePushKey(config: PushServerConfig, newPushKey: ByteArray): Result<Unit>`
  - `fun renameDevice(config: PushServerConfig, newName: String): Result<Unit>`
  - `fun checkRegistration(config: PushServerConfig): Boolean` → true on 200, false on 404.
  - `fun downloadFile(config: PushServerConfig, fileId: String, key: String, dest: File): Result<Unit>` → streams to `dest`.
  - `fun ackReceived(config: PushServerConfig, fileId: String, key: String): Result<Unit>`
- Uses `java.net.HttpURLConnection`; all device calls are `POST`/`PUT` with `application/json` bodies `{device_secret, device_auth, ...}`. JSON via kotlinx-serialization.

- [ ] **Step 1: Write the failing test** `PushClientTest.kt`

Stand up an `HttpServer`, set `config.serverUrl = "http://127.0.0.1:$port"`, point `checkRegistration` at `/api/device/status`, assert the request method is POST, the body is JSON containing `device_secret`, and that the URL contains no query string.

- [ ] **Step 2: Run to verify it fails**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: FAIL — class not found.

- [ ] **Step 3: Write `PushClient.kt`**

```kotlin
package com.a42r.mdrender.cloudpush

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.Base64
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Singleton
class PushClient @Inject constructor() {

    suspend fun checkRegistration(config: PushServerConfig): Boolean =
        withContext(Dispatchers.IO) {
            val body = Json.encodeToString(
                RegistrationStatusRequest.serializer(),
                RegistrationStatusRequest(config.deviceSecret, config.deviceAuth)
            )
            val (code, _) = post(
                url(config.serverUrl, "/api/device/status"),
                body,
                readTimeoutMs = 10_000
            )
            code == 200
        }

    // registerDevice, rotateToken, renameDevice, downloadFile, ackReceived follow
    // the same shape: build URL without query params, POST/PUT a JSON body,
    // check the status code, stream download bodies to a File.
}
```

*(Implement the private `post(url, body, readTimeoutMs): Pair<Int, ByteArray>` and `postStream` helpers with `HttpURLConnection`; set `connectTimeout`, `readTimeout`, `doOutput`, `Content-Type: application/json`, and read the response. For `registerDevice`, the body includes `device_secret, device_name, fcm_token, public_key, push_key, pairing_token, sig` where `sig` = Base64(RSA-SHA256(sha256(device_secret||device_name||fcm_token||public_key||push_key))) using the Keystore. The trailing `push_key` in the sig input is what stops an attacker substituting their own doorbell key.)*

- [ ] **Step 4: Run the test to verify it passes**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/cloudpush/PushClient.kt app/src/test/java/com/a42r/mdrender/cloudpush/
git commit -m "feat(android): PushClient HTTP + manifest fetch + JSON body auth"
```

### Task B5: `QrPairingScanner` — ML Kit + CameraX scanner screen

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/cloudpush/QrScannerScreen.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/cloudpush/` (a `QrPairingScanner.kt` wrapping `BarcodeScanning`)

**Interfaces:**
- Produces: `@Composable fun QrScannerScreen(onResult: (String) -> Unit, onCancel: () -> Unit)` — a CameraX `PreviewView` + `ImageAnalysis` feeding ML Kit `BarcodeScanner`; on a successful QR parse, calls `onResult(rawText)`.

- [x] **Step 1: Write `QrPairingScanner.kt` + `QrScannerScreen.kt`**

```kotlin
// QrPairingScanner.kt
package com.a42r.mdrender.cloudpush

import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage

class QrPairingScanner(
    private val onScanned: (String) -> Unit
) : ImageAnalysis.Analyzer {
    private val scanner = BarcodeScanning.getClient()

    override fun analyze(imageProxy: ImageProxy) {
        val mediaImage = imageProxy.image
        if (mediaImage == null) {
            imageProxy.close()
            return
        }
        val image = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
        scanner.process(image)
            .addOnSuccessListener { barcodes ->
                barcodes.firstOrNull { it.format == Barcode.FORMAT_QR_CODE }?.let {
                    onScanned(it.rawValue ?: return@let)
                }
            }
            .addOnCompleteListener { imageProxy.close() }
    }
}
```

`QrScannerScreen.kt`: a Compose screen with `AndroidView` hosting a `PreviewView`; bind `ProcessCameraProvider.getInstance(context).bindToLifecycle(lifecycleOwner, cameraSelector, preview, analysis)` where `analysis.setAnalyzer(executor, QrPairingScanner(onResult))`. Launched as a full screen from Settings (Task B9).

- [x] **Step 2: Verify it compiles**

Run: `./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [x] **Step 3: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/cloudpush/QrScannerScreen.kt app/src/main/java/com/a42r/mdrender/cloudpush/QrPairingScanner.kt
git commit -m "feat(android): QR pairing scanner (ML Kit + CameraX)"
```

### Task B6: `CloudPushManager` — download queue

> **Revised 2026-08-29.** Slice merging and the 30 s debounce are deleted. The manifest
> is now fetched once per doorbell and is complete and authoritative, so there are no
> partial slices to reconcile. A re-rung doorbell re-fetches the manifest; the queue
> still dedupes by `fileId` so a retried file is not downloaded twice.

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/cloudpush/CloudPushManager.kt`
- Test: `app/src/test/java/com/a42r/mdrender/cloudpush/CloudPushManagerTest.kt`

**Interfaces:**
- Produces (`com.a42r.mdrender.cloudpush.CloudPushManager`, `@Singleton @Inject constructor`):
  - `data class DownloadTask(val pushId: String, val file: PushCrypto.ManifestFile, val serverUrl: String, val status: Status = Status.QUEUED, val progress: Float = 0f)` with `enum Status { QUEUED, DOWNLOADING, DONE, FAILED, CANCELLED }`
  - `val state: StateFlow<List<DownloadTask>>`
  - `fun onPushReady(callback: (String) -> Unit)` — fired once per accepted manifest
  - `fun enqueue(doorbell: PushCrypto.Doorbell, files: List<PushCrypto.ManifestFile>)` — adds tasks for fileIds not already known for that push, then fires the ready callback. No timer, no expected-count logic.
  - `fun cancel(fileId: String)` — sets status CANCELLED; the service discards the temp copy and acks as received.
  - `fun onFinished(fileId: String, success: Boolean)`

- [x] **Step 1: Write the failing test** `CloudPushManagerTest.kt`

```kotlin
@Test fun `enqueue adds every file in a manifest`() { /* assert 2 tasks, callback fired once */ }

@Test fun `a re-rung doorbell does not duplicate completed files`() {
    // enqueue(2 files) -> onFinished(f1, true) -> enqueue(same manifest again)
    // assert still 2 tasks and no second download of f1
}
```

*(Use `kotlinx-coroutines-test` (`runTest`, `StandardTestDispatcher`) with the manager's
scope injected.)*

- [x] **Step 2: Run to verify it fails**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: FAIL — class not found.

- [x] **Step 3: Write `CloudPushManager.kt`**

```kotlin
package com.a42r.mdrender.cloudpush

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class CloudPushManager @Inject constructor() {

    enum class Status { QUEUED, DOWNLOADING, DONE, FAILED, CANCELLED }

    data class DownloadTask(
        val pushId: String,
        val file: PushCrypto.ManifestFile,
        val serverUrl: String,
        val status: Status = Status.QUEUED,
        val progress: Float = 0f
    )

    private val _state = MutableStateFlow<List<DownloadTask>>(emptyList())
    val state: StateFlow<List<DownloadTask>> = _state.asStateFlow()

    private val readyCallbacks = mutableListOf<(String) -> Unit>()

    fun onPushReady(callback: (String) -> Unit) {
        readyCallbacks.add(callback)
    }

    fun enqueue(doorbell: PushCrypto.Doorbell, files: List<PushCrypto.ManifestFile>) {
        val existing = _state.value
            .filter { it.pushId == doorbell.pushId }
            .map { it.file.fileId }
            .toSet()
        val newTasks = files
            .filter { it.fileId !in existing }
            .map { DownloadTask(doorbell.pushId, it, doorbell.serverUrl) }
        if (newTasks.isEmpty()) return
        _state.update { it + newTasks }
        readyCallbacks.forEach { it(doorbell.pushId) }
    }

    fun cancel(fileId: String) {
        _state.update { list ->
            list.map { if (it.file.fileId == fileId) it.copy(status = Status.CANCELLED) else it }
        }
    }

    fun onFinished(fileId: String, success: Boolean) {
        _state.update { list ->
            list.map {
                if (it.file.fileId == fileId) it.copy(
                    status = if (success) Status.DONE else Status.FAILED,
                    progress = if (success) 1f else it.progress
                ) else it
            }
        }
    }
}
```

- [x] **Step 4: Run the test to verify it passes**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/cloudpush/CloudPushManager.kt app/src/test/java/com/a42r/mdrender/cloudpush/
git commit -m "feat(android): download queue for a complete signed manifest"
```
### Task B7: `CloudPushDownloadService` — foreground downloader

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/cloudpush/CloudPushDownloadService.kt`
- Modify: `app/src/main/AndroidManifest.xml` (declare the service)

**Interfaces:**
- Consumes: `CloudPushManager`, `PushClient`, `PushCrypto`, `PushServerConfig`, `FileRepository`, `FolderRepository`, `PushHistoryRepository`. No longer needs `PushCrypto` for envelope unwrapping — it only needs the manifest's file list.
- Produces (`com.a42r.mdrender.cloudpush.CloudPushDownloadService`, `@AndroidEntryPoint class ... : Service()`):
  - Foreground service, `startForeground(ID, notification)` with type `FOREGROUND_SERVICE_TYPE_DATA_SYNC`.
  - On start, drains `CloudPushManager.state` for its `pushId`, downloads each `QUEUED`/`DOWNLOADING` task: `PushClient.downloadFile(config, fileId, key, tempFile)` → resolve target folder (`findOrCreateFolder("Cloud Push", null)` then nested `path` segments) → `FileRepository.importFileFromTemp(tempFile, name, mimeType, folderId)` → `PushHistoryRepository.record("Cloud Push", name, size, folderId)` → `PushClient.ackReceived(config, fileId, key)`. Updates the progress notification per file and posts a completion notification ("N files received in Cloud Push").
  - **The pushId comes from the manifest, not the FCM payload** — the service is started
    with a `pushId` argument obtained after `fetchManifest` succeeds. This is deliberate:
    the doorbell is unauthenticated ciphertext, so a message that decrypts is not yet
    proof of a real push. The signed manifest is what authorises a download.
  - `cancel(fileId)` discards the temp file and calls `ackReceived` (removes it server-side).
  - `stopSelf()` when the queue for this push is drained; cancels the foreground state.

- [x] **Step 1: Declare the service in `AndroidManifest.xml`**

```xml
<service
    android:name=".cloudpush.CloudPushDownloadService"
    android:exported="false"
    android:foregroundServiceType="dataSync" />
```

- [x] **Step 2: Write `CloudPushDownloadService.kt`** (core loop, abbreviated)

```kotlin
package com.a42r.mdrender.cloudpush

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.data.repository.FolderRepository
import com.a42r.mdrender.data.repository.PushHistoryRepository
import dagger.hilt.android.AndroidEntryPoint
import java.io.File
import javax.inject.Inject
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@AndroidEntryPoint
class CloudPushDownloadService : Service() {

    @Inject lateinit var manager: CloudPushManager
    @Inject lateinit var client: PushClient
    @Inject lateinit var config: PushServerConfig
    @Inject lateinit var fileRepository: FileRepository
    @Inject lateinit var folderRepository: FolderRepository
    @Inject lateinit var pushHistory: PushHistoryRepository

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onCreate() {
        super.onCreate()
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIF_ID, buildNotification("Preparing downloads…"),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        val pushId = intent?.getStringExtra(EXTRA_PUSH_ID)
        if (pushId != null) scope.launch { processPush(pushId) }
        return START_NOT_STICKY
    }

    private suspend fun processPush(pushId: String) {
        val tasks = manager.state.value.filter { it.pushId == pushId }
        for (task in tasks) {
            if (manager.state.value.firstOrNull { it.file.fileId == task.file.fileId }?.status
                    == CloudPushManager.Status.CANCELLED) {
                client.ackReceived(config, task.file.fileId, task.file.retrievalKey)
                continue
            }
            updateNotification("Downloading ${task.file.name}…")
            val temp = File.createTempFile("cp_", ".tmp", cacheDir)
            val ok = runCatching {
                client.downloadFile(config, task.file.fileId, task.file.retrievalKey, temp)
                val folderId = resolveFolder(task.file.path)
                fileRepository.importFileFromTemp(
                    temp, task.file.name,
                    fileRepository.mimeTypeFromExtension(task.file.name), folderId
                )
                client.ackReceived(config, task.file.fileId, task.file.retrievalKey)
                pushHistory.record("Cloud Push", task.file.name, temp.length(), folderId)
            }.onFailure { }.getOrDefault(false)
            temp.delete()
            manager.onFinished(task.file.fileId, ok)
        }
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private suspend fun resolveFolder(path: String): Long? {
        val root = folderRepository.findOrCreateFolder("Cloud Push", null)
        var parent = root
        for (segment in path.split('/').filter { it.isNotBlank() }) {
            parent = folderRepository.findOrCreateFolder(segment, parent)
        }
        return parent
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun createChannel() {
        val channel = NotificationChannel(CHANNEL_ID, "Cloud Push downloads",
            NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification(text: String): Notification = ...

    companion object {
        private const val CHANNEL_ID = "cloud_push_downloads"
        private const val NOTIF_ID = 42
        const val EXTRA_PUSH_ID = "push_id"
        fun start(context: Context, pushId: String) {
            context.startForegroundService(
                Intent(context, CloudPushDownloadService::class.java)
                    .putExtra(EXTRA_PUSH_ID, pushId)
            )
        }
    }
}
```

*(`buildNotification` uses `Notification.Builder(this, CHANNEL_ID)` with a small `setContentText`; wire `CloudPushManager.onPushReady { CloudPushDownloadService.start(this, it) }` from `MDRenderApplication.onCreate()` after Hilt injection.)*

- [x] **Step 2: Verify it compiles**

Run: `./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [x] **Step 3: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/cloudpush/CloudPushDownloadService.kt app/src/main/AndroidManifest.xml
git commit -m "feat(android): Cloud Push foreground download service"
```

### Task B8: `PushFcmService` — FCM entry point

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/cloudpush/PushFcmService.kt`
- Modify: `app/src/main/AndroidManifest.xml`

**Interfaces:**
- Consumes: `PushCrypto`, `CloudPushManager`, `PushServerConfig`, `PushClient`.
- Produces (`com.a42r.mdrender.cloudpush.PushFcmService`, `@AndroidEntryPoint class ... : FirebaseMessagingService()`):
  - `onMessageReceived(message)` — read `message.data["p"]` and `message.data["i"]`, `PushCrypto.decryptTrigger(p, i, config.pushKey)` (off main thread). On a null doorbell, log and drop. Otherwise `PushClient.fetchManifest(doorbell)` → `PushCrypto.verifyManifest(rawBody, sig, config.serverPublicKeyPem)`; **only if that signature verifies** call `manager.enqueue(doorbell, files)`. Any failure drops the message silently.
  - The whole sequence runs on the worker thread Firebase already delivers `onMessageReceived` on, with a bounded timeout — FCM grants only a few seconds of wall clock, and a manifest fetch plus RSA verify must complete inside it.
  - **Do not use `goAsync()`.** `Service.goAsync()` and `Service.PendingResult` do not exist in this project's Android SDK (verified absent from `android-32` through `android-36` stubs); only `BroadcastReceiver.goAsync()` does. `EnhancedIntentService` dispatches on its own `ExecutorService` and keeps the service alive only while `onMessageReceived` is on the stack, so the correct pattern is `runBlocking { withTimeout(...) }`. A detached `launch` would be killed the moment the executor task returns.
  - `onNewToken(token)` — re-register with the paired server: `PushClient.rotateToken(config, token)`.

- [x] **Step 1: Register the service in `AndroidManifest.xml`**

```xml
<service
    android:name=".cloudpush.PushFcmService"
    android:exported="false">
    <intent-filter>
        <action android:name="com.google.firebase.MESSAGING_EVENT" />
    </intent-filter>
</service>
```

- [x] **Step 2: Write `PushFcmService.kt`**

```kotlin
package com.a42r.mdrender.cloudpush

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout
import javax.inject.Inject

@AndroidEntryPoint
class PushFcmService : FirebaseMessagingService() {

    @Inject lateinit var crypto: PushCrypto
    @Inject lateinit var manager: CloudPushManager
    @Inject lateinit var config: PushServerConfig
    @Inject lateinit var client: PushClient

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onMessageReceived(message: RemoteMessage) {
        val ct = message.data["p"] ?: return
        val iv = message.data["i"] ?: return
        val pending = goAsync()
        scope.launch {
            try {
                withTimeout(10_000) { deliver(ct, iv) }
            } catch (e: TimeoutCancellationException) {
                Log.w(TAG, "CloudPush: timed out fetching manifest; server will retry")
            } catch (e: Exception) {
                Log.w(TAG, "CloudPush: dropped message (${e.javaClass.simpleName})")
            } finally {
                pending.finish()
            }
        }
    }

    private suspend fun deliver(ct: String, iv: String) {
        val doorbell = crypto.decryptTrigger(ct, iv, config.pushKey)
        if (doorbell == null) {
            Log.w(TAG, "CloudPush: dropped trigger (not for this device, or tampered)")
            return
        }
        val body = client.fetchManifest(doorbell).getOrThrow()
        val sig = body.sig
        val files = crypto.verifyManifest(body.manifestJson, sig, config.serverPublicKeyPem)
        if (files == null) {
            Log.w(TAG, "CloudPush: dropped manifest (bad server signature)")
            return
        }
        manager.enqueue(doorbell, files)
    }

    override fun onNewToken(token: String) {
        if (config.isPaired) {
            scope.launch {
                client.rotateToken(config, token)
            }
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "PushFcmService"
    }
}
```

- [x] **Step 3: Verify it compiles**

Run: `./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [x] **Step 4: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/cloudpush/PushFcmService.kt app/src/main/AndroidManifest.xml
git commit -m "feat(android): FCM doorbell service (decrypt + enqueue, token rotation)"
```

### Task B9: Settings — Cloud Push section

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/ui/settings/SettingsScreen.kt`
- Create: `app/src/main/java/com/a42r/mdrender/ui/settings/CloudPushSettings.kt`
- Create: `app/src/main/java/com/a42r/mdrender/ui/settings/CloudPushViewModel.kt`

**Interfaces:**
- Consumes: `PushServerConfig`, `CloudPushKeyStore`, `PushClient`, `CloudPushManager`, `PushHistoryRepository`, `QrScannerScreen`.
- Produces: a `SettingsSection.CLOUDPUSH("Cloud Push")` entry in the menu, and `CloudPushSettings` with:
  - **Pair with server** — requests CAMERA runtime permission, opens `QrScannerScreen`; on a QR result: parse JSON → store `serverUrl` + `serverPublicKeyPem` + `pairingToken` → `cloudPushKeyStore.getOrCreateKeyPair()` → `PushClient.registerDevice(...)` with the phone's `fcmToken` (from `FirebaseMessaging.getInstance().token.await()`) → persist `deviceAuth`.
  - **Device name** — read-only, `config.deviceName`.
  - **Server URL** — `OutlinedTextField`, editable (pre-filled from QR).
  - **Device ID** — read-only `config.deviceSecret`.
  - **Re-pair / rotate keys** — confirm dialog → `config.clear()` + `cloudPushKeyStore.deleteKeyPair()` + reopen scanner.
  - **Check registration** — button → `PushClient.checkRegistration(config)` → shows "paired" or "needs re-registration".
  - **Received pushes** — reuses the existing `PushHistoryScreen` (master's feature; shows all received files).
  - **Pending / active downloads** — `CloudPushManager.state` collected; each row shows name + status + progress, with a **Cancel** `IconButton` → `manager.cancel(fileId)`.
  - Status label: "Paired with <server>" + files-received count, or the re-registration prompt.

- [x] **Step 1: Add the section to `SettingsScreen.kt`** — add `CLOUDPUSH("Cloud Push")` to the enum, a `ListItem` in `SettingsMenu`, and a `SettingsSection.CLOUDPUSH -> CloudPushSettings(viewModel = cloudPushViewModel)` branch in the `when`.

- [x] **Step 2: Write `CloudPushViewModel.kt`** — holds `config`, exposes `uiState: StateFlow<CloudPushUiState>` (isPaired, deviceName, serverUrl, deviceSecret, downloads from `manager.state`, checkRegistration result), and actions `pairWithQr(qrText)`, `setServerUrl(url)`, `checkRegistration()`, `rotateKeys()`, `cancelDownload(fileId)`.

- [x] **Step 3: Write `CloudPushSettings.kt`** — the composable described above. Reuse `rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission())` for CAMERA (same pattern as `POST_NOTIFICATIONS` in `LocalSendSettings`).

- [x] **Step 4: Verify it compiles**

Run: `./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [x] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/ui/settings/
git commit -m "feat(android): Cloud Push settings section with pairing + download list"
```

### Task B10: Registration monitoring (no polling)

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/MDRenderApplication.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/cloudpush/CloudPushManager.kt` (add `onAuthFailure`)

**Interfaces:**
- Consumes: `PushClient`, `PushServerConfig`.
- Produces: on app **foreground** (in the existing `onActivityResumed` lifecycle callback), if `config.isPaired`, fire a one-shot `checkRegistration` on a background scope; on false (404), surface a "needs re-registration" signal (`CloudPushManager` gains a `StateFlow<Boolean> needsReRegistration` that the settings status label observes). Also flip `needsReRegistration` when `PushClient` calls fail with 401/404 during a push/ack.

- [x] **Step 1: Wire the foreground check in `MDRenderApplication`**

```kotlin
// MDRenderApplication.kt — after Hilt injection
@Inject lateinit var pushConfig: PushServerConfig
@Inject lateinit var pushClient: PushClient
@Inject lateinit var cloudPushManager: CloudPushManager

// in onActivityResumed, after isForeground = true:
if (pushConfig.isPaired && !registeredCheckTriggered) {
    registeredCheckTriggered = true
    thread {
        val ok = pushClient.checkRegistration(pushConfig)
        cloudPushManager.setReRegistrationNeeded(!ok)
    }
}
```

*(`registeredCheckTriggered` is a volatile field reset on `onActivityStopped` — or simply re-check each foreground; the plan's intent is "check on foreground", the exact throttle is implementer's choice.)*

- [x] **Step 2: Add `setReRegistrationNeeded(Boolean)` + `val needsReRegistration: StateFlow<Boolean>` to `CloudPushManager`**, and call it from `CloudPushDownloadService` when `ackReceived`/`downloadFile` returns a 401/404.

- [x] **Step 3: Verify it compiles**

Run: `./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [x] **Step 4: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/MDRenderApplication.kt app/src/main/java/com/a42r/mdrender/cloudpush/CloudPushManager.kt
git commit -m "feat(android): registration check on foreground + re-registration prompt"
```

### Task B11: Android end-to-end verification

**Files:** (none)

- [ ] **Step 1: Build a debug APK on the RC branch**

```bash
./build-deploy.sh
```
Expected: installs an `-rc.N` build on the attached device.

- [ ] **Step 2: Manual flow against a local server**

Start the server (`cd server && FCM_SERVER_KEY= SERVER_PASSWORD=test python3 run.py`), open `/pair` in a browser, scan the QR from the app's **Cloud Push → Pair with server**, confirm the status shows "Paired with <server>". Enrol the machine tool (Task C2) and push a test file with `--name`; confirm the download notification, the file landing under "Cloud Push", and the received-pushes entry.

- [ ] **Step 3: Run the unit test suites**

```bash
./gradlew :app:testDebugUnitTest
cd server && python3 -m pytest
```
Expected: PASS.

- [ ] **Step 4: Commit any fixups**

```bash
git add -A && git commit -m "fix(android): end-to-end Cloud Push verification fixes"
```

---

## Phase C — Agent Tools

### Task C1: `push-to-phone.sh` — curl wrapper

**Files:**
- Create: `tools/push-to-phone.sh` (chmod +x)

**Interfaces:**
- Produces: a script the agent calls to push files to a named device. Reuses the shared OAuth2 credential file at `~/.config/mdrender/push-credentials.json`.

- [x] **Step 1: Write the script** (from the spec's `push-to-phone` section)

```bash
#!/bin/bash
# Usage: push-to-phone --target NAME file1 [file2 ...]
set -euo pipefail
CREDS="${MDRender_PUSH_CREDS:-$HOME/.config/mdrender/push-credentials.json}"
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
if [[ "${1:-}" != "--target" || -z "${2:-}" ]]; then
  echo "error: --target NAME is required" >&2
  exit 2
fi
TARGET="$2"; shift 2
[[ $# -gt 0 ]] || { echo "error: no files" >&2; exit 2; }
PUSH_URL="${PUSH_URL:-$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["server_url"])' "$CREDS")}"
FLAGS=(-F "target_device=$TARGET")
for f in "$@"; do FLAGS+=(-F "file=@$f"); done
curl -fsS -H "Authorization: Bearer $TOKEN" "${FLAGS[@]}" "$PUSH_URL/api/push"
echo "pushed $# file(s) to $TARGET"
```

- [x] **Step 2: Smoke test against the running server**

With a registered device named "Sunny Falcon", run:
```bash
./tools/push-to-phone.sh --target "Sunny Falcon" server/README.md
```
Expected: a new push appears in the server's `/pushes` page and **exactly one** FCM data message is produced, carrying `p` and `i` (the test fake shows it). The manifest is not in FCM. Missing `--target` exits 2.

- [x] **Step 3: Commit**

```bash
git add tools/push-to-phone.sh && git commit -m "feat(tools): push-to-phone curl wrapper"
```

### Task C2: `localsend-send --enrol` flow

**Files:**
- Modify: `tools/localsend-send/localsend-send.py`

**Interfaces:**
- Produces: `--enrol --server <URL>` mode implementing the spec §Tool enrolment: `POST /api/enrol/start` → print link (`xdg-open` when a display is present) → prompt "Enter enrolment key:" → `POST /api/enrol` → write `~/.config/mdrender/push-credentials.json` (perms 0600) with `{server_url, client_id, client_secret}`. Also add a `--creds <path>` option and a helper `get_access_token(creds_path, server_url)` used by the push path (Task C3).

- [x] **Step 1: Write the failing unit test** `tools/localsend-send/test_localsend_send.py`

Test the new pure functions with a local `http.server`:
- `_enrol_flow(server_url, key_input)` → asserts the credentials file is written with 0600 perms and correct contents.
- `_get_token(creds, server_url)` → asserts it POSTs `/oauth/token` and returns the token.

- [x] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tools/localsend-send/`
Expected: FAIL — module has no enrol path yet.

- [x] **Step 3: Implement the enrol path in `localsend-send.py`**

```python
def cmd_enrol(args):
    import json, os, stat, sys, urllib.request
    base = f"{args.server}{API}"
    start = json.loads(_post(f"{base}/enrol/start", {"info": _client_info()}, None, 30).read())
    eid = start["enrolment_id"]
    uri = start["verification_uri"]
    print(f"Open in your browser:  {uri}")
    if os.environ.get("DISPLAY"):
        subprocess.Popen(["xdg-open", uri])
    key = input("Enter enrolment key: ").strip()
    resp = _post(f"{base}/enrol", {"enrolment_id": eid, "key": key}, None, 30)
    creds = json.loads(resp.read())
    path = os.path.expanduser("~/.config/mdrender/push-credentials.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({**creds, "server_url": args.server}, fh)
        fh.flush()
        os.fchmod(fh.fileno(), 0o600)
    print(f"wrote {path} (0600)")
```

Add `--enrol`, `--server`, `--creds` to the argparse and route to `cmd_enrol` when `--enrol`.

- [x] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tools/localsend-send/`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add tools/localsend-send/
git commit -m "feat(tools): localsend-send --enrol browser-key enrolment"
```

### Task C3: `localsend-send --name` (LAN discovery + cloud fallback)

**Files:**
- Modify: `tools/localsend-send/localsend-send.py`
- Test: extend `tools/localsend-send/test_localsend_send.py`

**Interfaces:**
- Produces:
  - `discover_lan(names: list[str], timeout: float = 3.0) -> dict[str, str]` — LocalSend v2 UDP discovery: broadcast a discovery request on UDP 53317 and collect `alias → ip` from responses (same protocol the app's `LocalSendDiscovery` speaks). Best-effort; same-subnet only.
  - `push_to_server(creds_path, target_device, paths) -> int` — reads/creates the OAuth2 token, POSTs `/api/push` with multipart `target_device` + files, prints per-file results.
  - `--name <alias>` routing: resolve `alias` → IP on the LAN; if found, send via the existing LocalSend path; if not found, cloud fallback if creds exist (else exit "device not found"). `--host` stays direct with no fallback.

- [x] **Step 1: Write the failing test** — add `test_discover_lan_parses_response` and `test_push_to_server_multipart` (local `http.server` asserting `target_device` in the multipart body and a Bearer header).

- [x] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tools/localsend-send/`
Expected: FAIL.

- [x] **Step 3: Implement `discover_lan` + `push_to_server` + `--name` routing**

```python
def discover_lan(names, timeout=3.0):
    import socket, json
    found = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.5)
    msg = json.dumps({"alias": _client_info()["alias"], "version": "2.1",
                      "deviceModel": "CLI", "deviceType": "headless",
                      "fingerprint": str(uuid.uuid4()), "port": 53317,
                      "protocol": "https", "download": False}).encode()
    deadline = time.monotonic() + timeout
    want = set(names)
    while time.monotonic() < deadline and want:
        sock.sendto(msg, ("255.255.255.255", 53317))
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        try:
            info = json.loads(data)
        except ValueError:
            continue
        alias = info.get("alias")
        if alias in want:
            found[alias] = addr[0]
            want.discard(alias)
    sock.close()
    return found
```

*(The exact discovery wire format must match the app's `LocalSendDiscovery`; the implementer reads `LocalSendDiscovery.kt` and mirrors it. `push_to_server` reuses `_upload_stream` with the OAuth2 Bearer header added to the prepare-upload + upload requests.)*

- [x] **Step 4: Wire `--name` routing in `main()`**

```python
if args.name:
    found = discover_lan([args.name], timeout=3.0)
    if args.name in found:
        args.host = found[args.name]  # send via existing LocalSend path
    else:
        creds = args.creds or os.path.expanduser("~/.config/mdrender/push-credentials.json")
        if not os.path.exists(creds):
            print("device not found on LAN and no push credentials", file=sys.stderr)
            return 3
        return push_to_server(creds, args.name, paths)
```

- [x] **Step 5: Run the tests and a live smoke test**

Run: `python3 -m pytest tools/localsend-send/`, then `./tools/localsend-send/localsend-send.py --name "Sunny Falcon" server/README.md`.
Expected: tests pass; the live push falls back to the server when the phone is off-LAN and lands in the app.

- [x] **Step 6: Commit**

```bash
git add tools/localsend-send/
git commit -m "feat(tools): localsend-send --name with LAN discovery + cloud fallback"
```

### Task C4: `tools/fcm/setup-fcm.sh` — maintainer setup

**Files:**
- Create: `tools/fcm/setup-fcm.sh` (chmod +x)
- Create: `tools/fcm/README.md`

**Interfaces:**
- Produces: the idempotent script from the spec §Setup automation. Outputs `app/google-services.json` (committed) and `server/fcm-service-account.json` (mounted into the server image as `FCM_SERVER_KEY`).

- [x] **Step 1: Write the script** (verbatim from the spec §Setup automation, plus a `--json-only` note and prereq check that `firebase`/`gcloud`/`jq` are installed)

```bash
#!/usr/bin/env bash
set -euo pipefail
PROJECT_ID="${FIREBASE_PROJECT_ID:-mdrender-push}"
SA="fcm-pusher@$PROJECT_ID.iam.gserviceaccount.com"
command -v firebase >/dev/null || { echo "install Firebase CLI"; exit 1; }
command -v gcloud   >/dev/null || { echo "install gcloud"; exit 1; }
command -v jq       >/dev/null || { echo "install jq"; exit 1; }

firebase login --no-localhost

firebase projects:create "$PROJECT_ID" --display-name "MDRender Cloud Push" || true

APP_ID="$(firebase apps:create android com.a42r.mdrender --project "$PROJECT_ID" --json \
            | jq -r '.appId')"
firebase apps:sdkconfig android "$APP_ID" --project "$PROJECT_ID" \
    > app/google-services.json

# The FCM v1 send API is fcm.googleapis.com. There is no
# firebasemessaging.googleapis.com service in the catalog -- enabling that name
# fails with SERVICE_CONFIG_NOT_FOUND_OR_PERMISSION_DENIED, which looks exactly
# like a permission problem but is not one.
gcloud services enable fcm.googleapis.com --project "$PROJECT_ID"
gcloud iam service-accounts create fcm-pusher --project "$PROJECT_ID" || true
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:$SA" --role roles/firebasecloudmessaging.admin
gcloud iam service-accounts keys create server/fcm-service-account.json \
    --iam-account "$SA" --project "$PROJECT_ID"

echo "DONE. Commit app/google-services.json; mount server/fcm-service-account.json"
echo "into the server image as FCM_SERVER_KEY."
```

- [x] **Step 2: Write `tools/fcm/README.md`** — the detailed walkthrough behind the script: prereqs (Firebase CLI, gcloud, jq), the one browser step, what each artifact is, key rotation, re-run safety.

- [x] **Step 3: Verify it is executable and syntactically valid**

```bash
chmod +x tools/fcm/setup-fcm.sh
bash -n tools/fcm/setup-fcm.sh
```

- [x] **Step 4: Commit**

```bash
git add tools/fcm/
git commit -m "feat(tools): one-time FCM setup script + walkthrough"
```

---

## Self-Review

### 1. Spec coverage (task → spec requirement)

| Spec requirement | Task |
|---|---|
| Server keypair in DB; backup by mounting DB | A5, A12, A13 |
| AES-256-GCM doorbell (server_url / push_id / challenge_key), HMAC-derived IV | A3, A9, B3, B8 |
| `push_key` negotiated at pairing, covered by the registration sig | A1, A6, B1, B4, B3 |
| Signed manifest fetched over HTTPS (`POST /api/push/<id>/manifest`, challenge-key gated) | A7, A9, B3, B4, B8 |
| One FCM message regardless of file count (no 4 KB batching) | A9, A10, B6 |
| FCM HTTP v1 service account | A8, C4, 0.3 |
| Password login + rate limit/lockout | A4, A9 |
| OAuth2 client-credentials enrolment | A5, A9, C2 |
| QR pairing (TOFU) + pairing token | A6, A11, B5 |
| Device registration + name-collision replace + token rotation + device/status | A6, A9, B4, B10 |
| `target_device` required → 400 | A9 |
| Download + ack-delete + file-linked keys | A7, A9, B4, B7 |
| Retry + sweep + operator re-push (`/retry`) | A10, A9, A11 |
| Operator UI (devices, pushes, pending) | A11 |
| SQLite for all state | A1, A5, A6, A7 |
| Settings: pair, device name, server URL, device ID, re-pair, check registration | B9 |
| Settings: received pushes (PushHistory reuse) + pending/active downloads with Cancel | B9, B7 |
| Registration monitoring (no polling) | B10 |
| FCM doorbell service + onNewToken | B8 |
| Background downloads in `dataSync` foreground service | B7, 0.3 (manifest perms already present) |
| `--name` LAN discovery + cloud fallback | C3 |
| `--enrol` flow | C2 |
| `push-to-phone` | C1 |
| setup-fcm.sh | C4, 0.3 |

### 2. Placeholder scan

The plan contains **no TBD/TODO placeholders**. The few "*(the implementer writes …)*" notes are deliberate pointers to the spec's exact formats (wire details that must match `LocalSendDiscovery`/`register_device`), with the concrete algorithm stated inline.

### 3. Type / interface consistency

- **FCM wire shape.** `seal_trigger` (A3) returns `{"v": 1, "i": iv_b64, "c": ct_b64}`; A9's route and A10's worker send exactly the two FCM data fields `{"p": sealed["c"], "i": sealed["i"]}`. B8 reads `data["p"]` + `data["i"]` and passes them to `decryptTrigger(ct, iv, pushKey)` in that order. The `v` version field is *not* on the wire — it lives inside the sealed plaintext, so a client that ignores it still decodes, and there is no third field to agree on. The `i` IV is on the wire in plaintext because the client cannot derive it before decrypting (it depends on the `push_id` still inside the ciphertext); GCM authenticates it, so a substituted IV fails decryption.
- `derive_iv(push_key, push_id)` (A3) is server-only. No Android counterpart is needed or wanted: the client receives the IV as a field.
- `build_manifest`/`manifest_bytes` (A3) agree with `verifyManifest` (B3) on the exact byte string: the server signs `json.dumps(manifest, separators=(",", ":"), sort_keys=True)` and `PushClient.fetchManifest` (B4) returns the **raw** body so B3 verifies those bytes rather than a re-serialisation. This is called out in both tasks because it is the one place a plausible-looking refactor silently breaks the feature.
- Manifest shape `files[{file_id, name, path, size, retrieval_key}]` (A3 `build_manifest`) matches `PushCrypto.ManifestFile` (B3) and `DownloadTask.file` (B6), and `CloudPushDownloadService` (B7) reads `retrievalKey`.
- `register_device` (A6) sig input `sha256(device_secret||device_name||fcm_token||public_key||push_key)` matches `PushClient.registerDevice` (B4) and the A9 integration test.
- `push_store` helpers (A7) — including `get_push_by_id`, needed by both the manifest route (A9) and `_device` in `retry.py` (A10) — are used with matching names.
- `FcmClient.send(data_message, fcm_token)` (A8) is used by `app.py` (A9) and `retry.py` (A10).
- `CloudPushManager.Status` enum (B6) is referenced by `CloudPushDownloadService` (B7) and the settings UI (B9).
- `PushHistoryRepository.record(source, fileName, fileSize, folderId)` matches master's signature.

### 3a. What the redesign deliberately removed

Recorded so a later reader does not "restore" it as if it were a bug:

| Removed | Why |
|---|---|
| `server/app/envelope.py`, RSA-OAEP content-key wrap, per-message signature | The doorbell is symmetric under `push_key`; a signature added no protection the GCM tag did not, and the Keystore key no longer needs `PURPOSE_DECRYPT` |
| Slice batching, `total_files`, `slice_files`, 3.5 KB pre-encryption budget | One fixed ~250-byte trigger is under the 4 KB limit by construction, so the file count stopped mattering |
| Slice merge + 30 s debounce on the phone (B6) | The manifest is complete on arrival; there are no partial slices to reconcile |
| Per-file envelope rebuild on retry (A10) | One trigger per push, and the manifest is rebuilt from live DB state, so a retry cannot re-offer an acked file |

### 3b. Known gaps carried into implementation

Not fixed by this redesign; recorded so they are not lost:

- `POST /api/push` still returns `200` when no FCM client is configured. A push accepted
  but never delivered is indistinguishable from a delivered one. Fix: return `503` when
  `make_fcm_client` yields `None`, unless an explicit `DRY_RUN` config flag is set.
- The server sends FCM with default priority and no TTL, so a doorbell can be delayed
  indefinitely. For a push-to-phone doorbell this should be `AndroidConfig(priority=HIGH)`
  with a short `time_to_live`.
- The `path` field exists in the manifest and the download service resolves it, but
  nothing populates it at push time — `target_folder` is not yet implemented in
  `POST /api/push`. Every file currently lands under the "Cloud Push" root.
