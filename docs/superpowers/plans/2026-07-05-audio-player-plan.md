# Audio Player Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Play encrypted audio files in MDRender with bookmark persistence, background playback via Media3 notification, a full-screen player, a persistent mini-player bar, and a headphone-only privacy setting.

**Architecture:** Media3 ExoPlayer in a `MediaSessionService` does all playback. A Hilt-singleton `AudioPlayerState` bridges the service to the Compose UI. Playback position is persisted in the Room `files` table. The mini-player bar is a `@Composable` rendered at the bottom of `MainActivity`'s content tree. Headphone detection uses `AudioManager` and a `BroadcastReceiver`.

**Tech Stack:** Media3 ExoPlayer 1.10.1, Jetpack Compose, Room, Hilt

## Global Constraints

- Media3 version must be `1.10.1` across all library entries
- File positions (scroll + playback) are currently keyed by file `id`; a future refactor will key by `(folder_id, filename)`. Build the new playback_position column on `id` for consistency with scroll_position, then migrate both later.
- All audio playback goes through decrypt-to-cache first — the ExoPlayer never reads encrypted data
- No playlist support — one file at a time, sequential play replaces the current file
- Headphone-only setting defaults to `false`

---

### Task 1: Gradle Dependencies, Manifest, and Data Layer

**Files:**
- Modify: `gradle/libs.versions.toml`
- Modify: `app/build.gradle.kts`
- Modify: `app/src/main/AndroidManifest.xml`
- Modify: `app/src/main/java/com/a42r/mdrender/data/entity/FileEntity.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/data/AppDatabase.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/data/dao/FileDao.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/data/repository/FileRepository.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/ui/navigation/FileType.kt`

**Interfaces:**
- Produces: `FileEntity.playbackPosition: Long`, `FileDao.updatePlaybackPosition(id, pos)`, `FileRepository.savePlaybackPosition(id, pos)`, `AppDatabase.MIGRATION_3_4`, `FileType.AUDIO`, audio MIME types in `FileRepository.mimeTypeFromExtension`, `AndroidManifest` with `FOREGROUND_SERVICE_MEDIA_PLAYBACK` permission and `AudioPlayerService` declaration

- [ ] **Step 1: Add Media3 dependencies to version catalog**

Target: `gradle/libs.versions.toml`

Add after the existing `[versions]` and `[libraries]` blocks:

```toml
media3 = "1.10.1"
```

```toml
androidx-media3-exoplayer = { group = "androidx.media3", name = "media3-exoplayer", version.ref = "media3" }
androidx-media3-session = { group = "androidx.media3", name = "media3-session", version.ref = "media3" }
androidx-media3-common-ktx = { group = "androidx.media3", name = "media3-common-ktx", version.ref = "media3" }
```

- [ ] **Step 2: Add implementation lines to app/build.gradle.kts**

Add inside the `dependencies { }` block:

```kotlin
implementation(libs.androidx.media3.exoplayer)
implementation(libs.androidx.media3.session)
implementation(libs.androidx.media3.common.ktx)
```

- [ ] **Step 3: Add manifest permission and service**

Target: `app/src/main/AndroidManifest.xml`

Add the permission inside `<manifest>`:
```xml
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK" />
```

Add the service inside `<application>`, alongside the existing `LocalSendService`:
```xml
<service
    android:name=".audio.AudioPlayerService"
    android:exported="false"
    android:foregroundServiceType="mediaPlayback" />
```

- [ ] **Step 4: Add playbackPosition to FileEntity**

Target: `app/src/main/java/com/a42r/mdrender/data/entity/FileEntity.kt`

Add after `scrollPosition`:
```kotlin
@ColumnInfo(name = "playback_position") val playbackPosition: Long = 0
```

The primary constructor will end with:
```kotlin
    @ColumnInfo(name = "updated_at") val updatedAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "scroll_position") val scrollPosition: Int = 0,
    @ColumnInfo(name = "playback_position") val playbackPosition: Long = 0
)
```

- [ ] **Step 5: Create Room migration v3→v4**

Target: `app/src/main/java/com/a42r/mdrender/data/AppDatabase.kt`

Change `@Database` version from `3` to `4`. Add `MIGRATION_3_4` after the existing `MIGRATION_2_3`:

```kotlin
@Database(entities = [FolderEntity::class, FileEntity::class], version = 4, exportSchema = false)
abstract class AppDatabase : RoomDatabase() {
    // ... existing code ...

    companion object {
        // ... existing MIGRATION_1_2, MIGRATION_2_3 ...

        /** v3 → v4: add files.playback_position for audio bookmarking. */
        val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE files ADD COLUMN playback_position INTEGER NOT NULL DEFAULT 0")
            }
        }
    }
}
```

