package com.a42r.mdrender.localsend

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/** Settings and identity for the LocalSend receiver. */
@Singleton
class LocalSendPrefs @Inject constructor(
    @ApplicationContext context: Context
) {
    private val prefs = context.getSharedPreferences("mdrender_localsend_prefs", Context.MODE_PRIVATE)

    var enabled: Boolean
        get() = prefs.getBoolean(KEY_ENABLED, false)
        set(value) = prefs.edit().putBoolean(KEY_ENABLED, value).apply()

    /** Device name shown to other LocalSend clients. Generated on first use. */
    var alias: String
        get() = prefs.getString(KEY_ALIAS, null) ?: regenerateAlias()
        set(value) = prefs.edit().putString(KEY_ALIAS, value).apply()

    /** Optional PIN senders must supply; empty = no PIN required. */
    var pin: String
        get() = prefs.getString(KEY_PIN, "") ?: ""
        set(value) = prefs.edit().putString(KEY_PIN, value).apply()

    /** Stable random fingerprint identifying this device on the network. */
    val fingerprint: String
        get() = prefs.getString(KEY_FINGERPRINT, null) ?: UUID.randomUUID().toString().also {
            prefs.edit().putString(KEY_FINGERPRINT, it).apply()
        }

    fun regenerateAlias(): String {
        val alias = "${ADJECTIVES.random()} ${NOUNS.random()}"
        prefs.edit().putString(KEY_ALIAS, alias).apply()
        return alias
    }

    companion object {
        private const val KEY_ENABLED = "enabled"
        private const val KEY_ALIAS = "alias"
        private const val KEY_PIN = "pin"
        private const val KEY_FINGERPRINT = "fingerprint"

        private val ADJECTIVES = listOf(
            "Swift", "Clever", "Quiet", "Brave", "Sunny", "Mellow", "Cosmic",
            "Rapid", "Gentle", "Bold", "Lucky", "Vivid", "Calm", "Nimble"
        )
        private val NOUNS = listOf(
            "Mango", "Falcon", "Otter", "Cactus", "Comet", "Maple", "Orchid",
            "Panda", "Nebula", "Tiger", "Willow", "Dolphin", "Ember", "Juniper"
        )
    }
}
