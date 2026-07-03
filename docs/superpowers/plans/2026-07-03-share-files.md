# Share Files from Browser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Share one or multiple files from the folder browser via the Android share sheet, with a confirmation warning when any shared file lives in a hidden folder.

**Architecture:** Decrypt-to-cache (spec Option A): files are decrypted into `cacheDir/share/` and exposed through an androidx `FileProvider`; the directory is wiped on every app start and before every share, so plaintext exists in exactly one place with crash-safe cleanup. A pure `SharePlan` decision function partitions a selection into hidden/visible; `BrowserViewModel` drives the confirm dialog and hands staging to a new `ShareOutManager`.

**Tech Stack:** Kotlin, Jetpack Compose (Material 3), Hilt, androidx.core `FileProvider`, JUnit4 + mockito-kotlin + kotlinx-coroutines-test.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-03-share-files-design.md`.
- Plaintext may exist **only** under `cacheDir/share/`; wiped in `MDRenderApplication.onCreate` and at the start of every share.
- Files only, browser entry points only (multi-select top bar + file long-press menu).
- Hidden-item dialog buttons: **Share all** / **Share non-hidden only** (mixed selections only) / **Cancel**; "Share" instead of "Share all" when every item is hidden.
- Decrypt failure for any file aborts the whole share (no partial share) and shows a snackbar naming the file.
- Work on branch `feature/share-files`; commit per task; no push, no merge to master without operator instruction.
- Do not add `Co-Authored-By` footers to commits.
- After code changes run `graphify update .` (repo rule) — do this once at the end of the final task, not per task.

---

### Task 0: Branch setup

**Files:** none

- [ ] **Step 1: Create the working branch**

```bash
cd /home/philg/src/AndroidStudioProjects/MDRender
git checkout -b feature/share-files
```

Expected: `Switched to a new branch 'feature/share-files'`. All subsequent task commits land here.

---

### Task 1: SharePlan decision logic

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/share/SharePlan.kt`
- Test: `app/src/test/java/com/a42r/mdrender/share/SharePlanTest.kt`

**Interfaces:**
- Consumes: `com.a42r.mdrender.data.entity.FileEntity` (existing Room entity).
- Produces: `SharePlan.of(files: List<FileEntity>, isHidden: (FileEntity) -> Boolean): SharePlan` returning `SharePlan.ShareNow(files)`, `SharePlan.NeedsConfirmation(visible, hidden)`, or `SharePlan.None`. Task 4 stores `NeedsConfirmation` as the pending-dialog state.

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/com/a42r/mdrender/share/SharePlanTest.kt`:

```kotlin
package com.a42r.mdrender.share

import com.a42r.mdrender.data.entity.FileEntity
import org.junit.Assert.*
import org.junit.Test

class SharePlanTest {

    private fun file(id: Long, folderId: Long?) = FileEntity(
        id = id, folderId = folderId, name = "f$id.md",
        mimeType = "text/markdown", encryptedBlob = byteArrayOf(), fileSize = 0L
    )

    @Test
    fun emptySelection_isNone() {
        assertEquals(SharePlan.None, SharePlan.of(emptyList()) { false })
    }

    @Test
    fun noHiddenItems_sharesImmediately() {
        val files = listOf(file(1, null), file(2, 10))
        val plan = SharePlan.of(files) { false }
        assertEquals(SharePlan.ShareNow(files), plan)
    }

    @Test
    fun mixedSelection_needsConfirmationWithCorrectPartition() {
        val visible = file(1, null)
        val hidden = file(2, 20)
        val plan = SharePlan.of(listOf(visible, hidden)) { it.folderId == 20L }
        assertEquals(
            SharePlan.NeedsConfirmation(visible = listOf(visible), hidden = listOf(hidden)),
            plan
        )
    }

    @Test
    fun allHidden_needsConfirmationWithEmptyVisible() {
        val files = listOf(file(1, 20), file(2, 20))
        val plan = SharePlan.of(files) { true }
        assertEquals(
            SharePlan.NeedsConfirmation(visible = emptyList(), hidden = files),
            plan
        )
    }
}
```

Note: `FileEntity.equals` compares by `id` only, so `assertEquals` on lists of entities compares identity by id — fine for these tests.

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "com.a42r.mdrender.share.SharePlanTest"`
Expected: compilation FAILURE — `unresolved reference: SharePlan`.

- [ ] **Step 3: Write the implementation**