Then add the migration to `DatabaseModule.kt`:
```kotlin
.addMigrations(AppDatabase.MIGRATION_1_2, AppDatabase.MIGRATION_2_3, AppDatabase.MIGRATION_3_4)
```

- [ ] **Step 6: Add DAO method for playback position**

Target: `app/src/main/java/com/a42r/mdrender/data/dao/FileDao.kt`

Add after `updateScrollPosition`:
```kotlin
@Query("UPDATE files SET playback_position = :pos WHERE id = :id")
suspend fun updatePlaybackPosition(id: Long, pos: Long)
```

- [ ] **Step 7: Add repository method + audio MIME types**

Target: `app/src/main/java/com/a42r/mdrender/data/repository/FileRepository.kt`

Add after `saveScrollPosition`:
```kotlin
suspend fun savePlaybackPosition(id: Long, pos: Long) = fileDao.updatePlaybackPosition(id, pos)
```

In `mimeTypeFromExtension`, add before the `else`:
```kotlin
filename.endsWith(".mp3", ignoreCase = true) -> "audio/mpeg"
filename.endsWith(".aac", ignoreCase = true) -> "audio/aac"
filename.endsWith(".ogg", ignoreCase = true) -> "audio/ogg"
filename.endsWith(".wav", ignoreCase = true) -> "audio/wav"
filename.endsWith(".flac", ignoreCase = true) -> "audio/flac"
```

- [ ] **Step 8: Add AUDIO FileType**

Target: `app/src/main/java/com/a42r/mdrender/ui/navigation/FileType.kt`

```kotlin
import androidx.compose.material.icons.filled.MusicNote

enum class FileType(val icon: ImageVector, val label: String) {
    FOLDER(Icons.Filled.Folder, "Folder"),
    MARKDOWN(Icons.Filled.Description, "Markdown"),
    TEXT(Icons.AutoMirrored.Filled.TextSnippet, "Text"),
    IMAGE(Icons.Filled.Image, "Image"),
    AUDIO(Icons.Filled.MusicNote, "Audio"),
    UNKNOWN(Icons.AutoMirrored.Filled.InsertDriveFile, "File");
```

In `fromMimeType`, add before the `else`:
```kotlin
mimeType.startsWith("audio/") -> AUDIO
```

- [ ] **Step 9: Verify compilation**

Run: `./gradlew :app:compileDebugKotlin`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 10: Commit**

```bash
git add gradle/libs.versions.toml app/build.gradle.kts app/src/main/AndroidManifest.xml \
       app/src/main/java/com/a42r/mdrender/data/entity/FileEntity.kt \
       app/src/main/java/com/a42r/mdrender/data/AppDatabase.kt \
       app/src/main/java/com/a42r/mdrender/data/dao/FileDao.kt \
       app/src/main/java/com/a42r/mdrender/data/repository/FileRepository.kt \
       app/src/main/java/com/a42r/mdrender/ui/navigation/FileType.kt
git commit -m "feat: add Media3 dependencies, audio data layer, and manifest declarations"
```

---

### Task 2: AudioPlayerPrefs and HeadphoneMonitor

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/audio/AudioPlayerPrefs.kt`
- Create: `app/src/main/java/com/a42r/mdrender/audio/HeadphoneMonitor.kt`

**Interfaces:**
- Produces: `AudioPlayerPrefs.headphonesOnly: Boolean` (read/write), `HeadphoneMonitor` (BroadcastReceiver that triggers a callback on connect/disconnect)

- [ ] **Step 1: Create AudioPlayerPrefs**

Target: Create `app/src/main/java/com/a42r/mdrender/audio/AudioPlayerPrefs.kt`

```kotlin
package com.a42r.mdrender.audio

