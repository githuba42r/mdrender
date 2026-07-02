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

    // Whether hidden folders are temporarily revealed. Runtime-only; reset on
    // every lock so authentication alone never exposes hidden content.
    private val _revealHidden = MutableStateFlow(false)
    val revealHidden: StateFlow<Boolean> = _revealHidden.asStateFlow()

    // Whether the currently-displayed folder is inside a hidden tree. Drives
    // FLAG_SECURE so the recents/app-switcher snapshot can't capture hidden
    // content. Deliberately NOT reset on background — it must stay true across
    // the moment the snapshot is taken, and is cleared only once the browser
    // navigates back to non-hidden content (e.g. home on the next unlock).
    private val _displayingHidden = MutableStateFlow(false)
    val displayingHidden: StateFlow<Boolean> = _displayingHidden.asStateFlow()

    fun setDisplayingHidden(value: Boolean) { _displayingHidden.value = value }

    // Skips the next background-lock (e.g. while the system file picker is open).
    @Volatile
    private var suspendNextLock = false

    fun unlock() { _isLocked.value = false }

    fun revealHiddenFolders() { _revealHidden.value = true }

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
        _revealHidden.value = false
    }
}
