package com.a42r.mdrender.ui.browser

import com.a42r.mdrender.data.entity.FileEntity
import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test

class MoveConflictWalkerTest {

    private var nextId = 100L
    private fun file(name: String, folderId: Long?) = FileEntity(
        id = nextId++, folderId = folderId, name = name,
        mimeType = "text/plain", encryptedBlob = byteArrayOf(), fileSize = 0L
    )

    /** In-memory store keyed by (folderId, name); mutated by move/delete. */
    private class FakeStore(files: List<FileEntity>) {
        val byId = files.associateBy { it.id }.toMutableMap()
        fun findByName(folderId: Long?, name: String): FileEntity? =
            byId.values.firstOrNull { it.folderId == folderId && it.name == name }
        fun move(id: Long, folderId: Long?) {
            byId[id] = byId.getValue(id).copy(folderId = folderId)
        }
        fun delete(id: Long) { byId.remove(id) }
    }

    private fun walker(store: FakeStore) = MoveConflictWalker(
        findByName = { f, n -> store.findByName(f, n) },
        moveFile = { id, f -> store.move(id, f) },
        deleteFile = { id -> store.delete(id) }
    )

    @Test
    fun noConflicts_movesEverything_neverAsks() = runTest {
        val a = file("a.md", 1); val b = file("b.md", 1)
        val store = FakeStore(listOf(a, b))
        val result = walker(store).run(listOf(a, b), targetFolderId = 2) { _, _ ->
            fail("askUser must not be called"); ConflictDecision.CancelBatch
        }
        assertEquals(MoveConflictWalker.Result(moved = 2, replaced = 0, skipped = 0, cancelled = false), result)
        assertEquals(2L, store.byId.getValue(a.id).folderId)
        assertEquals(2L, store.byId.getValue(b.id).folderId)
    }

    @Test
    fun sameFolderMove_isNoOp() = runTest {
        val a = file("a.md", 2)
        val store = FakeStore(listOf(a))
        val result = walker(store).run(listOf(a), targetFolderId = 2) { _, _ ->
            fail("askUser must not be called"); ConflictDecision.CancelBatch
        }
        assertEquals(MoveConflictWalker.Result(0, 0, 0, false), result)
    }

    @Test
    fun conflict_replace_deletesTargetAndMoves() = runTest {
        val source = file("a.md", 1); val target = file("a.md", 2)
        val store = FakeStore(listOf(source, target))
        val result = walker(store).run(listOf(source), 2) { f, remaining ->
            assertEquals(source.id, f.id); assertEquals(1, remaining)
            ConflictDecision.Replace(applyToAll = false)
        }
        assertEquals(MoveConflictWalker.Result(0, 1, 0, false), result)
        assertNull(store.byId[target.id])
        assertEquals(2L, store.byId.getValue(source.id).folderId)
    }

    @Test
    fun conflict_skip_leavesSourceInPlace() = runTest {
        val source = file("a.md", 1); val target = file("a.md", 2)
        val store = FakeStore(listOf(source, target))
        val result = walker(store).run(listOf(source), 2) { _, _ ->
            ConflictDecision.Skip(applyToAll = false)
        }
        assertEquals(MoveConflictWalker.Result(0, 0, 1, false), result)
        assertEquals(1L, store.byId.getValue(source.id).folderId)
        assertNotNull(store.byId[target.id])
    }

    @Test
    fun replaceAll_asksOnceThenAppliesToRest() = runTest {
        val s1 = file("a.md", 1); val s2 = file("b.md", 1)
        val t1 = file("a.md", 2); val t2 = file("b.md", 2)
        val store = FakeStore(listOf(s1, s2, t1, t2))
        var asks = 0
        val result = walker(store).run(listOf(s1, s2), 2) { _, _ ->
            asks++; ConflictDecision.Replace(applyToAll = true)
        }
        assertEquals(1, asks)
        assertEquals(MoveConflictWalker.Result(0, 2, 0, false), result)
        assertNull(store.byId[t1.id]); assertNull(store.byId[t2.id])
    }