import android.content.Context
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AudioPlayerPrefs @Inject constructor() {
    companion object {
        private const val PREFS_NAME = "audio_player"
        private const val KEY_HEADPHONES_ONLY = "headphones_only"
    }

    private var prefs: android.content.SharedPreferences? = null

    fun init(context: Context) {
        prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    var headphonesOnly: Boolean
        get() = prefs?.getBoolean(KEY_HEADPHONES_ONLY, false) ?: false
        set(value) { prefs?.edit()?.putBoolean(KEY_HEADPHONES_ONLY, value)?.apply() }
}
```

- [ ] **Step 2: Initialize AudioPlayerPrefs in Application**

Target: `app/src/main/java/com/a42r/mdrender/MDRenderApplication.kt`

Add `@Inject lateinit var audioPlayerPrefs: AudioPlayerPrefs` and call `audioPlayerPrefs.init(this)` in `onCreate()` after `instance = this`.

- [ ] **Step 3: Create HeadphoneMonitor**

Target: Create `app/src/main/java/com/a42r/mdrender/audio/HeadphoneMonitor.kt`

```kotlin
package com.a42r.mdrender.audio

import android.bluetooth.BluetoothA2dp
import android.bluetooth.BluetoothDevice
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioDeviceInfo
import android.media.AudioManager

object AudioManagerUtil {
    fun isHeadphonesConnected(context: Context): Boolean {
        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        if (audioManager.isWiredHeadsetOn || audioManager.isBluetoothA2dpOn) return true
        // Double-check via device list (more reliable on API 23+)
        val devices = audioManager.getDevices(AudioManager.GET_DEVICES_ALL)
        return devices.any { d ->
            d.type == AudioDeviceInfo.TYPE_WIRED_HEADSET ||
            d.type == AudioDeviceInfo.TYPE_WIRED_HEADPHONES ||
            d.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP ||
            d.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO
        }
    }
}

/** Listens for headphone plug/unplug events and calls [onHeadphonesChanged] with
 *  the new connected state. */
class HeadphoneMonitor(
    private val onHeadphonesChanged: (connected: Boolean) -> Unit
) : BroadcastReceiver() {

    private val filter = IntentFilter().apply {
        addAction(Intent.ACTION_HEADSET_PLUG)
        addAction(BluetoothDevice.ACTION_ACL_CONNECTED)
        addAction(BluetoothDevice.ACTION_ACL_DISCONNECTED)
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            addAction(BluetoothA2dp.ACTION_CONNECTION_STATE_CHANGED)
        }
    }

    fun register(context: Context) {
        context.registerReceiver(this, filter)
    }

    fun unregister(context: Context) {
        context.unregisterReceiver(this)
    }

    override fun onReceive(context: Context, intent: Intent?) {
        when (intent?.action) {
            Intent.ACTION_HEADSET_PLUG -> {
                val plugged = intent.getIntExtra("state", 0) == 1
                onHeadphonesChanged(AudioManagerUtil.isHeadphonesConnected(context))
            }
            BluetoothDevice.ACTION_ACL_CONNECTED,
            BluetoothDevice.ACTION_ACL_DISCONNECTED -> {
                onHeadphonesChanged(AudioManagerUtil.isHeadphonesConnected(context))
            }
            BluetoothA2dp.ACTION_CONNECTION_STATE_CHANGED -> {
                onHeadphonesChanged(AudioManagerUtil.isHeadphonesConnected(context))
            }
        }
    }
}
```

- [ ] **Step 4: Verify compilation**

Run: `./gradlew :app:compileDebugKotlin`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/audio/ \
       app/src/main/java/com/a42r/mdrender/MDRenderApplication.kt
git commit -m "feat: add AudioPlayerPrefs and HeadphoneMonitor for audio privacy"
```

---

### Task 3: AudioPlayerState Singleton

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/audio/AudioPlayerState.kt`

**Interfaces:**
- Produces: `AudioPlayerState` — injectable singleton with `info: StateFlow<AudioFileInfo>`, `isPlaying`, `currentPosition`, `duration`. Methods: `play(fileId, fileName)`, `pause()`, `resume()`, `seekTo(pos)`, `positionUpdated(pos)`, `setPlaying(playing)`, `stop()`.

- [ ] **Step 1: Create AudioPlayerState**

Target: Create `app/src/main/java/com/a42r/mdrender/audio/AudioPlayerState.kt`

```kotlin
package com.a42r.mdrender.audio

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

data class AudioFileInfo(
    val fileId: Long = 0,
    val fileName: String = ""
)

/** Shared state between AudioPlayerService and the Compose UI layers.
 *  The service mutates this in response to playback; the UI observes it. */
@Singleton
class AudioPlayerState @Inject constructor() {

    private val _info = MutableStateFlow(AudioFileInfo())
    val info: StateFlow<AudioFileInfo> = _info.asStateFlow()

    private val _isPlaying = MutableStateFlow(false)
    val isPlaying: StateFlow<Boolean> = _isPlaying.asStateFlow()

    val currentPosition = MutableStateFlow(0L)
    val duration = MutableStateFlow(0L)

    // --- Command intents (called from UI) ---
    // These are consumed by AudioPlayerService via a SharedFlow.
    private val _commands = MutableSharedFlow<PlayerCommand>(extraBufferCapacity = 4)
    val commands: SharedFlow<PlayerCommand> = _commands.asSharedFlow()

    // --- State mutations (called from service) ---
    fun setFileInfo(fileId: Long, fileName: String) {
        _info.value = AudioFileInfo(fileId, fileName)
    }

    fun setPlaying(playing: Boolean) {
        _isPlaying.value = playing
    }

    fun setDuration(dur: Long) {
        duration.value = dur
    }

    fun updatePosition(pos: Long) {
        currentPosition.value = pos
    }

    fun stop() {
        _info.value = AudioFileInfo()
        _isPlaying.value = false
        currentPosition.value = 0L
        duration.value = 0L
    }

