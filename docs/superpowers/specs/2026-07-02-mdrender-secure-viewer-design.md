# MDRender — Secure Markdown & File Viewer

**Status:** Design (approved 2026-07-02)
**Stack:** Kotlin, Jetpack Compose, Room, Hilt, Android Keystore, Material 3

## Scope

A secure, offline, viewer-only Android app for text, Markdown, and image files. Files are imported from the device filesystem or via Android share intents, organized into nested folders, and stored as AES-256-GCM encrypted BLOBs in a Room database. The app requires authentication (biometric, pattern, or PIN) to open and auto-locks when backgrounded, the screen turns off, or an idle timeout expires.

## Key Decisions

| Decision | Choice |
|----------|--------|
| Function | Viewer only (txt, md, image files) |
| Import | File picker + Android share intents |
| Lock triggers | Background, screen off, configurable idle timeout (default 2 min) |
| Auth methods | Biometric (default), pattern, PIN — configurable in settings |
| Organization | Nested folders |
| Storage | Room DB with files as encrypted BLOBs |
| Encryption | AES-256-GCM via Android Keystore |
| UI | Jetpack Compose + Material 3 (Material You dynamic color) |
| Build/deploy | version.properties + release.sh + Gradle Play Publisher |

## Architecture

Four-layer clean architecture:

```
UI Layer (Jetpack Compose)
  Activities, Composables, Navigation
  └─ ViewModel Layer
      AuthViewModel, FileBrowserVM, ViewerVM, SettingsVM
      └─ Repository Layer
          FileRepository, FolderRepository, AuthRepository
          └─ Data Layer
              Room DB, Android Keystore, BiometricManager, AppLockManager
```

## Security & Authentication

### Key Management
- On first launch, generate AES-256 key via `KeyGenParameterSpec` with `setUserAuthenticationRequired(true)`, stored in Android Keystore (hardware-backed where available).
- AndroidX Security `MasterKey` encrypts Room database at rest as a secondary layer.
- The AES file-encryption key is unlocked by successful authentication.

### Auth Methods
All three are supported and user-selectable in Settings. Default is biometric.

1. **Biometric** — `BiometricPrompt` with `BIOMETRIC_STRONG | DEVICE_CREDENTIAL`. Falls back to device lock screen on biometric failure.
2. **Pattern** — Custom 3x3 pattern lock view. Pattern hash (SHA-256) compared against stored hash.
3. **PIN** — 4–8 digit PIN. Hashed with PBKDF2 + random 16-byte salt. Compared against stored hash/salt pair.

Auth preference and credential hashes are stored in **EncryptedSharedPreferences** (AndroidX Security Crypto).

### Lock Triggers
- **Background**: `LifecycleObserver` on Application monitors `onStop` — sets in-memory locked flag immediately.
- **Screen off**: `BroadcastReceiver` for `ACTION_SCREEN_OFF` engages lock.
- **Idle timeout**: Coroutine-based timer in `AppLockManager`. Reset on every touch event via `dispatchTouchEvent` override in the Activity. Duration stored in EncryptedSharedPreferences (default: 2 minutes; options: 30s, 1m, 2m, 5m, 10m, Never).

### Re-auth Flow
- Transparent `LockScreenActivity` launches over the current content when app returns to foreground in a locked state.
- Successful auth finishes `LockScreenActivity` → user sees their last screen.
- After 5 consecutive failures: 30-second lockout with exponential backoff.

## File Organization & Storage

### Database Schema

**Folder table:**
| Column     | Type      | Notes                           |
|------------|-----------|---------------------------------|
| id         | INTEGER   | PRIMARY KEY AUTOINCREMENT       |
| name       | TEXT      | NOT NULL                        |
| parent_id  | INTEGER?  | FK → Folder.id, null = root     |
| created_at | INTEGER   | epoch millis                    |
| updated_at | INTEGER   | epoch millis                    |

**File table:**
| Column              | Type      | Notes                           |
|---------------------|-----------|---------------------------------|
| id                  | INTEGER   | PRIMARY KEY AUTOINCREMENT       |
| folder_id           | INTEGER?  | FK → Folder.id, null = root     |
| name                | TEXT      | NOT NULL                        |
| mime_type           | TEXT      | NOT NULL                        |
| encrypted_blob      | BLOB      | NOT NULL (AES-256-GCM)          |
| encrypted_thumbnail | BLOB      | nullable, max 256px             |
| file_size           | INTEGER   | unencrypted size in bytes       |
| created_at          | INTEGER   | epoch millis                    |
| updated_at          | INTEGER   | epoch millis                    |

### Encryption per File
- Before insert: `encrypt(plainBytes, aesKey) → IV (12 bytes) + ciphertext + GCM tag`.
- After query: `decrypt(blob, aesKey) → plainBytes`, using the prepended IV and authenticating the GCM tag.
- Room sees only the opaque encrypted BLOB.
- Folder names are **not encrypted** — they are organizational labels with no sensitive content.

### Folder Tree
- Self-referencing `parent_id` enables arbitrary nesting.
- Tree built in-memory on app start from flat Folder table. Shallow enough that full in-memory recursion is cheap.
- Cascade delete: removing a folder deletes all children and their files recursively.
- For images: small encrypted thumbnail (max 256px) stored separately for the grid/list view.

### Import Flow
1. User picks or shares a file → read entire file into `ByteArray` in memory.
2. Determine MIME type from extension and content inspection.
3. Encrypt bytes with AES-256-GCM → store as BLOB in Room.
4. For images: generate thumbnail (256px), encrypt it, store as `encrypted_thumbnail`.