    @Test
    fun skipAll_asksOnceThenSkipsRest() = runTest {
        val s1 = file("a.md", 1); val s2 = file("b.md", 1)
        val t1 = file("a.md", 2); val t2 = file("b.md", 2)
        val store = FakeStore(listOf(s1, s2, t1, t2))
        var asks = 0
        val result = walker(store).run(listOf(s1, s2), 2) { _, _ ->
            asks++; ConflictDecision.Skip(applyToAll = true)
        }
        assertEquals(1, asks)
        assertEquals(MoveConflictWalker.Result(0, 0, 2, false), result)
        assertEquals(1L, store.byId.getValue(s1.id).folderId)
        assertEquals(1L, store.byId.getValue(s2.id).folderId)
        assertNotNull(store.byId[t1.id])
        assertNotNull(store.byId[t2.id])
    }

    @Test
    fun stickyDecision_doesNotSuppressNonConflictMoves() = runTest {
        // s1 conflicts (skip-all chosen); s2 has no conflict and must still move.
        val s1 = file("a.md", 1); val s2 = file("c.md", 1)
        val t1 = file("a.md", 2)
        val store = FakeStore(listOf(s1, s2, t1))
        val result = walker(store).run(listOf(s1, s2), 2) { _, _ ->
            ConflictDecision.Skip(applyToAll = true)
        }
        assertEquals(MoveConflictWalker.Result(moved = 1, replaced = 0, skipped = 1, cancelled = false), result)
        assertEquals(2L, store.byId.getValue(s2.id).folderId)
    }

    @Test
    fun cancel_stopsBatch_earlierMovesStand() = runTest {
        val s1 = file("a.md", 1)          // moves cleanly
        val s2 = file("b.md", 1)          // conflicts -> cancel
        val s3 = file("c.md", 1)          // must remain untouched
        val t2 = file("b.md", 2)
        val store = FakeStore(listOf(s1, s2, s3, t2))
        val result = walker(store).run(listOf(s1, s2, s3), 2) { _, _ ->
            ConflictDecision.CancelBatch
        }
        assertEquals(MoveConflictWalker.Result(moved = 1, replaced = 0, skipped = 0, cancelled = true), result)
        assertEquals(2L, store.byId.getValue(s1.id).folderId)
        assertEquals(1L, store.byId.getValue(s2.id).folderId)
        assertEquals(1L, store.byId.getValue(s3.id).folderId)
    }

    @Test
    fun withinBatchCollision_secondFilePrompts() = runTest {
        // Two same-named files from different folders moved to an empty folder:
        // the first lands, the second collides with it.
        val s1 = file("a.md", 1); val s2 = file("a.md", 3)
        val store = FakeStore(listOf(s1, s2))
        var askedFor: Long? = null
        val result = walker(store).run(listOf(s1, s2), 2) { f, _ ->
            askedFor = f.id; ConflictDecision.Skip(applyToAll = false)
        }
        assertEquals(s2.id, askedFor)
        assertEquals(MoveConflictWalker.Result(moved = 1, replaced = 0, skipped = 1, cancelled = false), result)
        assertEquals(2L, store.byId.getValue(s1.id).folderId)
        assertEquals(3L, store.byId.getValue(s2.id).folderId)
    }

    @Test
    fun remainingCount_reflectsUnprocessedFiles() = runTest {
        val s1 = file("a.md", 1); val s2 = file("b.md", 1); val s3 = file("c.md", 1)
        val t1 = file("a.md", 2)
        val store = FakeStore(listOf(s1, s2, s3, t1))
        var seenRemaining: Int? = null
        walker(store).run(listOf(s1, s2, s3), 2) { _, remaining ->
            seenRemaining = remaining; ConflictDecision.Skip(applyToAll = false)
        }
        // s1 conflicts first with s2, s3 still unprocessed -> remaining = 3.
        assertEquals(3, seenRemaining)
        assertEquals(1L, store.byId.getValue(s1.id).folderId)
        assertEquals(2L, store.byId.getValue(s2.id).folderId)
        assertEquals(2L, store.byId.getValue(s3.id).folderId)
    }

    @Test
    fun selfCollision_fileAlreadyNamedSameAsItself_isNotAConflict() = runTest {
        // findByName returning the moving file itself must not prompt.
        val a = file("a.md", 2)
        val store = FakeStore(listOf(a))
        val result = walker(store).run(listOf(a.copy(folderId = 1)), 2) { _, _ ->
            fail("self-match must not prompt"); ConflictDecision.CancelBatch
        }
        // The store still holds the id at folder 2; walker sees findByName hit
        // with the same id and treats it as no conflict.
        assertEquals(MoveConflictWalker.Result(1, 0, 0, false), result)
    }
}
