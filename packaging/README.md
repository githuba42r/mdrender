# Packaging `mdrender-send`

`localsend-send.py` is standard-library-only, so it packages cleanly. The
installed **command and package name is `mdrender-send`**; the source file and
its docs keep the `localsend-send.py` name.

Bump the version in **one** place and keep it in step with these manifests:
`tools/localsend-send/localsend-send.py` → `__version__`, `packaging/arch/PKGBUILD`
→ `pkgver`, `packaging/nfpm.yaml` → `version`, `packaging/windows/mdrender-send.iss`
→ `AppVersion`, `packaging/macos/build-pkg.sh` → `VERSION`.

## Arch Linux / pacman

```sh
packaging/arch/build.sh            # builds mdrender-send-<ver>-1-any.pkg.tar.zst
sudo pacman -U packaging/arch/mdrender-send-*.pkg.tar.zst
mdrender-send --version
```

`build.sh` stages the source beside the `PKGBUILD`, runs `makepkg`, and cleans
up. `makepkg --printsrcinfo > .SRCINFO` for an AUR submission (there, point
`source=()` at the release tarball instead of the local file).

## Debian / Fedora / openSUSE / Alpine (`.deb`, `.rpm`, `.apk`)

Use [`nfpm`](https://nfpm.goreleaser.com). `packaging/build-packages.sh`
builds all three (and the Arch package when `makepkg` is present):

```sh
NFPM=/path/to/nfpm packaging/build-packages.sh   # or put nfpm on PATH
```

Or directly:

```sh
nfpm package -f packaging/nfpm.yaml -p deb -t packaging/dist
nfpm package -f packaging/nfpm.yaml -p rpm -t packaging/dist
nfpm package -f packaging/nfpm.yaml -p apk -t packaging/dist
```

Outputs: `mdrender-send_<ver>_all.deb`, `mdrender-send-<ver>-1.noarch.rpm`,
`mdrender-send_<ver>_noarch.apk`. All install `/usr/bin/mdrender-send` and a
README under `/usr/share/doc/mdrender-send/`.

## Standalone binary (no Python needed on the target)

PyInstaller bundles the interpreter into one file. It **cannot cross-compile**,
so build on each OS (or let the GitHub Actions `package` workflow do it on a
`v*` tag):

```sh
python3 -m pip install --user pyinstaller
packaging/pyinstaller/build.sh      # -> dist/mdrender-send (or .exe on Windows)
```

## Windows installer

1. Build the exe: `packaging/pyinstaller/build.sh` (or the CI artifact).
2. Compile `packaging/windows/mdrender-send.iss` with Inno Setup 6+
   (`ISCC.exe`). It installs `mdrender-send.exe` to Program Files with an
   optional "Add to PATH" task.

Code-signing the exe (OV/EV certificate) removes the SmartScreen warning;
unsigned still installs.

## macOS installer

```sh
packaging/pyinstaller/build.sh      # dist/mdrender-send (Mach-O)
packaging/macos/build-pkg.sh        # -> mdrender-send-<ver>.pkg
```

Installs `/usr/local/bin/mdrender-send`. For a clean install on other Macs,
codesign the binary and notarize the pkg with an Apple Developer ID:

```sh
codesign --options runtime --sign "Developer ID Application: …" dist/mdrender-send
xcrun notarytool submit mdrender-send-<ver>.pkg --keychain-profile … --wait
xcrun stapler staple mdrender-send-<ver>.pkg
```

A Homebrew formula/tap is the lighter alternative to a `.pkg`.

## Build everything in CI

`.github/workflows/package.yml` builds and uploads all of the above on a `v*`
tag (or manually via *Run workflow*):

| Runner | Artifacts |
|--------|-----------|
| ubuntu | `mdrender-send` (binary), `.deb`, `.rpm`, `.apk` |
| archlinux container | `mdrender-send-*.pkg.tar.zst` |
| windows | `mdrender-send.exe`, `…-setup.exe` (Inno Setup) |
| macos | `mdrender-send` (Mach-O), `mdrender-send-*.pkg` |

Windows/macOS installers are unsigned unless you add signing secrets
(`MACOS_CERT_P12` / `MACOS_CERT_PASSWORD`); unsigned still installs with a
Gatekeeper/SmartScreen warning.

## Notes

- License: **Proprietary — all rights reserved** (per the repository README), so
  packages declare `custom:Proprietary` / `Proprietary`.
- The tool installs as `mdrender-send`, so `--help`/`--version` show that name
  (argparse derives the program name from `argv[0]`).
- CI artifact names: `mdrender-send-ubuntu-latest`, `-windows-latest`,
  `-macos-latest`.
