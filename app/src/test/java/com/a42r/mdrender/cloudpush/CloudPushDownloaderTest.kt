package com.a42r.mdrender.cloudpush

import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.data.repository.FolderRepository
import com.a42r.mdrender.data.repository.PushHistoryRepository
import com.a42r.mdrender.localsend.LocalSendPrefs
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.any
import org.mockito.kotlin.anyOrNull
import org.mockito.kotlin.doReturn
import org.mockito.kotlin.doReturn
import org.mockito.kotlin.eq
import org.mockito.kotlin.mock
import org.mockito.kotlin.never
import org.mockito.kotlin.verifyBlocking
import java.io.File
import java.nio.file.Files

class CloudPushDownloaderTest {

    private lateinit var server: TestHttpServer
    private lateinit var config: PushServerConfig
    private lateinit var manager: CloudPushManager
    private lateinit var fileRepository: FileRepository
    private lateinit var folderRepository: FolderRepository
    private lateinit var pushHistory: PushHistoryRepository
    private lateinit var tempDir: File
    private lateinit var downloader: CloudPushDownloader

    private val started = mutableListOf<String>()

    /** File ids the app told the server it had received. */
    private fun ackedFileIds(): List<String> = server.requests
        .filter { it.path.endsWith("/received") }
        .map { it.path.removePrefix("/api/push/").removeSuffix("/received") }

    @Before
    fun setUp() {
        server = TestHttpServer().apply { start() }
        config = PushServerConfig(
            FakeSharedPreferences(),
            mock<LocalSendPrefs> { on { alias } doReturn "Pixel 9" },
        )
        config.serverUrl = server.baseUrl
        config.serverPublicKeyPem = "pem"
        config.deviceSecret = "sec-1"
        config.deviceAuth = "auth-1"
        manager = CloudPushManager()

        fileRepository = mock {
            onBlocking { mimeTypeFromExtension(any()) } doReturn "text/markdown"
        }
        folderRepository = mock {
            onBlocking { findOrCreateFolder(any(), anyOrNull()) } doReturn 7L
        }
        pushHistory = mock()

        server.on("/api/push/f1/download") { TestHttpServer.Resp(200, "hello world".toByteArray()) }
        server.on("/api/push/f2/download") { TestHttpServer.Resp(200, "second file body".toByteArray()) }
        server.on("/api/push/broken/download") { TestHttpServer.Resp(500, """{"error":"boom"}""") }
        server.on("/api/push/f1/received") { TestHttpServer.Resp(200, "{}") }
        server.on("/api/push/f2/received") { TestHttpServer.Resp(200, "{}") }
        server.on("/api/push/broken/received") { TestHttpServer.Resp(200, "{}") }

        tempDir = Files.createTempDirectory("cp-test").toFile()
        downloader = newDownloader(fileRepository)
    }

    private fun newDownloader(files: FileRepository = fileRepository) =
        CloudPushDownloader(manager, PushClient(), config, files, folderRepository, pushHistory)

    private fun file(id: String, path: String = "") = PushCrypto.ManifestFile(
        fileId = id, name = "$id.md", path = path, size = 0, retrievalKey = "rk-$id"
    )

    private fun doorbell(pushId: String = "push-1") =
        PushCrypto.Doorbell(config.serverUrl, pushId, "ck-1")

    @Test
    fun `downloads a file, imports it, acks it, and records history`() = runBlocking {
        manager.enqueue(doorbell(), listOf(file("f1")))

        val imported = downloader.drain("push-1", tempDir) { started += it }

        assertEquals(1, imported)
        assertEquals(CloudPushManager.Status.DONE, manager.state.value.single().status)
        assertEquals(listOf("f1.md"), started)
        assertEquals(listOf("f1"), ackedFileIds())
        verifyBlocking(fileRepository) {
            importFileFromTemp(any(), eq("f1.md"), eq("text/markdown"), eq(7L))
        }
        verifyBlocking(pushHistory) { record("Cloud Push", "f1.md", 11L, 7L) }
    }

    @Test
    fun `history records the real downloaded size, not the deleted temp file`() = runBlocking {
        manager.enqueue(doorbell(), listOf(file("f2")))

        downloader.drain("push-1", tempDir) { }

        // importFileFromTemp consumes and deletes the temp file, so the size has
        // to be read before the import or every entry would log zero bytes.
        verifyBlocking(pushHistory) { record("Cloud Push", "f2.md", 16L, 7L) }
    }

    @Test
    fun `a nested path becomes a chain of folders under Cloud Push`() = runBlocking {
        manager.enqueue(doorbell(), listOf(file("f1", path = "notes/2026/aug")))

        downloader.drain("push-1", tempDir) { }

        verifyBlocking(folderRepository) { findOrCreateFolder("Cloud Push", null) }
        verifyBlocking(folderRepository) { findOrCreateFolder("notes", 7L) }
        verifyBlocking(folderRepository) { findOrCreateFolder("2026", 7L) }
        verifyBlocking(folderRepository) { findOrCreateFolder("aug", 7L) }
    }

