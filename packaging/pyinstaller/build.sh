#!/bin/sh
# Build a standalone mdrender-send binary for the current OS.
# Requires PyInstaller:  python3 -m pip install --user pyinstaller
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
cd "$repo"

python3 -m PyInstaller --clean --noconfirm "$here/mdrender-send.spec"
echo "built: $repo/dist/mdrender-send"
