# Scroll Position Bookmark & Jump-to-Top — Design

## Goal

When a user views a markdown or text file, remembers the scroll position across navigation and app restarts, and provides a quick jump-to-top button accessible without scrolling back manually.

## Persistence

Scroll positions are stored in the existing `files` Room table via a new `scroll_position` column (integer, pixels from top). This ties position to the file's lifecycle — auto-cleaned on delete, persisted across app restart, and accessible via the existing `FileEntity`/`FileDao`/`FileRepository` chain.

## Scope

- `MarkdownViewerScreen` — get scroll bookmarks
- `TextViewerScreen` — get scroll bookmarks  
- `ImageViewerScreen` — not included (concept of "scroll position" doesn't apply)

## Data Layer Changes

### FileEntity (`data/entity/FileEntity.kt`)
- Add `val scrollPosition: Int = 0`

### AppDatabase (`data/AppDatabase.kt`)
- Version 2 → 3 migration: `ALTER TABLE files ADD COLUMN scroll_position INTEGER NOT NULL DEFAULT 0`
- Add `MIGRATION_2_3` companion object alongside existing `MIGRATION_1_2`

### FileDao (`data/dao/FileDao.kt`)
- Add: `@Query("UPDATE files SET scroll_position = :pos WHERE id = :id") suspend fun updateScrollPosition(id: Long, pos: Int)`

### FileRepository (`data/repository/FileRepository.kt`)
- Add: `suspend fun saveScrollPosition(id: Long, pos: Int) = fileDao.updateScrollPosition(id, pos)`
- `scrollPosition` is already accessible via `getFileMetadata(id)?.scrollPosition` since `FileEntity` carries it after the column addition; no separate DAO query needed.

### DatabaseModule (`di/DatabaseModule.kt`)
- Append `AppDatabase.MIGRATION_2_3` to the `addMigrations()` chain, after `MIGRATION_1_2`.

## ViewModel Changes

### ViewerUiState
- Add `initialScrollPosition: Int = 0` — set from `fileEntity.scrollPosition` after loading.

### ViewerViewModel
- Add `saveScrollPosition(pos: Int)` → delegates to `fileRepository.saveScrollPosition(fileId, pos)`.

This is intentionally lightweight — no debouncing, no state tracking. The caller decides when to save.

## Screen Changes

### Both viewers: scroll restore

The screen receives `uiState.initialScrollPosition` after content loads. A `LaunchedEffect(scrollState)` runs after initial layout:
```kotlin
LaunchedEffect(uiState.isLoading) {
    if (!uiState.isLoading && uiState.initialScrollPosition > 0) {
        scrollState.scrollTo(uiState.initialScrollPosition.coerceAtMost(scrollState.maxValue))
    }
}
```
Clamps to `maxValue` to handle files that have been shortened since last visit.

### Both viewers: scroll position save on dispose

The position is saved in a `DisposableEffect(Unit)` that fires once when the composable disposes (navigate away). A `rememberUpdatedState` captures the latest scroll value across recompositions so the dispose handler sees the current position regardless of when it fires.

```kotlin
val currentScroll by rememberUpdatedState(scrollState.value)
DisposableEffect(Unit) {
    onDispose { viewModel.saveScrollPosition(currentScroll) }
}
```

This guarantees exactly one encrypted write per viewing session. On-screen updates to `scrollState.value` do not trigger re-saves — the latest value is simply captured for the dispose handler via `rememberUpdatedState`.

### Both viewers: jump-to-top button

A `SmallFloatingActionButton` anchored to the bottom-end corner, visible when scrolled beyond 200px. Uses `AnimatedVisibility` for smooth fade in/out. On click, animates scroll to 0.

```kotlin
val showTopButton by remember {
    derivedStateOf { scrollState.value > 200 }
}

AnimatedVisibility(
    visible = showTopButton,
    modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
    enter = fadeIn(),
    exit = fadeOut()
) {
    SmallFloatingActionButton(
        onClick = { coroutineScope.launch { scrollState.animateScrollTo(0) } },
        containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.85f)
    ) {
        Icon(Icons.Default.KeyboardArrowUp, contentDescription = "Jump to top")
    }
}
```

The content area is wrapped in a `Box` to layer the scrollable text and the FAB. The existing composable structure (Scaffold → padding → content) is preserved; only the content region gains a `Box` wrapper.

## Edge Cases

| Case | Behaviour |
|---|---|
| File replaced/shorter | `coerceAtMost(maxValue)` clamps safely — lands at bottom |
| First open (no saved position) | `scrollPosition` defaults to 0, starts at top |
| App killed while viewing | Position from last completed viewing session is restored; unsaved position from killed session is lost (acceptable — encrypted DB writes per-frame would be worse) |
| Very long file | Works naturally — Int holds pixel offset |
| Configuration change | DisposableEffect fires on config change too — saves position unnecessarily but harmlessly |

## Files Changed

1. `app/src/main/java/com/a42r/mdrender/data/entity/FileEntity.kt`
2. `app/src/main/java/com/a42r/mdrender/data/AppDatabase.kt`
3. `app/src/main/java/com/a42r/mdrender/data/dao/FileDao.kt`
4. `app/src/main/java/com/a42r/mdrender/data/repository/FileRepository.kt`
5. `app/src/main/java/com/a42r/mdrender/di/DatabaseModule.kt`
6. `app/src/main/java/com/a42r/mdrender/ui/viewer/ViewerViewModel.kt`
7. `app/src/main/java/com/a42r/mdrender/ui/viewer/MarkdownViewerScreen.kt`
8. `app/src/main/java/com/a42r/mdrender/ui/viewer/TextViewerScreen.kt`
