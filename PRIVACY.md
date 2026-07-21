# Privacy Policy for MDRender

**Last updated:** July 22, 2026

## Overview

MDRender is a privacy-focused document viewer and file management application. This policy describes how the application handles your data.

## Data Collection

MDRender **does not collect, transmit, or share any personal data, usage analytics, crash reports, or telemetry**. The application has no analytics SDKs, no advertising SDKs, and no third-party tracking of any kind.

All data stored within MDRender remains exclusively on your device unless you explicitly choose to share it.

## Permissions

MDRender requests the following permissions. Each is used solely for local, on-device functionality:

| Permission | Purpose |
|---|---|
| `INTERNET` | Local network communication for the built-in LocalSend peer-to-peer file transfer service. All transfers occur over the local network only — no data is sent to external servers. |
| `ACCESS_NETWORK_STATE` / `ACCESS_WIFI_STATE` | Detecting available network interfaces for LocalSend discovery and transfers. |
| `CHANGE_WIFI_MULTICAST_STATE` | Enabling multicast for LocalSend device discovery on the local network. |
| `READ_EXTERNAL_STORAGE` (Android 12 and below) | Importing files from other applications via the Share sheet. |
| `POST_NOTIFICATIONS` | Showing transfer progress and audio playback controls in the notification shade. |
| `FOREGROUND_SERVICE` / `FOREGROUND_SERVICE_DATA_SYNC` | Running LocalSend transfers and audio playback as foreground services. |
| `FOREGROUND_SERVICE_MEDIA_PLAYBACK` | Audio playback as a foreground service with media session. |
| `RECEIVE_BOOT_COMPLETED` | Restarting the LocalSend service after a device reboot if it was previously enabled. |
| `USE_BIOMETRIC` | Device authentication (fingerprint, face unlock) for app unlock and accessing sensitive settings. |

## Data Storage

### Content You Provide
- Files you import into MDRender are stored encrypted on your device using AES-256 encryption via Android Keystore.
- Encrypted files are stored in the application's private directory and are not accessible to other applications.
- You can delete any file or folder at any time from within the application.

### Application-Generated Data
- App preferences (settings, gesture configurations) are stored locally in encrypted SharedPreferences.
- Bookmarks and scroll positions are stored in the local Room database.

### LocalSend Transfers
- Files transferred to your device via LocalSend are stored in the encrypted local storage.
- Transfer metadata (sender alias, file names) is ephemeral and exists only in memory during an active transfer session.
- No transfer logs or records are retained after the transfer completes.

## Data Sharing

MDRender provides mechanisms for you to **explicitly** share data:
- **Share out**: You can choose to export or share files to other applications via the Android Share sheet.
- **LocalSend**: You can choose to receive files from other devices on your local network. This must be explicitly enabled.

The application performs no automatic or background data sharing.

## Third-Party Services

MDRender uses no third-party services, APIs, SDKs, or frameworks that access your data. The application depends on standard Android platform APIs only:

- **Android Keystore** — on-device cryptographic key storage
- **Android Room** — local SQLite database
- **Android Media3 (ExoPlayer)** — local audio playback
- **NanoHTTPD** — embedded HTTP server for local-only LocalSend transfers

None of these dependencies transmit data off-device.

## Children's Privacy

MDRender does not knowingly collect any personal information from children. The application stores only user-provided content and operates entirely on-device.

## Data Deletion

All data stored by MDRender can be deleted by:
1. Deleting individual files or folders from within the application.
2. Uninstalling the application, which removes all stored data from the device.

## Security

- **Encryption at rest**: All file content is encrypted with AES-256/GCM using keys stored in Android Keystore.
- **App lock**: The application supports biometric and PIN/pattern authentication for access.
- **Hidden folders**: Folders can be marked hidden and require a configurable gesture to reveal.
- **Backup disabled**: `android:allowBackup` is set to `false`, preventing automatic cloud backup of app data.
- **Screen protection**: FLAG_SECURE prevents the app window from appearing in screenshots or screen recordings on supported devices.

## Changes to This Policy

This policy may be updated from time to time. Changes will be reflected in the application's source repository. Continued use of the application after changes constitutes acceptance of the updated policy.

## Contact

For questions about this privacy policy, open an issue at:
https://github.com/githuba42r/mdrender/issues

## Compliance

- **GDPR**: MDRender collects no personal data and therefore has no data processing to report or facilitate.
- **CCPA/CPRA**: MDRender does not sell, share, or collect personal information.
- **COPPA**: MDRender does not collect data from children.
