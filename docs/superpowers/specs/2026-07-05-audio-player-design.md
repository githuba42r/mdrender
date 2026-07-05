# Audio Player — Design

## Goal

Play audio files (MP3, AAC, OGG, WAV, FLAC) stored in the encrypted MDRender app, with bookmark continuity, a notification with playback controls, and a two-state UI (full-screen player + persistent mini-player bar).

## Media Library

**AndroidX Media3 (ExoPlayer) 1.10.1** — Google's official media stack. No custom MP3 decoding needed; supports all common audio formats out of the box. First-class Compose support via `media3-ui-compose-material3`.

### Dependencies

```toml
# gradle/libs.versions.toml
media3 = "1.10.1"
androidx-media3-exoplayer = { group = "androidx.media3", name = "media3-exoplayer", version.ref = "media3" }
androidx-media3-session = { group = "androidx.media3", name = "media3-session", version.ref = "media3" }
androidx-media3-common-ktx = { group = "androidx.media3", name = "media3-common-ktx", version.ref = "media3" }
androidx-media3-ui-compose-material3 = { group = "androidx.media3", name = "media3-ui-compose-material3", version.ref = "media3" }
```

### Manifest additions

```xml
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK" />

<service
    android:name=".audio.AudioPlayerService"
    android:exported="false"
    android:foregroundServiceType="mediaPlayback" />
```

## Architecture

```
┌──────────────────────────────────────────┐
│              MainActivity                 │
│  ┌───── Compose ──────────────────────┐  │
│  │  MDRenderNavHost (file browsing)   │  │
│  │  AudioMiniPlayerBar (persistent)   │  │
│  └────────────────────────────────────┘  │
└──────────────────────┬───────────────────┘
                       │ MediaController binding
┌──────────────────────▼───────────────────┐
│         AudioPlayerService                │
│  (MediaSessionService, foreground)        │
│                                           │
│  ┌─────────┐  ┌──────────────────────┐   │
│  │ ExoPlayer│  │ PlaybackStateManager │   │
│  └────┬────┘  │ - position tracking  │   │
│       │       │ - bookmark save/load │   │
│       │       │ - temp file lifecycle│   │
│       │       └──────────────────────┘   │
│       │                                  │
│  ┌────▼─────────────────────────────────┐│
│  │  EncryptedBlob → decrypt → cache URI ││
│  └──────────────────────────────────────┘│
└──────────────────────────────────────────┘
                       │
                       ▼
              FileRepository (Room DB)
              - playback_position
              - getDecryptedContent(id)
```

## Data Layer Changes

### FileEntity
- Add `val playbackPosition: Long = 0`

### AppDatabase
- Migration v3→v4: `ALTER TABLE files ADD COLUMN playback_position INTEGER NOT NULL DEFAULT 0`

### FileDao
- `@Query("UPDATE files SET playback_position = :pos WHERE id = :id") suspend fun updatePlaybackPosition(id: Long, pos: Long)`

### FileRepository
- `suspend fun savePlaybackPosition(id: Long, pos: Long)`
- `playback_position` is already accessible via `getFileMetadata(id)?.playbackPosition`

### FileType
- Add `AUDIO(Icons.Filled.MusicNote, "Audio")`
- `fromMimeType` add `mimeType.startsWith("audio/") -> AUDIO`

### FileRepository.mimeTypeFromExtension
- Add `.mp3` → `audio/mpeg`, `.aac` → `audio/aac`, `.ogg` → `audio/ogg`, `.wav` → `audio/wav`, `.flac` → `audio/flac`

## AudioPlayerService

A `MediaSessionService` that owns the single `ExoPlayer` instance. It is the canonical owner of playback state, surviving activity destroy/recreate.

### Key responsibilities

1. **Decrypt and cache** — when a play request comes in with `fileId`, decrypt the blob via `FileRepository.getDecryptedContent(id)` into `context.cacheDir/audio/<fileId>.<ext>`. The URI is passed to `MediaItem.fromUri()`.
2. **Expose MediaSession** — a `MediaSession` with a `MediaSession.Callback` that handles play/pause/seek from both the UI and the notification.
3. **Position tracking** — periodically (every 5s of movement) saves `playbackPosition` via `FileRepository.savePlaybackPosition(id, pos)`. Saves immediately on pause/stop/song-end.
4. **Notification** — handled by Media3's built-in `MediaNotification.Provider`. Customise `onGetNotification()` for the MDRender look.
5. **Auto-pause on background** — pause playback when the app goes to background. Keep notification visible so user can resume. On `onTaskRemoved` (user swipes notification away or clears recents), save position and `stopSelf()` to dismiss notification.
6. **Playback position restore** — on new play request, `seekTo(playbackPosition)` from the file's saved position.

### Lifecycle

| Event | Action |
|---|---|
| `onPlay(fileId)` intent | Decrypt, seek to saved position, start |
| `onPause()` from UI/notification | Pause, save position immediately |
| `onResume()` from UI/notification | Resume |
| App backgrounds | Pause (playWhenReady=false), notification stays |
| `onTaskRemoved()` | Save position, stopSelf (notification dismissed) |
| Song ends naturally | Reset position to 0, save, stop |
| New file selected | Stop current, save position, start new |

## Temp File Lifecycle

- Decrypted audio is written to `context.cacheDir/audio/<fileId>.<ext>`.
- When a new file is played, the old temp file is deleted.
- When the service stops, all files in `cacheDir/audio/` are cleaned up.
- Crash recovery: the `clearShareCache` pattern in `MDRenderApplication.onCreate` can also clean stale audio cache on startup.

