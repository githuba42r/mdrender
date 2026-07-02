package com.a42r.mdrender.ui.viewer

import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.getValue
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow

/**
 * Routes hardware volume-key presses from MainActivity to whichever viewer
 * screen is currently visible. While a viewer is registered the volume keys
 * adjust font size / zoom instead of media volume; everywhere else they keep
 * their normal behaviour.
 */
object ViewerZoom {

    @Volatile
    private var activeViewers = 0

    private val _events = MutableSharedFlow<Int>(extraBufferCapacity = 16)
    val events: SharedFlow<Int> = _events

    @Synchronized
    fun register() { activeViewers++ }

    @Synchronized
    fun unregister() { activeViewers = (activeViewers - 1).coerceAtLeast(0) }

    /** @param delta +1 for volume-up, -1 for volume-down.
     *  @return true if a viewer is active and consumed the key. */
    fun onVolumeKey(delta: Int): Boolean {
        if (activeViewers == 0) return false
        _events.tryEmit(delta)
        return true
    }
}

/** Register this composable's screen as the volume-key zoom target while it
 *  is in composition. [onDelta] receives +1 / -1 per key press. */
@Composable
fun RegisterViewerZoom(onDelta: (Int) -> Unit) {
    val currentOnDelta by rememberUpdatedState(onDelta)
    DisposableEffect(Unit) {
        ViewerZoom.register()
        onDispose { ViewerZoom.unregister() }
    }
    LaunchedEffect(Unit) {
        ViewerZoom.events.collect { currentOnDelta(it) }
    }
}
