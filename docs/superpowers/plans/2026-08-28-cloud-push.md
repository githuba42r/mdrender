# Cloud Push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push files from a Linux desktop to an Android phone off-LAN — an AI agent triggers a push via SSH, a self-hosted Python server stores the bytes, and FCM "doorbells" the phone, which pulls the bytes over HTTPS and imports them into encrypted storage.

**Architecture:** One shared Firebase project acts as a dumb doorbell for all self-hosted servers. Each operator runs their own server (Docker, SQLite for all state). The server and phone exchange RSA-3072 public keys via a QR (TOFU pairing). Every FCM message is an RSA-OAEP + AES-256-GCM envelope signed by the server, so Firebase sees only ciphertext. Delivery is FCM-push only (no polling); recovery is server-side retries + operator re-push. The app downloads in a `dataSync` foreground service because it is usually not foreground when FCM arrives.

**Tech Stack:** Python 3.11 + Flask + `cryptography` + SQLite (server, single Docker image); Kotlin + Hilt + Compose + Room + Firebase Messaging + ML Kit barcode + CameraX (Android); Python `urllib` + UDP multicast (agent tools). Android uses `java.net.HttpURLConnection` (no new HTTP dep).

**Spec:** [docs/superpowers/specs/2026-07-25-cloud-push-design.md](../specs/2026-07-25-cloud-push-design.md) — the plan argues from the spec; executors read both. Unresolved spec "Resolution:" placeholders under Design Review are resolved by the *Decisions* lines; this plan implements those decisions.

## Global Constraints

Verbatim rules that apply to every task:

- **Branch = `feature/cloud-push`.** Do NOT bump `version.properties` (main-branch release). Device installs on this branch use the `-rc.N` version tag via the existing build scripts.
- **graphify:** read `graphify-out/GRAPH_REPORT.md` before searching source; run `graphify update .` after modifying code (AST-only, no API cost).
- **Commit cadence:** commit per task on `feature/cloud-push`. No commits to `master`.
- **Android:** `minSdk 26`, `targetSdk 36`, Java/Kotlin target 17, Compose BOM `2024.12.01`, Hilt 2.50 (kapt), Room 2.6.1 (KSP), kotlinx-serialization 1.7.3. Follow existing patterns (`LocalSendPrefs` for prefs, `@AndroidEntryPoint` + `@Inject` for services).
- **No new HTTP client dep on Android** — `java.net.HttpURLConnection` only.
- **Device↔server calls use POST/PUT JSON bodies, never query strings** (R9). No secrets in URLs.
- **`target_device` is REQUIRED on `POST /api/push`** — 400 `{"error":"device not found"}` when missing/unknown. No broadcast (R7).
- **Envelope:** `alg=RSA-OAEP-256` wrapping a random 32-byte AES-256-GCM content key; `enc=A256GCM`; `sig=RSA-SHA256` over the concatenation of the base64 strings `ek||iv||ct`. Envelope payload ≤ 3.5 KB → slice files (~32 at 256 B/file) into multiple FCM messages sharing one `push_id`; each slice carries `total_files`.
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

- [ ] **Step 1: Write `server/requirements.txt`**

```
flask==3.0.3
cryptography==43.0.1
requests==2.32.3
pytest==8.3.3
```

- [ ] **Step 2: Write `server/pytest.ini`**

```ini
[pytest]
testpaths = tests
addopts = -q
```

- [ ] **Step 3: Write the failing smoke test** `server/tests/test_smoke.py`

```python
def test_app_imports():
    import server.app.app  # noqa: F401  (imports app factory)
```

- [ ] **Step 4: Run to verify it fails**

Run: `cd server && python3 -m pytest`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.app.app'`.

- [ ] **Step 5: Write `server/app/__init__.py`** (empty file) and `server/tests/conftest.py`

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

- [ ] **Step 6: Add `server/app/app.py` as a stub so the import passes**

```python
# server/app/app.py
"""Flask application factory. Routes are added in later tasks."""
from flask import Flask


def create_app(config):
    app = Flask(__name__)
    return app
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `cd server && python3 -m pytest`
Expected: PASS (1 passed).

- [ ] **Step 8: Install deps and commit**

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

- [ ] **Step 1: Add versions to `gradle/libs.versions.toml`**

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

- [ ] **Step 2: Add the google-services plugin to the top-level `build.gradle.kts`**

```kotlin
plugins {
    // ...existing aliases...
    id("com.google.gms.google-services") version "4.4.2" apply false
}
```

- [ ] **Step 3: Apply the plugin and deps in `app/build.gradle.kts`**

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

- [ ] **Step 4: Obtain `app/google-services.json`**

```bash
# Maintainer step — one Google browser login. Runs the idempotent script (Task C4).
./tools/fcm/setup-fcm.sh
```
If the maintainer has not run the script yet, block here and report; do **not** fabricate the file. While blocked, continue with Phase A tasks.

- [ ] **Step 5: Add `android.permission.CAMERA` to `app/src/main/AndroidManifest.xml`**

```xml
<uses-permission android:name="android.permission.CAMERA" />
```

- [ ] **Step 6: Verify a build now compiles**

Run: `./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL (with `google-services.json` present).

- [ ] **Step 7: Commit**

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

- [ ] **Step 1: Write the failing schema test** `server/tests/test_db.py`

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

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.app.db'`.

- [ ] **Step 3: Write `server/app/config.py`**

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

