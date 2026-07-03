# Name Collision Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** File moves prompt Replace/Skip (with apply-to-all) on name collisions; file renames and folder create/rename/move cannot create duplicate names.

**Architecture:** A standalone `MoveConflictWalker` walks a move batch sequentially against live DB state, delegating each conflict to a suspend `askUser` callback — fully unit-testable with fake lambdas, no mocking framework needed for the core. `BrowserViewModel` wires the walker to the repositories and bridges `askUser` to a Compose dialog via `StateFlow` + `CompletableDeferred`. Folder sibling uniqueness rides on a case-insensitive `FolderDao.findByName`.

**Tech Stack:** Kotlin, Jetpack Compose (Material 3), Hilt, Room, JUnit4 + kotlinx-coroutines-test + mockito-kotlin.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-03-name-collisions-design.md`.
- File-name comparisons are exact-match; folder-name comparisons are case-insensitive (`COLLATE NOCASE`).
- Conflict dialog buttons: **Replace**, **Skip**, **Cancel**; checkbox "Apply to all remaining conflicts" shown only when more than one unprocessed file remains; dialog dismiss = Cancel (never silently replace).
- Root folder displays as **"Home"** in conflict/snackbar text.
- Batch summary snackbar: base "Moved N", append ", replaced K" and ", skipped M" only when nonzero.
- Rename collisions (file and folder) show inline error "A file with this name already exists" / "A folder with this name already exists" and keep the dialog open — no Replace option.
- Folder-move collision snackbar: `A folder named "<name>" already exists in "<destination>"`.
- Existing duplicate folders are grandfathered — no migration.
- Work on branch `feature/name-collisions`; commit per task; no push, no merge to master without operator instruction; no `Co-Authored-By` footers.
- Run `graphify update .` once at the end of the final task.

---

### Task 0: Branch setup

**Files:** none

- [ ] **Step 1: Create the working branch**

```bash
cd /home/philg/src/AndroidStudioProjects/MDRender
git checkout -b feature/name-collisions
```

Expected: `Switched to a new branch 'feature/name-collisions'`.

---

### Task 1: DAO and repository name lookups

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/data/dao/FileDao.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/data/dao/FolderDao.kt:18-19`
- Modify: `app/src/main/java/com/a42r/mdrender/data/repository/FileRepository.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/data/repository/FolderRepository.kt`
- Test: `app/src/test/java/com/a42r/mdrender/data/repository/FolderRepositoryNamesTest.kt`

**Interfaces:**
- Consumes: existing DAO/repository structure.
- Produces (later tasks rely on these exact signatures):
  - `FileDao.findByName(folderId: Long?, name: String): FileEntity?` (exact match)
  - `FileRepository.findByName(folderId: Long?, name: String): FileEntity?`
  - `FolderRepository.getFolder(id: Long): FolderEntity?`
  - `FolderRepository.siblingNameExists(parentId: Long?, name: String, excludeId: Long? = null): Boolean` (case-insensitive via DAO)

- [ ] **Step 1: Write the failing test**

`FolderDao` is an interface, so mockito-kotlin mocks it directly. Create `app/src/test/java/com/a42r/mdrender/data/repository/FolderRepositoryNamesTest.kt`:

```kotlin
package com.a42r.mdrender.data.repository

import com.a42r.mdrender.data.dao.FolderDao
import com.a42r.mdrender.data.entity.FolderEntity
import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test
import org.mockito.kotlin.mock
import org.mockito.kotlin.whenever

class FolderRepositoryNamesTest {

    private val dao: FolderDao = mock()
    private val repo = FolderRepository(dao)

    private val existing = FolderEntity(id = 5, parentId = 1, name = "Work")

    @Test
    fun siblingNameExists_hit() = runTest {
        whenever(dao.findByName(1, "Work")).thenReturn(existing)
        assertTrue(repo.siblingNameExists(1, "Work"))
    }

    @Test
    fun siblingNameExists_miss() = runTest {
        whenever(dao.findByName(1, "Play")).thenReturn(null)
        assertFalse(repo.siblingNameExists(1, "Play"))
    }

    @Test
    fun siblingNameExists_excludesSelf() = runTest {
        // Renaming folder 5 to a different casing of its own name is allowed.
        whenever(dao.findByName(1, "WORK")).thenReturn(existing)
        assertFalse(repo.siblingNameExists(1, "WORK", excludeId = 5))
        assertTrue(repo.siblingNameExists(1, "WORK", excludeId = 99))
    }

    @Test
    fun getFolder_delegatesToDao() = runTest {
        whenever(dao.getById(5)).thenReturn(existing)
        assertEquals(existing, repo.getFolder(5))
    }
}
```

