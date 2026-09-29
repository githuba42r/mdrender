package com.a42r.mdrender.cloudpush

import com.a42r.mdrender.localsend.LocalSendPrefs
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.doReturn
import org.mockito.kotlin.mock
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.Signature
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

class CloudPushMessageHandlerTest {

    private val serverKeys: KeyPair =
        KeyPairGenerator.getInstance("RSA").apply { initialize(3072) }.generateKeyPair()
    private val attackerKeys: KeyPair =
        KeyPairGenerator.getInstance("RSA").apply { initialize(3072) }.generateKeyPair()

    private lateinit var config: PushServerConfig
    private lateinit var manager: CloudPushManager
    private lateinit var server: TestHttpServer
    private val crypto = PushCrypto(mock())
    private val client = PushClient()

    private val foreignKey = ByteArray(32).also { java.security.SecureRandom().nextBytes(it) }

    @Before
    fun setUp() {
        server = TestHttpServer().apply { start() }
        config = PushServerConfig(
            FakeSharedPreferences(),
            mock<LocalSendPrefs> { on { alias } doReturn "Pixel 9" },
        )
        config.serverUrl = server.baseUrl
        config.serverPublicKeyPem = pemEncode(serverKeys.public.encoded)
        config.deviceSecret = "sec-1"
        config.deviceAuth = "auth-1"
        manager = CloudPushManager()
    }

    private fun handler() = CloudPushMessageHandler(crypto, client, config, manager)

    private fun manifestJson(fileCount: Int = 1): String {
        val files = (1..fileCount).joinToString(",") { i ->
            """{"file_id":"f$i","name":"f$i.md","path":"","retrieval_key":"rk$i","size":10}"""
        }
        return """{"date":"2026-08-29T00:00:00+00:00","files":[$files],"push_id":"push-1"}"""
    }

    private fun sign(kp: KeyPair, body: String): String {
        val sig = Signature.getInstance("SHA256withRSA").run {
            initSign(kp.private)
            update(body.toByteArray())
            sign()
        }
        return Base64.getEncoder().encodeToString(sig)
    }

    private fun serveManifest(body: String, signature: String?) {
        server.on("/api/push/push-1/manifest") {
            TestHttpServer.Resp(
                200, body.toByteArray(),
                headers = signature?.let {
                    mapOf(PushClient.MANIFEST_SIGNATURE_HEADER to it)
                } ?: emptyMap(),
            )
        }
    }