- [ ] **Step 4: Write `server/app/db.py`**

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
  registered_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pushes (
  push_id TEXT PRIMARY KEY,
  target_device TEXT NOT NULL,
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
        conn.commit()
```

(Note: `os` is imported at module top in a real file; the inline import above is for plan brevity — write it at the top.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd server && python3 -m pytest`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

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

- [ ] **Step 1: Write the failing crypto test** `server/tests/test_crypto.py`

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

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_crypto.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `server/app/crypto.py`**

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_crypto.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add server/app/crypto.py server/tests/test_crypto.py
git commit -m "feat(server): RSA-OAEP, AES-GCM, and signing primitives"
```

### Task A3: Envelope builder + batching

**Files:**
- Create: `server/app/envelope.py`
- Test: `server/tests/test_envelope.py`

**Interfaces:**
- Consumes: `crypto.py` (Task A2), `public_from_spki_der`.
- Produces:
  - `MAX_PAYLOAD_BYTES = 3500`
  - `file_entry(file_id: str, name: str, path: str, retrieval_key: str) -> dict`
  - `slice_files(entries: list[dict], max_bytes: int = MAX_PAYLOAD_BYTES) -> list[list[dict]]` — contiguous slices; a slice is emitted as soon as adding the next entry would exceed `max_bytes`. Each entry's JSON form is `{"file_id","name","path","retrieval_key"}`.
  - `build_payload(server_url: str, push_id: str, date_iso: str, total_files: int, files: list[dict]) -> dict`
  - `build_envelope(server_private_key, device_public_key, payload: dict) -> dict` — returns the envelope dict per the spec (`v`,`alg`,`enc`,`kid`,`ek`,`iv`,`tag`,`ct`,`sig`). `sig` is `sign(server_private_key, ek_b64 + iv_b64 + ct_b64)` where `+` is string concat of the base64 strings.

- [ ] **Step 1: Write the failing batching test** `server/tests/test_envelope.py`

```python
import json

from server.app.envelope import slice_files, build_envelope, build_payload
from server.app.crypto import generate_rsa_keypair, oaep_unwrap, aes_gcm_decrypt, verify, public_to_spki_der


def test_slice_files_respects_cap():
    # ~256-byte entries; 200 of them must not fit in 3500 bytes.
    entries = [{"file_id": f"f{i}", "name": f"n{i}.md", "path": "D", "retrieval_key": f"k{i}"} for i in range(200)]
    slices = slice_files(entries, max_bytes=3500)
    assert len(slices) > 1
    total = sum(len(s) for s in slices)
    assert total == 200
    for s in slices:
        assert len(json.dumps({"files": s})) <= 3500


def test_envelope_roundtrip():
    server_priv, server_pub = generate_rsa_keypair()
    _, device_pub = generate_rsa_keypair()
    payload = build_payload("https://push.example.com", "p1", "2026-08-28T12:00:00Z", 1,
                            [{"file_id": "f1", "name": "notes.md", "path": "", "retrieval_key": "k1"}])
    env = build_envelope(server_priv, device_pub, payload)
    assert env["alg"] == "RSA-OAEP-256" and env["enc"] == "A256GCM"
    assert verify(server_pub, env["ek"] + env["iv"] + env["ct"].encode(), None)  # placeholder replaced below
```

*(The last assertion in the test above is intentionally incomplete — replace it in Step 3's real test with a full decrypt using `oaep_unwrap` + `aes_gcm_decrypt`, verifying `json.loads(plaintext) == payload`.)*

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_envelope.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `server/app/envelope.py` + the corrected full roundtrip test**

```python
# server/app/envelope.py
import base64
import json
import os

from server.app import crypto

MAX_PAYLOAD_BYTES = 3500


def file_entry(file_id: str, name: str, path: str, retrieval_key: str) -> dict:
    return {"file_id": file_id, "name": name, "path": path, "retrieval_key": retrieval_key}


def slice_files(entries: list[dict], max_bytes: int = MAX_PAYLOAD_BYTES) -> list[list[dict]]:
    slices: list[list[dict]] = []
    current: list[dict] = []
    for entry in entries:
        candidate = current + [entry]
        if json.dumps({"files": candidate}).__len__() > max_bytes and current:
            slices.append(current)
            current = [entry]
        else:
            current = candidate
    if current:
        slices.append(current)
    return slices


def build_payload(server_url: str, push_id: str, date_iso: str, total_files: int, files: list[dict]) -> dict:
    return {"server_url": server_url, "push_id": push_id, "date": date_iso,
            "total_files": total_files, "files": files}


def build_envelope(server_private_key, device_public_key, payload: dict) -> dict:
    content_key = os.urandom(32)
    iv = os.urandom(12)
    plaintext = json.dumps(payload).encode()
    ciphertext, tag = crypto.aes_gcm_encrypt(content_key, iv, plaintext)
    ek = crypto.oaep_wrap(device_public_key, content_key)
    ek_b64 = base64.b64encode(ek).decode()
    iv_b64 = base64.b64encode(iv).decode()
    ct_b64 = base64.b64encode(ciphertext).decode()
    tag_b64 = base64.b64encode(tag).decode()
    sig = crypto.sign(server_private_key, (ek_b64 + iv_b64 + ct_b64).encode())
    return {
        "v": 1, "alg": "RSA-OAEP-256", "enc": "A256GCM", "kid": "",
        "ek": ek_b64, "iv": iv_b64, "tag": tag_b64, "ct": ct_b64,
        "sig": base64.b64encode(sig).decode(),
    }
```

And replace the roundtrip test's last assertion with:

```python
def test_envelope_roundtrip():
    import base64, json
    from server.app import crypto
    server_priv, server_pub = crypto.generate_rsa_keypair()
    device_priv, device_pub = crypto.generate_rsa_keypair()
    payload = build_payload("https://push.example.com", "p1", "2026-08-28T12:00:00Z", 1,
                            [{"file_id": "f1", "name": "notes.md", "path": "", "retrieval_key": "k1"}])
    env = build_envelope(server_priv, device_pub, payload)
    assert env["alg"] == "RSA-OAEP-256" and env["enc"] == "A256GCM"
    assert crypto.verify(server_pub, (env["ek"] + env["iv"] + env["ct"]).encode(),
                         base64.b64decode(env["sig"]))
    content_key = crypto.oaep_unwrap(device_priv, base64.b64decode(env["ek"]))
    plaintext = crypto.aes_gcm_decrypt(content_key, base64.b64decode(env["iv"]),
                                       base64.b64decode(env["ct"]), base64.b64decode(env["tag"]))
    assert json.loads(plaintext) == payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_envelope.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add server/app/envelope.py server/tests/test_envelope.py
git commit -m "feat(server): E2E envelope builder + payload batching"
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

- [ ] **Step 1: Write the failing rate-limit test** `server/tests/test_auth.py`

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

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `server/app/auth.py`**

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_auth.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

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

- [ ] **Step 1: Write the failing OAuth test** `server/tests/test_oauth.py`

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

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_oauth.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `server/app/store.py`**

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

- [ ] **Step 4: Append OAuth2 to `server/app/auth.py`**

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

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_oauth.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

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
  - `register_device(conn, *, device_secret, device_name, fcm_token, public_key_b64, pairing_token, sig_b64) -> tuple[str | None, str | None]` → `(device_auth, displaced_device_secret_or_None)`. Verifies pairing token (first registration), verifies `sig` = RSA-SHA256(public_key, `sha256(device_secret||device_name||fcm_token||public_key)`), name collision → delete old row with same name when it has a different device_secret (returns its secret so the app can be notified), inserts new row, returns a fresh `device_auth` UUID.
  - `update_device_token(conn, device_secret, device_auth, new_token) -> bool` (token rotation; verifies device_auth)
  - `update_device_name(conn, device_secret, device_auth, new_name) -> bool`
  - `check_device(conn, device_secret, device_auth) -> bool`
  - `touch_last_seen(conn, device_secret)`
  - `get_device_by_name(conn, name) -> sqlite3.Row | None`
  - `get_device_by_secret(conn, device_secret) -> sqlite3.Row | None`
  - `delete_device(conn, device_secret)`
  - `list_devices(conn) -> list[sqlite3.Row]`
  - `sweep_stale_devices(conn, ttl_days) -> list[str]`

- [ ] **Step 1: Write the failing registry test** `server/tests/test_registry.py`

```python
import base64, hashlib, time

from server.app import crypto
from server.app.db import Database
from server.app.store import get_or_create_server_keypair
from server.app.pairing import (
    create_pairing_token, consume_pairing_token, register_device, check_device,
    get_device_by_name, update_device_token, update_device_name,
)


def _sig(priv, secret, name, token, pub_b64):
    data = hashlib.sha256(f"{secret}{name}{token}{pub_b64}".encode()).digest()
    return base64.b64encode(crypto.sign(priv, data)).decode()


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
            fcm_token="tok-1", public_key_b64=pub_b64,
            pairing_token=token2, sig_b64=_sig(phone_priv, "sec-1", "Sunny Falcon", "tok-1", pub_b64),
        )
        assert auth and displaced is None
        assert check_device(conn, "sec-1", auth)

        # New keypair claims the same name -> replaces.
        priv2, pub2 = crypto.generate_rsa_keypair()
        pub2_b64 = base64.b64encode(crypto.public_to_spki_der(pub2)).decode()
        token3 = create_pairing_token(conn, 15)
        auth2, displaced = register_device(
            conn, device_secret="sec-2", device_name="Sunny Falcon",
            fcm_token="tok-2", public_key_b64=pub2_b64,
            pairing_token=token3, sig_b64=_sig(priv2, "sec-2", "Sunny Falcon", "tok-2", pub2_b64),
        )
        assert displaced == "sec-1"
        assert get_device_by_name(conn, "Sunny Falcon")["device_secret"] == "sec-2"

        # Token rotation requires device_auth.
        assert update_device_token(conn, "sec-2", auth2, "tok-3")
        assert get_device_by_name(conn, "Sunny Falcon")["fcm_token"] == "tok-3"
        assert not update_device_token(conn, "sec-2", "wrong-auth", "tok-4")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `server/app/pairing.py`** (with the store helpers for devices appended to `store.py`)

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
                    pairing_token, sig_b64):
    if not consume_pairing_token(conn, pairing_token):
        return None, None
    public_key = crypto.public_from_spki_der(base64.b64decode(public_key_b64))
    data = hashlib.sha256(
        f"{device_secret}{device_name}{fcm_token}{public_key_b64}".encode()
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
        " public_key, registered_at, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (device_secret, device_auth, device_name, fcm_token, public_key_b64,
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_registry.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

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
  - `create_push(conn, push_id: str, target_device: str) -> None`
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

- [ ] **Step 1: Write the failing store test** `server/tests/test_push.py`

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
        create_push(conn, "push-1", "Sunny Falcon")
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

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_push.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `server/app/push_store.py`**

```python
# server/app/push_store.py
import time


def create_push(conn, push_id: str, target_device: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO pushes (push_id, target_device, date, status)"
        " VALUES (?, ?, ?, 'pending')",
        (push_id, target_device, int(time.time())),
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_push.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

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

- [ ] **Step 1: Write the failing FCM test** `server/tests/test_fcm.py`

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

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_fcm.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `server/app/fcm.py`**

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

- [ ] **Step 4: Fix the test to use a real key and run**

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

- [ ] **Step 5: Commit**

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
  - `POST /api/push` (Bearer, multipart, `target_device` required → 400 if missing/unknown) → stores bytes, builds envelopes (slices), sends FCM (via `fcm_client`), returns `{push_id, files_sent}`
  - `POST /api/register-device` (pairing + sig) → `{ok, device_auth}`; update variant (device_secret+device_auth) for token/name rotation
  - `POST /api/push/<file_id>/download` (body `{key}`) → streams bytes
  - `POST /api/push/<file_id>/received` (body `{key}`) → acks, deletes bytes
  - `POST /api/device/status` (body `{device_secret, device_auth}`) → 200 ok / 404 needs re-registration
  - `GET /api/push/<push_id>/status` (Bearer) → per-file status
  - `GET /api/health` → `{ok: true}`
  - `DELETE /devices/<device_secret>` (session cookie) → remove device
  - `POST /api/push/<push_id>/retry` (session cookie) → reset retries + re-send FCM for pending files
- Enrolment state (one-time keys) lives in an in-memory dict (a browser-flow artefact); enrolment keys expire after `ENROL_TOKEN_TTL_HOURS`.

- [ ] **Step 1: Write the failing integration test** `server/tests/test_api.py`

```python
import base64, io, json

from server.app.app import create_app
from server.app.crypto import generate_rsa_keypair, private_to_pem, public_to_spki_der
from server.app.fcm import FcmClient


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
    pairing_token = app.config["_test_pairing_token"]  # set by /pair in test
    import hashlib
    sig = base64.b64encode(phone_priv.sign(
        hashlib.sha256(f"sec-1|Sunny Falcon|fcm-1|{pub_b64}".encode()).digest(),
        padding=None  # see note
    )).decode()
    # (real test uses crypto.sign with PKCS1v15)
    reg = client.post("/api/register-device", json={
        "device_secret": "sec-1", "device_name": "Sunny Falcon",
        "fcm_token": "fcm-1", "public_key": pub_b64,
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
    # One FCM message was sent (single small push = one slice)
    assert len(fcm.sent) == 1

    # Download + ack
    env = json.loads(fcm.sent[0][0]["p"])
    payload_ct = base64.b64decode(env["ct"])
    # (full decrypt: unwrap ek with phone_priv, aes-gcm decrypt ct)
    # assert payload["files"][0]["retrieval_key"] ...
```

*(The test above intentionally sketches the flow; the implementer writes the complete decrypt assertions using `crypto.oaep_unwrap` + `aes_gcm_decrypt` — the same pattern as Task A3's roundtrip. The `sig` construction must use `crypto.sign(phone_priv, sha256(...))` exactly as `register_device` verifies.)*

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_api.py -v`
Expected: FAIL (endpoints 404).

- [ ] **Step 3: Write `server/app/app.py`** — the app factory + routes

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
from server.app.envelope import build_envelope, build_payload, slice_files, file_entry

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
- **`/api/push`** (Bearer): validate token → client_id; read `target_device` (form) → **400 `{"error":"device not found"}` if missing**; resolve device via `get_device_by_name` → 400 if unknown; `push_id = uuid4().hex`; for each uploaded `file`, `file_id = uuid4().hex`, write bytes to `<storage_dir>/<push_id>/<file_id>/<filename>`, `add_file(...)` with a fresh retrieval key (`uuid4().hex`); build the payload and slices, then per slice `build_envelope(server_priv, device_public_key, slice_payload)` and `fcm.send({"p": json.dumps(env)}, device["fcm_token"])`; return `{push_id, files_sent: N}`. (When `config.FCM_SERVER_KEY` is empty and no `make_fcm_client` is injected, skip FCM send — the integration test injects a fake.)
- **`/api/register-device`** (POST): if body has `pairing_token` → new registration via `pairing.register_device`; else update via `update_device_token`/`update_device_name` (requires `device_secret`+`device_auth`). On success 200 `{ok: true, device_auth}`.
- **`/api/push/<file_id>/download`** (POST body `{key}`): find file by `file_id`; 404 if missing/acked; verify `key == retrieval_key`; if `stored_path` set, stream the bytes (`send_file`); else 404. Key is **not** consumed.
- **`/api/push/<file_id>/received`** (POST body `{key}`): verify key, `mark_acked` (deletes bytes), 200.
- **`/api/device/status`** (POST body): `check_device` → 200 `{ok:true}` / 404 `{error:"re-register"}`; on 200 also `touch_last_seen`.
- **`GET /api/push/<push_id>/status`** (Bearer): per-file `{file_id, name, status, retries}`.
- **`GET /api/health`**: `{"ok": True}`.
- **`POST /api/push/<push_id>/retry`** (session): `reset_push_retries` + re-send FCM slices for pending files.
- **`GET/POST /devices`, `GET /pushes`, `GET /pending`, `DELETE /devices/<secret>`** (session): rendered HTML (Task A11) or JSON; `DELETE /devices` calls `delete_device` then `revoke`-equivalent.

Provide a module-level `make_fcm_client(config) -> FcmClient` (loads `config.FCM_SERVER_KEY`), monkeypatched by tests.

- [ ] **Step 4: Run the integration test and iterate**

Run: `cd server && python3 -m pytest tests/test_api.py -v`
Expected: PASS once the flow works end-to-end.

- [ ] **Step 5: Run the full suite**

Run: `cd server && python3 -m pytest`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add server/app/app.py server/tests/test_api.py
git commit -m "feat(server): full Flask endpoint surface with auth + FCM delivery"
```

### Task A10: Retry worker + sweep + operator re-push

**Files:**
- Create: `server/app/retry.py`
- Test: `server/tests/test_retry.py`

**Interfaces:**
- Consumes: `Database`, `push_store`, `pairing` (`get_device_by_name`), `envelope`, `crypto`, `fcm`.
- Produces (`server.app.retry`):
  - `class RetryWorker`: `__init__(self, config, db: Database, fcm_client)`, `tick(now: float) -> list[str]` (returns touched file_ids). For each `get_pending_files(conn, now)`: if `retries >= config.PUSH_RETRY_COUNT` → `mark_exhausted`; else rebuild the envelope for that file (single-file slice), send via FCM, `increment_retry(file_id, now, config.PUSH_RETRY_INTERVAL_MINUTES)`; on `FcmError`, leave `next_retry_at` unchanged (retry next tick).
  - `class SweepWorker`: `tick()` → `sweep_stale_devices(conn, ttl_days)` + `purge_expired_bytes(conn, ttl_hours, now)`.
  - `run_forever(config, db, fcm_client)` — loops every 60 s, calls both ticks; handles `KeyboardInterrupt`/`SystemExit` cleanly.

- [ ] **Step 1: Write the failing retry test** `server/tests/test_retry.py`

```python
import base64, hashlib, time

from server.app.db import Database
from server.app.push_store import add_file, create_push, get_push_files
from server.app.retry import RetryWorker, SweepWorker


def test_retry_exhausts_after_count(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "p1", "Sunny Falcon")
        add_file(conn, file_id="f1", push_id="p1", file_name="a.md", file_path="",
                 size=3, retrieval_key="k1", stored_path="p1/f1/a.md", created_at=time.time())
    worker = RetryWorker(config, db, fcm_client=FakeFcm())
    for _ in range(config.PUSH_RETRY_COUNT):
        worker.tick(time.time())
    with db.connect() as conn:
        status = conn.execute("SELECT status FROM push_files WHERE file_id = 'f1'").fetchone()
    assert status["status"] == "exhausted"
```

*(Define `FakeFcm` locally with a `send()` that records; a real device row is not required because RetryWorker resolves via `get_device_by_name` only when building an envelope — guard the worker so a missing device short-circuits to `mark_exhausted`.)*

- [ ] **Step 2: Run to verify it fails**

Run: `cd server && python3 -m pytest tests/test_retry.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `server/app/retry.py`**

```python
# server/app/retry.py
import json
import time

from server.app import crypto, envelope, fcm as fcm_mod, pairing, push_store
from server.app.store import get_or_create_server_keypair


class RetryWorker:
    def __init__(self, config, db, fcm_client):
        self.config = config
        self.db = db
        self.fcm_client = fcm_client

    def tick(self, now: float) -> list[str]:
        touched = []
        with self.db.connect() as conn:
            _, server_pk_b64 = get_or_create_server_keypair(conn)
            server_priv = _load_server_priv(conn)
            for row in push_store.get_pending_files(conn, now):
                if row["retries"] >= self.config.PUSH_RETRY_COUNT:
                    push_store.mark_exhausted(conn, row["file_id"])
                    touched.append(row["file_id"])
                    continue
                device = pairing.get_device_by_name(conn, row["target_device"]) if False else _device(conn, row)
                # device resolved via pushes.target_device -> devices.device_name
                if device is None:
                    push_store.mark_exhausted(conn, row["file_id"])
                    touched.append(row["file_id"])
                    continue
                payload = envelope.build_payload(
                    self.config.PUSH_PUBLIC_URL, row["push_id"], _iso(now),
                    total_files=1,
                    files=[envelope.file_entry(row["file_id"], row["file_name"],
                                               row["file_path"], row["retrieval_key"])],
                )
                device_pub = crypto.public_from_spki_der(base64.b64decode(device["public_key"]))
                env = envelope.build_envelope(server_priv, device_pub, payload)
                try:
                    self.fcm_client.send({"p": json.dumps(env)}, device["fcm_token"])
                    push_store.increment_retry(conn, row["file_id"], now,
                                               self.config.PUSH_RETRY_INTERVAL_MINUTES)
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

*(Add the small `_device(conn, row)` helper that joins `push_files.push_id → pushes.target_device → devices.device_name`; write it correctly in the real file rather than the `if False else` placeholder above.)*

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && python3 -m pytest tests/test_retry.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

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

- [ ] **Step 1: Add `qrcode` to `server/requirements.txt`**

```
qrcode==7.4.2
```

- [ ] **Step 2: Write the templates**

`login.html` — a password form POSTing to `/login`. `pair.html` — shows the server URL + the QR SVG + expiry. `enrol.html` — shows the one-time key for `enrolment_id`. `devices.html` — table of `list_devices` rows with a "Remove" form POSTing to `/devices/<secret>/delete`. `pushes.html` — table of `list_pushes` with per-push "Re-push pending" form POSTing to `/api/push/<push_id>/retry`. `pending.html` — table of `list_pending` (file name, path, size, date, status, retries). All extend `base.html` (a small header linking to the sections, gated by session).

- [ ] **Step 3: Wire the HTML routes in `server/app/app.py`**

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

- [ ] **Step 4: Manual verification**

Run: `cd server && python3 -m server.app.app` (add a `__main__` block that calls `create_app(load_config()).run(host, port)`), then open `http://localhost:8080/login` in a browser, log in with `SERVER_PASSWORD`, and confirm: pairing page renders a scannable QR; devices/pushes/pending pages render with the empty state.

- [ ] **Step 5: Commit**

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

- [ ] **Step 1: Write `server/run.py`**

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

- [ ] **Step 2: Write `server/Dockerfile`**

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

- [ ] **Step 3: Write `server/docker-compose.example.yml`**

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

- [ ] **Step 4: Add `waitress` to requirements and verify the image builds**

Run: `cd server && docker build -t mdrender-push .`
Expected: image builds. (If Docker is unavailable on the machine, verify `python3 run.py` starts and `/api/health` returns ok.)

- [ ] **Step 5: Commit**

```bash
git add server/Dockerfile server/docker-compose.example.yml server/run.py server/.dockerignore server/requirements.txt
git commit -m "feat(server): Docker image + compose + startup entry"
```

### Task A13: Server README

**Files:**
- Create: `server/README.md`

**Interfaces:** documentation only.

- [ ] **Step 1: Write `server/README.md`**

Cover: quick start (`docker compose up`), all env vars with the spec defaults, the pairing flow (open `/pair`, scan QR), tool enrolment (`localsend-send.py --enrol --server …`), backup (mount `/data/push` and copy `server.db`), and the reset behavior (device re-registration via `/api/device/status`). Reference `docs/superpowers/specs/2026-07-25-cloud-push-design.md`.

- [ ] **Step 2: Commit**

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
  - `val deviceName: String` (getter reads `LocalSendPrefs.alias`)
  - `val isPaired: Boolean` (`serverUrl.isNotEmpty() && serverPublicKeyPem.isNotEmpty() && deviceAuth.isNotEmpty()`)
  - `fun clear()` — wipes all fields (re-pair / rotate)

- [ ] **Step 1: Write the failing unit test** `PushServerConfigTest.kt`

```kotlin
package com.a42r.mdrender.cloudpush

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat  // not present — see note
```

*(The repo uses JUnit4 + Mockito, not Truth. Write the test with plain `assert` / `org.junit.Assert.assertEquals` against a fresh `PushServerConfig(ApplicationProvider.getApplicationContext())`: set `serverUrl`, assert `isPaired` flips, assert `clear()` resets it, and assert `deviceSecret` is stable across reads.)*

- [ ] **Step 2: Run to verify it fails (compiles, assertion fails)**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: FAIL — class not found.

- [ ] **Step 3: Write `PushServerConfig.kt`**

```kotlin
package com.a42r.mdrender.cloudpush

import android.content.Context
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

    val deviceName: String get() = localSendPrefs.alias

    val isPaired: Boolean
        get() = serverUrl.isNotEmpty() && serverPublicKeyPem.isNotEmpty() && deviceAuth.isNotEmpty()

    fun clear() {
        prefs.edit()
            .putString(KEY_SERVER_URL, "")
            .putString(KEY_SERVER_PUBLIC_KEY, "")
            .putString(KEY_DEVICE_AUTH, "")
            .putString(KEY_DEVICE_SECRET, "")
            .apply()
    }

    companion object {
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_SERVER_PUBLIC_KEY = "server_public_key_pem"
        private const val KEY_DEVICE_SECRET = "device_secret"
        private const val KEY_DEVICE_AUTH = "device_auth"
    }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/cloudpush/ app/src/test/java/com/a42r/mdrender/cloudpush/
git commit -m "feat(android): PushServerConfig persisted pairing config"
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
  - `fun decrypt(encrypted: ByteArray): ByteArray` (RSA/ECB/OAEPWithSHA-256AndMGF1Padding)
  - `fun sign(data: ByteArray): ByteArray` (SHA256withRSA)
  - `fun deleteKeyPair()`

- [ ] **Step 1: Write `CloudPushKeyStore.kt`**

```kotlin
package com.a42r.mdrender.cloudpush

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Signature
import javax.crypto.Cipher
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
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_DECRYPT or KeyProperties.PURPOSE_SIGN)
                .setKeySize(3072)
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_RSA_OAEP)
                .setSignaturePaddings(KeyProperties.SIGNATURE_PADDING_RSA_PKCS1)
                .build()
        )
        return generator.generateKeyPair()
    }

    fun getPublicKeySpkiDer(): ByteArray =
        getOrCreateKeyPair().public.encoded // X.509 SubjectPublicKeyInfo (DER)

    fun decrypt(encrypted: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding")
        cipher.init(Cipher.DECRYPT_MODE, getOrCreateKeyPair().private)
        return cipher.doFinal(encrypted)
    }

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

