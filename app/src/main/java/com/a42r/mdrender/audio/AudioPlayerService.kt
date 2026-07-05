package com.a42r.mdrender.audio

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Intent
import android.net.Uri
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaSessionService
import androidx.media3.session.MediaSession
import com.a42r.mdrender.data.repository.FileRepository
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
    private var currentFileId: Long = 0L
    private var playJob: Job? = null

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
                if (!isPlaying && exoPlayer.playbackState == Player.STATE_ENDED) {
                    // Save position and reset for the file that just ended.
                    val id = currentFileId
                    if (id != 0L) {
                        serviceScope?.launch(Dispatchers.IO) {
                            fileRepository.savePlaybackPosition(id, 0L)
                        }
                    }
                    playerState.updatePosition(0L)
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
                    is PlayerCommand.Stop -> stopPlayback()
                }
            }
        }
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = mediaSession

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.hasExtra(EXTRA_FILE_ID) == true) {
            val fileId = intent.getLongExtra(EXTRA_FILE_ID, 0L)
            if (fileId != 0L) {
                serviceScope?.launch {
                    val meta = fileRepository.getFileMetadata(fileId)
                    val name = meta?.name ?: ""
                    playFile(fileId, name)
                }
            }
        }
        return START_NOT_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        savePosition()
        stopPlayback()
        stopSelf()
    }

    override fun onDestroy() {
        savePosition()
        stopPlayback()
        headphoneMonitor?.unregister(this)
        playJob?.cancel()
        serviceScope?.cancel()
        mediaSession.release()
        exoPlayer.release()
        cleanCache()
        super.onDestroy()
    }

    private fun playFile(fileId: Long, fileName: String) {
        if (prefs.headphonesOnly && !AudioManagerUtil.isHeadphonesConnected(this)) {
            playerState.setPlaying(false)
            return
        }

        // Cancel any in-flight play request
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
                val (bytes, _) = fileRepository.getDecryptedContent(fileId)
                    ?: throw Exception("File not found")
                val ext = fileName.substringAfterLast('.', "mp3")
                val tempFile = File(cacheDir, "audio/${fileId}_$ext")
                tempFile.parentFile?.mkdirs()
                tempFile.writeBytes(bytes)

                withContext(Dispatchers.Main) {
                    currentTempFile = tempFile
                    val metadata = fileRepository.getFileMetadata(fileId)
                    val savedPos = metadata?.playbackPosition ?: 0L

                    val mediaItem = MediaItem.fromUri(Uri.fromFile(tempFile))
                    exoPlayer.setMediaItem(mediaItem)
                    exoPlayer.prepare()
                    if (savedPos > 0) exoPlayer.seekTo(savedPos)

                    playerState.setFileInfo(fileId, fileName)
                    playerState.setDuration(if (exoPlayer.duration > 0) exoPlayer.duration else 0L)
                    playerState.updatePosition(savedPos)

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
        private const val CHANNEL_ID = "audio_playback"
        const val EXTRA_FILE_ID = "file_id"
    }
}