    /** Seal a doorbell the way the server does, under the key the config holds. */
    private fun seal(pushId: String = "push-1", key: ByteArray = config.pushKey): Pair<String, String> {
        val iv = Mac.getInstance("HmacSHA256").run {
            init(SecretKeySpec(key, "HmacSHA256"))
            doFinal(pushId.toByteArray()).copyOfRange(0, 12)
        }
        val plaintext = """{"challenge_key":"ck-1","push_id":"$pushId",""" +
            """"server_url":"${config.serverUrl}","v":1}"""
        val sealed = Cipher.getInstance("AES/GCM/NoPadding").run {
            init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, iv))
            doFinal(plaintext.toByteArray())
        }
        return Base64.getEncoder().encodeToString(sealed) to
            Base64.getEncoder().encodeToString(iv)
    }

    @Test
    fun `a correctly signed manifest is verified and queued`() = runBlocking {
        val body = manifestJson(2)
        serveManifest(body, sign(serverKeys, body))
        val (ct, iv) = seal()

        val outcome = handler().handle(ct, iv)

        assertEquals(CloudPushMessageHandler.Outcome.Enqueued, outcome)
        assertEquals(listOf("f1", "f2"), manager.state.value.map { it.fileId })
    }

    @Test
    fun `nothing is queued when the manifest signature is from another key`() = runBlocking {
        val body = manifestJson(2)
        serveManifest(body, sign(attackerKeys, body))
        val (ct, iv) = seal()

        val outcome = handler().handle(ct, iv)

        assertEquals(
            CloudPushMessageHandler.Outcome.Dropped(CloudPushMessageHandler.Drop.BAD_MANIFEST_SIGNATURE),
            outcome,
        )
        assertTrue("a forged manifest must queue nothing", manager.state.value.isEmpty())
    }

    @Test
    fun `nothing is queued when the manifest body was tampered with`() = runBlocking {
        val body = manifestJson(1)
        serveManifest(body.replace("f1.md", "evil.sh"), sign(serverKeys, body))
        val (ct, iv) = seal()

        val outcome = handler().handle(ct, iv)

        assertEquals(
            CloudPushMessageHandler.Outcome.Dropped(CloudPushMessageHandler.Drop.BAD_MANIFEST_SIGNATURE),
            outcome,
        )
        assertTrue(manager.state.value.isEmpty())
    }

    @Test
    fun `nothing is queued when the manifest arrives with no signature header`() = runBlocking {
        serveManifest(manifestJson(1), signature = null)
        val (ct, iv) = seal()

        val outcome = handler().handle(ct, iv)

        assertEquals(
            CloudPushMessageHandler.Outcome.Dropped(CloudPushMessageHandler.Drop.MANIFEST_UNAVAILABLE),
            outcome,
        )
        assertTrue(manager.state.value.isEmpty())
    }

    @Test
    fun `a doorbell sealed for another device is dropped before any network call`() = runBlocking {
        val (ct, iv) = seal(key = foreignKey)

        val outcome = handler().handle(ct, iv)

        assertEquals(
            CloudPushMessageHandler.Outcome.Dropped(CloudPushMessageHandler.Drop.NOT_A_DOORBELL),
            outcome,
        )
        assertTrue("must not even reach the manifest endpoint", server.requests.isEmpty())
    }

    @Test
    fun `an unpaired install drops the message without a request`() = runBlocking {
        config.clear()
        val (ct, iv) = seal()

        val outcome = handler().handle(ct, iv)

        assertEquals(
            CloudPushMessageHandler.Outcome.Dropped(CloudPushMessageHandler.Drop.NOT_PAIRED),
            outcome,
        )
        assertTrue(server.requests.isEmpty())
    }

    @Test
    fun `a rejected challenge key drops the message`() = runBlocking {
        server.on("/api/push/push-1/manifest") {
            TestHttpServer.Resp(403, """{"error":"forbidden"}""")
        }
        val (ct, iv) = seal()

        val outcome = handler().handle(ct, iv)

        assertEquals(
            CloudPushMessageHandler.Outcome.Dropped(CloudPushMessageHandler.Drop.MANIFEST_UNAVAILABLE),
            outcome,
        )
        assertTrue(manager.state.value.isEmpty())
    }

    private fun pemEncode(der: ByteArray): String {
        val b64 = Base64.getEncoder().encodeToString(der)
        return buildString {
            append("-----BEGIN PUBLIC KEY-----\n")
            b64.chunked(64).forEach { append(it).append("\n") }
            append("-----END PUBLIC KEY-----\n")
        }
    }

    /** Minimal in-memory SharedPreferences, matching the one in PushServerConfigTest. */
    private class FakeSharedPreferences : android.content.SharedPreferences {
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
        override fun edit(): android.content.SharedPreferences.Editor = FakeEditor()
        override fun registerOnSharedPreferenceChangeListener(
            listener: android.content.SharedPreferences.OnSharedPreferenceChangeListener?
        ) = Unit

        override fun unregisterOnSharedPreferenceChangeListener(
            listener: android.content.SharedPreferences.OnSharedPreferenceChangeListener?
        ) = Unit

        private inner class FakeEditor : android.content.SharedPreferences.Editor {
            private val pending = mutableMapOf<String, Any?>()

            override fun putString(key: String, value: String?) = apply { pending[key] = value }
            override fun putStringSet(key: String, value: MutableSet<String>?) = apply { pending[key] = value }
            override fun putInt(key: String, value: Int) = apply { pending[key] = value }
            override fun putLong(key: String, value: Long) = apply { pending[key] = value }
            override fun putFloat(key: String, value: Float) = apply { pending[key] = value }
            override fun putBoolean(key: String, value: Boolean) = apply { pending[key] = value }
            override fun remove(key: String) = apply { pending.remove(key) }
            override fun clear() = apply { store.clear() }
            override fun commit(): Boolean {
                store.putAll(pending)
                return true
            }

            override fun apply() {
                commit()
            }
        }
    }
}