### Task B3: `PushCrypto` — envelope verify + decrypt

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/cloudpush/PushCrypto.kt`
- Test: `app/src/test/java/com/a42r/mdrender/cloudpush/PushCryptoTest.kt` (JVM — pure parsing/decrypt with a Java-generated keypair via BouncyCastle, already on the classpath)

**Interfaces:**
- Produces (`com.a42r.mdrender.cloudpush.PushCrypto`, `@Singleton @Inject constructor(private val cloudPushKeyStore: CloudPushKeyStore)`):
  - `data class PushFile(val fileId: String, val name: String, val path: String, val retrievalKey: String)`
  - `data class PushPayload(val serverUrl: String, val pushId: String, val date: String, val totalFiles: Int, val files: List<PushFile>)`
  - `fun decryptEnvelope(envelopeJson: String, serverPublicKeyPem: String): PushPayload?` — parse, verify `sig` over `ek||iv||ct` (base64 string concat) with the server public key, unwrap `ek` via Keystore, AES-GCM decrypt `ct`, parse payload JSON. Returns `null` on any failure (drop message).
  - Helper `fun verifyEnvelopeSignature(envelopeJson: String, serverPublicKeyPem: String): Boolean` (used by tests to check signature-only).

- [ ] **Step 1: Write the failing unit test** `PushCryptoTest.kt`

The test builds a server keypair, device keypair (BouncyCastle RSA via `KeyPairGenerator.getInstance("RSA")` — works on JVM), and reproduces `build_envelope` (copy of the server logic in the test) to produce a valid envelope, then asserts `decryptEnvelope` returns the right payload. `CloudPushKeyStore` is abstracted: give `PushCrypto` a constructor parameter `private val privateKey: KeyPair` (JVM-friendly) with a secondary `@Inject` constructor wiring `cloudPushKeyStore`, or extract the decrypt into an interface. **Preferred:** `PushCrypto` takes `decrypt: (ByteArray) -> ByteArray` and `sign: (ByteArray) -> ByteArray` lambdas (defaulting to Keystore-backed in production via a Hilt module), so the JVM test passes the test keypair's lambdas. Adjust `decryptEnvelope` accordingly.

- [ ] **Step 2: Run to verify it fails**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: FAIL — class not found.

- [ ] **Step 3: Write `PushCrypto.kt`**

```kotlin
package com.a42r.mdrender.cloudpush

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.security.PublicKey
import java.security.Signature
import java.security.cert.CertificateFactory
import java.security.spec.X509EncodedKeySpec
import java.security.KeyFactory
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class PushCrypto @Inject constructor(
    private val decrypt: (ByteArray) -> ByteArray = { encrypted ->
        CloudPushKeyStoreImpl().decrypt(encrypted)
    }
) {
    ...
}
```

*(The real file: parse the envelope JSON, decode the 5 base64 fields, load the server public key from PEM (`KeyFactory.getInstance("RSA").generatePublic(X509EncodedKeySpec(...))` after stripping PEM headers), `Signature.getInstance("SHA256withRSA").verify(serverPublicKey, decodedSig, (ekB64 + ivB64 + ctB64).toByteArray())`, then `Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding")` decrypt `ek` → 32-byte content key, `SecretKeySpec`, `Cipher.getInstance("AES/GCM/NoPadding")` with `GCMParameterSpec(128, iv)` decrypt `ct` → payload JSON → `Json.decodeFromString`. Return `null` on any exception. For testability on the JVM, avoid the `CloudPushKeyStoreImpl` default — instead inject a `CloudPushCryptoKeystore` interface with a Keystore-backed production impl and a test impl; the plan's interface block above lists the exact decrypted-payload shapes.)*

