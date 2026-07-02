# Share/Import Handover — 2026-07-02

## Blocking issue: Encryption key requires user auth but share/import doesn't have it

### Current state

After fixing share target registration and the ShareReceiverActivity crash,
file sharing from MyFiles still fails. The share intent is received correctly
(URI extracted, activity starts), but the Keystore encryption key throws:

```
KEY_USER_NOT_AUTHENTICATED (Keystore error code -26)
```

### Root cause

`KeystoreManager.kt` line 38 — `setUserAuthenticationRequired(true)` — requires
the user to have authenticated within the past ~5 seconds (biometric session
expiry). `ShareReceiverActivity` and `ImportScreen` never go through the app's
auth flow (by design — share shouldn't require re-auth or the user seeing the
full file browser).

### Fix applied (this commit)

1. Removed `setUserAuthenticationRequired(true)` from Keystore key generation.
2. Old key is deleted on next key generation (existing encrypted files lost —
   acceptable for v0.1 dev).
3. Reinstall required — `adb uninstall com.a42r.mdrender && adb install ...`
   to clear the old authenticated key.

### Rationale

- Device-level lock screen already protects physical access.
- App's own lock screen protects viewing stored files.
- The encryption key protects data-at-rest in Room DB against filesystem extraction.
- Requiring authentication for encryption _imports_ adds no meaningful security
  and breaks the share/import UX entirely.

## Architecture overview

### App structure

```
MDRenderApplication (@HiltAndroidApp)
├── MainActivity (file browser, viewer, settings, import)
├── LockScreenActivity (transparent, biometric/PIN/pattern gate)
└── ShareReceiverActivity (transient, receives share intents, imports to root)
```

### Lock lifecycle

- App starts **locked** (AppLockManager.isLocked = true).
- `MDRenderApplication.ActivityLifecycleCallbacks` on `onActivityResumed`:
  if MainActivity resumes and app is locked → launches LockScreenActivity.
- `onActivityPaused` → 3-second grace delay → lock.
- `suspendNextPause()` (called before file picker opens) → skips one pause-lock cycle.
- `isTransient()` excludes LockScreenActivity and ShareReceiverActivity from
  foreground/background lifecycle tracking.

### Import flow (FAB → ImportScreen)

1. User taps FAB → "Import File" → navigates to ImportScreen.
2. ImportScreen shows "Choose Files" button.
3. On click, calls `appLockManager.suspendNextPause()` then launches system
   file picker (`GetMultipleContents`).
4. Picker opens (app gets `onPause` → but lock is skipped via `suspendNextPause`).
5. User selects files → picker returns URIs → `ImportViewModel.importFiles()`.
6. Files are read from URIs, encrypted via `CryptoEngine`, stored as BLOBs in Room.
7. `importComplete` StateFlow fires → navigates back to browser.

### Share flow (MyFiles → ShareReceiverActivity)

1. User picks file in MyFiles → Share → chooses MDRender.
2. `ShareReceiverActivity` receives `Intent.ACTION_SEND` with `EXTRA_STREAM` URI.
3. URI extracted, file read via ContentResolver, encrypted, stored in Room (root folder).
4. Activity finishes immediately (no UI — transparent black flash).

### Encryption

- **AES-256-GCM** via Android Keystore hardware-backed key.
- Key alias: `mdrender_file_encryption_key`.
- 12-byte random IV prepended to ciphertext before Room storage.
- `CryptoEngine.encrypt(plaintext) → IV(12) + ciphertext + GCM tag`.
- `CryptoEngine.decrypt(ciphertext) → plaintext`.

### Database

- **Room** (`mdrender.db`) with two tables:
  - `folders` — id, name, parent_id (self-referencing FK), timestamps.
  - `files` — id, folder_id (FK), name, mime_type, encrypted_blob (BLOB),
    encrypted_thumbnail (BLOB nullable), file_size, timestamps.
- Cascade delete on folder hierarchies.

## Known issues & deferred items

| Issue | Status |
|-------|--------|
| Share intent import to root folder only (not current folder) | Deferred |
| Delete undo for encrypted files creates empty entry | Deferred (needs plaintext cache) |
| Inline markdown formatting (bold, italic) — markers stripped but not styled | Deferred (v1 renderer) |
| `gradlew.bat` missing for Windows | Minor |
| KSP → kapt fallback for Hilt (build slower, kapt deprecated) | Workaround for KSP NPE bug |
| No file-size limit on import (large images could be slow) | Deferred |
| Biometric auth fallback UX — no visible error when biometric unavailable | Deferred |

## Build & deploy

```bash
./build-deploy.sh          # debug APK → device
./bump_version.sh patch    # quick version increment
./release.sh patch         # full release with AAB + Play publish
```

Version: `0.1.0` (versionCode 1) in `version.properties`.

## Git history (16 tasks)

```
ad63cd3 test: add unit and instrumented tests
23a1f18 chore: add build-deploy.sh, release.sh, bump_version.sh
af99f15 feat: add file import via picker and share intent handling
b7b2282 feat: add Settings screen with auth method, pattern/PIN config
66a130d feat: add viewer screens — Markdown, Text, Image
887f2ec feat: add FolderBrowser UI with grid/list, breadcrumb, FAB
f157383 feat: add auth UI — LockScreen, BiometricPrompt, PatternLockView
ad120ba feat: add Material 3 theme with dynamic color, navigation
07a8e94 feat: add Application class, MainActivity, LockScreenActivity
e2a4c4d feat: add Hilt DI modules for database, security, repositories
4fa6a04 feat: add FolderRepository and FileRepository
d1362b2 feat: add auth preferences store and repository
79c9582 feat: add AppLockManager with idle timer, lockout logic
8a83f0b feat: add security layer — KeystoreManager, CryptoEngine
b791acb feat: add Room data layer — FolderEntity, FileEntity, DAOs
daa4eec chore: scaffold MDRender project with Gradle, version catalog
```

Test results: **9/9 unit tests pass**, instrumented test compiles.
