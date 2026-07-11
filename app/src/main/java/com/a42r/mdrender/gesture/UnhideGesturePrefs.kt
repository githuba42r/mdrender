package com.a42r.mdrender.gesture

import android.content.Context
import com.a42r.mdrender.MDRenderApplication
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import javax.inject.Inject
import javax.inject.Singleton

// --- Tap sequence types ---------------------------------------------------

@Serializable
sealed interface GestureAction {
    @Serializable
    data class Tap(val isLongPress: Boolean = false) : GestureAction
}

@Serializable
data class TapSequenceConfig(
    val enabled: Boolean = false,
    val tapWindowMs: Long = 30_000L,
    val pattern: List<GestureAction> = listOf(
        GestureAction.Tap(),
        GestureAction.Tap(),
        GestureAction.Tap(isLongPress = true),
        GestureAction.Tap()
    )
)

// --- Multi-touch types ----------------------------------------------------

@Serializable
sealed interface MultiTouchAction {
    @Serializable data class FingerDown(val zoneIds: List<Int>) : MultiTouchAction
    @Serializable data class RotateCW(val steps: Int = 1) : MultiTouchAction
    @Serializable data class RotateCCW(val steps: Int = 1) : MultiTouchAction
    @Serializable data class LiftFinger(val zoneId: Int) : MultiTouchAction
    @Serializable data class Slide(val fromZoneId: Int, val toZoneId: Int) : MultiTouchAction
}

@Serializable
data class MultiTouchConfig(
    val enabled: Boolean = false,
    val sequence: List<MultiTouchAction> = emptyList(),
    val zoneRows: Int = 4,
    val zoneCols: Int = 3
)

// --- Root config ----------------------------------------------------------

@Serializable
data class UnhideGestureConfig(
    val twelveTapEnabled: Boolean = true,
    val tapSequence: TapSequenceConfig = TapSequenceConfig(),
    val multiTouch: MultiTouchConfig = MultiTouchConfig()
)

enum class GestureMethod { TWELVE_TAP, TAP_SEQUENCE, MULTI_TOUCH }

// --- Persistence ----------------------------------------------------------

@Singleton
class UnhideGesturePrefs @Inject constructor() {

    companion object {
        private const val PREFS_NAME = "unhide_gestures"
        private const val KEY_CONFIG = "gesture_config"
    }

    private val prefs by lazy {
        MDRenderApplication.instance.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    private val json = Json { ignoreUnknownKeys = true }

    var config: UnhideGestureConfig
        get() {
            val raw = prefs.getString(KEY_CONFIG, null) ?: return UnhideGestureConfig()
            return json.decodeFromString(raw)
        }
        set(value) {
            prefs.edit().putString(KEY_CONFIG, json.encodeToString(value)).apply()
        }
}
