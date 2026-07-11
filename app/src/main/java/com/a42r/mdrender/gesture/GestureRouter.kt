package com.a42r.mdrender.gesture

import javax.inject.Inject
import javax.inject.Singleton

/** Routes title-tap events through the 12-tap counter and/or tap-sequence
 *  detector, depending on the user's [UnhideGesturePrefs] configuration.
 *
 *  Multi-touch and (future) knock gestures have their own independent
 *  callbacks into AppLock — they are not routed through this class. */
@Singleton
class GestureRouter @Inject constructor(
    private val prefs: UnhideGesturePrefs,
    private val tapSequenceDetector: TapSequenceDetector
) {
    // --- 12-tap state (existing gesture) --------------------------------
    private var titleTapCount = 0
    private var titleWindowStart = 0L

    /** Called each time the user taps the title bar.
     *  @param pressDurationMs null for simple clicks, or measured press duration
     *  @return true if any gesture engine fired the reveal action */
    fun onTitleTap(pressDurationMs: Long? = null): Boolean {
        val cfg = prefs.config

        // Try twelve-tap first (if enabled)
        if (cfg.twelveTapEnabled && checkTwelveTap()) return true

        // Try tap sequence
        if (tapSequenceDetector.onTap(pressDurationMs)) return true

        return false
    }

    private fun checkTwelveTap(): Boolean {
        val now = System.currentTimeMillis()
        if (now - titleWindowStart > 30_000L) {
            titleWindowStart = now
            titleTapCount = 1
        } else {
            titleTapCount++
        }
        if (titleTapCount >= 12) {
            titleTapCount = 0
            return true
        }
        return false
    }

    /** Reset all internal state (e.g., when navigating away). */
    fun resetState() {
        titleTapCount = 0
        tapSequenceDetector.resetState()
    }
}
