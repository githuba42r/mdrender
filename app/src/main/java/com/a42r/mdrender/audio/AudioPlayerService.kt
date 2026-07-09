package com.a42r.mdrender.audio

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.net.Uri
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaSessionService
import androidx.media3.session.MediaSession
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.data.repository.FolderRepository
import dagger.hilt.android.AndroidEntryPoint
import java.io.File
import javax.inject.Inject
import kotlinx.coroutines.*

@AndroidEntryPoint
class AudioPlayerService : MediaSessionService() {

    @Inject lateinit var fileRepository: FileRepository
    @Inject lateinit var folderRepository: FolderRepository
    @Inject lateinit var playerState: AudioPlayerState
    @Inject lateinit var prefs: AudioPlayerPrefs

    private lateinit var exoPlayer: ExoPlayer
    private lateinit var mediaSession: MediaSession
    private var serviceScope: CoroutineScope? = null
    private var currentTempFile: File? = null
    private var positionJob: Job? = null
    private var headphoneMonitor: HeadphoneMonitor? = null
    private var currentFileId: Long = 0L
    private var playJob: Job? = null
    private var hiddenPauseJob: Job? = null
    private var isHiddenFile: Boolean = false

    override fun onCreate() {
        super.onCreate()

        // Create notification channel for media playback notification (API 26+)
        val channel = NotificationChannel(
            CHANNEL_ID, "Audio playback", NotificationManager.IMPORTANCE_LOW
        ).apply { setShowBadge(false) }
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(channel)

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

        // Single permanent Player.Listener — no leak across file changes.
        exoPlayer.addListener(object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                playerState.setPlaying(isPlaying)
                updateNotification(isPlaying)
                if (isPlaying) {
                    hiddenPauseJob?.cancel()
                    hiddenPauseJob = null
                } else if (exoPlayer.playbackState == Player.STATE_ENDED) {
                    val id = currentFileId
                    if (id != 0L) {
                        serviceScope?.launch(Dispatchers.IO) { fileRepository.savePlaybackPosition(id, 0L) }
                    }
                    playerState.updatePosition(0L)
                } else if (!isPlaying && isHiddenFile) {
                    hiddenPauseJob?.cancel()
                    hiddenPauseJob = serviceScope?.launch {
                        delay(HIDDEN_PAUSE_TIMEOUT_MS)
                        Log.d(TAG, "Hidden file pause timeout — stopping")
                        savePosition()
                        stopNow()
                        stopSelf()
                    }
                }
            }
            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_READY && exoPlayer.duration > 0) {
                    playerState.setDuration(exoPlayer.duration)
                }
            }
        })

        // Listen for headphone disconnection
        headphoneMonitor = HeadphoneMonitor { connected ->
            if (prefs.headphonesOnly && !connected && exoPlayer.isPlaying) {
                exoPlayer.pause()
            }
        }
        headphoneMonitor?.register(this)

        serviceScope = CoroutineScope(Dispatchers.Main + SupervisorJob())
        serviceScope?.launch {
            playerState.commands.collect { command ->
                when (command) {
                    is PlayerCommand.Play -> playFile(command.fileId, command.fileName)
                    is PlayerCommand.Pause -> exoPlayer.pause()
                    is PlayerCommand.Resume -> exoPlayer.play()
                    is PlayerCommand.SeekTo -> exoPlayer.seekTo(command.positionMs)
                    is PlayerCommand.Stop -> savePosition().also { stopNow() }
                }
            }
        }
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = mediaSession

    private fun buildNotification(isPlaying: Boolean): NotificationCompat.Builder {
        val playPauseIntent = PendingIntent.getService(this, 0,
            Intent(this, AudioPlayerService::class.java).apply { action = ACTION_PLAY_PAUSE },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val stopIntent = PendingIntent.getService(this, 1,
            Intent(this, AudioPlayerService::class.java).apply { action = ACTION_STOP },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(playerState.info.value.fileName.ifEmpty { "MDRender" })
            .setContentText(
                if (isHiddenFile) "Hidden file" else if (isPlaying) "Playing" else "Paused"
            )
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOngoing(true)
            .addAction(
                if (isPlaying) android.R.drawable.ic_media_pause else android.R.drawable.ic_media_play,
                if (isPlaying) "Pause" else "Play",
                playPauseIntent
            )
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stopIntent)
    }

    private fun updateNotification(isPlaying: Boolean) {
        val notif = buildNotification(isPlaying).build()
        NotificationManagerCompat.from(this).notify(NOTIF_ID, notif)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_PLAY_PAUSE -> {
                if (exoPlayer.isPlaying) exoPlayer.pause() else exoPlayer.play()
                updateNotification(exoPlayer.isPlaying)
            }
            ACTION_STOP -> {
                savePosition()
                stopNow()
                stopSelf()
            }
            else -> {
                startForeground(NOTIF_ID, buildNotification(false).build())
                super.onStartCommand(intent, flags, startId)
                if (intent?.hasExtra(EXTRA_FILE_ID) == true) {
                    val fileId = intent.getLongExtra(EXTRA_FILE_ID, 0L)
                    if (fileId != 0L) {
                        serviceScope?.launch {
                            try {
                                playFile(fileId, fileRepository.getFileMetadata(fileId)?.name ?: "unknown")
                            } catch (e: Exception) { Log.e(TAG, "onStartCommand failed", e) }
                        }
                    }
                }
            }
        }
        return START_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        savePosition()
        stopNow()
        stopSelf()
    }

    override fun onDestroy() {
        savePosition()
        stopNow()
        headphoneMonitor?.unregister(this)
        hiddenPauseJob?.cancel()
        playJob?.cancel()
        serviceScope?.cancel()
        if (::mediaSession.isInitialized) mediaSession.release()
        if (::exoPlayer.isInitialized) exoPlayer.release()
        cleanCache()
        super.onDestroy()
    }

    private fun playFile(fileId: Long, fileName: String) {
        if (prefs.headphonesOnly && !AudioManagerUtil.isHeadphonesConnected(this)) {
            playerState.setPlaying(false)
            return
        }

        hiddenPauseJob?.cancel()
        hiddenPauseJob = null
        playJob?.cancel()
        savePosition()

        // Stop current playback on the main thread (ExoPlayer thread requirement)
        if (currentFileId != 0L) {
            exoPlayer.stop()
        }
        cleanCache()
        currentFileId = fileId

        playJob = serviceScope?.launch(Dispatchers.IO) {
            try {
                val stream = fileRepository.getDecryptedStream(fileId)
                    ?: throw Exception("File not found")
                val ext = fileName.substringAfterLast('.', "mp3")
                val tempFile = File(cacheDir, "audio/${fileId}_$ext")
                tempFile.parentFile?.mkdirs()
                tempFile.outputStream().use { out -> stream.use { it.copyTo(out) } }

                withContext(Dispatchers.Main) {
                    currentTempFile = tempFile
                    val metadata = fileRepository.getFileMetadata(fileId)
                    val savedPos = metadata?.playbackPosition ?: 0L
                    isHiddenFile = metadata?.folderId?.let { folderRepository.isInHiddenTree(it) } ?: false

                    val mediaItem = MediaItem.fromUri(Uri.fromFile(tempFile))
                    exoPlayer.setMediaItem(mediaItem)
                    exoPlayer.prepare()
                    if (savedPos > 0) exoPlayer.seekTo(savedPos)

                    playerState.setFileInfo(fileId, fileName)
                    playerState.setDuration(if (exoPlayer.duration > 0) exoPlayer.duration else 0L)
                    playerState.updatePosition(savedPos)
                    updateNotification(true)

                    exoPlayer.play()
                }

                // Track position every 2 seconds
                positionJob?.cancel()
                positionJob = serviceScope?.launch(Dispatchers.Main) {
                    while (isActive) {
                        delay(2000)
                        val pos = exoPlayer.currentPosition
                        playerState.updatePosition(pos)
                        withContext(Dispatchers.IO) {
                            fileRepository.savePlaybackPosition(fileId, pos)
                        }
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "playFile failed for fileId=$fileId", e)
                withContext(Dispatchers.Main) {
                    playerState.setFileInfo(0, "")
                    playerState.setPlaying(false)
                }
            }
        }
    }

    private fun savePosition() {
        val id = currentFileId
        val pos = playerState.currentPosition.value
        if (id != 0L) {
            serviceScope?.launch(Dispatchers.IO) {
                fileRepository.savePlaybackPosition(id, pos)
            }
        }
    }

    private fun stopNow() {
        exoPlayer.stop()
        playerState.setPlaying(false)
        playerState.stop()
        positionJob?.cancel()
        hiddenPauseJob?.cancel()
        hiddenPauseJob = null
        isHiddenFile = false
        NotificationManagerCompat.from(this).cancel(NOTIF_ID)
    }

    private fun cleanCache() {
        currentTempFile?.delete()
        currentTempFile = null
    }

    companion object {
        private const val TAG = "AudioPlayerService"
        private const val CHANNEL_ID = "audio_playback"
        private const val NOTIF_ID = 1001
        private const val HIDDEN_PAUSE_TIMEOUT_MS = 120_000L
        const val EXTRA_FILE_ID = "file_id"
        private const val ACTION_PLAY_PAUSE = "com.a42r.mdrender.PLAY_PAUSE"
        private const val ACTION_STOP = "com.a42r.mdrender.STOP"
    }
}