Note: case-insensitivity itself lives in the SQL (`COLLATE NOCASE`) and is exercised on-device; these tests cover the repository logic around it.

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "com.a42r.mdrender.data.repository.FolderRepositoryNamesTest"`
Expected: compilation FAILURE — `unresolved reference: siblingNameExists`.

- [ ] **Step 3: Write the implementation**

In `app/src/main/java/com/a42r/mdrender/data/dao/FileDao.kt`, add after `getNamesInFolder`:

```kotlin
    @Query("SELECT * FROM files WHERE folder_id IS :folderId AND name = :name LIMIT 1")
    suspend fun findByName(folderId: Long?, name: String): FileEntity?
```

In `app/src/main/java/com/a42r/mdrender/data/dao/FolderDao.kt`, change the existing `findByName` query (line 18) to be case-insensitive:

```kotlin
    @Query("SELECT * FROM folders WHERE parent_id IS :parentId AND name = :name COLLATE NOCASE LIMIT 1")
    suspend fun findByName(parentId: Long?, name: String): FolderEntity?
```

In `app/src/main/java/com/a42r/mdrender/data/repository/FileRepository.kt`, add after `getFileMetadata`:

```kotlin
    /** The file named [name] in [folderId] (exact match), or null. */
    suspend fun findByName(folderId: Long?, name: String): FileEntity? =
        fileDao.findByName(folderId, name)