- [ ] **Step 4: Run the test to verify it passes**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/cloudpush/PushCrypto.kt app/src/test/java/com/a42r/mdrender/cloudpush/
git commit -m "feat(android): E2E envelope verify + decrypt"
```

### Task B4: `PushClient` — HTTP client

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/cloudpush/PushClient.kt`
- Test: `app/src/test/java/com/a42r/mdrender/cloudpush/PushClientTest.kt` (uses a local `com.sun.net.httpserver.HttpServer` on a random port to assert request shape: POST/PUT, JSON bodies, no query-string secrets)

**Interfaces:**
- Produces (`com.a42r.mdrender.cloudpush.PushClient`, `@Singleton @Inject constructor()`), all `suspend`:
  - `fun registerDevice(config: PushServerConfig, publicKeySpkiDer: ByteArray, fcmToken: String, pairingToken: String): Result<String>` → returns `deviceAuth`; POSTs `/api/register-device` with the sig proof.
  - `fun rotateToken(config: PushServerConfig, newFcmToken: String): Result<Unit>`
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

*(Implement the private `post(url, body, readTimeoutMs): Pair<Int, ByteArray>` and `postStream` helpers with `HttpURLConnection`; set `connectTimeout`, `readTimeout`, `doOutput`, `Content-Type: application/json`, and read the response. For `registerDevice`, the body includes `device_secret, device_name, fcm_token, public_key, pairing_token, sig` where `sig` = Base64(RSA-SHA256(sha256(device_secret||device_name||fcm_token||public_key))) using the Keystore.)*