Create `app/src/main/java/com/a42r/mdrender/share/SharePlan.kt`:

```kotlin
package com.a42r.mdrender.share

import com.a42r.mdrender.data.entity.FileEntity

/** Decision for a share request: share immediately, ask about hidden items
 *  first, or nothing to do. Pure logic — see BrowserViewModel for the flow. */
sealed interface SharePlan {
    /** No hidden items involved — open the share sheet directly. */
    data class ShareNow(val files: List<FileEntity>) : SharePlan

    /** At least one item is in a hidden folder — confirm before sharing.
     *  [visible] may be empty (everything selected is hidden). */
    data class NeedsConfirmation(
        val visible: List<FileEntity>,
        val hidden: List<FileEntity>
    ) : SharePlan

    /** Empty selection. */
    object None : SharePlan

    companion object {
        fun of(files: List<FileEntity>, isHidden: (FileEntity) -> Boolean): SharePlan {
            if (files.isEmpty()) return None
            val (hidden, visible) = files.partition(isHidden)
            return if (hidden.isEmpty()) ShareNow(files)
            else NeedsConfirmation(visible = visible, hidden = hidden)
        }
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "com.a42r.mdrender.share.SharePlanTest"`
Expected: BUILD SUCCESSFUL, 4 tests passed.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/share/SharePlan.kt \
        app/src/test/java/com/a42r/mdrender/share/SharePlanTest.kt
git commit -m "feat: SharePlan hidden-item partition logic for sharing"
```

---

### Task 2: Share naming and MIME helpers

**Files:**
- Create: `app/src/main/java/com/a42r/mdrender/share/ShareOutManager.kt` (companion helpers only in this task)
- Test: `app/src/test/java/com/a42r/mdrender/share/ShareOutManagerHelpersTest.kt`

**Interfaces:**
- Produces: `ShareOutManager.dedupeNames(names: List<String>): List<String>` and `ShareOutManager.commonMimeType(mimes: List<String>): String` (companion functions). Task 3 uses them when staging files and typing the intent.

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/com/a42r/mdrender/share/ShareOutManagerHelpersTest.kt`:

```kotlin
package com.a42r.mdrender.share

import org.junit.Assert.assertEquals
import org.junit.Test

class ShareOutManagerHelpersTest {

    @Test
    fun dedupeNames_uniqueNamesUnchanged() {
        assertEquals(
            listOf("a.md", "b.md"),
            ShareOutManager.dedupeNames(listOf("a.md", "b.md"))
        )
    }

    @Test
    fun dedupeNames_duplicatesGetNumericSuffixBeforeExtension() {
        assertEquals(
            listOf("a.md", "a (1).md", "a (2).md"),
            ShareOutManager.dedupeNames(listOf("a.md", "a.md", "a.md"))
        )
    }

    @Test
    fun dedupeNames_extensionlessDuplicates() {
        assertEquals(
            listOf("notes", "notes (1)"),
            ShareOutManager.dedupeNames(listOf("notes", "notes"))
        )
    }

    @Test
    fun dedupeNames_suffixSkipsAlreadyTakenName() {
        assertEquals(
            listOf("a.md", "a (1).md", "a (2).md"),
            ShareOutManager.dedupeNames(listOf("a.md", "a (1).md", "a.md"))
        )
    }

    @Test
    fun commonMimeType_singleTypeIsExact() {
        assertEquals("text/markdown",
            ShareOutManager.commonMimeType(listOf("text/markdown", "text/markdown")))
    }

    @Test
    fun commonMimeType_samePrimaryTypeWildcardsSubtype() {
        assertEquals("image/*",
            ShareOutManager.commonMimeType(listOf("image/png", "image/jpeg")))
    }

    @Test
    fun commonMimeType_mixedPrimaryTypesIsStarStar() {
        assertEquals("*/*",
            ShareOutManager.commonMimeType(listOf("image/png", "text/plain")))
    }

    @Test
    fun commonMimeType_emptyIsStarStar() {
        assertEquals("*/*", ShareOutManager.commonMimeType(emptyList()))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "com.a42r.mdrender.share.ShareOutManagerHelpersTest"`
Expected: compilation FAILURE — `unresolved reference: ShareOutManager`.

- [ ] **Step 3: Write the implementation**

Create `app/src/main/java/com/a42r/mdrender/share/ShareOutManager.kt` with only the class shell and companion (staging methods come in Task 3):

