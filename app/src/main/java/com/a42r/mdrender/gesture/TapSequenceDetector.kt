package com.a42r.mdrender.gesture

import javax.inject.Inject
import javax.inject.Singleton

/** State machine that tracks a user-defined tap/hold sequence on the title bar.
 *  Each call to [onTap] checks the next expected step; returns true when the
 *  full sequence is matched and the reveal action should fire. */
@Singleton
class TapSequenceDetector @Inject constructor(
    private val prefs: UnhideGesturePrefs
) {
    private var stepIndex = 0
    private var windowStart = 0L
    private var holdStart = 0L
    private var inHold = false

    /** Notify the detector of a tap on the title area.
     *  @param pressDurationMs null for the old 12-tap path, or measured press duration
     *  @return true when the full sequence was matched */
    fun onTap(pressDurationMs: Long? = null): Boolean {
        val config = prefs.config.tapSequence
        if (!config.enabled) return false

        val now = System.currentTimeMillis()

        // Window expiry resets the sequence
        if (now - windowStart > config.tapWindowMs) {
            reset()
            windowStart = now
        }

        val expected = config.pattern.getOrNull(stepIndex) ?: return resetAndFail()

        val isMatch = when (expected) {
            is GestureAction.Tap -> {
                if (expected.isLongPress) {
                    // Long press: pressDuration must be >500ms, or if null we
                    // accept it as a long press match.
                    pressDurationMs == null || pressDurationMs > 500L
                } else {
                    // Short tap: pressDuration must be <=500ms
                    pressDurationMs == null || pressDurationMs <= 500L
                }
            }
        }

        return if (isMatch) {
            stepIndex++
            if (stepIndex >= config.pattern.size) {
                reset()
                true // FIRED!
            } else false
        } else {
            resetAndFail()
        }
    }

    /** Check whether a tap (null pressDuration = 12-tap path) would have been
     *  consumed as a tap-sequence step. Used by GestureRouter to decide routing. */
    fun isActiveFor(pressDurationMs: Long?): Boolean {
        val config = prefs.config.tapSequence
        if (!config.enabled) return false
        if (config.pattern.isEmpty()) return false
        val now = System.currentTimeMillis()
        return now - windowStart <= config.tapWindowMs || stepIndex > 0
    }

    /** Check if the 12-tap is still enabled in global config. */
    val twelveTapEnabled: Boolean get() = prefs.config.twelveTapEnabled

    private fun reset() {
        stepIndex = 0
        inHold = false
    }

    private fun resetAndFail(): Boolean {
        reset()
        return false
    }

    /** Reset internal state (called when navigating away or disabling). */
    fun resetState() { reset() }

    // --- Test mode (separate state, does not interfere with live detection) --

    private var testStepIndex = -1

    /** Current step index in test mode, or -1 when not testing. */
    val currentTestStep: Int get() = testStepIndex

    /** Total steps in the configured pattern. */
    val testTotalSteps: Int get() = prefs.config.tapSequence.pattern.size

    /** Reset the test session. Call before starting a new test. */
    fun testReset() { testStepIndex = -1 }

    /** Feed a tap event into the test-mode state machine.
     *  @return 1 if the step matched but the sequence is not yet complete,
     *          2 if the full sequence matched (success),
     *          -1 if the tap did not match the expected action (failure). */
    fun testOnTap(pressDurationMs: Long?): Int {
        val config = prefs.config.tapSequence
        if (!config.enabled || config.pattern.isEmpty()) return -1
        if (testStepIndex < 0) testStepIndex = 0
        val expected = config.pattern.getOrNull(testStepIndex) ?: return -1
        val isMatch = when (expected) {
            is GestureAction.Tap -> {
                if (expected.isLongPress) pressDurationMs == null || pressDurationMs > 500L
                else pressDurationMs == null || pressDurationMs <= 500L
            }
        }
        return if (isMatch) {
            testStepIndex++
            if (testStepIndex >= config.pattern.size) {
                testStepIndex = -1
                2  // success
            } else 1  // step matched, continue
        } else {
            testStepIndex = -1
            -1  // wrong tap type
        }
    }
}