    @Test
    fun `a cancelled file is acked without being downloaded or imported`() = runBlocking {
        manager.enqueue(doorbell(), listOf(file("f1"), file("f2")))
        manager.cancel("f1")

        downloader.drain("push-1", tempDir) { started += it }

        assertEquals(listOf("f1", "f2"), ackedFileIds())
        assertEquals(CloudPushManager.Status.CANCELLED, manager.state.value[0].status)
        assertEquals(CloudPushManager.Status.DONE, manager.state.value[1].status)
        verifyBlocking(fileRepository, never()) {
            importFileFromTemp(any(), eq("f1.md"), any(), any())
        }
    }

    @Test
    fun `a failed download leaves the task failed and does not ack`() = runBlocking {
        manager.enqueue(doorbell(), listOf(file("broken")))

        val imported = downloader.drain("push-1", tempDir) { started += it }

        assertEquals(0, imported)
        assertEquals(CloudPushManager.Status.FAILED, manager.state.value.single().status)
        assertTrue(ackedFileIds().isEmpty())
    }

    @Test
    fun `a failed import is not acked, so the server keeps the file for a retry`() = runBlocking {
        val failing = mock<FileRepository>()
        org.mockito.Mockito.doThrow(IllegalStateException("disk full")).`when`(failing)
            .importFileFromTemp(anyOrNull(), anyOrNull(), anyOrNull(), anyOrNull())
        org.mockito.Mockito.`when`(failing.mimeTypeFromExtension(anyOrNull()))
            .thenReturn("text/markdown")
        manager.enqueue(doorbell(), listOf(file("f1")))

        val imported = newDownloader(failing).drain("push-1", tempDir) { started += it }

        assertEquals(0, imported)
        assertEquals(CloudPushManager.Status.FAILED, manager.state.value.single().status)
        assertTrue("only a stored file counts as progress", ackedFileIds().isEmpty())
    }

    @Test
    fun `reports progress for every file it drains`() = runBlocking {
        val progress = mutableListOf<String>()
        manager.enqueue(doorbell(), listOf(file("f1"), file("f2")))

        val imported = downloader.drain("push-1", tempDir) { progress += it }

        assertEquals(2, imported)
        assertEquals(listOf("f1.md", "f2.md"), progress)
    }

    @Test
    fun `a 404 from the server flags that re-pairing is needed`() = runBlocking {
        server.on("/api/push/gone/download") {
            TestHttpServer.Resp(404, """{"error":"unknown device"}""")
        }
        manager.enqueue(doorbell(), listOf(file("gone")))

        downloader.drain("push-1", tempDir) { }

        assertTrue(manager.needsReRegistration.value)
    }

    @Test
    fun `an ordinary server error does not demand re-pairing`() = runBlocking {
        manager.enqueue(doorbell(), listOf(file("broken")))

        downloader.drain("push-1", tempDir) { }

        // A 500 is transient; nagging the user to re-pair would be wrong.
        assertTrue(!manager.needsReRegistration.value)
    }

    @Test
    fun `an unknown push id drains nothing`() = runBlocking {
        manager.enqueue(doorbell(), listOf(file("f1")))

        assertEquals(0, downloader.drain("push-999", tempDir) { started += it })
        assertTrue(ackedFileIds().isEmpty())
    }

    @Test
    fun `no temp files are left behind`() = runBlocking {
        manager.enqueue(doorbell(), listOf(file("f1")))

        downloader.drain("push-1", tempDir) { }

        assertTrue(tempDir.listFiles().orEmpty().isEmpty())
    }

    @Test
    fun `a retry after a failure re-downloads the file`() = runBlocking {
        var failNext = true
        server.on("/api/push/f1/download") {
            if (failNext) {
                failNext = false
                TestHttpServer.Resp(500, """{"error":"boom"}""")
            } else {
                TestHttpServer.Resp(200, "hello world".toByteArray())
            }
        }
        manager.enqueue(doorbell(), listOf(file("f1")))
        assertEquals(0, downloader.drain("push-1", tempDir) { started += it })

        assertEquals(1, downloader.drain("push-1", tempDir) { started += it })
        assertEquals(CloudPushManager.Status.DONE, manager.state.value.single().status)
        assertEquals(listOf("f1"), ackedFileIds())
    }

    private class FakeSharedPreferences : android.content.SharedPreferences {
        private val store = mutableMapOf<String, Any?>()

        override fun getAll(): MutableMap<String, *> = store
        override fun getString(key: String?, defValue: String?): String? = store[key] as? String ?: defValue
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
            override fun commit(): Boolean { store.putAll(pending); return true }
            override fun apply() { commit() }
        }
    }
}