    // --- Commands from UI ---
    fun play(fileId: Long, fileName: String) {
        _commands.tryEmit(PlayerCommand.Play(fileId, fileName))
    }

    fun pause() {
        _commands.tryEmit(PlayerCommand.Pause)
    }

    fun resume() {
        _commands.tryEmit(PlayerCommand.Resume)
    }

    fun seekTo(positionMs: Long) {
        _commands.tryEmit(PlayerCommand.SeekTo(positionMs))
    }

    fun stopPlayback() {
        _commands.tryEmit(PlayerCommand.Stop)
        stop()
    }
}

sealed class PlayerCommand {
    data class Play(val fileId: Long, val fileName: String) : PlayerCommand()
    data object Pause : PlayerCommand()
    data object Resume : PlayerCommand()
    data class SeekTo(val positionMs: Long) : PlayerCommand()
    data object Stop : PlayerCommand()
}
```

- [ ] **Step 2: Verify compilation**

Run: `./gradlew :app:compileDebugKotlin`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 3: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/audio/AudioPlayerState.kt
git commit -m "feat: add AudioPlayerState singleton bridging service and UI"
```

---

### Task 4: AudioPlayerService (MediaSessionService)

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/audio/AudioPlayerService.kt`

**Interfaces:**
- Consumes: `AudioPlayerState`, `AudioPlayerPrefs`, `FileRepository`, `HeadphoneMonitor`
- Produces: `AudioPlayerService` — MediaSessionService that owns ExoPlayer, handles commands, manages temp audio files, publishes notification, tracks position

- [ ] **Step 1: Create AudioPlayerService**

Target: Create `app/src/main/java/com/a42r/mdrender/audio/AudioPlayerService.kt`

```kotlin
package com.a42r.mdrender.audio

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.net.Uri
import android.support.v4.media.session.MediaSessionCompat
import androidx.core.app.NotificationCompat
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaSessionService
import androidx.media3.session.MediaSession
import com.a42r.mdrender.MDRenderApplication
import com.a42r.mdrender.R
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.ui.MainActivity
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.*
import java.io.File
import javax.inject.Inject

@AndroidEntryPoint
class AudioPlayerService : MediaSessionService() {

    @Inject lateinit var fileRepository: FileRepository
    @Inject lateinit var playerState: AudioPlayerState
    @Inject lateinit var prefs: AudioPlayerPrefs

    private lateinit var exoPlayer: ExoPlayer
    private lateinit var mediaSession: MediaSession
    private var serviceScope: CoroutineScope? = null
    private var currentTempFile: File? = null
    private var positionJob: Job? = null
    private var headphoneMonitor: HeadphoneMonitor? = null

    override fun onCreate() {
        super.onCreate()
        exoPlayer = ExoPlayer.Builder(this)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(C.USAGE_MEDIA)
                    .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
                    .build(),
                true // handle audio focus
            )
            .build()

        mediaSession = MediaSession.Builder(this, exoPlayer).build()

        // Listen for headphone disconnection
        headphoneMonitor = HeadphoneMonitor { connected ->
            if (prefs.headphonesOnly && !connected && exoPlayer.isPlaying) {
                exoPlayer.pause()
            }
        }
        headphoneMonitor?.register(this)

        serviceScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
        serviceScope?.launch {
            playerState.commands.collect { command ->
                when (command) {
                    is PlayerCommand.Play -> playFile(command.fileId, command.fileName)
                    is PlayerCommand.Pause -> exoPlayer.pause()
                    is PlayerCommand.Resume -> exoPlayer.play()
                    is PlayerCommand.SeekTo -> exoPlayer.seekTo(command.positionMs)
                    is PlayerCommand.Stop -> stopPlayback()
                }
            }
        }
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = mediaSession

    override fun onTaskRemoved(rootIntent: Intent?) {
        savePosition()
        stopPlayback()
        stopSelf()
    }

    override fun onDestroy() {
        savePosition()
        stopPlayback()
        headphoneMonitor?.unregister(this)
        serviceScope?.cancel()
        mediaSession.release()
        exoPlayer.release()
        cleanCache()
        super.onDestroy()
    }

