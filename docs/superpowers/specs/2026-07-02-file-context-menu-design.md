# File Context Menu — Design

**Date:** 2026-07-02
**Status:** Approved-by-default (user AFK; requested feature verbatim: "long press on a file should display a menu for delete, rename, move open")

## Goal

Long-pressing a file in the folder browser (grid or list view) opens a context
menu offering **Open, Rename, Move, Delete**. Folders are out of scope for this
iteration.

## UI

- **Gesture:** `combinedClickable` on file items — tap keeps opening the file,
  long-press opens the menu. List-view rows gain tap-to-open (previously dead).
- **Menu:** `ModalBottomSheet` (matches the existing FAB import sheet idiom)
  with four `ListItem` actions:
  - **Open** — same MIME-routing as tap (markdown/text/image viewer).
  - **Rename** — `AlertDialog` with `OutlinedTextField` pre-filled with the
    current name (same pattern as the New Folder dialog). Confirm disabled for
    blank input.
  - **Move** — `AlertDialog` listing destinations: "Root" plus all folders
    flattened from `FolderRepository.buildTree()` with depth indentation.
    The file's current location is disabled. Selecting a destination moves the
    file and dismisses.
  - **Delete** — delegates to the existing `BrowserViewModel.deleteFile()`
    (keeps the undo snackbar behaviour).

## Data layer

- `FileDao` gains two targeted queries (avoids rewriting the encrypted blob):
  - `UPDATE files SET name = :name, updated_at = :ts WHERE id = :id`
  - `UPDATE files SET folder_id = :folderId, updated_at = :ts WHERE id = :id`
- `FileRepository.renameFile(id, newName)` / `moveFile(id, folderId)` wrap them.
- `BrowserViewModel` gains `renameFile`, `moveFile`, and a `moveTargets`
  StateFlow populated on demand from `buildTree()` (data class
  `MoveTarget(folder, depth)`).

## Refresh & error handling

- The browser observes Room `Flow`s, so rename/move/delete update the list
  automatically — no manual reload.
- Moving a file out of the current folder removes it from the visible list,
  which is the user feedback for a successful move.

## Testing

- Build + existing unit test suite must pass.
- Manual verification on device: long-press → menu; each action exercised.

## Alternatives considered

- `DropdownMenu` anchored to the item — rejected: bottom sheet matches the
  app's existing menu idiom and is friendlier for touch.
- Navigable mini folder-browser for Move — rejected for v0.1: flat indented
  tree is simpler and hierarchies are shallow.
- Including folders in the long-press menu — deferred: not requested; folder
  management (rename/delete UI) can be a follow-up.
