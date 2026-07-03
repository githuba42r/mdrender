# Scroll Position Bookmark & Jump-to-Top Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remember scroll position across navigation and app restarts for markdown/text files, plus add a jump-to-top FAB button.

**Architecture:** Add a `scroll_position` column to the encrypted Room `files` table (v2→3 migration). The `ViewerViewModel` exposes existing scroll position on load and saves on navigate-away. Each viewer screen restores the position in `LaunchedEffect` after content loads and saves it via `DisposableEffect`. A `SmallFloatingActionButton` appears in the bottom-right corner when scrolled >200px.

**Tech Stack:** Jetpack Compose, Room, Kotlin coroutines, Hilt DI

## Global Constraints

- Database version must be 2→3, not destructive — use `MIGRATION_2_3`
- All encrypted blob columns untouched — new column is plain `INTEGER NOT NULL DEFAULT 0`
- No new dependencies — all API surface already in project
- Scroll position saved at most once per viewing session (on dispose), not per-pixel
- Save-on-dispose uses `rememberUpdatedState` + `DisposableEffect(Unit)` pattern, NOT `DisposableEffect(scrollState.value)` which would re-trigger on every scroll change

---

### Task 1: Data Layer — FileEntity, Room Migration, DAO, Repository, DI

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/data/entity/FileEntity.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/data/AppDatabase.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/data/dao/FileDao.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/data/repository/FileRepository.kt`
- Modify: `app/src/main/java/com/a42r/mdrender/di/DatabaseModule.kt`

**Interfaces:**
- Produces: `FileEntity.scrollPosition: Int` (default 0), `FileDao.updateScrollPosition(id, pos)`, `FileRepository.saveScrollPosition(id, pos)`, `AppDatabase.MIGRATION_2_3`, `DatabaseModule` wired with both migrations

- [ ] **Step 1: Add scrollPosition to FileEntity**

Target: `app/src/main/java/com/a42r/mdrender/data/entity/FileEntity.kt`

Add the field after `updatedAt`:

```kotlin
@ColumnInfo(name = "scroll_position") val scrollPosition: Int = 0,
```

The full file will look like (new field at end of primary constructor):

```kotlin
@Entity(
    tableName = "files",
    foreignKeys = [ForeignKey(
        entity = FolderEntity::class,
        parentColumns = ["id"],
        childColumns = ["folder_id"],
        onDelete = ForeignKey.CASCADE
    )],
    indices = [Index(value = ["folder_id"])]
)
data class FileEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "folder_id") val folderId: Long? = null,
    @ColumnInfo(name = "name") val name: String,
    @ColumnInfo(name = "mime_type") val mimeType: String,
    @ColumnInfo(name = "encrypted_blob") val encryptedBlob: ByteArray,
    @ColumnInfo(name = "encrypted_thumbnail") val encryptedThumbnail: ByteArray? = null,
    @ColumnInfo(name = "file_size") val fileSize: Long,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "updated_at") val updatedAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "scroll_position") val scrollPosition: Int = 0
) {
    override fun equals(other: Any?): Boolean = this === other || (other is FileEntity && id == other.id)
    override fun hashCode(): Int = id.hashCode()
}
```

- [ ] **Step 2: Add DAO method to update scroll position**

Target: `app/src/main/java/com/a42r/mdrender/data/dao/FileDao.kt`

Add inside the `interface FileDao` body, after the existing methods:

```kotlin
@Query("UPDATE files SET scroll_position = :pos WHERE id = :id")
suspend fun updateScrollPosition(id: Long, pos: Int)
```

Full addition (12 lines into the existing file, after the `deleteByFolderId` method):

```
    @Query("DELETE FROM files WHERE folder_id = :folderId")
    suspend fun deleteByFolderId(folderId: Long)

    // 👇 new method
    @Query("UPDATE files SET scroll_position = :pos WHERE id = :id")
    suspend fun updateScrollPosition(id: Long, pos: Int)
}
```

- [ ] **Step 3: Add repository method**

Target: `app/src/main/java/com/a42r/mdrender/data/repository/FileRepository.kt`

Add inside the `FileRepository` class, after the existing public methods:

```kotlin
suspend fun saveScrollPosition(id: Long, pos: Int) = fileDao.updateScrollPosition(id, pos)
```

- [ ] **Step 4: Create Room migration v2→v3**

Target: `app/src/main/java/com/a42r/mdrender/data/AppDatabase.kt`

Change the `@Database` annotation version from `2` to `3`. Add `MIGRATION_2_3` alongside the existing `MIGRATION_1_2`:

```kotlin
@Database(entities = [FolderEntity::class, FileEntity::class], version = 3, exportSchema = false)
abstract class AppDatabase : RoomDatabase() {
    abstract fun folderDao(): FolderDao
    abstract fun fileDao(): FileDao