    private fun playFile(fileId: Long, fileName: String) {
        if (prefs.headphonesOnly && !AudioManagerUtil.isHeadphonesConnected(this)) {
            // Can't play — show a toast via the activity
            playerState.setPlaying(false)
            return
        }

        // Stop current, save position
        if (playerState.info.value.fileId != 0L) {
            savePosition()
            exoPlayer.stop()
        }
        cleanCache() // remove previous temp file

        serviceScope?.launch {
            try {
                val (bytes, _) = fileRepository.getDecryptedContent(fileId)
                    ?: throw Exception("File not found")
                val ext = fileName.substringAfterLast('.', "mp3")
                val tempFile = File(cacheDir, "audio/${fileId}_$ext")
                tempFile.parentFile?.mkdirs()
                tempFile.writeBytes(bytes)
                currentTempFile = tempFile

                val metadata = fileRepository.getFileMetadata(fileId)
                val savedPos = metadata?.playbackPosition ?: 0L

                withContext(Dispatchers.Main) {
                    val mediaItem = MediaItem.fromUri(Uri.fromFile(tempFile))
                    exoPlayer.setMediaItem(mediaItem)
                    exoPlayer.prepare()
                    if (savedPos > 0) exoPlayer.seekTo(savedPos)

                    playerState.setFileInfo(fileId, fileName)
                    playerState.setDuration(if (exoPlayer.duration > 0) exoPlayer.duration else 0L)
                    playerState.updatePosition(savedPos)

                    exoPlayer.addListener(object : Player.Listener {
                        override fun onIsPlayingChanged(isPlaying: Boolean) {
                            playerState.setPlaying(isPlaying)
                            if (!isPlaying && exoPlayer.playbackState == Player.STATE_ENDED) {
                                onPlaybackEnded()
                            }
                        }
                        override fun onPlaybackStateChanged(state: Int) {
                            if (state == Player.STATE_READY && exoPlayer.duration > 0) {
                                playerState.setDuration(exoPlayer.duration)
                            }
                        }
                    })

                    exoPlayer.play()
                }

                // Track position every 2 seconds
                positionJob?.cancel()
                positionJob = serviceScope?.launch {
                    while (isActive) {
                        delay(2000)
                        val pos = withContext(Dispatchers.Main) { exoPlayer.currentPosition }
                        playerState.updatePosition(pos)
                        fileRepository.savePlaybackPosition(fileId, pos)
                    }
                }
            } catch (e: Exception) {
                playerState.setFileInfo(0, "")
                playerState.setPlaying(false)
            }
        }
    }

    private fun onPlaybackEnded() {
        savePosition()
        // Reset position for next play
        fileRepository.apply {
            serviceScope?.launch {
                savePlaybackPosition(playerState.info.value.fileId, 0L)
            }
        }
        playerState.updatePosition(0L)
    }

    private fun savePosition() {
        val id = playerState.info.value.fileId
        val pos = playerState.currentPosition.value
        if (id != 0L) {
            serviceScope?.launch {
                fileRepository.savePlaybackPosition(id, pos)
            }
        }
    }

    private fun stopPlayback() {
        exoPlayer.stop()
        playerState.setPlaying(false)
        positionJob?.cancel()
    }

    private fun cleanCache() {
        currentTempFile?.delete()
        currentTempFile = null
    }

    companion object {
        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "audio_playback"
    }
}
```

- [ ] **Step 2: Verify compilation**

Run: `./gradlew :app:compileDebugKotlin`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 3: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/audio/AudioPlayerService.kt
git commit -m "feat: add AudioPlayerService with Media3 ExoPlayer and background playback"
```

---

### Task 5: AudioPlayerViewModel and Full-Screen AudioPlayerScreen

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/audio/AudioPlayerViewModel.kt`
- Create: `app/src/main/java/com/a42r/mdrender/audio/AudioPlayerScreen.kt`

**Interfaces:**
- Consumes: `AudioPlayerState`
- Produces: `AudioPlayerScreen` — full-screen Compose player with controls, progress slider, skip buttons

- [ ] **Step 1: Create AudioPlayerViewModel**

Target: Create `app/src/main/java/com/a42r/mdrender/audio/AudioPlayerViewModel.kt`

```kotlin
package com.a42r.mdrender.audio

import androidx.lifecycle.ViewModel
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject

