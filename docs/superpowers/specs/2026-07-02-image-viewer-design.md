# Image Viewer & Thumbnails — Design

**Date:** 2026-07-02 · **Branch:** feature/image-viewer

## Goal

Show image thumbnails in the browser, and let the image viewer swipe between
the folder's images (up = next, down = previous) with pinch-to-zoom.

## Thumbnails in the browser

- `FileItem` gains an optional decrypted-thumbnail `ByteArray?`. For image
  files the browser loads it lazily via `produceState(file.id)` calling
  `FileRepository.getDecryptedThumbnail(id)` (already exists), rendered with
  Coil `AsyncImage` cropped into the tile.
- Falls back to the image icon while loading or when no thumbnail exists.
- Grid tiles and the list-row leading slot. Only visible items are decrypted;
  thumbnails are ~256px JPEG (few KB).

## Swipe between images

- The image viewer becomes a `VerticalPager` (Compose foundation, available in
  BOM 2024.12) over the ordered image files in the current folder, name-sorted
  to match the browser.
- Opening an image starts the pager at that file's index. Swipe **up → next**,
  **down → previous** (VerticalPager's natural direction). No wrap — stops at
  the ends.
- Each page decrypts and shows its own image via `produceState(pageFileId)`
  → `FileRepository.getDecryptedContent(id)`.
- `ViewerViewModel` exposes the sibling image-id list and the start index
  (looks up the file's folder, pulls its image files). Markdown/Text viewers
  are unchanged.

## Pinch to zoom

- Kept per-page via `detectTransformGestures` (scale 0.5–5×, pan when zoomed).
- Pager scroll disabled while zoomed (`userScrollEnabled = scale == 1f`) so a
  drag pans the zoomed image instead of flipping pages; returning to 1×
  re-enables paging.
- Existing tap-to-toggle app bar and volume-key zoom retained.

## Data / plumbing

- New: `Folder/FileRepository` helper `getImageFilesInFolder(folderId)` — a
  snapshot list of the folder's image files, name-ordered (via a suspend DAO
  query). No schema change.

## Testing

- Unit test for the sibling-list + start-index lookup.
- On-device: thumbnails render; swipe up/down including at first/last image;
  pinch zoom disables paging; volume-key zoom still works.
