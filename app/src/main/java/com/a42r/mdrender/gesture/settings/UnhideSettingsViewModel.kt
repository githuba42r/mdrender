package com.a42r.mdrender.gesture.settings

import androidx.lifecycle.ViewModel
import com.a42r.mdrender.gesture.GestureAction
import com.a42r.mdrender.gesture.MultiTouchAction
import com.a42r.mdrender.gesture.MultiTouchDetector
import com.a42r.mdrender.gesture.TapSequenceConfig
import com.a42r.mdrender.gesture.MultiTouchConfig
import com.a42r.mdrender.gesture.TapSequenceDetector
import com.a42r.mdrender.gesture.UnhideGestureConfig
import com.a42r.mdrender.gesture.UnhideGesturePrefs
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import javax.inject.Inject

data class UnhideSettingsUiState(
    val twelveTapEnabled: Boolean = true,
    // Tap sequence
    val tapSequenceEnabled: Boolean = false,
    val tapPattern: List<GestureAction> = TapSequenceConfig().pattern,
    val tapTestStatus: String = "Idle",
    val tapTestStepIndex: Int = -1,
    val tapTestTotalSteps: Int = 0,
    // Multi-touch
    val multiTouchEnabled: Boolean = false,
    val multiTouchSequence: List<MultiTouchAction> = emptyList(),
    val multiTouchTestStatus: String = "Idle",
    val multiTouchTestStepIndex: Int = -1,
    val multiTouchTestTotalSteps: Int = 0,
    val multiTouchDebugOverlay: Boolean = false,
    // General
    val showDisableWarning: Boolean = false
)

