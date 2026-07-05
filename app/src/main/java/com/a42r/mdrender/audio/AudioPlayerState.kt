package com.a42r.mdrender.audio

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
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