```

In `app/src/main/java/com/a42r/mdrender/data/repository/FolderRepository.kt`, add after `folderExists`:

```kotlin
    suspend fun getFolder(id: Long): FolderEntity? = folderDao.getById(id)

    /** True when a sibling of [parentId] already uses [name]
     *  (case-insensitive), ignoring [excludeId] (for renames). */
    suspend fun siblingNameExists(parentId: Long?, name: String, excludeId: Long? = null): Boolean {
        val found = folderDao.findByName(parentId, name) ?: return false
        return found.id != excludeId
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "com.a42r.mdrender.data.repository.FolderRepositoryNamesTest"`
Expected: BUILD SUCCESSFUL, 4 tests passed.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/data/dao/FileDao.kt \
        app/src/main/java/com/a42r/mdrender/data/dao/FolderDao.kt \
        app/src/main/java/com/a42r/mdrender/data/repository/FileRepository.kt \
        app/src/main/java/com/a42r/mdrender/data/repository/FolderRepository.kt \
        app/src/test/java/com/a42r/mdrender/data/repository/FolderRepositoryNamesTest.kt
git commit -m "feat: name-lookup queries for collision detection"
```

---

### Task 2: MoveConflictWalker

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/ui/browser/MoveConflictWalker.kt`
- Test: `app/src/test/java/com/a42r/mdrender/ui/browser/MoveConflictWalkerTest.kt`

**Interfaces:**
- Consumes: `FileEntity` (existing).
- Produces (Task 3 relies on these exact names/types):

```kotlin
sealed interface ConflictDecision {
    data class Replace(val applyToAll: Boolean) : ConflictDecision
    data class Skip(val applyToAll: Boolean) : ConflictDecision
    object CancelBatch : ConflictDecision
}

class MoveConflictWalker(
    findByName: suspend (folderId: Long?, name: String) -> FileEntity?,
    moveFile: suspend (id: Long, folderId: Long?) -> Unit,
    deleteFile: suspend (id: Long) -> Unit,
) {
    data class Result(val moved: Int, val replaced: Int, val skipped: Int, val cancelled: Boolean)
    suspend fun run(
        files: List<FileEntity>,
        targetFolderId: Long?,
        askUser: suspend (file: FileEntity, remaining: Int) -> ConflictDecision
    ): Result
}
```

`remaining` = count of files not yet fully processed, INCLUDING the conflicting one (the dialog shows its checkbox when `remaining > 1`).

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/com/a42r/mdrender/ui/browser/MoveConflictWalkerTest.kt`. The fake store is a mutable map folder→(name→file), so within-batch collisions surface naturally:

```kotlin
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "com.a42r.mdrender.ui.browser.MoveConflictWalkerTest"`
Expected: compilation FAILURE — `unresolved reference: MoveConflictWalker`.

- [ ] **Step 3: Write the implementation**

Create `app/src/main/java/com/a42r/mdrender/ui/browser/MoveConflictWalker.kt`:

```kotlin
package com.a42r.mdrender.ui.browser

import com.a42r.mdrender.data.entity.FileEntity

/** User's answer to a move name-conflict prompt. */
sealed interface ConflictDecision {
    data class Replace(val applyToAll: Boolean) : ConflictDecision
    data class Skip(val applyToAll: Boolean) : ConflictDecision
    object CancelBatch : ConflictDecision
}

/** Walks a move batch sequentially against live storage state, asking the
 *  user (via [run]'s askUser) about each name conflict unless a sticky
 *  apply-to-all decision is active. Checking live state means two same-named
 *  files in one batch conflict with each other — intended. */
class MoveConflictWalker(
    private val findByName: suspend (folderId: Long?, name: String) -> FileEntity?,
    private val moveFile: suspend (id: Long, folderId: Long?) -> Unit,
    private val deleteFile: suspend (id: Long) -> Unit,
) {
    data class Result(val moved: Int, val replaced: Int, val skipped: Int, val cancelled: Boolean)

    suspend fun run(
        files: List<FileEntity>,
        targetFolderId: Long?,
        askUser: suspend (file: FileEntity, remaining: Int) -> ConflictDecision
    ): Result {
        var moved = 0
        var replaced = 0
        var skipped = 0
        var sticky: ConflictDecision? = null

        for ((index, file) in files.withIndex()) {
            if (file.folderId == targetFolderId) continue
            val existing = findByName(targetFolderId, file.name)?.takeIf { it.id != file.id }
            if (existing == null) {
                moveFile(file.id, targetFolderId)
                moved++
                continue
            }
            val decision = sticky ?: askUser(file, files.size - index)
            when (decision) {
                is ConflictDecision.Replace -> {
                    if (decision.applyToAll) sticky = decision
                    deleteFile(existing.id)
                    moveFile(file.id, targetFolderId)
                    replaced++
                }
                is ConflictDecision.Skip -> {
                    if (decision.applyToAll) sticky = decision
                    skipped++
                }
                ConflictDecision.CancelBatch ->
                    return Result(moved, replaced, skipped, cancelled = true)
            }
        }
        return Result(moved, replaced, skipped, cancelled = false)
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "com.a42r.mdrender.ui.browser.MoveConflictWalkerTest"`
Expected: BUILD SUCCESSFUL, 11 tests passed.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/ui/browser/MoveConflictWalker.kt \
        app/src/test/java/com/a42r/mdrender/ui/browser/MoveConflictWalkerTest.kt
git commit -m "feat: MoveConflictWalker with replace/skip/apply-to-all semantics"
```

---

### Task 3: BrowserViewModel wiring

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/ui/browser/BrowserViewModel.kt`

**Interfaces:**
- Consumes: Task 1's `FileRepository.findByName`, `FolderRepository.getFolder`/`siblingNameExists`; Task 2's `MoveConflictWalker`/`ConflictDecision`.
- Produces (Task 4 consumes verbatim):
  - `data class MoveConflict(val file: FileEntity, val targetFolderName: String, val remaining: Int)`
  - `fun moveFilesResolvingConflicts(ids: Collection<Long>, targetFolderId: Long?)`
  - `val moveConflict: StateFlow<MoveConflict?>`
  - `fun resolveMoveConflict(decision: ConflictDecision)`
  - `val userMessage: SharedFlow<String>`
  - `suspend fun fileNameExists(folderId: Long?, name: String, excludeId: Long): Boolean`
  - `suspend fun folderNameExists(parentId: Long?, name: String, excludeId: Long? = null): Boolean`
  - `fun renameFolder(id: Long, newName: String)`
  - `fun moveFolder(id, targetFolderId)` — now guarded (existing signature kept)
  - REMOVED: `fun moveFile(...)`, `fun moveFiles(...)` (Task 4 removes their call sites in the same breath; the tree only compiles fully again after Task 4 — see Step 2)

- [ ] **Step 1: Rework the ViewModel**

In `app/src/main/java/com/a42r/mdrender/ui/browser/BrowserViewModel.kt`:

Add import:

```kotlin
import kotlinx.coroutines.CompletableDeferred
```

Add below the `MoveTarget` data class (top level, after line ~43):

```kotlin
/** A move name-conflict currently awaiting the user's decision. */
data class MoveConflict(
    val file: FileEntity,
    val targetFolderName: String,
    val remaining: Int
)
```

DELETE the old methods:

```kotlin
    fun moveFile(id: Long, targetFolderId: Long?) { ... }
    fun moveFiles(ids: Set<Long>, targetFolderId: Long?) { ... }
```

Add in their place:

```kotlin
    // --- Move with conflict resolution ------------------------------------

    /** Non-null while the Replace/Skip dialog should be showing. */
    private val _moveConflict = MutableStateFlow<MoveConflict?>(null)
    val moveConflict: StateFlow<MoveConflict?> = _moveConflict.asStateFlow()

    /** One-off user-facing notices (batch summaries, blocked folder moves). */
    private val _userMessage = MutableSharedFlow<String>()
    val userMessage: SharedFlow<String> = _userMessage.asSharedFlow()

    private var conflictAnswer: CompletableDeferred<ConflictDecision>? = null

    private val walker = MoveConflictWalker(
        findByName = { folderId, name -> fileRepository.findByName(folderId, name) },
        moveFile = { id, folderId -> fileRepository.moveFile(id, folderId) },
        deleteFile = { id -> fileRepository.deleteFile(id) }
    )

    /** Moves [ids] into [targetFolderId], prompting Replace/Skip per name
     *  conflict. Used by both the single-file and multi-select Move dialogs. */
    fun moveFilesResolvingConflicts(ids: Collection<Long>, targetFolderId: Long?) {
        viewModelScope.launch {
            val files = ids.mapNotNull { fileRepository.getFileMetadata(it) }
            val targetName = folderDisplayName(targetFolderId)
            val result = walker.run(files, targetFolderId) { file, remaining ->
                val answer = CompletableDeferred<ConflictDecision>()
                conflictAnswer = answer
                _moveConflict.value = MoveConflict(file, targetName, remaining)
                try {
                    answer.await()
                } finally {
                    _moveConflict.value = null
                }
            }
            val summary = buildString {
                append("Moved ${result.moved}")
                if (result.replaced > 0) append(", replaced ${result.replaced}")
                if (result.skipped > 0) append(", skipped ${result.skipped}")
            }
            _userMessage.emit(summary)
        }
    }

    /** Called by the conflict dialog's buttons (and its dismiss = CancelBatch). */
    fun resolveMoveConflict(decision: ConflictDecision) {
        conflictAnswer?.takeIf { !it.isCompleted }?.complete(decision)
    }

    private suspend fun folderDisplayName(folderId: Long?): String =
        folderId?.let { folderRepository.getFolder(it)?.name } ?: "Home"

    // --- Name availability checks (rename / create dialogs) ---------------

    /** True when another file in [folderId] already uses [name] (exact match). */
    suspend fun fileNameExists(folderId: Long?, name: String, excludeId: Long): Boolean =
        fileRepository.findByName(folderId, name)?.let { it.id != excludeId } ?: false

    /** True when a sibling folder already uses [name] (case-insensitive). */
    suspend fun folderNameExists(parentId: Long?, name: String, excludeId: Long? = null): Boolean =
        folderRepository.siblingNameExists(parentId, name, excludeId)

    fun renameFolder(id: Long, newName: String) {
        viewModelScope.launch {
            folderRepository.renameFolder(id, newName)
        }
    }
```

REPLACE the existing `moveFolder` (currently an unguarded pass-through) with:

```kotlin
    /** Blocked when the destination already has a same-named sibling. */
    fun moveFolder(id: Long, targetFolderId: Long?) {
        viewModelScope.launch {
            val folder = folderRepository.getFolder(id) ?: return@launch
            if (folderRepository.siblingNameExists(targetFolderId, folder.name, excludeId = id)) {
                _userMessage.emit(
                    "A folder named \"${folder.name}\" already exists in \"${folderDisplayName(targetFolderId)}\""
                )
                return@launch
            }
            folderRepository.moveFolder(id, targetFolderId)
        }
    }
```

- [ ] **Step 2: Verify the ViewModel and tests compile**

`FolderBrowserScreen.kt` still calls the removed `moveFile`/`moveFiles` until Task 4, so compile just the unit-test tree (which does not compile the screen? it does — Android unit tests compile main sources). Therefore: expect FULL compile to fail at the two screen call sites ONLY; that is the expected intermediate state. Run:

Run: `./gradlew compileDebugKotlin 2>&1 | grep -E "error|FAILED" | head`
Expected: errors ONLY in `FolderBrowserScreen.kt` referencing `moveFile`/`moveFiles` (lines ~629, ~642, ~724, ~742). Any other error = fix before proceeding.

To keep the branch green per-commit, Task 3 and Task 4's screen call-site switch are committed TOGETHER — do NOT commit yet. Proceed directly to Task 4 in the same working tree. (Implementer note: if you are executing only Task 3, stop after Step 2 and report; the controller will chain Task 4.)

---

### Task 4: Screen wiring, dialogs, and device verification

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/ui/browser/FolderBrowserScreen.kt`

**Interfaces:**
- Consumes: everything Task 3 produces, plus `ConflictDecision` from Task 2.

- [ ] **Step 1: Switch the move-dialog call sites**

In the multi-select move dialog (`FolderBrowserScreen.kt:616-653`), replace BOTH `viewModel.moveFiles(idsToMove, null)` and `viewModel.moveFiles(idsToMove, target.folder.id)` with:

```kotlin
                                viewModel.moveFilesResolvingConflicts(idsToMove, null)
```
```kotlin
                                    viewModel.moveFilesResolvingConflicts(idsToMove, target.folder.id)
```
(keeping the existing `selectedIds = emptySet()` and `moveMulti = false` lines).

In the single-file move dialog (`FolderBrowserScreen.kt:712-757`), replace `viewModel.moveFile(file.id, null)` and `viewModel.moveFile(file.id, target.folder.id)` with:

```kotlin
                                viewModel.moveFilesResolvingConflicts(listOf(file.id), null)
```
```kotlin
                                    viewModel.moveFilesResolvingConflicts(listOf(file.id), target.folder.id)
```

- [ ] **Step 2: Collect userMessage and moveConflict state**

Near the other `collectAsStateWithLifecycle()` calls (after `shareInProgress`), add:

```kotlin
    val moveConflict by viewModel.moveConflict.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
```

Add import `kotlinx.coroutines.launch` (for `scope.launch`).

Next to the existing share collectors, add:

```kotlin
    LaunchedEffect(Unit) {
        viewModel.userMessage.collect { snackbarHostState.showSnackbar(it) }
    }
```

- [ ] **Step 3: Add the conflict dialog**

After the share warning dialog block (`pendingShare?.let { ... }`), add:

```kotlin
    // Move name-conflict dialog (Replace / Skip / Cancel + apply-to-all)
    moveConflict?.let { conflict ->
        var applyToAll by remember(conflict) { mutableStateOf(false) }
        AlertDialog(
            onDismissRequest = { viewModel.resolveMoveConflict(ConflictDecision.CancelBatch) },
            title = { Text("Name conflict") },
            text = {
                Column {
                    Text("\"${conflict.file.name}\" already exists in \"${conflict.targetFolderName}\".")
                    if (conflict.remaining > 1) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.padding(top = 8.dp)
                        ) {
                            Checkbox(checked = applyToAll, onCheckedChange = { applyToAll = it })
                            Text("Apply to all remaining conflicts")
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.resolveMoveConflict(ConflictDecision.Replace(applyToAll))
                }) { Text("Replace") }
            },
            dismissButton = {
                Row {
                    TextButton(onClick = {
                        viewModel.resolveMoveConflict(ConflictDecision.Skip(applyToAll))
                    }) { Text("Skip") }
                    TextButton(onClick = {
                        viewModel.resolveMoveConflict(ConflictDecision.CancelBatch)
                    }) { Text("Cancel") }
                }
            }
        )
    }
