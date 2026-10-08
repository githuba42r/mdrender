#!/bin/sh
# Build a macOS .pkg for mdrender-send from the PyInstaller binary.
# Also bundles mdrender-push (tools/push-to-phone.sh, cloud push only; needs
# bash + curl, which macOS ships, and python3 from the Command Line Tools).
#
#   1) packaging/pyinstaller/build.sh        (produces dist/mdrender-send)
#   2) packaging/macos/build-pkg.sh          (run from the repository root)
#
# For a warning-free install on other Macs, codesign the binary and notarize
# the package with an Apple Developer ID (see packaging/README.md).
set -eu

VERSION="1.2.0"
IDENTIFIER="com.aspedia.mdrender.send"
BIN="dist/mdrender-send"

if [ ! -x "$BIN" ]; then
  echo "error: $BIN not found; run packaging/pyinstaller/build.sh first" >&2
  exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir -p "$work/root/usr/local/bin"
cp "$BIN" "$work/root/usr/local/bin/mdrender-send"
cp tools/push-to-phone.sh "$work/root/usr/local/bin/mdrender-push"

pkgbuild --root "$work/root" \
  --identifier "$IDENTIFIER" \
  --version "$VERSION" \
  --install-location / \
  "$work/mdrender-send-component.pkg"

productbuild --package "$work/mdrender-send-component.pkg" \
  "mdrender-send-$VERSION.pkg"

echo "built: mdrender-send-$VERSION.pkg (installs /usr/local/bin/mdrender-send and mdrender-push)"