```kotlin
package com.a42r.mdrender.share

import android.content.Context
import com.a42r.mdrender.data.repository.FileRepository
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/** Stages decrypted copies of files for outbound sharing.
 *
 *  Plaintext only ever exists under cacheDir/share/ — wiped on every app
 *  start (crash-safe) and again before each share. */
@Singleton
class ShareOutManager @Inject constructor(
    @ApplicationContext private val context: Context,
    private val fileRepository: FileRepository
) {
    companion object {
        const val SHARE_DIR = "share"

        /** On-disk names for staged files: duplicates get "name (1).ext". */
        fun dedupeNames(names: List<String>): List<String> {
            val used = mutableSetOf<String>()
            return names.map { name ->
                if (used.add(name)) return@map name
                val dot = name.lastIndexOf('.')
                val base = if (dot > 0) name.substring(0, dot) else name
                val ext = if (dot > 0) name.substring(dot) else ""
                var n = 1
                var candidate = "$base ($n)$ext"
                while (!used.add(candidate)) {
                    n++
                    candidate = "$base ($n)$ext"
                }
                candidate
            }
        }

        /** Narrowest MIME type covering [mimes]: exact match, "type/*", or "*/*". */
        fun commonMimeType(mimes: List<String>): String {
            if (mimes.isEmpty()) return "*/*"
            if (mimes.distinct().size == 1) return mimes[0]
            val primaries = mimes.map { it.substringBefore('/') }.distinct()
            return if (primaries.size == 1) "${primaries[0]}/*" else "*/*"
        }
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "com.a42r.mdrender.share.ShareOutManagerHelpersTest"`
Expected: BUILD SUCCESSFUL, 8 tests passed.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/share/ShareOutManager.kt \
        app/src/test/java/com/a42r/mdrender/share/ShareOutManagerHelpersTest.kt
git commit -m "feat: share file-name dedupe and common-MIME helpers"
```

---

### Task 3: Staging, FileProvider, and startup wipe

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/share/ShareOutManager.kt` (add `clearShareCache`, `stage`, `StageResult`)
- Create: `app/src/main/res/xml/share_paths.xml`
- Modify: `app/src/main/AndroidManifest.xml` (add `<provider>` inside `<application>`)
- Modify: `app/src/main/java/com/a42r/mdrender/MDRenderApplication.kt` (startup wipe)

**Interfaces:**
- Consumes: `FileRepository.getDecryptedContent(id: Long): Pair<ByteArray, String>?` (existing), Task 2 helpers.
- Produces:
  - `ShareOutManager.clearShareCache()` — deletes `cacheDir/share/` recursively.
  - `suspend ShareOutManager.stage(files: List<FileEntity>): StageResult` where `sealed interface StageResult { data class Ready(val intent: Intent) : StageResult; data class Failed(val fileName: String) : StageResult }`. `Ready.intent` is a chooser intent, ready for `startActivity`. Task 4 calls both.

No unit test for this task — it is Android-framework glue (`File`, `Uri`, `Intent`, `FileProvider`); verified by compilation here and end-to-end on device in Task 5.

- [ ] **Step 1: Add staging code to ShareOutManager**

Add imports to `ShareOutManager.kt`:

```kotlin
import android.content.ClipData
import android.content.Intent
import android.net.Uri
import androidx.core.content.FileProvider
import com.a42r.mdrender.data.entity.FileEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
```

Add inside the class body (above `companion object`):