```

- [ ] **Step 4: Inline error in the Rename File dialog**

Replace the whole rename dialog block (`renameFile?.let { file -> ... }`, currently lines 686-709) with:

```kotlin
    // Rename dialog
    renameFile?.let { file ->
        var renameError by remember(file) { mutableStateOf(false) }
        AlertDialog(
            onDismissRequest = { renameFile = null },
            title = { Text("Rename File") },
            text = {
                Column {
                    OutlinedTextField(
                        value = renameText,
                        onValueChange = { renameText = it; renameError = false },
                        label = { Text("File name") },
                        singleLine = true,
                        isError = renameError
                    )
                    if (renameError) {
                        Text(
                            "A file with this name already exists",
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall
                        )
                    }
                }
            },
            confirmButton = {
                TextButton(
                    enabled = renameText.isNotBlank(),
                    onClick = {
                        val newName = renameText.trim()
                        scope.launch {
                            if (viewModel.fileNameExists(file.folderId, newName, excludeId = file.id)) {
                                renameError = true
                            } else {
                                viewModel.renameFile(file.id, newName)
                                renameFile = null
                            }
                        }
                    }
                ) { Text("Rename") }
            },
            dismissButton = { TextButton(onClick = { renameFile = null }) { Text("Cancel") } }
        )
    }
