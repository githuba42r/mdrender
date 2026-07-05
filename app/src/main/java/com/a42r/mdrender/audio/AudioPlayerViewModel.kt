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
