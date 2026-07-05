package com.a42r.mdrender.ui.browser

import com.a42r.mdrender.data.dao.FileMetadata
import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test

class MoveConflictWalkerTest {

    private var nextId = 100L
    private fun meta(name: String, folderId: Long?) = FileMetadata(
        id = nextId++, folderId = folderId, name = name,
        mimeType = "text/plain", fileSize = 0L
    )

    /** In-memory store keyed by (folderId, name); mutated by move/delete. */
    private class FakeStore(files: List<FileMetadata>) {
        val byId = files.associateBy { it.id }.toMutableMap()
        fun findByName(folderId: Long?, name: String): FileMetadata? =
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
        val a = meta("a.md", 1); val b = meta("b.md", 1)
        val store = FakeStore(listOf(a, b))
        val result = walker(store).run(listOf(a, b), targetFolderId = 2) { _, _ ->
            fail("askUser must not be called"); ConflictDecision.CancelBatch
        }
        assertEquals(MoveConflictWalker.Result(moved = 2, replaced = 0, skipped = 0, cancelled = false), result)
    }

    @Test
    fun conflict_replacesExisting_afterAsking() = runTest {
        val source = meta("n.md", 1)
        val dest = meta("n.md", 2)
        val store = FakeStore(listOf(source, dest))
        val result = walker(store).run(listOf(source), targetFolderId = 2) { _, _ ->
            ConflictDecision.Replace(applyToAll = false)
        }
        assertEquals(MoveConflictWalker.Result(moved = 0, replaced = 1, skipped = 0, cancelled = false), result)
        assertEquals("must be in dest folder", 2L, store.byId[source.id]?.folderId)
        assertNull("original must be deleted", store.byId[dest.id])
    }

    @Test
    fun conflict_skipsExisting_afterAsking() = runTest {
        val source = meta("n.md", 1)
        val dest = meta("n.md", 2)
        val store = FakeStore(listOf(source, dest))
        val result = walker(store).run(listOf(source), targetFolderId = 2) { _, _ ->
            ConflictDecision.Skip(applyToAll = false)
        }
        assertEquals(MoveConflictWalker.Result(moved = 0, replaced = 0, skipped = 1, cancelled = false), result)
        assertEquals("source stays in original folder", 1L, store.byId[source.id]?.folderId)
        assertNotNull("dest is untouched", store.byId[dest.id])
    }

    @Test
    fun skipSameFolder_skipsWithoutAsking() = runTest {
        val f = meta("n.md", 1)
        val store = FakeStore(listOf(f))
        var asked = false
        val result = walker(store).run(listOf(f), targetFolderId = 1) { _, _ ->
            asked = true; ConflictDecision.Skip(applyToAll = false)
        }
        assertFalse("must not ask when target is the same folder", asked)
        assertEquals(MoveConflictWalker.Result(moved = 0, replaced = 0, skipped = 0, cancelled = false), result)
    }

    @Test
    fun replaceWithApplyToAll_doesNotAskForSecond() = runTest {
        val s1 = meta("a.md", 1); val d1 = meta("a.md", 2)
        val s2 = meta("b.md", 1); val d2 = meta("b.md", 2)
        val store = FakeStore(listOf(s1, s2, d1, d2))
        var askCount = 0
        val result = walker(store).run(listOf(s1, s2), targetFolderId = 2) { _, _ ->
            askCount++; ConflictDecision.Replace(applyToAll = true)
        }
        assertEquals("only asked once", 1, askCount)
        assertEquals(MoveConflictWalker.Result(moved = 0, replaced = 2, skipped = 0, cancelled = false), result)
    }

    @Test
    fun skipWithApplyToAll_doesNotAskForSecond() = runTest {
        val s1 = meta("a.md", 1); val d1 = meta("a.md", 2)
        val s2 = meta("b.md", 1); val d2 = meta("b.md", 2)
        val store = FakeStore(listOf(s1, s2, d1, d2))
        var askCount = 0
        val result = walker(store).run(listOf(s1, s2), targetFolderId = 2) { _, _ ->
            askCount++; ConflictDecision.Skip(applyToAll = true)
        }
        assertEquals("only asked once", 1, askCount)
        assertEquals(MoveConflictWalker.Result(moved = 0, replaced = 0, skipped = 2, cancelled = false), result)
    }

    @Test
    fun cancelBatch_stopsMidway_firstFileStays() = runTest {
        val s1 = meta("a.md", 1); val d1 = meta("a.md", 2)
        val s2 = meta("b.md", 1); val d2 = meta("b.md", 2)
        val store = FakeStore(listOf(s1, s2, d1, d2))
        val result = walker(store).run(listOf(s1, s2), targetFolderId = 2) { _, _ ->
            ConflictDecision.CancelBatch
        }
        assertTrue("cancelled", result.cancelled)
        assertEquals(0, result.moved + result.replaced + result.skipped)
    }

    @Test
    fun replacesAndSkipsMixedRespectsDecision() = runTest {
        val s1 = meta("a.md", 1); val d1 = meta("a.md", 2)
        val s2 = meta("b.md", 1); val d2 = meta("b.md", 2)
        val store = FakeStore(listOf(s1, s2, d1, d2))
        var call = 0
        val result = walker(store).run(listOf(s1, s2), targetFolderId = 2) { _, _ ->
            call++; if (call == 1) ConflictDecision.Replace(applyToAll = false) else ConflictDecision.Skip(applyToAll = false)
        }
        assertEquals(MoveConflictWalker.Result(moved = 0, replaced = 1, skipped = 1, cancelled = false), result)
        assertEquals("s1 moved to dest", 2L, store.byId[s1.id]?.folderId)
        assertEquals("s2 stayed in source", 1L, store.byId[s2.id]?.folderId)
        assertNotNull("d2 untouched", store.byId[d2.id])
    }

    @Test
    fun itemsAlreadyInTargetFolder_areSkipped() = runTest {
        val f1 = meta("f.md", 1)
        val f2 = meta("f2.md", 2)
        val store = FakeStore(listOf(f1, f2))
        val result = walker(store).run(listOf(f1, f2), targetFolderId = 2) { _, _ ->
            fail("must not ask"); ConflictDecision.CancelBatch
        }
        assertEquals(MoveConflictWalker.Result(moved = 1, replaced = 0, skipped = 0, cancelled = false), result)
    }
}