```

- [ ] **Step 5: Inline error in the New Folder dialog**

Replace the New Folder dialog block (`if (showNewFolderDialog) { ... }`, currently lines 760-783) with:

```kotlin
    // New Folder Dialog
    if (showNewFolderDialog) {
        var folderError by remember { mutableStateOf(false) }
        AlertDialog(
            onDismissRequest = { showNewFolderDialog = false },
            title = { Text("New Folder") },
            text = {
                Column {
                    OutlinedTextField(
                        value = newFolderName,
                        onValueChange = { newFolderName = it; folderError = false },
                        label = { Text("Folder name") },
                        singleLine = true,
                        isError = folderError
                    )
                    if (folderError) {
                        Text(
                            "A folder with this name already exists",
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall
                        )
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    val name = newFolderName.trim()
                    if (name.isNotBlank()) {
                        scope.launch {
                            if (viewModel.folderNameExists(uiState.currentFolderId, name)) {
                                folderError = true
                            } else {
                                viewModel.createFolder(name)
                                newFolderName = ""
                                showNewFolderDialog = false
                            }
                        }
                    }
                }) { Text("Create") }
            },
            dismissButton = { TextButton(onClick = { showNewFolderDialog = false }) { Text("Cancel") } }
        )
    }
```

- [ ] **Step 6: Folder Rename menu entry and dialog**

Add screen state next to `moveFolderState` (~line 49):

```kotlin
    var renameFolderState by remember { mutableStateOf<com.a42r.mdrender.data.entity.FolderEntity?>(null) }
    var renameFolderText by remember { mutableStateOf("") }
