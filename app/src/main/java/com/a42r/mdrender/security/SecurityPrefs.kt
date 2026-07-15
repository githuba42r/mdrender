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
}
