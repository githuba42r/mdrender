package com.a42r.mdrender.gesture

import kotlin.math.PI
import kotlin.math.atan2
import kotlin.math.abs
import kotlin.math.sqrt
import javax.inject.Inject
import javax.inject.Singleton

/** Tracks multi-touch gestures on a 7x3 zone grid and matches against a
 *  configured [MultiTouchAction] sequence. Callers (via pointerInput in
 *  FolderBrowserScreen) feed pointer events through [onPointerEvent].
 *
 *  Zone grid: screen is divided into [cols]×[rows] zones in row-major order.
 *  Zone ID = row * cols + col.
 *
 *  Rotation detection tracks the centroid of all active fingers; when the
 *  cumulative angle delta crosses ~30°, a rotation step is registered. */
@Singleton
class MultiTouchDetector @Inject constructor(
    private val prefs: UnhideGesturePrefs
) {
    private var sequenceIndex = 0
    private val fingerZones = mutableMapOf<Long, Int>()     // pointerId -> zoneId
    private val fingerPositions = mutableMapOf<Long, Pair<Float, Float>>()
    private var cumulativeRotation = 0.0   // degrees, negative = CCW
    private var lastAngle = -1.0            // average angle from centroid
    private var totalRotationSteps = 0     // consumed CW rotation steps
    private var totalRotationStepsCCW = 0  // consumed CCW rotation steps
    private var activePointerCount = 0

    private var screenWidth = 0
    private var screenHeight = 0
    private var cols = 3
    private var rows = 4

    /** Reset the entire gesture session. Call when all pointers are lifted or
     *  the gesture should abort. */
    fun reset() {
        fingerZones.clear()
        fingerPositions.clear()
        sequenceIndex = 0
        cumulativeRotation = 0.0
        lastAngle = -1.0
        totalRotationSteps = 0
        totalRotationStepsCCW = 0
        activePointerCount = 0
    }

    /** Feed a pointer event into the detector.
     *  @param pointerId unique pointer identifier
     *  @param eventType 0=down, 1=up, 2=move
     *  @param x,y pointer position in screen coordinates
     *  @param width,height current display dimensions
     *  @return true if the configured sequence was fully matched */
    fun onPointerEvent(
        pointerId: Long, eventType: Int,
        x: Float, y: Float,
        width: Int, height: Int
    ): Boolean {
        if (!prefs.config.multiTouch.enabled) return false
        val cfg = prefs.config.multiTouch
        cols = cfg.zoneCols; rows = cfg.zoneRows
        screenWidth = width; screenHeight = height

        val zoneId = zoneFor(x, y, cols, rows)

        when (eventType) {
            0 -> { // Down
                fingerPositions[pointerId] = Pair(x, y)
                fingerZones[pointerId] = zoneId
                activePointerCount = fingerPositions.size
                lastAngle = -1.0
                cumulativeRotation = 0.0
                checkFingerDown()
            }
            1 -> { // Up
                val fromZone = fingerZones.remove(pointerId)
                fingerPositions.remove(pointerId)
                activePointerCount = fingerPositions.size
                if (fromZone != null) checkLiftFinger(fromZone)
                if (activePointerCount == 0) reset()
            }
            2 -> { // Move
                fingerPositions[pointerId] = Pair(x, y)
                val prevZone = fingerZones[pointerId]
                if (prevZone != zoneId) {
                    // Finger moved to a different zone -> check Slide
                    fingerZones[pointerId] = zoneId
                    checkSlide(prevZone, zoneId)
                }
                val cfgA = currentAction()
                if (cfgA is MultiTouchAction.RotateCW || cfgA is MultiTouchAction.RotateCCW) {
                    checkRotation()
                }
            }
        }

        return sequenceIndex >= cfg.sequence.size
    }

    // --- Zone math -------------------------------------------------------

    fun zoneFor(x: Float, y: Float, cols: Int, rows: Int): Int {
        val col = ((x / screenWidth) * cols).toInt().coerceIn(0, cols - 1)
        val row = ((y / screenHeight) * rows).toInt().coerceIn(0, rows - 1)
        return row * cols + col + 1
    }

    // --- Action matching -------------------------------------------------

    private fun currentAction(): MultiTouchAction? =
        prefs.config.multiTouch.sequence.getOrNull(sequenceIndex)

    private fun checkFingerDown() {
        val action = currentAction() as? MultiTouchAction.FingerDown ?: return
        val currentZones = fingerZones.values.toSet()
        // All required zoneIds must be currently occupied
        if (action.zoneIds.all { it in currentZones }) {
            advanceSequence()
        }
    }

    private fun checkLiftFinger(zoneId: Int) {
        val action = currentAction() as? MultiTouchAction.LiftFinger ?: return
        if (action.zoneId == zoneId) {
            advanceSequence()
        }
    }

    private fun checkSlide(fromZone: Int?, toZone: Int) {
        val action = currentAction() as? MultiTouchAction.Slide ?: return
        if (action.fromZoneId == fromZone && action.toZoneId == toZone) {
            advanceSequence()
        }
    }

    private fun checkRotation() {
        if (activePointerCount < 2) return

        // Compute centroid
        var cx = 0f; var cy = 0f
        for ((_, pos) in fingerPositions) {
            cx += pos.first; cy += pos.second
        }
        cx /= activePointerCount; cy /= activePointerCount

        // Compute average angle of all pointers from centroid
        var avgAngle = 0.0
        for ((_, pos) in fingerPositions) {
            val dx = pos.first - cx
            val dy = pos.second - cy
            if (abs(dx) < 1f && abs(dy) < 1f) return
            avgAngle += atan2(dy.toDouble(), dx.toDouble()) * 180.0 / PI
        }
        avgAngle /= activePointerCount

        if (lastAngle < 0) {
            lastAngle = avgAngle
            return
        }

        val delta = avgAngle - lastAngle
        val wrapped = when {
            delta > 180.0 -> delta - 360.0
            delta < -180.0 -> delta + 360.0
            else -> delta
        }
        cumulativeRotation += wrapped
        lastAngle = avgAngle

        val action = currentAction()
        val threshold = 30.0 // degrees per rotation step

        when {
            action is MultiTouchAction.RotateCW && cumulativeRotation >= threshold -> {
                totalRotationSteps++
                cumulativeRotation -= threshold
                if (totalRotationSteps >= (action).steps) {
                    totalRotationSteps = 0
                    advanceSequence()
                }
            }
            action is MultiTouchAction.RotateCCW && cumulativeRotation <= -threshold -> {
                totalRotationStepsCCW++
                cumulativeRotation += threshold
                if (totalRotationStepsCCW >= (action).steps) {
                    totalRotationStepsCCW = 0
                    advanceSequence()
                }
            }
        }
    }

    private fun advanceSequence() {
        sequenceIndex++
    }

    /** Provide live finger + zone info for the debug overlay.
     *  When [forTest] is true, exposes the test-mode state. */
    fun debugInfo(forTest: Boolean = false): MultiTouchDebugInfo = MultiTouchDebugInfo(
        fingerZones = if (forTest) testFingerZones.toMap() else fingerZones.toMap(),
        fingerPositions = if (forTest) testFingerPositions.toMap() else fingerPositions.toMap(),
        sequenceIndex = if (forTest) testSequenceIndex else sequenceIndex,
        totalSteps = prefs.config.multiTouch.sequence.size,
        activePointerCount = if (forTest) testActivePointerCount else activePointerCount,
        cumulativeRotation = if (forTest) testCumulativeRotation else cumulativeRotation
    )

    // --- Test mode (separate state, does not interfere with live detection) --

    private var testSequenceIndex = 0
    private val testFingerZones = mutableMapOf<Long, Int>()
    private val testFingerPositions = mutableMapOf<Long, Pair<Float, Float>>()
    private var testCumulativeRotation = 0.0
    private var testLastAngle = -1.0
    private var testTotalRotationSteps = 0
    private var testTotalRotationStepsCCW = 0
    private var testActivePointerCount = 0
    private var testFailed = false

    /** Reset the test session. Call before starting a new test. */
    fun testReset() {
        testFingerZones.clear()
        testFingerPositions.clear()
        testSequenceIndex = 0
        testCumulativeRotation = 0.0
        testLastAngle = -1.0
        testTotalRotationSteps = 0
        testTotalRotationStepsCCW = 0
        testActivePointerCount = 0
        testFailed = false
    }

    /** Feed a pointer event into the test-mode state machine.
     *  @return same as [onPointerEvent]: true if the full sequence matched. */
    fun testPointerEvent(
        pointerId: Long, eventType: Int,
        x: Float, y: Float,
        width: Int, height: Int
    ): Boolean {
        if (testFailed) return false
        val cfg = prefs.config.multiTouch
        if (!cfg.enabled || cfg.sequence.isEmpty()) return false

        val cols = cfg.zoneCols; val rows = cfg.zoneRows
        val zoneId = zoneForTest(x, y, width, height, cols, rows)

        when (eventType) {
            0 -> {
                testFingerPositions[pointerId] = Pair(x, y)
                testFingerZones[pointerId] = zoneId
                testActivePointerCount = testFingerPositions.size
                testLastAngle = -1.0
                testCumulativeRotation = 0.0
                checkTestFingerDown()
            }
            1 -> {
                val fromZone = testFingerZones.remove(pointerId)
                testFingerPositions.remove(pointerId)
                testActivePointerCount = testFingerPositions.size
                if (fromZone != null) checkTestLiftFinger(fromZone)
                if (testActivePointerCount == 0) testReset()
            }
            2 -> {
                testFingerPositions[pointerId] = Pair(x, y)
                val prevZone = testFingerZones[pointerId]
                if (prevZone != zoneId) {
                    testFingerZones[pointerId] = zoneId
                    checkTestSlide(prevZone, zoneId)
                }
                val testAction = testCurrentAction()
                if (testAction is MultiTouchAction.RotateCW || testAction is MultiTouchAction.RotateCCW) {
                    checkTestRotation()
                }
            }
        }
        return testSequenceIndex >= cfg.sequence.size
    }

    private fun testCurrentAction(): MultiTouchAction? =
        prefs.config.multiTouch.sequence.getOrNull(testSequenceIndex)

    private fun checkTestFingerDown() {
        val action = testCurrentAction() as? MultiTouchAction.FingerDown ?: return
        if (action.zoneIds.all { it in testFingerZones.values.toSet() }) testAdvance()
    }

    private fun checkTestLiftFinger(zoneId: Int) {
        val action = testCurrentAction() as? MultiTouchAction.LiftFinger ?: return
        if (action.zoneId == zoneId) testAdvance()
    }

    private fun checkTestSlide(fromZone: Int?, toZone: Int) {
        val action = testCurrentAction() as? MultiTouchAction.Slide ?: return
        if (action.fromZoneId == fromZone && action.toZoneId == toZone) testAdvance()
    }

    private fun checkTestRotation() {
        if (testActivePointerCount < 2) return
        var cx = 0f; var cy = 0f
        for ((_, pos) in testFingerPositions) { cx += pos.first; cy += pos.second }
        cx /= testActivePointerCount; cy /= testActivePointerCount

        var avgAngle = 0.0
        for ((_, pos) in testFingerPositions) {
            val dx = pos.first - cx; val dy = pos.second - cy
            if (kotlin.math.abs(dx) < 1f && kotlin.math.abs(dy) < 1f) return
            avgAngle += kotlin.math.atan2(dy.toDouble(), dx.toDouble()) * 180.0 / kotlin.math.PI
        }
        avgAngle /= testActivePointerCount

        if (testLastAngle < 0) { testLastAngle = avgAngle; return }
        val delta = avgAngle - testLastAngle
        testCumulativeRotation += when {
            delta > 180.0 -> delta - 360.0; delta < -180.0 -> delta + 360.0; else -> delta
        }
        testLastAngle = avgAngle

        val action = testCurrentAction()
        val threshold = 30.0
        when {
            action is MultiTouchAction.RotateCW && testCumulativeRotation >= threshold -> {
                testTotalRotationSteps++
                testCumulativeRotation -= threshold
                if (testTotalRotationSteps >= action.steps) { testTotalRotationSteps = 0; testAdvance() }
            }
            action is MultiTouchAction.RotateCCW && testCumulativeRotation <= -threshold -> {
                testTotalRotationStepsCCW++
                testCumulativeRotation += threshold
                if (testTotalRotationStepsCCW >= action.steps) { testTotalRotationStepsCCW = 0; testAdvance() }
            }
        }
    }

    private fun testAdvance() { testSequenceIndex++ }

    private fun zoneForTest(x: Float, y: Float, width: Int, height: Int, cols: Int, rows: Int): Int {
        val col = ((x / width) * cols).toInt().coerceIn(0, cols - 1)
        val row = ((y / height) * rows).toInt().coerceIn(0, rows - 1)
        return row * cols + col + 1
    }
}

data class MultiTouchDebugInfo(
    val fingerZones: Map<Long, Int>,
    val fingerPositions: Map<Long, Pair<Float, Float>>,
    val sequenceIndex: Int,
    val totalSteps: Int,
    val activePointerCount: Int,
    val cumulativeRotation: Double
)