```

In the folder context menu, add between the "Open" and "Move" ListItems:

```kotlin
                ListItem(
                    headlineContent = { Text("Rename") },
                    leadingContent = { Icon(Icons.Filled.Edit, "Rename") },
                    modifier = Modifier.clickable {
                        folderMenu = null
                        renameFolderText = folder.name
                        renameFolderState = folder
                    }
                )
```

After the folder move dialog block, add:

```kotlin
    // Rename Folder dialog
    renameFolderState?.let { folder ->
        var folderRenameError by remember(folder) { mutableStateOf(false) }
        AlertDialog(
            onDismissRequest = { renameFolderState = null },
            title = { Text("Rename Folder") },
            text = {
                Column {
                    OutlinedTextField(
                        value = renameFolderText,
                        onValueChange = { renameFolderText = it; folderRenameError = false },
                        label = { Text("Folder name") },
                        singleLine = true,
                        isError = folderRenameError
                    )
                    if (folderRenameError) {
                        Text(
                            "A folder with this name already exists",
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall
                        )
                    }
                }
            },
            confirmButton = {
                TextButton(
                    enabled = renameFolderText.isNotBlank(),
                    onClick = {
                        val newName = renameFolderText.trim()
                        scope.launch {
                            if (viewModel.folderNameExists(folder.parentId, newName, excludeId = folder.id)) {
                                folderRenameError = true
                            } else {
                                viewModel.renameFolder(folder.id, newName)
                                renameFolderState = null
                            }
                        }
                    }
                ) { Text("Rename") }
            },
            dismissButton = { TextButton(onClick = { renameFolderState = null }) { Text("Cancel") } }
        )
    }
