package com.a42r.mdrender.cloudpush

import com.a42r.mdrender.localsend.ConflictStrategy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CloudPushManagerTest {

    private fun file(id: String, name: String = "$id.md") = PushCrypto.ManifestFile(
        fileId = id,
        name = name,
        path = "",
        size = 10,
        retrievalKey = "rk-$id",
    )

    private fun doorbell(pushId: String = "push-1", serverUrl: String = "https://push.example.com") =
        PushCrypto.Doorbell(serverUrl, pushId, "ck-$pushId")

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
    fun `starts empty`() {
        assertTrue(CloudPushManager().state.value.isEmpty())
    }

    @Test
    fun `enqueue adds every file in a manifest and fires the callback once`() {
        val manager = CloudPushManager()
        val fired = mutableListOf<String>()
        manager.onPushReady { fired += it }

        manager.enqueue(doorbell(), manifest(file("f1"), file("f2")))

        assertEquals(2, manager.state.value.size)
        assertEquals(listOf("f1", "f2"), manager.state.value.map { it.file.fileId })
        assertEquals(listOf("push-1"), fired)
    }

    @Test
    fun `a re-rung doorbell does not re-queue completed files`() {
        val manager = CloudPushManager()
        val files = manifest(file("f1"), file("f2"))
        manager.enqueue(doorbell(), files)
        manager.onFinished("f1", success = true)
        manager.onFinished("f2", success = true)
        var fires = 0
        manager.onPushReady { fires++ }

        manager.enqueue(doorbell(), files)

        assertEquals(2, manager.state.value.size)
        assertEquals("nothing new to do, so the service is not woken", 0, fires)
        assertTrue(manager.state.value.all { it.status == CloudPushManager.Status.DONE })
    }

    @Test
    fun `a re-rung doorbell adds only genuinely new files`() {
        val manager = CloudPushManager()
        manager.enqueue(doorbell(), manifest(file("f1"), file("f2")))
        manager.onFinished("f1", success = true)
        val fired = mutableListOf<String>()
        manager.onPushReady { fired += it }

        // The server manifest is rebuilt live, so a retry after a partial
        // success carries only what is still unacked plus anything new.
        manager.enqueue(doorbell(), manifest(file("f2"), file("f3")))

        assertEquals(listOf("f1", "f2", "f3"), manager.state.value.map { it.file.fileId })
        assertEquals(listOf("push-1"), fired)
        assertEquals(CloudPushManager.Status.DONE, manager.state.value[0].status)
        assertEquals(CloudPushManager.Status.QUEUED, manager.state.value[2].status)
    }

    @Test
    fun `each queued file carries the push-wide folder and conflict options`() {
        val manager = CloudPushManager()

        manager.enqueue(
            doorbell(),
            manifest(file("f1"), file("f2"), targetFolder = "Story/cloud-send-images", conflict = "replace"),
        )

        assertTrue(manager.state.value.all { it.targetFolder == "Story/cloud-send-images" })
        assertTrue(manager.state.value.all { it.conflict == ConflictStrategy.REPLACE })
    }

    @Test
    fun `an unrecognised conflict value falls back to rename rather than failing`() {
        val manager = CloudPushManager()

        manager.enqueue(doorbell(), manifest(file("f1"), conflict = "clobber"))

        assertEquals(ConflictStrategy.RENAME, manager.state.value.single().conflict)
    }

    @Test
    fun `files from a different push do not collide by id`() {
        val manager = CloudPushManager()
        manager.enqueue(doorbell("push-1"), manifest(file("f1")))

        manager.enqueue(doorbell("push-2"), manifest(file("f1")))

        assertEquals(2, manager.state.value.size)
        assertEquals(
            listOf("push-1", "push-2"),
            manager.state.value.map { it.pushId },
        )
    }

    @Test
    fun `cancel marks the task cancelled without removing it`() {
        val manager = CloudPushManager()
        manager.enqueue(doorbell(), manifest(file("f1"), file("f2")))

        manager.cancel("f1")

        assertEquals(2, manager.state.value.size)
        assertEquals(CloudPushManager.Status.CANCELLED, manager.state.value[0].status)
        assertEquals(CloudPushManager.Status.QUEUED, manager.state.value[1].status)
    }

    @Test
    fun `onFinished records success at full progress`() {
        val manager = CloudPushManager()
        manager.enqueue(doorbell(), manifest(file("f1")))

        manager.onFinished("f1", success = true)

        assertEquals(CloudPushManager.Status.DONE, manager.state.value.single().status)
        assertEquals(1f, manager.state.value.single().progress, 0.0001f)
    }

    @Test
    fun `onFinished records failure without claiming full progress`() {
        val manager = CloudPushManager()
        manager.enqueue(doorbell(), manifest(file("f1")))

        manager.onFinished("f1", success = false)

        assertEquals(CloudPushManager.Status.FAILED, manager.state.value.single().status)
        assertEquals(0f, manager.state.value.single().progress, 0.0001f)
    }

    @Test
    fun `a file already in flight is not re-queued while queued`() {
        val manager = CloudPushManager()
        val files = manifest(file("f1"))
        manager.enqueue(doorbell(), files)
        var fires = 0
        manager.onPushReady { fires++ }

        // Still queued, not finished: a re-ring must not start a second copy.
        manager.enqueue(doorbell(), files)

        assertEquals(1, manager.state.value.size)
        assertEquals(0, fires)
    }
}
