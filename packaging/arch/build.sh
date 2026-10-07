#!/bin/sh
# Build the Arch package for mdrender-send.
#
# Stages the tool's source beside the PKGBUILD, runs makepkg, then cleans up.
# Any arguments are passed through to makepkg (e.g. --install, -c).
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

cp "$repo/tools/localsend-send/localsend-send.py" "$here/localsend-send.py"
cp "$repo/tools/localsend-send/README.md" "$here/README.md"
cp "$repo/tools/push-to-phone.sh" "$here/push-to-phone.sh"
trap 'rm -f "$here/localsend-send.py" "$here/README.md" "$here/push-to-phone.sh"' EXIT

cd "$here"
makepkg -f "$@"
