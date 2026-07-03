# Share Files from Browser — Design

Date: 2026-07-03
Status: Approved (Option A, decrypt-to-cache)

## Goal

Allow sharing one or multiple files from the folder browser via the Android
share sheet. Files stored in hidden folders require explicit confirmation
before sharing, with a warning that the content is sensitive and normally
hidden for security purposes.

## Scope

- Files only — folders cannot be shared.
- Browser entry points only (no viewer share action):
  1. Share icon in the multi-select top bar (alongside Move/Delete), shares
     all selected files.
  2. "Share" entry in the single-file long-press menu (`menuFile` sheet).

Both entry points converge on one share flow keyed by a set of file IDs.

## Hidden-item confirmation

On share request, `BrowserViewModel` resolves each file's `folderId` and
checks `FolderRepository.isInHiddenTree()`.

- **No hidden items** → share sheet opens immediately, no dialog.
- **Some or all hidden** → confirmation dialog first:
  - Text names the hidden file(s) and warns: "N of the items you are sharing
    are stored in a hidden folder. These items are sensitive in nature and
    are normally hidden for security purposes." (singular phrasing for one).
  - Buttons:
    - **Share all** — proceed with the full selection.
    - **Share non-hidden only** — only shown for a mixed selection; proceeds
      with the non-hidden subset.
    - **Cancel** — aborts entirely.
  - If every selected item is hidden the middle button is omitted
    (Share / Cancel only).

Note: hidden files are only reachable in the browser while reveal is on, so
the dialog can only appear in that state; the warning still always fires for
hidden items.

## Share mechanics (Option A — decrypt to cache + FileProvider)

1. Wipe `cacheDir/share/` (delete recursively, recreate).
2. Decrypt each file via `FileRepository.getDecryptedContent(id)` off the
   main thread and write to `cacheDir/share/<name>`. On duplicate names
   across folders, suffix (`name (1).ext`).
3. Expose via androidx `FileProvider` declared in the manifest with a
   `cache-path` entry for `share/`.
4. Fire `ACTION_SEND` (single) or `ACTION_SEND_MULTIPLE` (multiple) with
   `content://` URIs, `FLAG_GRANT_READ_URI_PERMISSION`, wrapped in
   `Intent.createChooser`. MIME type: the file's own type for single share;
   for multiple, the common type prefix (`image/*`, `text/*`) or `*/*`.

## Plaintext-on-disk containment (hard requirement)

Decrypted plaintext exists **only** under `cacheDir/share/` and nowhere else.

- Wiped on **every app start** (Application.onCreate) — covers crashes,
  force-stops, and any unexpected closure leaving fragments behind.
- Wiped again **before every new share** — bounds the window to one share
  session.
- `android:allowBackup="false"` already prevents the cache from being backed
  up.
- The cache directory is app-private storage; no other app can read it on a
  non-rooted device.

## Non-goals / unchanged behavior

- App-lock and secure-window behavior unchanged; the system chooser is
  system UI over the app.
- Inbound sharing (ShareReceiverActivity) untouched.
- No share action inside the viewers (may be added later).

## Components touched

- `AndroidManifest.xml` — FileProvider declaration + `res/xml/share_paths.xml`.
- `MDRenderApplication` — wipe share cache on startup.
- New `ShareHelper` (or similar) — cache wipe, decrypt-to-cache, intent build.
- `BrowserViewModel` — share request + hidden check + share state.
- `FolderBrowserScreen` — top-bar Share action, menu entry, warning dialog,
  in-progress indicator while decrypting.

## Error handling

- Decryption failure for a file: abort the share, show a snackbar naming the
  failing file; no partial share sheet.
- Empty effective selection (e.g. "non-hidden only" leaves zero files):
  dialog simply closes, nothing shared.

## Testing

- Unit: hidden-partitioning logic (none/some/all hidden → dialog state and
  button set).
- Manual on device: single share, multi share, mixed-selection all three
  buttons, share cache wiped after app restart, share from inside a hidden
  folder while revealed.