    companion object {
        /** v1 → v2: add the folders.hidden flag without dropping data. */
        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE folders ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
            }
        }

        /** v2 → v3: add files.scroll_position for scroll bookmarking. */
        val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE files ADD COLUMN scroll_position INTEGER NOT NULL DEFAULT 0")
            }
        }
    }
}
```

- [ ] **Step 5: Wire MIGRATION_2_3 into DatabaseModule**

Target: `app/src/main/java/com/a42r/mdrender/di/DatabaseModule.kt`

Change the `addMigrations()` call to include both migrations:

```kotlin
@Provides
@Singleton
fun provideAppDatabase(@ApplicationContext context: Context): AppDatabase {
    return Room.databaseBuilder(
        context,
        AppDatabase::class.java,
        "mdrender.db"
    )
        .addMigrations(AppDatabase.MIGRATION_1_2, AppDatabase.MIGRATION_2_3)
        .fallbackToDestructiveMigration()
        .build()
}
```

- [ ] **Step 6: Verify compilation**

Run: `./gradlew :app:compileDebugKotlin`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 7: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/data/entity/FileEntity.kt \
       app/src/main/java/com/a42r/mdrender/data/AppDatabase.kt \
       app/src/main/java/com/a42r/mdrender/data/dao/FileDao.kt \
       app/src/main/java/com/a42r/mdrender/data/repository/FileRepository.kt \
       app/src/main/java/com/a42r/mdrender/di/DatabaseModule.kt
git commit -m "feat: add scroll_position column to files table for scroll bookmarks"
```

---

### Task 2: ViewModel — Expose initial scroll position and save method

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/ui/viewer/ViewerViewModel.kt`

**Interfaces:**
- Consumes: `fileEntity.scrollPosition`, `FileRepository.saveScrollPosition(id, pos)`
- Produces: `ViewerUiState.initialScrollPosition: Int = 0`, `ViewerViewModel.saveScrollPosition(pos: Int)`

- [ ] **Step 1: Add initialScrollPosition to ViewerUiState**

Target: `app/src/main/java/com/a42r/mdrender/ui/viewer/ViewerViewModel.kt`

Add the field:

```kotlin
data class ViewerUiState(
    val fileName: String = "",
    val mimeType: String = "",
    val markdownContent: String = "",
    val textContent: String = "",
    val imageBytes: ByteArray? = null,
    val isLoading: Boolean = true,
    val error: String? = null,
    val initialScrollPosition: Int = 0
)
```

- [ ] **Step 2: Set initialScrollPosition after load and add save method**

In the `loadContent()` function within `viewModelScope.launch`, after `mimeType` resolution but before the `when` block, set `initialScrollPosition`:

Find the block that starts with:

```kotlin
                _uiState.update {
                    it.copy(
                        fileName = metadata?.name ?: "Unknown",
                        mimeType = mimeType,
                        isLoading = false
                    )
                }
```

Change it to:

```kotlin
                _uiState.update {
                    it.copy(
                        fileName = metadata?.name ?: "Unknown",
                        mimeType = mimeType,
                        isLoading = false,
                        initialScrollPosition = metadata?.scrollPosition ?: 0
                    )
                }
```

Then add a new method after `loadContent()`:

```kotlin
    fun saveScrollPosition(pos: Int) {
        viewModelScope.launch {
            fileRepository.saveScrollPosition(fileId, pos)
        }
    }
```

- [ ] **Step 3: Verify compilation**

Run: `./gradlew :app:compileDebugKotlin`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 4: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/ui/viewer/ViewerViewModel.kt
git commit -m "feat: expose initialScrollPosition and saveScrollPosition in ViewerViewModel"
```

---