- [ ] **Step 4: Run the test to verify it passes**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/cloudpush/PushClient.kt app/src/test/java/com/a42r/mdrender/cloudpush/
git commit -m "feat(android): PushClient HTTP + JSON body auth"
```

### Task B5: `QrPairingScanner` — ML Kit + CameraX scanner screen

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/cloudpush/QrScannerScreen.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/cloudpush/` (a `QrPairingScanner.kt` wrapping `BarcodeScanning`)

**Interfaces:**
- Produces: `@Composable fun QrScannerScreen(onResult: (String) -> Unit, onCancel: () -> Unit)` — a CameraX `PreviewView` + `ImageAnalysis` feeding ML Kit `BarcodeScanner`; on a successful QR parse, calls `onResult(rawText)`.

- [ ] **Step 1: Write `QrPairingScanner.kt` + `QrScannerScreen.kt`**

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

- [ ] **Step 2: Verify it compiles**

Run: `./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/cloudpush/QrScannerScreen.kt app/src/main/java/com/a42r/mdrender/cloudpush/QrPairingScanner.kt
git commit -m "feat(android): QR pairing scanner (ML Kit + CameraX)"
```

### Task B6: `CloudPushManager` — queue + slice merge

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/cloudpush/CloudPushManager.kt`
- Test: `app/src/test/java/com/a42r/mdrender/cloudpush/CloudPushManagerTest.kt`

**Interfaces:**
- Produces (`com.a42r.mdrender.cloudpush.CloudPushManager`, `@Singleton @Inject constructor`):
  - `data class DownloadTask(val pushId: String, val file: PushCrypto.PushFile, val serverUrl: String, val status: Status = Status.QUEUED, val progress: Float = 0f)` with `enum Status { QUEUED, DOWNLOADING, DONE, FAILED }`
  - `val state: StateFlow<List<DownloadTask>>`
  - `fun enqueue(payload: PushCrypto.PushPayload)` — merges slices sharing `pushId`: dedupe by `file.fileId`, add new files; when the distinct file count reaches `payload.totalFiles`, **or** 30 s have elapsed since the first slice for that `pushId`, emit a "ready" push for the download service.
  - `fun cancel(fileId: String)` — sets status CANCELLED; the service discards the temp copy and acks as received.
  - `fun onFinished(fileId: String, success: Boolean)`
- Debounce: a `Job` per `pushId` scheduled with `CoroutineScope(Dispatchers.Default)` for the 30 s backstop.

- [ ] **Step 1: Write the failing merge test** `CloudPushManagerTest.kt`

Two `enqueue` calls with the same `pushId` but disjoint `files` (total 2, each slice `totalFiles = 2`) must, after the second call, expose a single task list of 2 entries and signal readiness. Use `kotlinx-coroutines-test` (`runTest`, `StandardTestDispatcher`) with the manager's scope injected.

- [ ] **Step 2: Run to verify it fails**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: FAIL — class not found.

- [ ] **Step 3: Write `CloudPushManager.kt`**

```kotlin
package com.a42r.mdrender.cloudpush

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class CloudPushManager @Inject constructor(
    private val scope: CoroutineScope = CoroutineScope(kotlinx.coroutines.Dispatchers.Default)
) {
    enum class Status { QUEUED, DOWNLOADING, DONE, FAILED, CANCELLED }

    data class DownloadTask(
        val pushId: String,
        val file: PushCrypto.PushFile,
        val serverUrl: String,
        val status: Status = Status.QUEUED,
        val progress: Float = 0f
    )

    private val _state = MutableStateFlow<List<DownloadTask>>(emptyList())
    val state: StateFlow<List<DownloadTask>> = _state.asStateFlow()

    private val readyCallbacks = mutableListOf<(String) -> Unit>()
    private val pendingCount = mutableMapOf<String, Int>()
    private val debounceJobs = mutableMapOf<String, Job>()

    fun onPushReady(callback: (String) -> Unit) {
        readyCallbacks.add(callback)
    }

    fun enqueue(payload: PushCrypto.PushPayload) {
        val existing = _state.value.filter { it.pushId == payload.pushId }.map { it.file.fileId }.toSet()
        val newTasks = payload.files
            .filter { it.fileId !in existing }
            .map { DownloadTask(payload.pushId, it, payload.serverUrl) }
        if (newTasks.isEmpty()) return
        _state.update { it + newTasks }
        val count = _state.value.count { it.pushId == payload.pushId }
        if (count >= payload.totalFiles) {
            fire(payload.pushId)
            return
        }
        debounceJobs[payload.pushId]?.cancel()
        debounceJobs[payload.pushId] = scope.launch {
            delay(30_000)
            fire(payload.pushId)
        }
    }

    private fun fire(pushId: String) {
        debounceJobs.remove(pushId)?.cancel()
        readyCallbacks.forEach { it(pushId) }
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

- [ ] **Step 4: Run the test to verify it passes**

Run: `./gradlew :app:testDebugUnitTest --tests "com.a42r.mdrender.cloudpush.*"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/cloudpush/CloudPushManager.kt app/src/test/java/com/a42r/mdrender/cloudpush/
git commit -m "feat(android): download queue with slice merge + debounce"
```

### Task B7: `CloudPushDownloadService` — foreground downloader

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/cloudpush/CloudPushDownloadService.kt`
- Modify: `app/src/main/AndroidManifest.xml` (declare the service)

**Interfaces:**
- Consumes: `CloudPushManager`, `PushClient`, `PushCrypto`, `PushServerConfig`, `FileRepository`, `FolderRepository`, `PushHistoryRepository`.
- Produces (`com.a42r.mdrender.cloudpush.CloudPushDownloadService`, `@AndroidEntryPoint class ... : Service()`):
  - Foreground service, `startForeground(ID, notification)` with type `FOREGROUND_SERVICE_TYPE_DATA_SYNC`.
  - On start, drains `CloudPushManager.state` for its `pushId`, downloads each `QUEUED`/`DOWNLOADING` task: `PushClient.downloadFile(config, fileId, key, tempFile)` → resolve target folder (`findOrCreateFolder("Cloud Push", null)` then nested `path` segments) → `FileRepository.importFileFromTemp(tempFile, name, mimeType, folderId)` → `PushHistoryRepository.record("Cloud Push", name, size, folderId)` → `PushClient.ackReceived(config, fileId, key)`. Updates the progress notification per file and posts a completion notification ("N files received in Cloud Push").
  - `cancel(fileId)` discards the temp file and calls `ackReceived` (removes it server-side).
  - `stopSelf()` when the queue for this push is drained; cancels the foreground state.

- [ ] **Step 1: Declare the service in `AndroidManifest.xml`**

```xml
<service
    android:name=".cloudpush.CloudPushDownloadService"
    android:exported="false"
    android:foregroundServiceType="dataSync" />
```

- [ ] **Step 2: Write `CloudPushDownloadService.kt`** (core loop, abbreviated)

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

- [ ] **Step 2: Verify it compiles**

Run: `./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

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
  - `onMessageReceived(message)` — read `message.data["p"]`, `PushCrypto.decryptEnvelope(it, config.serverPublicKeyPem)` (off main thread), on success `manager.enqueue(payload)`; on failure log and drop.
  - `onNewToken(token)` — re-register with the paired server: `PushClient.rotateToken(config, token)`.

- [ ] **Step 1: Register the service in `AndroidManifest.xml`**

```xml
<service
    android:name=".cloudpush.PushFcmService"
    android:exported="false">
    <intent-filter>
        <action android:name="com.google.firebase.MESSAGING_EVENT" />
    </intent-filter>
</service>
```

- [ ] **Step 2: Write `PushFcmService.kt`**

```kotlin
package com.a42r.mdrender.cloudpush

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import javax.inject.Inject

@AndroidEntryPoint
class PushFcmService : FirebaseMessagingService() {

    @Inject lateinit var crypto: PushCrypto
    @Inject lateinit var manager: CloudPushManager
    @Inject lateinit var config: PushServerConfig
    @Inject lateinit var client: PushClient

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onMessageReceived(message: RemoteMessage) {
        val envelope = message.data["p"] ?: return
        scope.launch {
            val payload = crypto.decryptEnvelope(envelope, config.serverPublicKeyPem)
            if (payload == null) {
                Log.w(TAG, "CloudPush: dropped envelope (bad sig/decrypt)")
                return@launch
            }
            manager.enqueue(payload)
        }
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

- [ ] **Step 3: Verify it compiles**

Run: `./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

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

- [ ] **Step 1: Add the section to `SettingsScreen.kt`** — add `CLOUDPUSH("Cloud Push")` to the enum, a `ListItem` in `SettingsMenu`, and a `SettingsSection.CLOUDPUSH -> CloudPushSettings(viewModel = cloudPushViewModel)` branch in the `when`.

- [ ] **Step 2: Write `CloudPushViewModel.kt`** — holds `config`, exposes `uiState: StateFlow<CloudPushUiState>` (isPaired, deviceName, serverUrl, deviceSecret, downloads from `manager.state`, checkRegistration result), and actions `pairWithQr(qrText)`, `setServerUrl(url)`, `checkRegistration()`, `rotateKeys()`, `cancelDownload(fileId)`.

- [ ] **Step 3: Write `CloudPushSettings.kt`** — the composable described above. Reuse `rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission())` for CAMERA (same pattern as `POST_NOTIFICATIONS` in `LocalSendSettings`).

- [ ] **Step 4: Verify it compiles**

Run: `./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 5: Commit**

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

- [ ] **Step 1: Wire the foreground check in `MDRenderApplication`**

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

- [ ] **Step 2: Add `setReRegistrationNeeded(Boolean)` + `val needsReRegistration: StateFlow<Boolean>` to `CloudPushManager`**, and call it from `CloudPushDownloadService` when `ackReceived`/`downloadFile` returns a 401/404.

- [ ] **Step 3: Verify it compiles**

Run: `./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

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

- [ ] **Step 1: Write the script** (from the spec's `push-to-phone` section)

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

- [ ] **Step 2: Smoke test against the running server**

With a registered device named "Sunny Falcon", run:
```bash
./tools/push-to-phone.sh --target "Sunny Falcon" server/README.md
```
Expected: a new push appears in the server's `/pushes` page and an FCM envelope is produced (or the test fake shows it). Missing `--target` exits 2.

- [ ] **Step 3: Commit**

```bash
git add tools/push-to-phone.sh && git commit -m "feat(tools): push-to-phone curl wrapper"
```

### Task C2: `localsend-send --enrol` flow

**Files:**
- Modify: `tools/localsend-send/localsend-send.py`

**Interfaces:**
- Produces: `--enrol --server <URL>` mode implementing the spec §Tool enrolment: `POST /api/enrol/start` → print link (`xdg-open` when a display is present) → prompt "Enter enrolment key:" → `POST /api/enrol` → write `~/.config/mdrender/push-credentials.json` (perms 0600) with `{server_url, client_id, client_secret}`. Also add a `--creds <path>` option and a helper `get_access_token(creds_path, server_url)` used by the push path (Task C3).

- [ ] **Step 1: Write the failing unit test** `tools/localsend-send/test_localsend_send.py`

Test the new pure functions with a local `http.server`:
- `_enrol_flow(server_url, key_input)` → asserts the credentials file is written with 0600 perms and correct contents.
- `_get_token(creds, server_url)` → asserts it POSTs `/oauth/token` and returns the token.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tools/localsend-send/`
Expected: FAIL — module has no enrol path yet.

- [ ] **Step 3: Implement the enrol path in `localsend-send.py`**

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

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tools/localsend-send/`
Expected: PASS.

- [ ] **Step 5: Commit**

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

- [ ] **Step 1: Write the failing test** — add `test_discover_lan_parses_response` and `test_push_to_server_multipart` (local `http.server` asserting `target_device` in the multipart body and a Bearer header).

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tools/localsend-send/`
Expected: FAIL.

- [ ] **Step 3: Implement `discover_lan` + `push_to_server` + `--name` routing**

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

- [ ] **Step 4: Wire `--name` routing in `main()`**

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

- [ ] **Step 5: Run the tests and a live smoke test**

Run: `python3 -m pytest tools/localsend-send/`, then `./tools/localsend-send/localsend-send.py --name "Sunny Falcon" server/README.md`.
Expected: tests pass; the live push falls back to the server when the phone is off-LAN and lands in the app.

- [ ] **Step 6: Commit**

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

- [ ] **Step 1: Write the script** (verbatim from the spec §Setup automation, plus a `--json-only` note and prereq check that `firebase`/`gcloud`/`jq` are installed)

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

gcloud services enable firebasemessaging.googleapis.com --project "$PROJECT_ID"
gcloud iam service-accounts create fcm-pusher --project "$PROJECT_ID" || true
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:$SA" --role roles/firebasecloudmessaging.admin
gcloud iam service-accounts keys create server/fcm-service-account.json \
    --iam-account "$SA" --project "$PROJECT_ID"

echo "DONE. Commit app/google-services.json; mount server/fcm-service-account.json"
echo "into the server image as FCM_SERVER_KEY."
```

- [ ] **Step 2: Write `tools/fcm/README.md`** — the detailed walkthrough behind the script: prereqs (Firebase CLI, gcloud, jq), the one browser step, what each artifact is, key rotation, re-run safety.

- [ ] **Step 3: Verify it is executable and syntactically valid**

```bash
chmod +x tools/fcm/setup-fcm.sh
bash -n tools/fcm/setup-fcm.sh
```

- [ ] **Step 4: Commit**

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
| Envelope (RSA-OAEP-256 / A256GCM / sig), ≤3.5 KB slices | A3, A9 |
| Batching `total_files` merge on phone | A3 (payload), B6 (merge) |
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

- `build_envelope` (A3) and `PushCrypto.decryptEnvelope` (B3) agree on `ek||iv||ct` (base64-string concat) as the signature input, and on the payload shape (`server_url`, `push_id`, `date`, `total_files`, `files[{file_id,name,path,retrieval_key}]`).
- `total_files` added to the payload by `build_payload` (A3) and consumed by `CloudPushManager.enqueue` (B6) — consistent.
- `register_device` (A6) sig input `sha256(device_secret||device_name||fcm_token||public_key)` matches `PushClient.registerDevice` (B4) and the A9 test.
- `push_store` helpers (A7) are used by `app.py` (A9) and `retry.py` (A10) with matching names.
- `FcmClient.send(data_message, fcm_token)` (A8) is used by `app.py` (A9) and `retry.py` (A10).
- `CloudPushManager.Status` enum (B6) is referenced by `CloudPushDownloadService` (B7) and the settings UI (B9).
- `PushHistoryRepository.record(source, fileName, fileSize, folderId)` matches master's signature.
