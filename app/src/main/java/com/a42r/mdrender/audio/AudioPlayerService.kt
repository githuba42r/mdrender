package com.a42r.mdrender.audio

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