### Task 3: MarkdownViewerScreen — Scroll restore, save on dispose, jump-to-top

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/ui/viewer/MarkdownViewerScreen.kt`

**Interfaces:**
- Consumes: `uiState.initialScrollPosition: Int`, `viewModel.saveScrollPosition(pos: Int)`

- [ ] **Step 1: Add missing imports to MarkdownViewerScreen**

Target: `app/src/main/java/com/a42r/mdrender/ui/viewer/MarkdownViewerScreen.kt`

Add after the existing `imports`:

```kotlin
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material3.SmallFloatingActionButton
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.derivedStateOf
import kotlinx.coroutines.launch
```

- [ ] **Step 2: Restructure MarkdownViewerScreen content region**

Current structure of the `else` branch (inside `Scaffold { padding -> when { ... else -> ... } }`):

```kotlin
            else -> {
                val scrollState = rememberScrollState()
                SelectionContainer {
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(padding)
                            .then(if (showAppBar) Modifier else Modifier.statusBarsPadding())
                            .verticalScroll(scrollState)
                            .pointerInput(Unit) {
                                detectTapGestures(onTap = { showAppBar = !showAppBar })
                            }
                            .padding(16.dp)
                    ) {
                        MarkdownText(uiState.markdownContent, fontScale)
                    }
                }
            }
```

Replace the entire `else -> { ... }` block with:

```kotlin
            else -> {
                val scrollState = rememberScrollState()
                val coroutineScope = rememberCoroutineScope()

                val showTopButton by remember {
                    derivedStateOf { scrollState.value > 200 }
                }

                // Restore saved scroll position once content is loaded
                LaunchedEffect(uiState.isLoading) {
                    if (!uiState.isLoading && uiState.initialScrollPosition > 0) {
                        scrollState.scrollTo(
                            uiState.initialScrollPosition.coerceAtMost(scrollState.maxValue)
                        )
                    }
                }

                // Save scroll position when leaving the screen
                val currentScroll by rememberUpdatedState(scrollState.value)
                DisposableEffect(Unit) {
                    onDispose { viewModel.saveScrollPosition(currentScroll) }
                }

                Box(modifier = Modifier.fillMaxSize()) {
                    SelectionContainer {
                        Box(
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(padding)
                                .then(if (showAppBar) Modifier else Modifier.statusBarsPadding())
                                .verticalScroll(scrollState)
                                .pointerInput(Unit) {
                                    detectTapGestures(onTap = { showAppBar = !showAppBar })
                                }
                                .padding(16.dp)
                        ) {
                            MarkdownText(uiState.markdownContent, fontScale)
                        }
                    }

                    // Jump-to-top FAB
                    AnimatedVisibility(
                        visible = showTopButton,
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(16.dp),
                        enter = fadeIn(),
                        exit = fadeOut()
                    ) {
                        SmallFloatingActionButton(
                            onClick = { coroutineScope.launch { scrollState.animateScrollTo(0) } },
                            containerColor = MaterialTheme.colorScheme.surfaceVariant
                                .copy(alpha = 0.85f)
                        ) {
                            Icon(
                                Icons.Default.KeyboardArrowUp,
                                contentDescription = "Jump to top"
                            )
                        }
                    }
                }
            }
```

- [ ] **Step 3: Remove unused import if present**

The existing import `import androidx.compose.runtime.getValue` is used by the existing code (for `by` delegation). `import androidx.compose.runtime.*` also covers it. No changes needed to existing imports except adding the new ones from Step 1.

- [ ] **Step 4: Verify compilation**

Run: `./gradlew :app:compileDebugKotlin`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/ui/viewer/MarkdownViewerScreen.kt
git commit -m "feat: scroll bookmarks and jump-to-top button for MarkdownViewerScreen"
```

---

### Task 4: TextViewerScreen — Scroll restore, save on dispose, jump-to-top

**Files:**
- Modify: `app/src/main/java/com/a42r/mdrender/ui/viewer/TextViewerScreen.kt`

**Interfaces:**
- Consumes: `uiState.initialScrollPosition: Int`, `viewModel.saveScrollPosition(pos: Int)`

- [ ] **Step 1: Add missing imports to TextViewerScreen**

Target: `app/src/main/java/com/a42r/mdrender/ui/viewer/TextViewerScreen.kt`

Add after the existing imports:

```kotlin
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material3.SmallFloatingActionButton
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.derivedStateOf
import kotlinx.coroutines.launch
```

- [ ] **Step 2: Restructure TextViewerScreen content region**

