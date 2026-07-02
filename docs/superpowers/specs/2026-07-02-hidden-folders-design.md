# Hidden Folders — Design

**Date:** 2026-07-02 · **Branch:** feature/hidden-folders

## Goal

Let the user hide folders so they are invisible even when the app is
authenticated. Hidden folders (and everything inside them) are revealed only by
a secret gesture, stay revealed until the app next locks, then revert to hidden.
Authentication must never, by itself, expose hidden content.

## Data model

- `FolderEntity` gains `hidden: Boolean` → column `hidden INTEGER NOT NULL
  DEFAULT 0`.
- Room DB bumped to version 2 with a real migration
  (`ALTER TABLE folders ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0`) so
  existing data survives. `fallbackToDestructiveMigration` stays as a backstop.
- **Effectively hidden**: a folder is hidden if it or any ancestor has
  `hidden = true`.

## Reveal state (runtime, never persisted)

- `AppLock` exposes `revealHidden: StateFlow<Boolean>`, default `false`.
- `revealHiddenFolders()` sets it `true`.
- Forced back to `false` on every lock (`AppLock.onBackground` and any `lock`).
  Unlocking never sets it true — the gesture must be repeated each session.

## Reveal gesture

Tapping the "MDRender" title in the browser top bar **12 times within 30
seconds** calls `revealHiddenFolders()`. Rolling window in the browser screen:
track tap count and first-tap timestamp; on the 12th qualifying tap → reveal;
the window resets if 30 s elapse without completing.

## Browser filtering

`BrowserViewModel` combines the child-folder flow with `revealHidden`:
- reveal **off** → hidden folders filtered out of the listing (cannot be seen
  or navigated into);
- reveal **on** → shown, each with a small `VisibilityOff` badge; otherwise
  identical to normal folders.

## Hide / unhide UI

Folders get a long-press context menu (none today): **Hide folder**, or
**Unhide folder** when already hidden (only reachable in reveal mode). Backed by
`BrowserViewModel.setFolderHidden(id, hidden)`. Scope: only Hide/Unhide (no
folder rename/delete in this change).

## Security — never expose hidden content after auth

1. **Listing filter** blocks navigating into hidden folders while reveal is off.
2. **Folder restore**: `initialize()` / persisted last-folder falls back to root
   when the target is in a hidden tree and reveal is off.
3. **On unlock** (isLocked → false): if the browser's current folder is in a
   hidden tree, or a viewer is open on a file whose folder is in a hidden tree,
   navigate/pop to home. Uses `FolderRepository.isInHiddenTree(folderId)`
   (walks the parent chain), checked from the NavHost when the lock clears.

## Testing

- Unit: `isInHiddenTree`, reveal-reset-on-lock, tap-window counter.
- On-device: hide a folder → 12-tap reveal → badge shown → enter it → lock →
  reopen + auth → hidden again and returned to home.
