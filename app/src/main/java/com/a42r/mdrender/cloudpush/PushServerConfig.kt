package com.a42r.mdrender.cloudpush

import android.content.SharedPreferences
import com.a42r.mdrender.localsend.LocalSendPrefs
import java.util.Base64
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Pairing state for the cloud push server, persisted in SharedPreferences.
 *
 * [pushKey] is the doorbell key: the phone mints it, sends it to the server once at
 * registration, and then never transmits it again. Every FCM doorbell is encrypted to it,
 * so [clear] must rotate it — otherwise a doorbell captured before re-pairing would still
 * decrypt afterwards.
 */
@Singleton
class PushServerConfig @Inject constructor(
    private val prefs: SharedPreferences,
    private val localSendPrefs: LocalSendPrefs,
) {
    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, "") ?: ""
        set(value) = prefs.edit().putString(KEY_SERVER_URL, value).apply()

    /** RSA public key pinned from the QR code at pairing. */
    var serverPublicKeyPem: String
        get() = prefs.getString(KEY_SERVER_PUBLIC_KEY, "") ?: ""
        set(value) = prefs.edit().putString(KEY_SERVER_PUBLIC_KEY, value).apply()

    var deviceSecret: String
        get() = prefs.getString(KEY_DEVICE_SECRET, null)
            ?: UUID.randomUUID().toString().also {
                prefs.edit().putString(KEY_DEVICE_SECRET, it).apply()
            }
        set(value) = prefs.edit().putString(KEY_DEVICE_SECRET, value).apply()

    /** OAuth2 bearer token for the API, from the pairing flow. */
    var deviceAuth: String
        get() = prefs.getString(KEY_DEVICE_AUTH, "") ?: ""
        set(value) = prefs.edit().putString(KEY_DEVICE_AUTH, value).apply()

    /** Base64 doorbell key, as sent to the server during registration. */
    var pushKey: ByteArray
        get() = prefs.getString(KEY_PUSH_KEY, null)?.takeIf { it.isNotEmpty() }
            ?.let { Base64.getDecoder().decode(it) }
            ?: ByteArray(32).also { java.security.SecureRandom().nextBytes(it) }
                .also { prefs.edit().putString(KEY_PUSH_KEY, Base64.getEncoder().encodeToString(it)).apply() }
        set(value) = prefs.edit().putString(KEY_PUSH_KEY, Base64.getEncoder().encodeToString(value)).apply()

    val pushKeyB64: String get() = Base64.getEncoder().encodeToString(pushKey)

    val deviceName: String get() = localSendPrefs.alias

    val isPaired: Boolean
        get() = serverUrl.isNotEmpty() && serverPublicKeyPem.isNotEmpty() && deviceAuth.isNotEmpty()

    fun clear() {
        prefs.edit()
            .putString(KEY_SERVER_URL, "")
            .putString(KEY_SERVER_PUBLIC_KEY, "")
            .putString(KEY_DEVICE_AUTH, "")
            // Removed rather than blanked: these two are generated on first read,
            // and only a missing entry triggers that. Storing "" would leave a
            // re-paired device with an empty device secret and push key.
            .remove(KEY_DEVICE_SECRET)
            .remove(KEY_PUSH_KEY)
            .apply()
    }

    companion object {
        const val PREFS_NAME = "mdrender_cloudpush_prefs"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_SERVER_PUBLIC_KEY = "server_public_key_pem"
        private const val KEY_DEVICE_SECRET = "device_secret"
        private const val KEY_DEVICE_AUTH = "device_auth"
        private const val KEY_PUSH_KEY = "push_key"
    }
}
