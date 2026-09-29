package com.a42r.mdrender.cloudpush

import com.a42r.mdrender.localsend.LocalSendPrefs
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.any
import org.mockito.kotlin.doReturn
import org.mockito.kotlin.mock
import java.io.File

class PushClientTest {
    private lateinit var server: TestHttpServer
    private lateinit var config: PushServerConfig
    private val client = PushClient()

    @Before
    fun setUp() {
        server = TestHttpServer().apply { start() }
        config = PushServerConfig(
            FakeSharedPreferences(),
            mock<LocalSendPrefs> { on { alias } doReturn "Pixel 9" },
        )
        config.serverUrl = server.baseUrl
    }

    @After
    fun tearDown() {
        server.stop()
    }

    private fun doorbell(pushId: String = "push-1", challengeKey: String = "ck-abc") =
        PushCrypto.Doorbell(config.serverUrl, pushId, challengeKey)

    @Test
    fun `checkRegistration posts json credentials and no query string`() = runBlocking {
        server.on("/api/device/status") { TestHttpServer.Resp(200, """{"ok":true}""") }
        config.deviceSecret = "sec-1"
        config.deviceAuth = "auth-1"

        val registered = client.checkRegistration(config)

        assertTrue(registered)
        val req = server.requests.single()
        assertEquals("POST", req.method)
        assertEquals("/api/device/status", req.path)
        assertNull("credentials must never ride in the query string", req.query)
        assertTrue(req.header("Content-Type")!!.startsWith("application/json"))
        assertTrue(req.body.contains("\"device_secret\":\"sec-1\""))
        assertTrue(req.body.contains("\"device_auth\":\"auth-1\""))
    }

    @Test
    fun `checkRegistration is false when the server says re-register`() = runBlocking {
        server.on("/api/device/status") { TestHttpServer.Resp(404, """{"error":"re-register"}""") }

        assertFalse(client.checkRegistration(config))
    }

    @Test
    fun `fetchManifest returns the raw signed body and the signature header`() = runBlocking {
        val body = """{"date":"2026-08-29T00:00:00+00:00","files":[],"push_id":"push-1"}"""
        server.on("/api/push/push-1/manifest") {
            TestHttpServer.Resp(
                200, body.toByteArray(),
                headers = mapOf(PushClient.MANIFEST_SIGNATURE_HEADER to "c2ln"),
            )
        }

        val result = client.fetchManifest(doorbell())

        assertTrue(result.isSuccess)
        val fetched = result.getOrThrow()
        // The body must arrive untouched: the signature covers these exact bytes.
        assertEquals(body, fetched.body)
        assertEquals("c2ln", fetched.signature)
        val req = server.requests.single()
        assertEquals("/api/push/push-1/manifest", req.path)
        assertNull(req.query)
        assertTrue(req.body.contains("\"challenge_key\":\"ck-abc\""))
    }

    @Test
    fun `fetchManifest fails when the signature header is absent`() = runBlocking {
        server.on("/api/push/push-1/manifest") {
            TestHttpServer.Resp(200, """{"files":[],"push_id":"push-1"}""")
        }

        assertTrue(client.fetchManifest(doorbell()).isFailure)
    }

    @Test
    fun `fetchManifest fails on a rejected challenge key`() = runBlocking {
        server.on("/api/push/push-1/manifest") {
            TestHttpServer.Resp(403, """{"error":"forbidden"}""")
        }

        assertTrue(client.fetchManifest(doorbell()).isFailure)
    }

    @Test
    fun `registerDevice sends the key-possession proof over the push key`() = runBlocking {
        // The Android Keystore is unavailable on the JVM, so stand in for the
        // signer with a fixed signature. That still proves the request carries
        // whatever the Keystore produced, over the negotiated push key.
        val signature = "c2lnbmVkLWJ5LWtleXN0b3Jl"
        val crypto = PushCrypto(
            mock<CloudPushKeyStore> { on { sign(any()) } doReturn signature.toByteArray() }
        )
        val serverPk = "c2VydmVyLXB1Yi1rZXk="
        server.on("/api/register-device") {
            TestHttpServer.Resp(
                200, """{"ok":true,"device_auth":"auth-new","server_pk":"$serverPk"}"""
            )
        }

        val result = client.registerDevice(
            serverUrl = config.serverUrl,
            config = config,
            crypto = crypto,
            publicKeySpkiDer = "PUBKEY".toByteArray(),
            fcmToken = "fcm-1",
            pairingToken = "pair-1",
        )

        val registered = result.getOrThrow()
        assertEquals("auth-new", registered.deviceAuth)
        // The key arrives with the registration rather than in the QR code.
        assertEquals(serverPk, registered.serverPublicKeyB64)
        val req = server.requests.single()
        assertEquals("POST", req.method)
        assertNull(req.query)
        val sent = Json.parseToJsonElement(req.body).jsonObject
        assertEquals(
            java.util.Base64.getEncoder().encodeToString("PUBKEY".toByteArray()),
            sent["public_key"]!!.jsonPrimitive.content,
        )
        assertEquals("fcm-1", sent["fcm_token"]!!.jsonPrimitive.content)
        assertEquals("pair-1", sent["pairing_token"]!!.jsonPrimitive.content)
        // signRegistration base64s the Keystore signature for the wire.
        assertEquals(
            java.util.Base64.getEncoder().encodeToString(signature.toByteArray()),
            sent["sig"]!!.jsonPrimitive.content,
        )
        // The doorbell key travels in the body and is covered by the signature.
        assertTrue(sent["push_key"]!!.jsonPrimitive.content.isNotEmpty())
    }