Current structure of the `else` branch (inside `Scaffold { padding -> when { ... else -> ... } }`):

```kotlin
            else -> SelectionContainer {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(padding)
                        .then(if (showAppBar) Modifier else Modifier.statusBarsPadding())
                        .verticalScroll(rememberScrollState())
                        .pointerInput(Unit) {
                            detectTapGestures(onTap = { showAppBar = !showAppBar })
                        }
                        .padding(16.dp)
                ) {
                    Text(
                        text = uiState.textContent,
                        fontFamily = FontFamily.Monospace,
                        fontSize = (14 * fontScale).sp,
                        lineHeight = (20 * fontScale).sp
                    )
                }
            }
```

Replace the entire `else -> ...` block with:

```kotlin
            else -> {
                val scrollState = rememberScrollState()
                val coroutineScope = rememberCoroutineScope()

                val showTopButton by remember {
                    derivedStateOf { scrollState.value > 200 }
                }

                // Restore saved scroll position once content is loaded
                LaunchedEffect(uiState.isLoading) {
                    if (!uiState.isLoading && uiState.initialScrollPosition > 0) {
                        scrollState.scrollTo(
                            uiState.initialScrollPosition.coerceAtMost(scrollState.maxValue)
                        )
                    }
                }

                // Save scroll position when leaving the screen
                val currentScroll by rememberUpdatedState(scrollState.value)
                DisposableEffect(Unit) {
                    onDispose { viewModel.saveScrollPosition(currentScroll) }
                }

                Box(modifier = Modifier.fillMaxSize()) {
                    SelectionContainer {
                        Box(
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(padding)
                                .then(if (showAppBar) Modifier else Modifier.statusBarsPadding())
                                .verticalScroll(scrollState)
                                .pointerInput(Unit) {
                                    detectTapGestures(onTap = { showAppBar = !showAppBar })
                                }
                                .padding(16.dp)
                        ) {
                            Text(
                                text = uiState.textContent,
                                fontFamily = FontFamily.Monospace,
                                fontSize = (14 * fontScale).sp,
                                lineHeight = (20 * fontScale).sp
                            )
                        }
                    }

                    // Jump-to-top FAB
                    AnimatedVisibility(
                        visible = showTopButton,
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(16.dp),
                        enter = fadeIn(),
                        exit = fadeOut()
                    ) {
                        SmallFloatingActionButton(
                            onClick = { coroutineScope.launch { scrollState.animateScrollTo(0) } },
                            containerColor = MaterialTheme.colorScheme.surfaceVariant
                                .copy(alpha = 0.85f)
                        ) {
                            Icon(
                                Icons.Default.KeyboardArrowUp,
                                contentDescription = "Jump to top"
                            )
                        }
                    }
                }
            }
```

Note the key difference from Task 3: the `verticalScroll` modifier here passes `scrollState` (the named variable) instead of `rememberScrollState()` (which was inline before). The scroll state must be shared between the scroll modifier and the FAB.

- [ ] **Step 3: Verify compilation**

Run: `./gradlew :app:compileDebugKotlin`
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 4: Commit**

```bash
git add app/src/main/java/com/a42r/mdrender/ui/viewer/TextViewerScreen.kt
git commit -m "feat: scroll bookmarks and jump-to-top button for TextViewerScreen"
```

---

### Task 5: Build, deploy, and verify on device

- [ ] **Step 1: Build and deploy**

Run: `bash build-deploy.sh`
Expected: `BUILD SUCCESSFUL` + `Deployment complete.`

- [ ] **Step 2: Verify scroll restore**

1. Open a long markdown file in the viewer
2. Scroll to a specific position (e.g., halfway)
3. Tap back to close the viewer
4. Re-open the same file
5. Expected: File opens at the same scroll position as step 2

- [ ] **Step 3: Verify jump-to-top button**

1. While still scrolled down, confirm the jump-to-top FAB is visible (bottom-right, semi-transparent)
2. Tap the FAB
3. Expected: Scroll animates smoothly to the top of the document

- [ ] **Step 4: Verify button hides at top**

1. After animating to top, confirm the FAB is no longer visible
2. Expected: FAB fades out when scroll position is <= 200px

- [ ] **Step 5: Verify plain text files**

1. Open a plain text file (`.txt`)
2. Repeat steps 2-4
3. Expected: Same bookmark and jump-to-top behavior works for text files
