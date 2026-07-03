# Name Collision Handling — Design

Date: 2026-07-03
Status: Approved (design); spec includes one flagged addition (folder Rename UI)

## Goal

1. File moves that collide with an existing name in the destination prompt
   Replace / Skip with an apply-to-all option, instead of silently creating
   duplicate names.
2. File renames cannot create a duplicate name in the same folder.
3. Folder names are unique among siblings (case-insensitive), enforced on
   create, rename, and move.

## Part A — File move conflict flow

### Detection
- New query `FileDao.findByName(folderId: Long?, name: String): FileEntity?`
  (exact-match, `folder_id = :folderId AND name = :name`, works for null
  folderId = root).
- File name comparisons are **exact-match**, consistent with the existing
  import auto-renamer (`uniqueNameInFolder`).

### Batch walker (BrowserViewModel)
`moveFilesResolvingConflicts(ids: Collection<Long>, targetFolderId: Long?)`
replaces the direct `moveFile`/`moveFiles` calls from the Move dialog (both
single-file and multi-select paths). It runs in ONE coroutine, sequentially:

For each file id, in order:
1. Skip files already in the target folder (moving to same folder = no-op).
2. `existing = fileDao.findByName(targetFolderId, file.name)`; exclude the
   file itself (`existing.id != file.id`).
3. No collision → `fileRepository.moveFile(id, targetFolderId)`.
4. Collision with a sticky decision active (`replaceAll` / `skipAll`) →
   apply it without prompting.
5. Collision, no sticky decision → publish
   `MoveConflict(file, existingName, targetFolderName, remainingCount)` to
   `pendingMoveConflict: StateFlow<MoveConflict?>` and suspend on a
   `CompletableDeferred<ConflictDecision>` until the UI resolves it.

`ConflictDecision` = `Replace(applyToAll)`, `Skip(applyToAll)`, `CancelBatch`.
- Replace: delete the existing destination file (`fileRepository.deleteFile`),
  then move the source file.
- Skip: leave the source file untouched.
- CancelBatch: stop processing remaining ids; already-completed moves stand.
- applyToAll=true sets the sticky decision for the REST of this batch only.

Because each iteration checks live DB state, two same-named files moved in
one batch collide with each other (the second prompts) — this is intended.

### Completion feedback
After the batch, emit a snackbar summary via the existing snackbar flow:
"Moved N" with ", replaced K" / ", skipped M" appended when nonzero
(e.g. "Moved 3, replaced 1, skipped 2"). Cancelled batches report what
completed before the cancel.

### Conflict dialog (FolderBrowserScreen)
Driven by `pendingMoveConflict`:
- Title: "Name conflict"
- Text: `"<name>" already exists in "<target folder name or Home>".`
  (root folder displays as "Home", matching the breadcrumb's root label)
- Checkbox: "Apply to all remaining conflicts" (visible only when
  remainingCount > 1)
- Buttons: **Replace**, **Skip**, **Cancel** (Cancel = CancelBatch; dialog
  dismiss (tap outside/back) = CancelBatch too — never silently replace).

No undo for Replace (the existing delete-undo path cannot restore content;
see BrowserViewModel.deleteFile's known limitation). The dialog IS the
confirmation.

## Part B — File rename collision

In the existing Rename File dialog: on confirm, the ViewModel checks
`fileDao.findByName(file.folderId, newName)` (excluding the file itself).
- Collision → dialog stays open, inline error text under the field:
  "A file with this name already exists". Confirm does not close the dialog.
- No collision → rename proceeds as today.
- Unchanged name (rename to itself, including no-op) → allowed, closes.
No Replace option on rename (deliberate — replacing via rename is almost
always an accident).

## Part C — Folder sibling uniqueness (case-insensitive)

### Rule
No two folders with the same name (case-insensitive) under the same parent.
Enforced at creation, rename, and move. Existing duplicates (if any) are
grandfathered — no migration; they just can't be recreated.

### Detection
`FolderDao.findByName` gains case-insensitive matching (`COLLATE NOCASE` on
the name comparison). Its existing caller (LocalSend get-or-create folder)
inherits case-insensitive matching — acceptable and arguably correct (avoids
LocalSend creating "photos" next to "Photos").
Repository helper: `FolderRepository.siblingNameExists(parentId: Long?,
name: String, excludeId: Long? = null): Boolean`.

### Enforcement points
1. **New Folder dialog**: on Create, check `siblingNameExists(currentFolderId,
   name)`. Collision → dialog stays open with inline error "A folder with
   this name already exists".
2. **Folder rename** (NEW UI — flagged addition): the folder long-press menu
   gains a "Rename" entry (between Open and Move, mirroring the file menu),
   opening a Rename Folder dialog identical in shape to the file one, wired
   to the existing (currently unused) `FolderRepository.renameFolder`. Same
   inline collision error, excluding the folder itself so case-only renames
   of the same folder ("work" → "Work") are allowed.
3. **Folder move**: before `moveFolder`, check
   `siblingNameExists(targetParentId, folder.name)`. Collision → do not move;
   snackbar: `A folder named "<name>" already exists in "<destination>"`.
   Applies to the folder Move dialog path.

## Security note — hidden-folder existence oracle (accepted trade-off)

The sibling-uniqueness checks run against ALL siblings, including hidden
ones, while the browser filters hidden folders from view. With reveal off,
attempting to create/rename/move a folder whose name matches a hidden
sibling surfaces "already exists" — confirming that hidden folder's name.
This is inherent to the uniqueness invariant: permitting the duplicate
would collide the moment the hidden folder is revealed, and failing
silently would be a worse mystery. Accepted by the owner (2026-07-03).
Case-insensitive matching widens this probe surface slightly (ASCII-only
NOCASE; accented names remain case-sensitive).

## Components touched

- `FileDao` — add `findByName`.
- `FolderDao` — `findByName` becomes case-insensitive.
- `FolderRepository` — add `siblingNameExists`.
- `BrowserViewModel` — batch walker + conflict state/decision API, rename
  checks (file + folder), guarded folder move, snackbar summaries, new
  `renameFolder` pass-through.
- `FolderBrowserScreen` — conflict dialog, inline errors in New Folder /
  Rename File dialogs, new folder Rename menu entry + dialog.
- Move dialog call sites switch from `moveFile`/`moveFiles` to the walker.

The old `BrowserViewModel.moveFile`/`moveFiles` methods are removed with
their call sites (the walker replaces them); `FileRepository.moveFile` stays.

## Error handling

- DB errors during a batch move surface the existing generic snackbar path
  and abort the remainder of the batch (same semantics as CancelBatch).
- The conflict `CompletableDeferred` is completed by exactly one decision;
  repeat button taps are no-ops (dialog closes on first).
- ViewModel clear during a pending conflict: coroutine is cancelled with the
  viewModelScope; no dangling state.

## Testing

Unit (mocked DAOs/repositories, coroutines-test):
- Walker: no conflicts; replace one; skip one; replace-all; skip-all;
  cancel mid-batch (earlier moves stand, later ids untouched); within-batch
  collision (two same-named sources, second prompts); move-to-same-folder
  no-op; sticky decision does not leak across batches.
- `siblingNameExists`: case-insensitive hit, miss, excludeId behavior.
Manual on device: single + multi move with conflicts (all buttons +
checkbox), rename collisions (file + folder), New Folder duplicate,
folder move into a parent with a same-named sibling, root ("Home") naming
in dialog text.
