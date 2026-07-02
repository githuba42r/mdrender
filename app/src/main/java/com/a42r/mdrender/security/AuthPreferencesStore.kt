package com.a42r.mdrender.security

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AuthPreferencesStore @Inject constructor(
    @ApplicationContext context: Context
) {
    private val masterKey = MasterKey.Builder(context)
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()

    private val prefs = EncryptedSharedPreferences.create(
        context,
        "mdrender_auth_prefs",
        masterKey,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
    )

    var authMethod: AuthMethod
        get() {
            val name = prefs.getString(KEY_AUTH_METHOD, AuthMethod.BIOMETRIC.name) ?: AuthMethod.BIOMETRIC.name
            return AuthMethod.valueOf(name)
        }
        set(value) = prefs.edit().putString(KEY_AUTH_METHOD, value.name).apply()

    var patternHash: String?
        get() = prefs.getString(KEY_PATTERN_HASH, null)
        set(value) = prefs.edit().putString(KEY_PATTERN_HASH, value).apply()

    var pinHash: String?
        get() = prefs.getString(KEY_PIN_HASH, null)
        set(value) = prefs.edit().putString(KEY_PIN_HASH, value).apply()

    var pinSalt: String?
        get() = prefs.getString(KEY_PIN_SALT, null)
        set(value) = prefs.edit().putString(KEY_PIN_SALT, value).apply()

    var idleTimeoutSeconds: Int
        get() = prefs.getInt(KEY_IDLE_TIMEOUT, AppLockManager.DEFAULT_IDLE_TIMEOUT_SECONDS)
        set(value) = prefs.edit().putInt(KEY_IDLE_TIMEOUT, value).apply()

    companion object {
        private const val KEY_AUTH_METHOD = "auth_method"
        private const val KEY_PATTERN_HASH = "pattern_hash"
        private const val KEY_PIN_HASH = "pin_hash"
        private const val KEY_PIN_SALT = "pin_salt"
        private const val KEY_IDLE_TIMEOUT = "idle_timeout"
    }
}
