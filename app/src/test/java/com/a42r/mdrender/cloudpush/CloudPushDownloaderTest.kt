package com.a42r.mdrender.cloudpush

import com.a42r.mdrender.data.dao.FileMetadata
import com.a42r.mdrender.data.repository.FileBookmarks
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
import org.mockito.kotlin.doAnswer
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

        // The default conflict is rename, so every drain asks for a free name.
        // Echoing the desired name back is the "nothing is in the way" case;
        // tests that care about a collision stub it themselves.
        fileRepository = mock {
            onBlocking { mimeTypeFromExtension(any()) } doReturn "text/markdown"
            onBlocking { uniqueNameInFolder(anyOrNull(), any()) } doAnswer { it.getArgument(1) }
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

    private fun manifest(
        vararg files: PushCrypto.ManifestFile,
        targetFolder: String = "",
        conflict: String = "rename",
    ) = PushCrypto.Manifest(
        pushId = "push-1",
        date = "2026-08-29T00:00:00+00:00",
        targetFolder = targetFolder,
        conflict = conflict,
        files = files.toList(),
    )

    @Test
    fun `downloads a file, imports it, acks it, and records history`() = runBlocking {
        manager.enqueue(doorbell(), manifest(file("f1")))

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
        manager.enqueue(doorbell(), manifest(file("f2")))

        downloader.drain("push-1", tempDir) { }

        // importFileFromTemp consumes and deletes the temp file, so the size has
        // to be read before the import or every entry would log zero bytes.
        verifyBlocking(pushHistory) { record("Cloud Push", "f2.md", 16L, 7L) }
    }

    @Test
    fun `a per-file path is a chain of folders hanging off the app root`() = runBlocking {
        manager.enqueue(doorbell(), manifest(file("f1", path = "notes/2026/aug")))

        downloader.drain("push-1", tempDir) { }

        // Matches LocalSend: a sender's path is relative to the library root, so
        // the first segment is created against null rather than under a
        // transport-specific folder the user never asked for.
        verifyBlocking(folderRepository) { findOrCreateFolder("notes", null) }
        verifyBlocking(folderRepository) { findOrCreateFolder("2026", 7L) }
        verifyBlocking(folderRepository) { findOrCreateFolder("aug", 7L) }
        verifyBlocking(folderRepository, never()) { findOrCreateFolder("Cloud Push", null) }
    }

    @Test
    fun `a cancelled file is acked without being downloaded or imported`() = runBlocking {
        manager.enqueue(doorbell(), manifest(file("f1"), file("f2")))
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
        manager.enqueue(doorbell(), manifest(file("broken")))

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
        manager.enqueue(doorbell(), manifest(file("f1")))

        val imported = newDownloader(failing).drain("push-1", tempDir) { started += it }

        assertEquals(0, imported)
        assertEquals(CloudPushManager.Status.FAILED, manager.state.value.single().status)
        assertTrue("only a stored file counts as progress", ackedFileIds().isEmpty())
    }

    @Test
    fun `reports progress for every file it drains`() = runBlocking {
        val progress = mutableListOf<String>()
        manager.enqueue(doorbell(), manifest(file("f1"), file("f2")))

        val imported = downloader.drain("push-1", tempDir) { progress += it }

        assertEquals(2, imported)
        assertEquals(listOf("f1.md", "f2.md"), progress)
    }

    @Test
    fun `a 404 from the server flags that re-pairing is needed`() = runBlocking {
        server.on("/api/push/gone/download") {
            TestHttpServer.Resp(404, """{"error":"unknown device"}""")
        }
        manager.enqueue(doorbell(), manifest(file("gone")))

        downloader.drain("push-1", tempDir) { }

        assertTrue(manager.needsReRegistration.value)
    }

    @Test
    fun `an ordinary server error does not demand re-pairing`() = runBlocking {
        manager.enqueue(doorbell(), manifest(file("broken")))

        downloader.drain("push-1", tempDir) { }

        // A 500 is transient; nagging the user to re-pair would be wrong.
        assertTrue(!manager.needsReRegistration.value)
    }

    @Test
    fun `an unknown push id drains nothing`() = runBlocking {
        manager.enqueue(doorbell(), manifest(file("f1")))

        assertEquals(0, downloader.drain("push-999", tempDir) { started += it })
        assertTrue(ackedFileIds().isEmpty())
    }

    @Test
    fun `no temp files are left behind`() = runBlocking {
        manager.enqueue(doorbell(), manifest(file("f1")))

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
        manager.enqueue(doorbell(), manifest(file("f1")))
        assertEquals(0, downloader.drain("push-1", tempDir) { started += it })

        assertEquals(1, downloader.drain("push-1", tempDir) { started += it })
        assertEquals(CloudPushManager.Status.DONE, manager.state.value.single().status)
        assertEquals(listOf("f1"), ackedFileIds())
    }

    @Test
    fun `a push-wide target folder is created under the app root`() = runBlocking {
        manager.enqueue(doorbell(), manifest(file("f1"), targetFolder = "Story/cloud-send-images"))

        downloader.drain("push-1", tempDir) { }

        // Unlike a per-file path, the sender's folder hangs off the app root so
        // it lands where they will actually look for it.
        verifyBlocking(folderRepository) { findOrCreateFolder("Story", null) }
        verifyBlocking(folderRepository) { findOrCreateFolder("cloud-send-images", 7L) }
        verifyBlocking(folderRepository, never()) { findOrCreateFolder("Cloud Push", null) }
    }

    @Test
    fun `a blank target folder falls back to the Cloud Push root`() = runBlocking {
        manager.enqueue(doorbell(), manifest(file("f1"), targetFolder = ""))

        downloader.drain("push-1", tempDir) { }

        verifyBlocking(folderRepository) { findOrCreateFolder("Cloud Push", null) }
    }

    @Test
    fun `a per-file path overrides the push-wide target folder`() = runBlocking {
        manager.enqueue(doorbell(), manifest(file("f1", path = "Elsewhere"), targetFolder = "Story/cloud-send-images"))

        downloader.drain("push-1", tempDir) { }

        verifyBlocking(folderRepository) { findOrCreateFolder("Elsewhere", null) }
        verifyBlocking(folderRepository, never()) { findOrCreateFolder("Story", null) }
    }

    @Test
    fun `replace keeps the reader's place in the file it overwrites`() = runBlocking {
        val existing = FileMetadata(
            id = 42L, name = "f1.md", mimeType = "text/markdown", fileSize = 11L,
            scrollPosition = 300, playbackPosition = 9_000L,
        )
        val files = mock<FileRepository> {
            onBlocking { mimeTypeFromExtension(any()) } doReturn "text/markdown"
            onBlocking { findByName(7L, "f1.md") } doReturn existing
            onBlocking { getLastOpenedAt(42L) } doReturn 1_700_000L
        }
        val d = newDownloader(files)
        manager.enqueue(doorbell(), manifest(file("f1"), conflict = "replace"))

        assertEquals(1, d.drain("push-1", tempDir) { })

        verifyBlocking(files) {
            replaceFileFromTemp(
                any(), eq("f1.md"), eq("text/markdown"), eq(7L),
                oldId = eq(42L),
                bookmarks = eq(FileBookmarks(scrollPosition = 300, playbackPosition = 9_000L, lastOpenedAt = 1_700_000L)),
            )
        }
        verifyBlocking(files, never()) { importFileFromTemp(any(), anyOrNull(), anyOrNull(), anyOrNull()) }
    }

    @Test
    fun `skip acks without downloading when the name is already taken`() = runBlocking {
        val existing = FileMetadata(id = 42L, name = "f1.md", mimeType = "text/markdown", fileSize = 11L)
        val files = mock<FileRepository> {
            onBlocking { mimeTypeFromExtension(any()) } doReturn "text/markdown"
            onBlocking { findByName(7L, "f1.md") } doReturn existing
        }
        val d = newDownloader(files)
        manager.enqueue(doorbell(), manifest(file("f1"), conflict = "skip"))

        downloader.drain("push-1", tempDir) { }

        // Nothing moves, but the server still has to be told we are done with it,
        // otherwise it will offer the same file on the next doorbell forever.
        assertEquals(listOf("f1"), ackedFileIds())
        verifyBlocking(files, never()) { replaceFileFromTemp(any(), anyOrNull(), anyOrNull(), anyOrNull(), any(), anyOrNull()) }
        assertEquals(CloudPushManager.Status.DONE, manager.state.value.single().status)
    }

    @Test
    fun `rename stores a free name when the original is taken`() = runBlocking {
        val existing = FileMetadata(id = 42L, name = "f1.md", mimeType = "text/markdown", fileSize = 11L)
        val files = mock<FileRepository> {
            onBlocking { mimeTypeFromExtension(any()) } doReturn "text/markdown"
            onBlocking { findByName(7L, "f1.md") } doReturn existing
            onBlocking { uniqueNameInFolder(7L, "f1.md") } doReturn "f1 (1).md"
        }
        val d = newDownloader(files)
        manager.enqueue(doorbell(), manifest(file("f1"), conflict = "rename"))

        assertEquals(1, d.drain("push-1", tempDir) { })

        verifyBlocking(files) { importFileFromTemp(any(), eq("f1 (1).md"), eq("text/markdown"), eq(7L)) }
    }

    @Test
    fun `replace with a free name just imports, since there is nothing to carry over`() = runBlocking {
        val files = mock<FileRepository> {
            onBlocking { mimeTypeFromExtension(any()) } doReturn "text/markdown"
            onBlocking { findByName(7L, "f1.md") } doReturn null
        }
        val d = newDownloader(files)
        manager.enqueue(doorbell(), manifest(file("f1"), conflict = "replace"))

        assertEquals(1, d.drain("push-1", tempDir) { })

        verifyBlocking(files) { importFileFromTemp(any(), eq("f1.md"), eq("text/markdown"), eq(7L)) }
        verifyBlocking(files, never()) { replaceFileFromTemp(any(), anyOrNull(), anyOrNull(), anyOrNull(), any(), anyOrNull()) }
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