@HiltViewModel
class UnhideSettingsViewModel @Inject constructor(
    private val prefs: UnhideGesturePrefs,
    private val tapDetector: TapSequenceDetector,
    private val multiTouchDetector: MultiTouchDetector
) : ViewModel() {

    private val _uiState = MutableStateFlow(UnhideSettingsUiState(
        twelveTapEnabled = prefs.config.twelveTapEnabled,
        tapSequenceEnabled = prefs.config.tapSequence.enabled,
        tapPattern = prefs.config.tapSequence.pattern,
        multiTouchEnabled = prefs.config.multiTouch.enabled,
        multiTouchSequence = prefs.config.multiTouch.sequence,
        tapTestTotalSteps = prefs.config.tapSequence.pattern.size,
        multiTouchTestTotalSteps = prefs.config.multiTouch.sequence.size
    ))
    val uiState: StateFlow<UnhideSettingsUiState> = _uiState.asStateFlow()

    fun setTwelveTapEnabled(enabled: Boolean) {
        if (!enabled) {
            _uiState.update { it.copy(showDisableWarning = true) }
            return
        }
        applyTwelveTap(true)
    }

    fun confirmDisableTwelveTap() {
        applyTwelveTap(false)
        _uiState.update { it.copy(showDisableWarning = false) }
    }

    fun cancelDisableWarning() {
        _uiState.update { it.copy(showDisableWarning = false) }
    }

    private fun applyTwelveTap(enabled: Boolean) {
        val c = prefs.config
        prefs.config = c.copy(twelveTapEnabled = enabled)
        _uiState.update { it.copy(twelveTapEnabled = enabled) }
    }

    // --- Tap sequence ---------------------------------------------------

    fun setTapSequenceEnabled(enabled: Boolean) {
        val c = prefs.config
        prefs.config = c.copy(tapSequence = c.tapSequence.copy(enabled = enabled))
        _uiState.update { it.copy(tapSequenceEnabled = enabled, tapTestTotalSteps = c.tapSequence.pattern.size) }
    }

    fun addTapStep() {
        val newPattern = _uiState.value.tapPattern + GestureAction.Tap()
        updateTapPattern(newPattern)
    }

    fun removeTapStep() {
        val newPattern = _uiState.value.tapPattern.dropLast(1)
        if (newPattern.isNotEmpty()) updateTapPattern(newPattern)
    }

    fun toggleTapStepLongPress(index: Int) {
        val pattern = _uiState.value.tapPattern.toMutableList()
        val current = pattern[index] as? GestureAction.Tap ?: return
        val series = if (current.isLongPress) GestureAction.Tap() else GestureAction.Tap(isLongPress = true)
        pattern[index] = series
        updateTapPattern(pattern)
    }

    private fun updateTapPattern(pattern: List<GestureAction>) {
        val c = prefs.config
        prefs.config = c.copy(tapSequence = c.tapSequence.copy(pattern = pattern))
        _uiState.update { it.copy(tapPattern = pattern, tapTestTotalSteps = pattern.size) }
    }

    // --- Tap sequence test ----------------------------------------------

    fun startTapTest() {
        tapDetector.testReset()
        val total = _uiState.value.tapTestTotalSteps
        _uiState.update {
            it.copy(
                tapTestStatus = if (total == 0) "No steps configured" else "Tap or hold the box below…",
                tapTestStepIndex = 0
            )
        }
    }

    fun stopTapTest() {
        tapDetector.testReset()
        _uiState.update { it.copy(tapTestStatus = "Idle", tapTestStepIndex = -1) }
    }

    /** Feed a tap into the test detector. [pressDurationMs] is the measured
     *  press-and-hold time. Returns true while the test is in progress. */
    fun feedTestTap(pressDurationMs: Long): Boolean {
        val result = tapDetector.testOnTap(pressDurationMs)
        val step = tapDetector.currentTestStep
        val total = _uiState.value.tapTestTotalSteps
        when (result) {
            2 -> {
                _uiState.update {
                    it.copy(tapTestStatus = "✓ Success! Sequence matched.", tapTestStepIndex = -1)
                }
                return false
            }
            1 -> {
                _uiState.update {
                    it.copy(tapTestStatus = "Step $step/$total matched — keep going", tapTestStepIndex = step)
                }
                return true
            }
            -1 -> {
                val expected = prefs.config.tapSequence.pattern.getOrNull(step)
                val hint = when (expected) {
                    is GestureAction.Tap -> if (expected.isLongPress) "expected a HOLD (long press)" else "expected a TAP (quick tap)"
                    else -> "wrong tap type"
                }
                _uiState.update {
                    it.copy(tapTestStatus = "✗ Failed — $hint", tapTestStepIndex = -1)
                }
                tapDetector.testReset()
                return false
            }
        }
        return false
    }

    // --- Multi-touch ---------------------------------------------------

    fun setMultiTouchEnabled(enabled: Boolean) {
        val c = prefs.config
        prefs.config = c.copy(multiTouch = c.multiTouch.copy(enabled = enabled))
        _uiState.update { it.copy(multiTouchEnabled = enabled, multiTouchTestTotalSteps = c.multiTouch.sequence.size) }
    }

    fun addMultiTouchAction(action: MultiTouchAction) {
        val newSeq = _uiState.value.multiTouchSequence + action
        updateMultiTouchSequence(newSeq)
    }

    fun removeMultiTouchAction(index: Int) {
        val newSeq = _uiState.value.multiTouchSequence.toMutableList()
        if (index in newSeq.indices) {
            newSeq.removeAt(index)
            updateMultiTouchSequence(newSeq)
        }
    }

    private fun updateMultiTouchSequence(sequence: List<MultiTouchAction>) {
        val c = prefs.config
        prefs.config = c.copy(multiTouch = c.multiTouch.copy(sequence = sequence))
        _uiState.update { it.copy(multiTouchSequence = sequence, multiTouchTestTotalSteps = sequence.size) }
    }

    fun toggleDebugOverlay() {
        _uiState.update { it.copy(multiTouchDebugOverlay = !it.multiTouchDebugOverlay) }
    }

    // --- Multi-touch test ----------------------------------------------

    fun startMultiTouchTest() {
        multiTouchDetector.testReset()
        val total = prefs.config.multiTouch.sequence.size
        _uiState.update {
            it.copy(
                multiTouchTestStatus = if (total == 0) "No steps configured" else "Perform the gesture in the grid below…",
                multiTouchTestStepIndex = 0
            )
        }
    }

    fun stopMultiTouchTest() {
        multiTouchDetector.testReset()
        _uiState.update { it.copy(multiTouchTestStatus = "Idle", multiTouchTestStepIndex = -1) }
    }

    fun feedTestPointerEvent(
        pointerId: Long, eventType: Int,
        x: Float, y: Float, width: Int, height: Int
    ) {
        val matched = multiTouchDetector.testPointerEvent(pointerId, eventType, x, y, width, height)
        val step = multiTouchDetector.debugInfo(forTest = true).sequenceIndex
        val total = prefs.config.multiTouch.sequence.size
        _uiState.update {
            it.copy(
                multiTouchTestStepIndex = if (matched) -1 else step.coerceAtMost(total),
                multiTouchTestStatus = if (matched) "✓ Success! Sequence matched."
                else "Step ${step + 1}/$total"
            )
        }
    }
}
