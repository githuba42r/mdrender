package com.a42r.mdrender.security

import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AppLockManager @Inject constructor() {
    companion object {
        const val DEFAULT_IDLE_TIMEOUT_SECONDS = 120
    }

    private val _isLocked = MutableStateFlow(true)
    val isLocked: StateFlow<Boolean> = _isLocked.asStateFlow()

    private val scope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    private var idleTimeoutSeconds: Int = DEFAULT_IDLE_TIMEOUT_SECONDS
    private var idleTimerJob: Job? = null
    private var consecutiveFailures: Int = 0
    private var lockoutUntil: Long = 0L

    fun lock() {
        _isLocked.value = true
        cancelIdleTimer()
    }

    fun unlock() {
        _isLocked.value = false
        consecutiveFailures = 0
        lockoutUntil = 0L
        startIdleTimer()
    }

    fun onUserInteraction() {
        if (_isLocked.value) return
        resetIdleTimer()
    }

    fun setIdleTimeoutSeconds(seconds: Int) {
        idleTimeoutSeconds = seconds
        if (!_isLocked.value) resetIdleTimer()
    }

    fun onAppInForeground() {
        if (!_isLocked.value) startIdleTimer()
    }

    fun onAppInBackground() {
        lock()
    }

    fun recordFailedAttempt(): Boolean {
        consecutiveFailures++
        if (consecutiveFailures >= 5) {
            lockoutUntil = System.currentTimeMillis() + 30_000L * (1 shl (consecutiveFailures - 5).coerceAtMost(3))
            return true // locked out
        }
        return false
    }

    fun isLockedOut(): Boolean {
        if (lockoutUntil == 0L) return false
        if (System.currentTimeMillis() > lockoutUntil) {
            lockoutUntil = 0L
            consecutiveFailures = 0
            return false
        }
        return true
    }

    fun lockoutRemainingMillis(): Long {
        return (lockoutUntil - System.currentTimeMillis()).coerceAtLeast(0L)
    }

    private fun startIdleTimer() {
        cancelIdleTimer()
        if (idleTimeoutSeconds <= 0) return
        idleTimerJob = scope.launch {
            delay(idleTimeoutSeconds * 1000L)
            lock()
        }
    }

    private fun resetIdleTimer() {
        if (!_isLocked.value) startIdleTimer()
    }

    private fun cancelIdleTimer() {
        idleTimerJob?.cancel()
        idleTimerJob = null
    }
}
