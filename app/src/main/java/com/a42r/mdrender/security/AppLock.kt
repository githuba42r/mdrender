package com.a42r.mdrender.security

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Tracks app state related to security (hidden folder reveal).
 * Unlike the original design, there is no app-lock gate — the app opens
 * directly without requiring biometric or device authentication on every
 * resume. Hidden-folder reveal state is still cleared on background for
 * safety.
 */
@Singleton
class AppLock @Inject constructor() {

    // Always unlocked — no app-level lock gate.
    private val _isLocked = MutableStateFlow(false)
    val isLocked: StateFlow<Boolean> = _isLocked.asStateFlow()

    // Whether hidden folders are temporarily revealed. Runtime-only; reset on
    // background so hidden content isn't accidentally exposed.
    private val _revealHidden = MutableStateFlow(false)
    val revealHidden: StateFlow<Boolean> = _revealHidden.asStateFlow()

    // Whether the currently-displayed folder is inside a hidden tree. Drives
    // FLAG_SECURE so the recents/app-switcher snapshot can't capture hidden
    // content.
    private val _displayingHidden = MutableStateFlow(false)
    val displayingHidden: StateFlow<Boolean> = _displayingHidden.asStateFlow()

    fun setDisplayingHidden(value: Boolean) { _displayingHidden.value = value }

    fun revealHiddenFolders() { _revealHidden.value = true }

    /** Turn reveal off manually (via the title-bar toggle). */
    fun hideHiddenFolders() { _revealHidden.value = false }

    /** The app has gone to the background — clear the hidden-folders reveal
     *  so hidden content isn't accidentally exposed on return. */
    fun onBackground() {
        _revealHidden.value = false
    }
}
