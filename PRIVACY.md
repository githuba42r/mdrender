# Privacy Policy for MDRender

**Last updated:** October 3, 2026

## Overview

MDRender is a privacy-focused document viewer and file management application. This policy describes how the application handles your data, including its optional **Cloud Push** feature: an end-to-end encrypted channel between the app and a cloud push server that you choose to pair with.

## Data Collection

MDRender **does not collect, transmit, or share any usage analytics, crash reports, or telemetry**. The application has no analytics SDKs, no advertising SDKs, and no third-party tracking of any kind.

All data stored within MDRender remains exclusively on your device unless you explicitly choose to share it — or you enable Cloud Push.

### Cloud Push (optional)

Cloud Push is disabled until you scan a pairing QR code. When enabled, the app sends only the following to **the server you paired with** (which may be self-hosted and publishes its own privacy policy):

- the pairing code from the QR, which ties the device to your account on that server;
- the device name and model, as shown in the server's device list;
- a Firebase Cloud Messaging (FCM) registration token, so wake-up messages can reach the app;
- device public keys, so pushes and files can be end-to-end encrypted to this device.

Wake-up messages are delivered via Google FCM and contain no file content — only the cue to fetch. Pushed files and messages are downloaded from the paired server and decrypted on the device; with end-to-end encryption enabled, the server never sees the plaintext.

## Permissions

MDRender requests the following permissions. Each is used solely for local, on-device functionality, except where Cloud Push is noted:

| Permission | Purpose |
|---|---|
| `INTERNET` | Local network communication for the built-in LocalSend peer-to-peer file transfer service, and — only when Cloud Push is enabled — HTTPS calls to the paired server plus receiving FCM messages. |
| `ACCESS_NETWORK_STATE` / `ACCESS_WIFI_STATE` | Detecting available network interfaces for LocalSend discovery and transfers. |
| `CHANGE_WIFI_MULTICAST_STATE` | Enabling multicast for LocalSend device discovery on the local network. |
| `CAMERA` | Scanning the pairing QR code when you set up Cloud Push. No image from the camera is stored or transmitted. |
| `READ_EXTERNAL_STORAGE` (Android 12 and below) | Importing files from other applications via the Share sheet. |
| `POST_NOTIFICATIONS` | Showing transfer progress, audio playback controls, and delivered Cloud Push notifications in the notification shade. |
| `FOREGROUND_SERVICE` / `FOREGROUND_SERVICE_DATA_SYNC` | Running LocalSend transfers, Cloud Push downloads, and audio playback as foreground services. |
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

### Cloud Push Data
- Pairing credentials (the server URL and the device secret issued at registration) and the device keypairs are stored locally in the Android Keystore; private keys never leave the device.
- Files pushed to the device are written into the app's encrypted storage, the same as imported files.
- Cloud Push keeps no server-side copy beyond what the paired server itself retains under its own policy.

### LocalSend Transfers
- Files transferred to your device via LocalSend are stored in the encrypted local storage.
- Transfer metadata (sender alias, file names) is ephemeral and exists only in memory during an active transfer session.
- No transfer logs or records are retained after the transfer completes.

## Data Sharing

MDRender provides mechanisms for you to **explicitly** share data:
- **Share out**: You can choose to export or share files to other applications via the Android Share sheet.
- **LocalSend**: You can choose to receive files from other devices on your local network. This must be explicitly enabled.
- **Cloud Push**: When paired, the pairing information listed above is sent to the server you chose, and wake-up messages arrive through Google Firebase Cloud Messaging (subject to Google's policies). Unpairing stops both.

The application performs no automatic or background data sharing beyond the Cloud Push feature you enable.

## Third-Party Services

MDRender uses no third-party services, APIs, SDKs, or frameworks that access your data for advertising, analytics, or tracking. The application depends on standard Android platform APIs and one delivery channel:

- **Android Keystore** — on-device cryptographic key storage
- **Android Room** — local SQLite database
- **Android Media3 (ExoPlayer)** — local audio playback
- **NanoHTTPD** — embedded HTTP server for local-only LocalSend transfers
- **Google Firebase Cloud Messaging** — delivery of wake-up messages for Cloud Push only; carries no file content. Used only while Cloud Push is enabled.

None of these dependencies transmit your documents or usage data off-device.

## Children's Privacy

MDRender does not knowingly collect any personal information from children. The application stores only user-provided content and operates entirely on-device, apart from the optional Cloud Push pairing described above.

## Data Deletion

All data stored by MDRender can be deleted by:
1. Deleting individual files or folders from within the application.
2. Turning off Cloud Push / unpairing the device, which removes the pairing credentials and keys from the app.
3. Uninstalling the application, which removes all stored data from the device.

Data held by a paired cloud push server is governed by that server's policy: revoke the device from the server (or delete the server-side account) to remove it there.

## Security

- **Encryption at rest**: All file content is encrypted with AES-256/GCM using keys stored in Android Keystore.
- **App lock**: The application supports biometric and PIN/pattern authentication for access.
- **Hidden folders**: Folders can be marked hidden and require a configurable gesture to reveal.
- **Backup disabled**: `android:allowBackup` is set to `false`, preventing automatic cloud backup of app data.
- **Screen protection**: FLAG_SECURE prevents the app window from appearing in screenshots or screen recordings on supported devices.
- **End-to-end push encryption**: Cloud Push uses device keypairs held in Android Keystore; pushed content is sealed to the device's public key so the paired server cannot read it when encryption is enabled.

## Changes to This Policy

This policy may be updated from time to time. Changes will be reflected in the application's source repository. Continued use of the application after changes constitutes acceptance of the updated policy.

## Contact

For questions about this privacy policy, open an issue at:
https://github.com/githuba42r/mdrender/issues

## Compliance

- **GDPR**: The app itself performs no tracking and retains no personal data on-device beyond your own files. The only personal data it can transmit is the optional Cloud Push pairing information, sent solely to the server you choose and used only to deliver pushes to this device.
- **CCPA/CPRA**: MDRender does not sell, share, or collect personal information for cross-context behavioural advertising; it has none.
- **COPPA**: MDRender does not collect data from children.