```

- [ ] **Step 7: Full build, tests, install**

```bash
./gradlew compileDebugKotlin testDebugUnitTest
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Expected: BUILD SUCCESSFUL (all unit tests pass — 34 pre-existing + 15 new), install Success.

- [ ] **Step 8: Commit Tasks 3+4 together, update graph**

```bash
graphify update .
git add app/src/main/java/com/a42r/mdrender/ui/browser/BrowserViewModel.kt \
        app/src/main/java/com/a42r/mdrender/ui/browser/FolderBrowserScreen.kt
git commit -m "feat: move conflict dialog, rename collision checks, unique folder names"
```

- [ ] **Step 9: On-device manual verification (operator)**

1. Move a file onto a same-named file → conflict dialog; try Replace (target's old file gone, content is the moved file's), Skip, Cancel.
2. Multi-move with 2+ conflicts → checkbox appears; Replace-all and Skip-all each prompt once; summary snackbar counts are right.
3. Cancel mid-batch → earlier moves stand, later files untouched.
4. Move two same-named files (different folders) to one destination in one batch → second prompts.
5. Rename a file to an existing name → inline error, dialog stays open; rename to its own name → allowed.
6. New Folder with an existing sibling name (try different case too) → inline error.
7. Folder menu → Rename: rename to existing sibling name (case-insensitive) → inline error; case-only rename of itself ("work" → "Work") → allowed.
8. Move a folder into a parent with a same-named sibling → blocked + snackbar.
9. Dismiss the conflict dialog by tapping outside → batch cancels (nothing replaced).

---

## Self-Review Notes

- Spec coverage: Part A detection/walker/dialog/summary (Tasks 1, 2, 3, 4 steps 1-3), Part B rename inline error (Task 4 step 4), Part C create/rename/move enforcement incl. new folder-rename UI (Tasks 1, 3, 4 steps 5-6), grandfathering (no migration task — intentional), LocalSend case-insensitive inheritance (Task 1 FolderDao change; no extra code).
- Green-branch caveat: Task 3 alone doesn't compile the app (removed methods still referenced by the screen); the plan makes Tasks 3+4 a single commit and says so explicitly in both tasks.
- Type consistency: `ConflictDecision` variants, `MoveConflict(file, targetFolderName, remaining)`, `moveFilesResolvingConflicts`, `resolveMoveConflict`, `userMessage`, `fileNameExists(folderId, name, excludeId)`, `folderNameExists(parentId, name, excludeId)` match across Tasks 2/3/4.