## AudioPlayerViewModel

A ViewModel scoped to the `AudioPlayerScreen` composable, binding to the `MediaSession` via `MediaController`.

### State exposed

```kotlin
data class AudioPlayerUiState(
    val fileName: String = "",
    val fileId: Long = 0,
    val isPlaying: Boolean = false,
    val currentPosition: Long = 0,
    val duration: Long = 0,
    val isExpanded: Boolean = false      // full-screen vs mini
)
```

### Actions

- `play(fileId: Long)`
- `pause()` / `resume()`
- `seekTo(position: Long)`
- `skipForward(seconds: Int)`
- `skipBackward(seconds: Int)`
- `toggleExpanded()`
- `close()` — collapses to mini, does not stop

The ViewModel sends commands to the service via an `Intent` or a bound service interface. The position and state are observed from `MediaController` callbacks.

## UI: Full-Screen Player

Shown when the user taps an audio file in the browser, or taps the mini-player bar.

- Top app bar: "Now Playing" title, back button collapses to mini
- Large file icon (MusicNote) centered
- File name, folder name
- Progress slider (`Slider` bound to `currentPosition` / `duration`)
- Controls row: skip back 15s, play/pause, skip forward 15s
- Playback speed selector (optional)
- "Mini Player" button at bottom to collapse

## UI: Mini-Player Bar

A thin bar (~56dp) at the bottom of every screen, rendered in `MainActivity` above the bottom navigation area. Only visible when `fileId != 0` (an audio file is loaded).

```
┌──────────────────────────────────────┐
│  ▶  song-title.mp3    0:42 ──────── │
└──────────────────────────────────────┘
```

- Play/pause icon on left
- File name (single line, ellipsize if long)
- Elapsed time on right (formatted m:ss)
- No progress bar to keep it compact
- Tap anywhere → `toggleExpanded()` (navigates to or shows full-screen view)

The mini-player observes shared state from the ViewModel / service to update play/pause icon and elapsed time.

## Notification

Media3's `MediaSessionService` provides a standard media notification: play/pause, skip to next/previous (disabled for single-file playback), progress bar, album art (none for audio files). The notification is automatically posted when playback starts and dismissed when it stops.

When the app is in the background and the user taps play from the notification, playback resumes. Media3 handles all of this — the service just needs to return a `MediaNotification` from `onGetNotification()`.

## Navigation Flow

1. User taps `.mp3` file in `FolderBrowserScreen` → Routes to `AudioPlayerScreen(fileId)` or directly opens full-screen player
2. Full-screen player appears → playback starts
3. User taps Back or Mini Player button → collapses to mini-player, returns to previous screen
4. User navigates freely (open MD files, images, settings) → mini-player persists at bottom
5. User taps another audio file → current stops, position saved, new starts
6. User closes app → mini-player hides, service pauses, notification stays
7. User returns via notification → app opens, mini-player reappears

## Constraints and Non-Goals

- No equalizer or audio effects
- No playlist management (plays one file at a time, sequential new file replaces current)
- No network streaming (all files are local encrypted blobs)
- No gapless playback
- No lock-screen controls (Android handles this via MediaSession)
- Audio cache files are ephemeral and cleaned on service stop

## Headphone-Only Playback Setting

A privacy setting that prevents audio from playing over the device speaker. When enabled, playback blocks unless wired headphones or a Bluetooth A2DP device is connected.

### Setting

A toggle in Settings screen: **"Headphones only"** with supporting text: *"Require headphones or Bluetooth audio to play files. Prevents private audio playing over the device speaker."*

The preference is stored in a simple `SharedPreferences` key `headphones_only` (default `false`), following the existing pattern of `LocalSendPrefs`.

### Detection

Use `AudioManager` to detect active audio output:

```kotlin
fun isHeadphonesConnected(context: Context): Boolean {
    val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    return audioManager.isWiredHeadsetOn || audioManager.isBluetoothA2dpOn
}
```

- `isWiredHeadsetOn` — wired 3.5mm or USB-C headphones
- `isBluetoothA2dpOn` — Bluetooth A2DP profile (music headphones/speakers)

Both flags reflect the current hardware state; no broadcast receiver is needed for the check itself. A `BroadcastReceiver` for `ACTION_HEADSET_PLUG` and `BluetoothDevice.ACTION_ACL_CONNECTED` is used to auto-pause/resume when headphones are unplugged/plugged while this setting is active.

### Playback gating

When `headphones_only` is enabled and the user taps an audio file:

1. Check `isHeadphonesConnected()` before starting playback
2. If no headphones detected → show a snackbar/toast: *"Connect headphones to play audio"* and do not start playback
3. If headphones are connected → start playback normally

### Auto-pause on unplug

A `BroadcastReceiver` registered in `AudioPlayerService` listens for:
- `ACTION_HEADSET_PLUG` — wired headset unplugged
- `BluetoothDevice.ACTION_ACL_DISCONNECTED` — Bluetooth audio disconnected
- `BluetoothA2dp.ACTION_CONNECTION_STATE_CHANGED` — A2DP profile disconnection

When the active audio route is lost and `headphones_only` is enabled: pause playback immediately and show a notification / snackbar: *"Audio paused — headphones disconnected."` Playback resumes automatically when headphones are reconnected (or must be tapped manually based on the existing pause/resume UX).

### Mini-player indicator

When `headphones_only` is enabled and no headphones are connected, the mini-player shows a muted/disconnected icon (headphones icon with a slash) instead of the play button, indicating playback is blocked.