@HiltViewModel
class AudioPlayerViewModel @Inject constructor(
    val playerState: AudioPlayerState
) : ViewModel() {
    // The ViewModel exposes the shared AudioPlayerState.
    // Commands are sent through playerState.play(), .pause(), etc.
    // No local state — everything flows through the singleton service.
}
```

- [ ] **Step 2: Create AudioPlayerScreen**

Target: Create `app/src/main/java/com/a42r/mdrender/audio/AudioPlayerScreen.kt`

```kotlin
package com.a42r.mdrender.audio

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AudioPlayerScreen(
    onBack: () -> Unit,
    viewModel: AudioPlayerViewModel = hiltViewModel()
) {
    val state = viewModel.playerState
    val info by state.info.collectAsState()
    val isPlaying by state.isPlaying.collectAsState()
    val position by state.currentPosition.collectAsState()
    val duration by state.duration.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Now Playing") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Collapse to mini player")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            Icon(
                Icons.Filled.MusicNote,
                contentDescription = null,
                modifier = Modifier.size(96.dp),
                tint = MaterialTheme.colorScheme.primary
            )

            Spacer(Modifier.height(24.dp))

            Text(
                info.fileName,
                style = MaterialTheme.typography.headlineSmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )

            Spacer(Modifier.height(4.dp))

            Text(
                formatDuration(position) + " / " + formatDuration(duration),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            Spacer(Modifier.height(16.dp))

            // Progress slider
            Slider(
                value = if (duration > 0) position.toFloat().coerceAtMost(duration.toFloat()) else 0f,
                onValueChange = { state.seekTo(it.toLong()) },
                valueRange = 0f..if (duration > 0) duration.toFloat() else 1f,
                modifier = Modifier.fillMaxWidth()
            )

            Spacer(Modifier.height(16.dp))

            // Controls
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceEvenly,
                modifier = Modifier.fillMaxWidth()
            ) {
                // Skip back 15s
                IconButton(onClick = {
                    val newPos = (position - 15_000).coerceAtLeast(0)
                    state.seekTo(newPos)
                }) {
                    Icon(Icons.Filled.Replay10, "Skip back 10s", modifier = Modifier.size(48.dp))
                }

                IconButton(
                    onClick = {
                        if (isPlaying) state.pause() else state.resume()
                    },
                    modifier = Modifier.size(72.dp)
                ) {
                    Icon(
                        if (isPlaying) Icons.Filled.PauseCircle else Icons.Filled.PlayCircle,
                        contentDescription = if (isPlaying) "Pause" else "Play",
                        modifier = Modifier.size(64.dp),
                        tint = MaterialTheme.colorScheme.primary
                    )
                }

                IconButton(onClick = {
                    state.seekTo((position + 10_000).coerceAtMost(duration))
                }) {
                    Icon(Icons.Filled.Forward10, "Skip forward 10s", modifier = Modifier.size(48.dp))
                }
            }

            Spacer(Modifier.height(32.dp))

            // Collapse to mini player
            OutlinedButton(onClick = onBack) {
                Icon(Icons.Filled.Minimize, "Mini player")
                Spacer(Modifier.width(8.dp))
                Text("Mini Player")
            }
        }
    }
}

private fun formatDuration(ms: Long): String {
    val totalSec = ms / 1000
    val min = totalSec / 60
    val sec = totalSec % 60
    return "%d:%02d".format(min, sec)
}
```

- [ ] **Step 3: Verify compilation**

Run: `./gradlew :app:compileDebugKotlin`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 4: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/audio/AudioPlayerViewModel.kt \
       app/src/main/java/com/a42r/mdrender/audio/AudioPlayerScreen.kt
git commit -m "feat: add AudioPlayerScreen and ViewModel"
```

---

### Task 6: AudioMiniPlayerBar and MainActivity Integration

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/audio/AudioMiniPlayerBar.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/ui/MainActivity.kt`

**Interfaces:**
- Consumes: `AudioPlayerState`, `AudioPlayerPrefs`
- Produces: Persistent mini-player bar at the bottom of every screen

- [ ] **Step 1: Create AudioMiniPlayerBar composable**

Target: Create `app/src/main/java/com/a42r/mdrender/audio/AudioMiniPlayerBar.kt`

```kotlin
package com.a42r.mdrender.audio

import androidx.compose.animation.*
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import com.a42r.mdrender.ui.navigation.Routes

