package com.a42r.mdrender.cloudpush

import android.content.SharedPreferences
import com.a42r.mdrender.localsend.LocalSendPrefs
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.doReturn
import org.mockito.kotlin.mock

class PushServerConfigTest {
    private lateinit var prefs: FakeSharedPreferences
    private lateinit var config: PushServerConfig

    @Before
    fun setUp() {
        prefs = FakeSharedPreferences()
        config = PushServerConfig(prefs, mock<LocalSendPrefs> { on { alias } doReturn "Pixel 9" })
    }

    @Test
    fun `starts unpaired`() {
        assertFalse(config.isPaired)
    }

    @Test
    fun `isPaired once url key and auth are all set`() {
        config.serverUrl = "https://push.example.com"
        assertFalse(config.isPaired)
        config.serverPublicKeyPem = "-----BEGIN PUBLIC KEY-----"
        assertFalse(config.isPaired)
        config.deviceAuth = "Bearer abc123"
        assertTrue(config.isPaired)
    }

    @Test
    fun `deviceSecret is generated once and stable across reads`() {
        val first = config.deviceSecret
        assertTrue(first.isNotEmpty())
        assertEquals(first, config.deviceSecret)
    }

    @Test
    fun `deviceName comes from the LocalSend alias`() {
        assertEquals("Pixel 9", config.deviceName)
    }

    @Test
    fun `pushKey is 32 bytes and stable across reads`() {
        val first = config.pushKey
        assertEquals(32, first.size)
        assertArrayEquals(first, config.pushKey)
    }

    @Test
    fun `pushKeyB64 round-trips the raw key`() {
        val raw = config.pushKey
        assertArrayEquals(raw, java.util.Base64.getDecoder().decode(config.pushKeyB64))
    }

    @Test
    fun `clear resets pairing and rotates the pushKey`() {
        config.serverUrl = "https://push.example.com"
        config.serverPublicKeyPem = "pem"
        config.deviceAuth = "Bearer abc123"
        val keyBefore = config.pushKey

        config.clear()

        assertFalse(config.isPaired)
        assertEquals("", config.serverUrl)
        assertEquals("", config.serverPublicKeyPem)
        assertEquals("", config.deviceAuth)
        // Re-pairing must invalidate any doorbell captured before the clear.
        assertNotEquals(java.util.Base64.getEncoder().encodeToString(keyBefore), config.pushKeyB64)
        assertEquals(32, config.pushKey.size)
    }

    @Test
    fun `values written are visible to a second instance over the same prefs`() {
        config.serverUrl = "https://push.example.com"
        val second = PushServerConfig(prefs, mock<LocalSendPrefs> { on { alias } doReturn "Pixel 9" })
        assertEquals("https://push.example.com", second.serverUrl)
        assertArrayEquals(config.pushKey, second.pushKey)
    }

    /** Minimal in-memory SharedPreferences so the config logic is testable on the JVM. */
    private class FakeSharedPreferences : SharedPreferences {
        private val store = mutableMapOf<String, Any?>()

        override fun getAll(): MutableMap<String, *> = store
        override fun getString(key: String?, defValue: String?): String? = store[key] as? String ?: defValue

        @Suppress("UNCHECKED_CAST")
        override fun getStringSet(key: String?, defValues: MutableSet<String>?): MutableSet<String>? =
            store[key] as? MutableSet<String> ?: defValues

        override fun getInt(key: String?, defValue: Int): Int = store[key] as? Int ?: defValue
        override fun getLong(key: String?, defValue: Long): Long = store[key] as? Long ?: defValue
        override fun getFloat(key: String?, defValue: Float): Float = store[key] as? Float ?: defValue
        override fun getBoolean(key: String?, defValue: Boolean): Boolean = store[key] as? Boolean ?: defValue
        override fun contains(key: String?): Boolean = store.containsKey(key)
        override fun edit(): SharedPreferences.Editor = FakeEditor()
        override fun registerOnSharedPreferenceChangeListener(
            listener: SharedPreferences.OnSharedPreferenceChangeListener?
        ) = Unit

        override fun unregisterOnSharedPreferenceChangeListener(
            listener: SharedPreferences.OnSharedPreferenceChangeListener?
        ) = Unit

        private inner class FakeEditor : SharedPreferences.Editor {
            private val pending = mutableMapOf<String, Any?>()
            private val removed = mutableSetOf<String>()
            private var clearAll = false

            override fun putString(key: String, value: String?) = apply { pending[key] = value }
            override fun putStringSet(key: String, value: MutableSet<String>?) = apply { pending[key] = value }
            override fun putInt(key: String, value: Int) = apply { pending[key] = value }
            override fun putLong(key: String, value: Long) = apply { pending[key] = value }
            override fun putFloat(key: String, value: Float) = apply { pending[key] = value }
            override fun putBoolean(key: String, value: Boolean) = apply { pending[key] = value }
            override fun remove(key: String) = apply { removed += key }
            override fun clear() = apply { clearAll = true }

            override fun commit(): Boolean {
                if (clearAll) store.clear()
                removed.forEach { store.remove(it) }
                store.putAll(pending)
                return true
            }

            override fun apply() {
                commit()
            }
        }
    }
}
