package com.a42r.mdrender.security

import android.content.Context
import com.a42r.mdrender.MDRenderApplication
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class SecurityPrefs @Inject constructor() {
    companion object {
        private const val PREFS_NAME = "security"
        private const val KEY_REQUIRE_SYSTEM_AUTH = "require_system_auth"
        private const val KEY_ALLOW_WEAK_BIOMETRIC = "allow_weak_biometric"
    }

    private val prefs by lazy {
        MDRenderApplication.instance.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    // Default true preserves current behavior: system auth is mandatory unless
    // the user has explicitly opted out (with biometric confirmation, enforced
    // at the call site in SettingsScreen — not here).
    var requireSystemAuth: Boolean
        get() = prefs.getBoolean(KEY_REQUIRE_SYSTEM_AUTH, true)
        set(value) { prefs.edit().putBoolean(KEY_REQUIRE_SYSTEM_AUTH, value).apply() }

    /** Whether to accept BIOMETRIC_WEAK (e.g. face unlock) in addition to
     *  BIOMETRIC_STRONG. Defaults to false for security-conscious default. */
    var allowWeakBiometric: Boolean
        get() = prefs.getBoolean(KEY_ALLOW_WEAK_BIOMETRIC, false)
        set(value) { prefs.edit().putBoolean(KEY_ALLOW_WEAK_BIOMETRIC, value).apply() }
}