```kotlin
    /** Outcome of staging: a launchable chooser intent, or the name of the
     *  file whose decryption failed (nothing is shared partially). */
    sealed interface StageResult {
        data class Ready(val intent: Intent) : StageResult
        data class Failed(val fileName: String) : StageResult
    }

    private val shareDir get() = File(context.cacheDir, SHARE_DIR)

    /** Remove all staged plaintext. Called on app start and before staging. */
    fun clearShareCache() {
        shareDir.deleteRecursively()
    }

    /** Decrypts [files] into cacheDir/share/ and builds a chooser intent.
     *  Any decryption failure wipes the cache again and aborts. */
    suspend fun stage(files: List<FileEntity>): StageResult = withContext(Dispatchers.IO) {
        clearShareCache()
        shareDir.mkdirs()
        val names = dedupeNames(files.map { it.name })
        val uris = ArrayList<Uri>(files.size)
        for ((i, file) in files.withIndex()) {
            val bytes = fileRepository.getDecryptedContent(file.id)?.first
            if (bytes == null) {
                clearShareCache()
                return@withContext StageResult.Failed(file.name)
            }
            val staged = File(shareDir, names[i])
            staged.writeBytes(bytes)
            uris += FileProvider.getUriForFile(
                context, "${context.packageName}.fileprovider", staged
            )
        }
        val send = if (uris.size == 1) {
            Intent(Intent.ACTION_SEND).apply { putExtra(Intent.EXTRA_STREAM, uris[0]) }
        } else {
            Intent(Intent.ACTION_SEND_MULTIPLE).apply {
                putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris)
            }
        }
        send.type = commonMimeType(files.map { it.mimeType })
        // ClipData so the chooser target gets read grants on every URI.
        send.clipData = ClipData.newUri(context.contentResolver, "share", uris[0]).apply {
            for (j in 1 until uris.size) addItem(ClipData.Item(uris[j]))
        }
        send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        val chooser = Intent.createChooser(send, null)
        chooser.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        StageResult.Ready(chooser)
    }
```

- [ ] **Step 2: Create the FileProvider paths resource**

Create `app/src/main/res/xml/share_paths.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<paths>
    <cache-path name="share" path="share/" />
</paths>
```

- [ ] **Step 3: Declare the FileProvider in the manifest**

In `app/src/main/AndroidManifest.xml`, add inside `<application>` after the `ShareReceiverActivity` `</activity>` close tag:

```xml
        <provider
            android:name="androidx.core.content.FileProvider"
            android:authorities="${applicationId}.fileprovider"
            android:exported="false"
            android:grantUriPermissions="true">
            <meta-data
                android:name="android.support.FILE_PROVIDER_PATHS"
                android:resource="@xml/share_paths" />
        </provider>
```

- [ ] **Step 4: Wipe the share cache on app start**

In `MDRenderApplication.kt`, add the injected field next to the existing ones:

```kotlin
    @Inject lateinit var shareOutManager: ShareOutManager
```

with import `com.a42r.mdrender.share.ShareOutManager` and `kotlin.concurrent.thread`. In `onCreate()`, directly after `instance = this`:

```kotlin
        // Crash-safe plaintext cleanup: any decrypted share copies left by an
        // unexpected shutdown are removed before anything else runs.
        thread { shareOutManager.clearShareCache() }
```

- [ ] **Step 5: Verify it compiles and existing tests pass**

Run: `./gradlew compileDebugKotlin testDebugUnitTest`
Expected: BUILD SUCCESSFUL, all unit tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/share/ShareOutManager.kt \
        app/src/main/res/xml/share_paths.xml \
        app/src/main/AndroidManifest.xml \
        app/src/main/java/com/a42r/mdrender/MDRenderApplication.kt
git commit -m "feat: decrypt-to-cache share staging with FileProvider and startup wipe"
```

---

### Task 4: BrowserViewModel share flow

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/ui/browser/BrowserViewModel.kt`