    @Test
    fun `registerDevice fails when the server withholds its public key`() = runBlocking {
        val crypto = PushCrypto(
            mock<CloudPushKeyStore> {
                on { sign(any()) } doReturn "c2ln".toByteArray()
            }
        )
        server.on("/api/register-device") {
            TestHttpServer.Resp(200, """{"ok":true,"device_auth":"auth-new"}""")
        }

        val result = client.registerDevice(
            serverUrl = config.serverUrl,
            config = config,
            crypto = crypto,
            publicKeySpkiDer = "PUBKEY".toByteArray(),
            fcmToken = "fcm-1",
            pairingToken = "pair-1",
        )

        // Pairing must not look successful without a key to verify manifests.
        assertTrue(result.isFailure)
    }

    @Test
    fun `rotateToken updates the stored token`() = runBlocking {
        server.on("/api/register-device") { TestHttpServer.Resp(200, """{"ok":true}""") }
        config.deviceSecret = "sec-1"
        config.deviceAuth = "auth-1"

        assertTrue(client.rotateToken(config, "fcm-2").isSuccess)

        assertTrue(server.requests.single().body.contains("\"fcm_token\":\"fcm-2\""))
    }

    @Test
    fun `renameDevice updates the device name`() = runBlocking {
        server.on("/api/register-device") { TestHttpServer.Resp(200, """{"ok":true}""") }
        config.deviceSecret = "sec-1"
        config.deviceAuth = "auth-1"

        assertTrue(client.renameDevice(config, "New Name").isSuccess)

        assertTrue(server.requests.single().body.contains("\"device_name\":\"New Name\""))
    }

    @Test
    fun `ackReceived posts the retrieval key`() = runBlocking {
        server.on("/api/push/f1/received") { TestHttpServer.Resp(200, """{"ok":true}""") }

        assertTrue(client.ackReceived(config, "f1", "rk-1").isSuccess)

        val req = server.requests.single()
        assertEquals("/api/push/f1/received", req.path)
        assertTrue(req.body.contains("\"key\":\"rk-1\""))
    }

    @Test
    fun `downloadFile writes the response body to disk`() = runBlocking {
        val content = "hello from the server".toByteArray()
        server.on("/api/push/f1/download") {
            TestHttpServer.Resp(200, content, contentType = "application/octet-stream")
        }
        val dest = File.createTempFile("pushdl", ".bin").also { it.delete() }

        assertTrue(client.downloadFile(config, "f1", "rk-1", dest).isSuccess)

        assertTrue(dest.exists())
        assertArrayEquals(content, dest.readBytes())
        assertTrue(server.requests.single().body.contains("\"key\":\"rk-1\""))
        dest.delete()
        Unit
    }

    @Test
    fun `downloadFile fails and leaves no partial file when the server refuses`() = runBlocking {
        server.on("/api/push/f1/download") {
            TestHttpServer.Resp(403, """{"error":"forbidden"}""")
        }
        val dest = File.createTempFile("pushdl", ".bin").also { it.delete() }

        assertTrue(client.downloadFile(config, "f1", "bad-key", dest).isFailure)

        assertFalse("a refused download must not leave a stub file behind", dest.exists())
    }

    @Test
    fun `unpaired config fails fast instead of calling a server with no url`() = runBlocking {
        config.serverUrl = ""

        assertFalse(client.checkRegistration(config))
        assertNotNull(client.fetchManifest(PushCrypto.Doorbell("", "p", "c")).exceptionOrNull())
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
