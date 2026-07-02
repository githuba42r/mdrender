package com.a42r.mdrender.security

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Tracks whether the app is currently locked. The app starts locked and must
 * be unlocked via the device's own authentication (biometric / device PIN /
 * pattern) — there is no app-specific credential. Re-locks whenever the app
 * goes to the background.
 */
@Singleton
class AppLock @Inject constructor() {

    private val _isLocked = MutableStateFlow(true)
    val isLocked: StateFlow<Boolean> = _isLocked.asStateFlow()

    // Skips the next background-lock (e.g. while the system file picker is open).
    @Volatile
    private var suspendNextLock = false

    fun unlock() { _isLocked.value = false }

    /** Call before launching a system activity (file picker) so returning to
     *  the app doesn't demand re-authentication. */
    fun suspendNextLock() { suspendNextLock = true }

    /** The app has gone to the background — re-lock unless suspended. */
    fun onBackground() {
        if (suspendNextLock) {
            suspendNextLock = false
            return
        }
        _isLocked.value = true
    }
}
