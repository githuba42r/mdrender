package com.a42r.mdrender.ui.viewer

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.viewModelScope
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import com.a42r.mdrender.data.repository.FileRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import java.io.File
import javax.inject.Inject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class VideoUiState(
    val fileName: String = "",
    val isLoading: Boolean = true,
    val error: String? = null,
    val isPlaying: Boolean = false,
    val position: Long = 0,
    val duration: Long = 0
)

/**
 * Owns the ExoPlayer instance for a single local video file.
 *
 * Resolves the file the same way the audio service does — plain storage
 * plays directly from disk, encrypted files decrypt to a size-keyed cache
 * first. The player is created on the main thread with the ViewModel and
 * released in [onCleared].
 */
@HiltViewModel
class VideoPlayerViewModel @Inject constructor(
    application: Application,
    private val fileRepository: FileRepository,
    savedStateHandle: SavedStateHandle
) : AndroidViewModel(application) {

    private val fileId: Long = savedStateHandle.get<Long>("fileId") ?: 0L

    private val _uiState = MutableStateFlow(VideoUiState())
    val uiState: StateFlow<VideoUiState> = _uiState.asStateFlow()

    val player: ExoPlayer = ExoPlayer.Builder(application).build()

    init {
        player.addListener(object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                _uiState.update { it.copy(isPlaying = isPlaying) }
            }

            override fun onEvents(player: Player, events: Player.Events) {
                _uiState.update {
                    it.copy(
                        position = player.currentPosition.coerceAtLeast(0),
                        duration = player.duration.coerceAtLeast(0)
                    )
                }
            }
        })
        load()
        startPositionPolling()
    }

    private fun load() {
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val meta = fileRepository.getFileMetadata(fileId)
                    ?: throw Exception("File not found")
                _uiState.update { it.copy(fileName = meta.name) }

                val source: File = if (meta.storageType == "plain") {
                    fileRepository.getPlainFile(fileId)
                        ?: throw Exception("File not found on disk")
                } else {
                    val ext = meta.name.substringAfterLast('.', "mp4")
                    val cacheFile = File(
                        getApplication<Application>().cacheDir,
                        "video/${fileId}.$ext"
                    )
                    cacheFile.parentFile?.mkdirs()
                    if (!cacheFile.exists() || cacheFile.length() != meta.fileSize) {
                        val (bytes, _) = fileRepository.getDecryptedContent(fileId)
                            ?: throw Exception("Cannot decrypt file")
                        cacheFile.writeBytes(bytes)
                    }
                    cacheFile
                }

                withContext(Dispatchers.Main) {
                    player.setMediaItem(MediaItem.fromUri(Uri.fromFile(source)))
                    player.prepare()
                    player.playWhenReady = true
                    _uiState.update { it.copy(isLoading = false) }
                }
                fileRepository.saveLastOpenedAt(fileId)
            } catch (e: Exception) {
                _uiState.update { it.copy(isLoading = false, error = e.message) }
            }
        }
    }

    /** Push the playhead into [uiState] while playing so the slider tracks. */
    private fun startPositionPolling() {
        viewModelScope.launch {
            while (true) {
                if (player.isPlaying) {
                    _uiState.update { it.copy(position = player.currentPosition) }
                }
                delay(500)
            }
        }
    }

    fun togglePlayPause() {
        if (player.isPlaying) player.pause() else player.play()
    }

    fun seekTo(positionMs: Long) {
        player.seekTo(positionMs.coerceAtLeast(0))
        _uiState.update { it.copy(position = player.currentPosition.coerceAtLeast(0)) }
    }

    fun skipBy(deltaMs: Long) {
        val duration = player.duration
        val upper = if (duration > 0) duration else Long.MAX_VALUE
        val target = (player.currentPosition + deltaMs).coerceIn(0L, upper)
        seekTo(target)
    }

    override fun onCleared() {
        player.release()
    }
}
