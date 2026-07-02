# App Icon — Design

**Date:** 2026-07-03 · **Branch:** feature/app-icon

## Concept

Shield with the markdown "M↓" mark — signals "encrypted" (shield) + "markdown"
(the universal M-and-down-arrow glyph). Coral-on-navy, matching the app palette.

## Execution

Adaptive launcher icon (vector drawables; minSdk 26 covers all devices, no PNGs).

- **Background** (`ic_launcher_background.xml`): 108×108 filled with a vertical
  gradient, deep navy `#1E1E32` → `#141420`.
- **Foreground** (`ic_launcher_foreground.xml`): within the adaptive safe zone,
  - a filled coral `#E94560` shield (rounded shoulders, tapering to a point);
  - the markdown "M↓" mark stroked in cream `#FDEFE8` on the shield — a
    two-stroke M and a down-arrow.
- Content stays inside the central ~66dp safe zone so launcher masks (circle,
  squircle, rounded square) don't clip the shield.

## Wiring

- `mipmap-anydpi-v26/ic_launcher.xml` and new `ic_launcher_round.xml` reference
  the two drawables.
- `AndroidManifest.xml` gains `android:roundIcon="@mipmap/ic_launcher_round"`.
- No Gradle change — manifest already points at `@mipmap/ic_launcher`.

## Verification

Render the layers to PNG (circle + squircle masks) for review, then build and
deploy so the icon shows on the launcher.
