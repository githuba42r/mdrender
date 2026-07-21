# MDRender

A secure, privacy-focused Android document viewer with encrypted storage, audio playback, and peer-to-peer file transfer.

## Features

### Encrypted Storage
- All files stored encrypted at rest using AES-256 via Android Keystore
- Large files (>1 MB) stored as encrypted files on disk instead of SQLite BLOBs
- Optional per-file encryption toggle for compatibility

### File Browser
- Folder hierarchy with breadcrumb navigation
- Grid and list view toggle
- File import from other apps (Share sheet, "Open with")
- Multi-select delete and move with conflict resolution (Replace/Skip/Rename)
- Image thumbnails (on-the-fly generation, toggleable)
- Folder and file rename, move, delete, properties
- Undo delete with 5-second auto-commit timer

### Viewers
- **Markdown**: Rendered with table of contents from `INDEX.md` files (toggleable)
- **Image**: Viewable with pinch-to-zoom, image-sibling swiping
- **Text**: Raw plain text viewer
- **Audio**: Background playback via `MediaSessionService` with notification controls, headphones-only mode, skip-back/skip-forward

### LocalSend (Peer-to-Peer Transfers)
- Built-in HTTP server using LocalSend protocol
- Device discovery on local network
- Configurable transfer PIN with auto-accept option
- File conflict strategy: Skip, Replace, or Rename
- Transfer progress notifications
- Boot receiver for auto-start

### Security
- **App Lock**: Locks on background or screen-off; finishes task if locked
- **Biometric/PIN unlock** on app resume (configurable via Settings)
- **BIOMETRIC_WEAK** (face unlock) opt-in toggle
- **Hidden folders**: Mark folders as hidden with configurable unlock gestures:
  - 12-Tap (single-tap counter) — **default**
  - Tap Sequence (configurable pattern of taps/long-presses) — **recommended**
  - Multi-Touch (finger-down zones, slides, rotations) — experimental, limited testing
- **Hidden folder security note**: The app ships with `INDEX.md` and `README` documentation that describes the default 12-tap unhide method. Anyone with access to the installed app can read these shipped files and learn how to unhide folders using the default gesture. For true hidden folder protection, go to Settings → Advanced and configure a custom **Tap Sequence** (recommended — balances security and ease of use), and disable 12-Tap as an unlock method. Multi-Touch is available but has had limited testing and is not recommended as a primary unlock method. Using a custom gesture ensures that knowing the app's documented defaults is not sufficient to reveal hidden content.
- Hidden folder share warning dialog
- Secure screen flag (prevent screenshots in recent apps)
- Keyboard-based gesture entry as accessibility fallback

### Settings
Hierarchical settings menu with sub-pages:
- **Authentication**: Require unlock, face unlock toggle
- **Folders**: INDEX.md TOC, thumbnails, encryption toggle
- **Audio**: Headphones-only, expanded notification
- **LocalSend**: Enable/disable, device name (regenerable), transfer PIN, auto-accept
- **Advanced**: Hidden folder gesture configuration (auth-gated) — configure custom gestures and disable the default 12-tap for true hidden folder protection (see security note below)
- **About**: Build version with RC tag and build date/time

### Sharing
- Share files out to other apps (plain file or encrypted blob extracted to temp)
- Hidden item warning when sharing from hidden folders
- Share sheet receiver for importing files

## Requirements

- **Android**: minSdk 26 (Android 8.0), targetSdk 36
- **Build**: JDK 17, Gradle 8.11.1
- **Android Studio**: Hedgehog (2023.1+) or IntelliJ IDEA with Android plugin

## Build Setup

```bash
# Clone
git clone git@github.com:githuba42r/mdrender.git
cd mdrender

# Debug build and install on connected device
./gradlew :app:installDebug

# Release build
./gradlew :app:assembleRelease
```

### Keystore (Release Builds)

Create `keystore.properties` in the project root:

```properties
storeFile=/path/to/keystore.jks
storePassword=your_store_password
keyAlias=your_key_alias
keyPassword=your_key_password
```

Without this file, release builds will be unsigned.

### Play Store Publishing (Optional)

Create `play-service-account.json` in the project root with a Google Play service account key. Publishing uses the [play-publisher](https://github.com/Triple-T/gradle-play-publisher) plugin targeting the internal track as a draft.

```bash
# Publish to Play Store internal track
./gradlew :app:publishRelease
```

## Architecture

| Layer | Technology |
|---|---|
| UI | Jetpack Compose, Material 3 |
| Navigation | Navigation Compose |
| Dependency Injection | Dagger Hilt |
| Database | Room (SQLite) |
| Encryption | Android Keystore + AES-256/GCM |
| Audio | ExoPlayer / Media3 |
| Networking | NanoHTTPD (embedded LocalSend server) |
| Async | Kotlin Coroutines + Flow |

### Key Modules

- `data/` — Room database, DAOs, repositories (File, Folder, preferences)
- `security/` — KeystoreManager, CryptoEngine, AppLock, DeviceAuth, BiometricPrompt
- `gesture/` — Hidden folder reveal gestures: 12-tap, tap sequence, multi-touch, gesture settings
- `localsend/` — LocalSend protocol implementation (HTTP server, discovery, session manager)
- `audio/` — Background audio player with MediaSessionService
- `share/` — Share-out manager with encryption and hidden-item detection
- `ui/` — Compose screens: browser, viewers (markdown/image/text), audio player, settings

## Versioning

Semantic versioning (`vX.Y.Z`). Version components in `version.properties` at the project root.

### Release Candidates

On non-default branches, the displayed version gets an `-rc.N` suffix using the next patch version from `version.properties`:

```
version.properties: 1.0.10
feature branch build  →  v1.0.11-rc.3
master branch build   →  v1.0.10
```

Bump with `./bump_version.sh [patch|minor|major]`.

## Database

Uses Room with schema versioning. Current version: 7. Migration objects in `AppDatabase.kt`. A one-shot orphan cleanup (on v7+ migration) removes unreferenced disk files from `encrypted/` and `plain/` directories.

## License

Proprietary — all rights reserved.