**Interfaces:**
- Consumes: `SharePlan.of(...)` (Task 1), `ShareOutManager.stage(...)` / `StageResult` (Task 3), existing `FolderRepository.isInHiddenTree(folderId: Long?): Boolean`, `FileRepository.getFileMetadata(id: Long): FileEntity?`.
- Produces (used by Task 5's UI):
  - `fun requestShare(ids: Collection<Long>)`
  - `val pendingShare: StateFlow<SharePlan.NeedsConfirmation?>`
  - `fun confirmShareAll()`, `fun confirmShareVisibleOnly()`, `fun cancelShare()`
  - `val shareIntent: SharedFlow<Intent>` — chooser intents to `startActivity`
  - `val shareError: SharedFlow<String>` — snackbar messages
  - `val shareInProgress: StateFlow<Boolean>` — true while decrypting/staging

- [ ] **Step 1: Add the share flow to the ViewModel**

Add imports to `BrowserViewModel.kt`:

```kotlin
import android.content.Intent
import com.a42r.mdrender.share.SharePlan
import com.a42r.mdrender.share.ShareOutManager
```

Add `shareOutManager` to the constructor, after `localSendPrefs`:

```kotlin
    private val localSendPrefs: LocalSendPrefs,
    private val shareOutManager: ShareOutManager,
    @ApplicationContext private val context: Context
```

Add to the class body (e.g. after the `undoDelete` declarations):

```kotlin
    // --- Sharing ---------------------------------------------------------

    /** Non-null while the hidden-item warning dialog should be showing. */
    private val _pendingShare = MutableStateFlow<SharePlan.NeedsConfirmation?>(null)
    val pendingShare: StateFlow<SharePlan.NeedsConfirmation?> = _pendingShare.asStateFlow()

    /** Chooser intents ready to launch. */
    private val _shareIntent = MutableSharedFlow<Intent>()
    val shareIntent: SharedFlow<Intent> = _shareIntent.asSharedFlow()

    /** Share failures, for the snackbar. */
    private val _shareError = MutableSharedFlow<String>()
    val shareError: SharedFlow<String> = _shareError.asSharedFlow()

    /** True while files are being decrypted and staged. */
    private val _shareInProgress = MutableStateFlow(false)
    val shareInProgress: StateFlow<Boolean> = _shareInProgress.asStateFlow()

    /** Entry point for both the top-bar Share action and the file menu.
     *  Hidden items divert to the confirmation dialog via [pendingShare]. */
    fun requestShare(ids: Collection<Long>) {
        viewModelScope.launch {
            val files = ids.mapNotNull { fileRepository.getFileMetadata(it) }
            val hiddenByFolder = files.map { it.folderId }.distinct()
                .associateWith { folderRepository.isInHiddenTree(it) }
            when (val plan = SharePlan.of(files) { hiddenByFolder[it.folderId] == true }) {
                is SharePlan.ShareNow -> stageAndShare(plan.files)
                is SharePlan.NeedsConfirmation -> _pendingShare.value = plan
                SharePlan.None -> Unit
            }
        }
    }

    fun confirmShareAll() {
        val pending = _pendingShare.value ?: return
        _pendingShare.value = null
        stageAndShare(pending.visible + pending.hidden)
    }

    fun confirmShareVisibleOnly() {
        val pending = _pendingShare.value ?: return
        _pendingShare.value = null
        if (pending.visible.isNotEmpty()) stageAndShare(pending.visible)
    }

    fun cancelShare() {
        _pendingShare.value = null
    }

    private fun stageAndShare(files: List<FileEntity>) {
        if (files.isEmpty()) return
        viewModelScope.launch {
            _shareInProgress.value = true
            try {
                when (val result = shareOutManager.stage(files)) {
                    is ShareOutManager.StageResult.Ready -> _shareIntent.emit(result.intent)
                    is ShareOutManager.StageResult.Failed ->
                        _shareError.emit("Couldn't prepare \"${result.fileName}\" — nothing was shared")
                }
            } finally {
                _shareInProgress.value = false
            }
        }
    }
```

- [ ] **Step 2: Verify it compiles and existing tests pass**

Run: `./gradlew compileDebugKotlin testDebugUnitTest`
Expected: BUILD SUCCESSFUL. (The hidden-partition decision is covered by `SharePlanTest`; this task is coroutine/DI glue over it.)

- [ ] **Step 3: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/ui/browser/BrowserViewModel.kt
git commit -m "feat: share request flow with hidden-item confirmation in BrowserViewModel"
```

---

### Task 5: Browser UI wiring and device verification

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/ui/browser/FolderBrowserScreen.kt`

**Interfaces:**
- Consumes: everything Task 4 produces; `SharePlan.NeedsConfirmation` fields `visible` / `hidden` (Task 1).

- [ ] **Step 1: Collect share state and launch chooser intents**

In `FolderBrowserScreen.kt` add imports:

```kotlin
import androidx.compose.ui.platform.LocalContext
import com.a42r.mdrender.share.SharePlan
```

(`Icons.Filled.*` is already wildcard-imported.)

Near the other `collectAsStateWithLifecycle()` calls (after `localSendEnabled`), add:

```kotlin
    val pendingShare by viewModel.pendingShare.collectAsStateWithLifecycle()
    val shareInProgress by viewModel.shareInProgress.collectAsStateWithLifecycle()
    val context = LocalContext.current
```

Next to the existing undo-delete collector `LaunchedEffect`, add:

```kotlin
    // Launch the system share sheet when staging completes; leaving selection
    // mode afterwards since the action is done.
    LaunchedEffect(Unit) {
        viewModel.shareIntent.collect { intent ->
            context.startActivity(intent)
            selectedIds = emptySet()
        }
    }
    LaunchedEffect(Unit) {
        viewModel.shareError.collect { snackbarHostState.showSnackbar(it) }
    }
```

- [ ] **Step 2: Add the Share action to the selection top bar**

In the `if (selectionMode)` `TopAppBar` `actions` block, add BEFORE the Move `IconButton`:

```kotlin
                        IconButton(onClick = { viewModel.requestShare(selectedIds) }) {
                            Icon(Icons.Filled.Share, contentDescription = "Share")
                        }
```

- [ ] **Step 3: Add Share to the file long-press menu**

In the `menuFile?.let { file -> ... }` bottom sheet, add a `ListItem` between "Open" and "Rename":

```kotlin
                ListItem(
                    headlineContent = { Text("Share") },
                    leadingContent = { Icon(Icons.Filled.Share, "Share") },
                    modifier = Modifier.clickable {
                        menuFile = null
                        viewModel.requestShare(listOf(file.id))
                    }
                )
```

- [ ] **Step 4: Add the hidden-item confirmation dialog**

Near the other dialogs (e.g. after the multi-delete confirmation), add:

```kotlin
    // Hidden-item share warning
    pendingShare?.let { pending ->
        val n = pending.hidden.size
        val warning = if (n == 1)
            "One of the items you are sharing is stored in a hidden folder. " +
            "It is sensitive in nature and is normally hidden for security purposes."
        else
            "$n of the items you are sharing are stored in a hidden folder. " +
            "These items are sensitive in nature and are normally hidden for security purposes."
        AlertDialog(
            onDismissRequest = { viewModel.cancelShare() },
            icon = { Icon(Icons.Filled.Warning, contentDescription = null) },
            title = { Text("Share hidden items?") },
            text = {
                Text(warning + "\n\n" + pending.hidden.joinToString("\n") { "• ${it.name}" })
            },
            confirmButton = {
                TextButton(onClick = { viewModel.confirmShareAll() }) {
                    Text(if (pending.visible.isEmpty()) "Share" else "Share all")
                }
            },
            dismissButton = {
                Row {
                    if (pending.visible.isNotEmpty()) {
                        TextButton(onClick = { viewModel.confirmShareVisibleOnly() }) {
                            Text("Share non-hidden only")
                        }
                    }
                    TextButton(onClick = { viewModel.cancelShare() }) { Text("Cancel") }
                }
            }
        )
    }
```

- [ ] **Step 5: Show progress while staging**

At the top of the Scaffold content `Column` (immediately before `BreadcrumbBar(...)`), add:

```kotlin
            if (shareInProgress) {
                LinearProgressIndicator(Modifier.fillMaxWidth())
            }
```

- [ ] **Step 6: Build, install, and verify on device**

```bash
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Expected: BUILD SUCCESSFUL, install Success. Then verify manually on the device (operator or agent with operator's help — auth gate requires the operator):

1. Long-press a non-hidden file → Share → share sheet opens directly, file received intact by target app.
2. Select 2+ non-hidden files → Share icon in top bar → `ACTION_SEND_MULTIPLE` sheet, both files received.
3. Reveal hidden folders (12 title taps), select one hidden + one non-hidden file → Share → dialog shows warning + hidden file name + all three buttons; verify each button behaves (all / non-hidden only / cancel).
4. Share a file inside a hidden folder alone → dialog shows "Share" / "Cancel" only.
5. After a share, force-stop the app (`adb shell am force-stop com.a42r.mdrender`), relaunch, then check the plaintext is gone:
   `adb shell run-as com.a42r.mdrender ls cache/share` → expected: `No such file or directory`.

- [ ] **Step 7: Update the knowledge graph and commit**

```bash
graphify update .
git add app/src/main/java/com/a42r/mdrender/ui/browser/FolderBrowserScreen.kt
git commit -m "feat: share files from browser with hidden-item warning dialog"
```

(`graphify-out/` is untracked; leave it that way unless the operator says otherwise.)

---

## Self-Review Notes

- Spec coverage: entry points (Task 5 steps 2–3), hidden gate + three-button dialog (Tasks 1, 4, 5), Option A staging + FileProvider (Task 3), startup + pre-share wipe (Task 3), abort-on-decrypt-failure with named snackbar (Tasks 3–4), name dedupe and MIME selection (Task 2), unit tests for partition logic (Task 1), manual device checklist (Task 5 step 6). No gaps found.
- Type consistency: `StageResult.Ready/Failed`, `SharePlan.ShareNow/NeedsConfirmation/None`, and the ViewModel API names match across tasks.