/** Thin persistent bar at the bottom of every screen when an audio file is loaded. */
@Composable
fun AudioMiniPlayerBar(
    navController: NavController,
    playerState: AudioPlayerState,
    prefs: AudioPlayerPrefs
) {
    val info by playerState.info.collectAsState()
    val isPlaying by playerState.isPlaying.collectAsState()
    val position by playerState.currentPosition.collectAsState()

    AnimatedVisibility(
        visible = info.fileId != 0L,
        enter = slideInVertically(initialOffsetY = { it }) + fadeIn(),
        exit = slideOutVertically(targetOffsetY = { it }) + fadeOut()
    ) {
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { navController.navigate(Routes.AudioPlayer.createRoute(info.fileId)) },
            tonalElevation = 3.dp,
            shadowElevation = 6.dp
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                val blocked = prefs.headphonesOnly &&
                    !AudioManagerUtil.isHeadphonesConnected(LocalContext.current)

                if (blocked) {
                    Icon(
                        Icons.Filled.HeadsetOff,
                        contentDescription = "No headphones",
                        tint = MaterialTheme.colorScheme.error
                    )
                } else {
                    IconButton(
                        onClick = {
                            if (!blocked) {
                                if (isPlaying) playerState.pause() else playerState.resume()
                            }
                        },
                        modifier = Modifier.size(36.dp)
                    ) {
                        Icon(
                            if (isPlaying) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                            contentDescription = if (isPlaying) "Pause" else "Play"
                        )
                    }
                }

                Spacer(Modifier.width(8.dp))

                Text(
                    info.fileName,
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f)
                )

                Spacer(Modifier.width(8.dp))

                Text(
                    formatDuration(position),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

private fun formatDuration(ms: Long): String {
    val totalSec = ms / 1000
    val min = totalSec / 60
    val sec = totalSec % 60
    return "%d:%02d".format(min, sec)
}
```

- [ ] **Step 2: Add audio player imports to MainActivity and render mini-player**

Target: `app/src/main/java/com/a42r/mdrender/ui/MainActivity.kt`

Add to the `setContent` block, after the `MDRenderTheme { }` content but inside the same `Surface` or as an overlay `Box`:

```kotlin
import com.a42r.mdrender.audio.AudioMiniPlayerBar
import com.a42r.mdrender.audio.AudioPlayerPrefs
import com.a42r.mdrender.audio.AudioPlayerState
```

Add `@Inject lateinit var` fields and use them in `setContent`:

```kotlin
@Inject lateinit var playerState: AudioPlayerState
@Inject lateinit var audioPlayerPrefs: AudioPlayerPrefs
```

Wrap the existing `Surface + NavHost` content and the mini-player in a `Box`:

```kotlin
setContent {
    MDRenderTheme {
        // ... existing displayingHidden, LockGate/Surface code ...
        
        Box(modifier = Modifier.fillMaxSize()) {
            // Existing content
            if (locked) {
                LockGate()
            } else {
                Surface(modifier = Modifier.fillMaxSize()) {
                    Box(modifier = Modifier.fillMaxSize()) {
                        MDRenderNavHost()
                        AudioMiniPlayerBar(
                            navController = navController,
                            playerState = playerState,
                            prefs = audioPlayerPrefs
                        )
                    }
                }
                LocalSendOverlays()
            }
        }
    }
}
```

Note: The `navController` is created inside `MDRenderNavHost` — it needs to be available to the mini-player. Either hoist it up to MainActivity or restructure so the NavController is remembered in MainActivity.

**Restructure:** In `MainActivity`, create the `NavController` via `rememberNavController()` and pass it to `MDRenderNavHost(navController)` and to `AudioMiniPlayerBar(navController = navController, ...)`.

- [ ] **Step 3: Verify compilation**

Run: `./gradlew :app:compileDebugKotlin`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 4: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/audio/AudioMiniPlayerBar.kt \
       app/src/main/java/com/a42r/mdrender/ui/MainActivity.kt
git commit -m "feat: add AudioMiniPlayerBar and integrate into MainActivity"
```

---

### Task 7: Wire Navigation — Routes, NavHost, FileType Routing

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/ui/navigation/Routes.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/ui/navigation/MDRenderNavHost.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/ui/browser/FolderBrowserScreen.kt`

**Interfaces:**
- Produces: Audio file tap routes to `AudioPlayerScreen`

- [ ] **Step 1: Add AudioPlayer route**

Target: `app/src/main/java/com/a42r/mdrender/ui/navigation/Routes.kt`

Add inside the `sealed class Routes`:
```kotlin
data object AudioPlayer : Routes("audio_player/{fileId}") {
    fun createRoute(fileId: Long): String = "audio_player/$fileId"
}
```

- [ ] **Step 2: Add AudioPlayer composable to NavHost**

Target: `app/src/main/java/com/a42r/mdrender/ui/navigation/MDRenderNavHost.kt`

Add import:
```kotlin
import com.a42r.mdrender.audio.AudioPlayerScreen
```

Add inside the `NavHost` body:
```kotlin
composable(
    route = Routes.AudioPlayer.route,
    arguments = listOf(navArgument("fileId") { type = NavType.LongType })
) { backStackEntry ->
    val fileId = backStackEntry.arguments?.getLong("fileId") ?: 0L
    // Start playback via a LaunchedEffect
    val viewModel: AudioPlayerViewModel = hiltViewModel()
    LaunchedEffect(fileId) {
        viewModel.playerState.play(fileId, "")
    }
    AudioPlayerScreen(onBack = { navController.popBackStack() })
}
```

- [ ] **Step 3: Route audio file taps to AudioPlayerScreen**

Target: `app/src/main/java/com/a42r/mdrender/ui/browser/FolderBrowserScreen.kt`

In the `openFile` lambda, add a branch for audio files:

```kotlin
val openFile: (FileEntity) -> Unit = { file ->
    val route = when {
        file.mimeType.startsWith("text/markdown") -> Routes.MarkdownViewer.createRoute(file.id)
        file.mimeType.startsWith("text/plain") -> Routes.TextViewer.createRoute(file.id)
        file.mimeType.startsWith("image/") -> Routes.ImageViewer.createRoute(file.id)
        file.mimeType.startsWith("audio/") -> Routes.AudioPlayer.createRoute(file.id)
        else -> Routes.TextViewer.createRoute(file.id)
    }
    navController.navigate(route)
}
```

- [ ] **Step 4: Verify compilation**

Run: `./gradlew :app:compileDebugKotlin`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/ui/navigation/Routes.kt \
       app/src/main/java/com/a42r/mdrender/ui/navigation/MDRenderNavHost.kt \
       app/src/main/java/com/a42r/mdrender/ui/browser/FolderBrowserScreen.kt
git commit -m "feat: wire audio file navigation through Routes and NavHost"
```

---

### Task 8: Settings — Headphone-Only Toggle

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/ui/settings/SettingsViewModel.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/ui/settings/SettingsScreen.kt`

**Interfaces:**
- Consumes: `AudioPlayerPrefs`
- Produces: Toggle in Settings for `headphonesOnly`

- [ ] **Step 1: Add headphone-only state to SettingsViewModel**

Target: `app/src/main/java/com/a42r/mdrender/ui/settings/SettingsViewModel.kt`

Add imports and inject `AudioPlayerPrefs`:

```kotlin
import com.a42r.mdrender.audio.AudioPlayerPrefs

class SettingsViewModel @Inject constructor(
    private val localSendPrefs: LocalSendPrefs,
    private val audioPlayerPrefs: AudioPlayerPrefs,
    @ApplicationContext private val context: Context
) : ViewModel() {
```

Add to `SettingsUiState`:
```kotlin
data class SettingsUiState(
    val appVersion: String = "0.1.0",
    val localSendEnabled: Boolean = false,
    val localSendAlias: String = "",
    val localSendPin: String = "",
    val localSendAutoAccept: Boolean = false,
    val headphonesOnly: Boolean = false
)
```

Populate in the constructor:
```kotlin
private val _uiState = MutableStateFlow(SettingsUiState(
    localSendEnabled = localSendPrefs.enabled,
    localSendAlias = localSendPrefs.alias,
    localSendPin = localSendPrefs.pin,
    localSendAutoAccept = localSendPrefs.autoAccept,
    headphonesOnly = audioPlayerPrefs.headphonesOnly
))
```

Add method:
```kotlin
fun setHeadphonesOnly(enabled: Boolean) {
    audioPlayerPrefs.headphonesOnly = enabled
    _uiState.update { it.copy(headphonesOnly = enabled) }
}
```

- [ ] **Step 2: Add toggle to SettingsScreen**

Target: `app/src/main/java/com/a42r/mdrender/ui/settings/SettingsScreen.kt`

Add inside the `Column` after the `LocalSend` section and before `HorizontalDivider`:

```kotlin
HorizontalDivider()

// Audio section
ListItem(
    headlineContent = { Text("Headphones only") },
    supportingContent = {
        Text("Require headphones or Bluetooth audio to play files. " +
             "Prevents private audio playing over the device speaker.")
    },
    trailingContent = {
        Switch(
            checked = uiState.headphonesOnly,
            onCheckedChange = { viewModel.setHeadphonesOnly(it) }
        )
    }
)
```

- [ ] **Step 3: Verify compilation**

Run: `./gradlew :app:compileDebugKotlin`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 4: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/ui/settings/SettingsViewModel.kt \
       app/src/main/java/com/a42r/mdrender/ui/settings/SettingsScreen.kt
git commit -m "feat: add headphone-only playback setting to Settings"
```

---

### Task 9: Build, Deploy, and Verify

- [ ] **Step 1: Run all unit tests**

Run: `./gradlew :app:testDebugUnitTest`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 2: Build and deploy**

Run: `bash build-deploy.sh`
Expected: `BUILD SUCCESSFUL` + `Deployment complete.`

- [ ] **Step 3: Verify audio file detection**

1. Import an MP3 file into the app (via share or import)
2. Tap the file in the browser
3. Expected: AudioPlayerScreen opens, playback starts

- [ ] **Step 4: Verify mini-player persistence**

1. While audio is playing, press back to collapse to mini-player
2. Navigate to other files/folders
3. Expected: Mini-player bar persists at bottom of every screen

- [ ] **Step 5: Verify notification**

1. While audio is playing, background the app
2. Expected: Media notification appears with play/pause controls
3. Tap pause from notification
4. Expected: Playback pauses

- [ ] **Step 6: Verify bookmark persistence**

1. Play an audio file, let it play for a few seconds
2. Pause and close the file
3. Re-open the same file
4. Expected: Playback resumes from the saved position

- [ ] **Step 7: Verify headphone-only setting**

1. Enable "Headphones only" in Settings
2. Disconnect all headphones
3. Tap an audio file
4. Expected: Playback does not start, mini-player shows muted icon
5. Connect headphones
6. Tap the audio file again
7. Expected: Playback starts normally