## UI & Navigation

### Screen Map
```
AppLockScreen (transparent)
  └─ MainActivity
       ├─ FolderBrowserScreen   (grid/list of folders + files at current level)
       ├─ MarkdownViewerScreen  (rendered MD)
       ├─ TextViewerScreen      (plain text, monospace)
       ├─ ImageViewerScreen     (full-screen, pinch-to-zoom)
       ├─ SettingsScreen        (auth method, timeout, about)
       └─ ImportScreen          (file picker trigger)
```

### FolderBrowserScreen (Home)
- **Breadcrumb bar**: `Home > Projects > Notes` for hierarchical navigation.
- **Grid/List toggle** for folders and files.
- **Grid items**: folder icon + name; file thumbnail with type badge (MD/txt/image).
- **FAB (+)** with bottom sheet: "New Folder" | "Import File".
- **Swipe-to-delete** with 5-second undo snackbar. After undo window: permanent Room deletion.
- **Long-press** for multi-select → batch delete or move.

### MarkdownViewerScreen
- Renders MD to styled Compose text: headings, bold, italic, code blocks, bullet/numbered lists.
- Links: tap to copy URL to clipboard (no browser — offline viewer).
- Syntax-highlighted code blocks with monospace background.
- Image references in MD are not fetched (offline only).

### ImageViewerScreen
- Full image from decrypted blob.
- Pinch-to-zoom and pan gestures.
- Tap to toggle immersive mode (hide/show app bar).

### TextViewerScreen
- Plain text rendered in monospace Compose `Text`.
- Word wrap toggle.

### SettingsScreen
- Auth method picker: Biometric / Pattern / PIN.
- Pattern configuration: 3x3 grid, draw + confirm.
- PIN configuration: enter + confirm, 4–8 digits.
- Idle timeout: slider with options 30s / 1m / 2m (default) / 5m / 10m / Never.
- Theme: System default / Light / Dark.
- App version info (from `version.properties`).

### Theming
- Material 3 with dynamic color (Material You) for Android 12+.
- Dark mode support with manual override in settings.

## Build, Deploy & Release

### version.properties
```
VERSION_MAJOR=0
VERSION_MINOR=1
VERSION_PATCH=0
VERSION_CODE=1
```
Loaded by `app/build.gradle.kts`, injected into `BuildConfig` and `AndroidManifest`.

### build-deploy.sh
Quick debug build + deploy to connected device. Handles signature mismatch (Play Store vs local keystore) by offering uninstall-first.

### release.sh
Full release pipeline (mirrors PEVPlugShare/DestinationETATracker):
- `patch|minor|major` bump with optional `--pre-release` (rc.0, rc.1…).
- Reads `version.properties`, calculates new version, confirms with user.
- Updates `version.properties`, commits, creates annotated git tag.
- Runs `./gradlew clean bundleRelease`.
- Generates `RELEASE_NOTES.txt` with git log since last tag.
- Optional `--publish-internal-testing` publishes AAB to Google Play Internal Testing.

### Play Store Publishing
- **Gradle Play Publisher** (triplet plugin) with service account JSON.
- Track: `internal` by default, configurable per release.
- AAB preferred over APK.

### Signing
- `keystore.properties` (gitignored): keystore path, passwords, key alias.
- `secrets.properties` (gitignored): any API keys or build config.

## Project Structure
```
MDRender/
  app/
    src/main/java/com/a42r/mdrender/
      ui/              — Compose screens, ViewModels
        auth/          — LockScreen, BiometricPrompt wrapper, PatternView
        browser/       — FolderBrowserScreen, file grid/list
        viewer/        — MD viewer, text viewer, image viewer
        settings/      — Settings screen
      data/            — Room entities, DAOs, Database, repositories
      security/        — KeystoreManager, CryptoEngine, AppLockManager, IdleTimer
      di/              — Hilt modules
    src/main/res/      — resources, themes, strings
  gradle/
    libs.versions.toml
  build.gradle.kts
  settings.gradle.kts
  version.properties
  keystore.properties        (gitignored)
  secrets.properties         (gitignored)
  build-deploy.sh
  release.sh
```

## Dependencies (key libraries)

| Library | Purpose |
|---------|---------|
| Jetpack Compose + Material 3 | UI framework |
| Navigation Compose | Screen navigation |
| Room | SQLite database |
| Hilt | Dependency injection |
| AndroidX Security Crypto | EncryptedSharedPreferences, MasterKey |
| AndroidX Biometric | BiometricPrompt |
| Coil | Image loading/thumbnails |
| Gradle Play Publisher (triplet) | Play Store publishing |

## Error Handling

- **Decryption failure**: Catch `AEADBadTagException` — if key is corrupted or tampered, show error with option to delete the file.
- **Keystore unavailable**: Show fallback message if device lacks hardware keystore (rare on API 26+ devices).
- **Biometric unavailable**: Gracefully fall back to configured secondary method (pattern/PIN).
- **Import failure**: Display snackbar with reason (file too large, unsupported type, read error).
- **Room migration**: Use proper Room migrations with fallback to destructive migration only for early dev versions.

## Testing Strategy

- **Unit tests**: CryptoEngine encrypt/decrypt round-trip, AppLockManager timer logic, AuthRepository credential verification.
- **Integration tests**: Room DAO queries with encrypted BLOBs, folder cascade delete.
- **UI tests**: Lock → unlock flow, folder navigation, file import and view.
- **Security tests**: Verify blobs are stored encrypted on disk, verify failed auth does not decrypt, key invalidation on auth method change.
