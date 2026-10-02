#!/usr/bin/env bash
# Bump the mdrender-send version everywhere it is recorded.
#
#   packaging/bump-version.sh 1.0.17
#
# Run this whenever client code changes so package managers (pacman, deb,
# rpm, apk, brew/cask, Inno Setup) see the new build as an upgrade. Each
# spot is patched with an anchored pattern so unrelated numbers never match.
set -euo pipefail

new="${1:?usage: bump-version.sh <new-version>  (e.g. 1.0.17)}"
if ! [[ "$new" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "error: version must look like 1.2.3, got: $new" >&2
    exit 1
fi

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

old="$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' tools/localsend-send/localsend-send.py)"
if [[ -z "$old" ]]; then
    echo "error: could not read current __version__ from localsend-send.py" >&2
    exit 1
fi
if [[ "$old" == "$new" ]]; then
    echo "already at $new"
    exit 0
fi

sed -i "s/^__version__ = \".*\"/__version__ = \"$new\"/" \
    tools/localsend-send/localsend-send.py
sed -i "s/^pkgver=.*/pkgver=$new/" packaging/arch/PKGBUILD
sed -i "s/^version: .*/version: \"$new\"/" packaging/nfpm.yaml
sed -i "s/^#define AppVersion \".*\"/#define AppVersion \"$new\"/" \
    packaging/windows/mdrender-send.iss
sed -i "s/^VERSION=.*/VERSION=\"$new\"/" packaging/macos/build-pkg.sh

echo "bumped $old -> $new in:"
echo "  tools/localsend-send/localsend-send.py  (__version__)"
echo "  packaging/arch/PKGBUILD                 (pkgver)"
echo "  packaging/nfpm.yaml                     (version)"
echo "  packaging/windows/mdrender-send.iss     (AppVersion)"
echo "  packaging/macos/build-pkg.sh            (VERSION)"
echo "rebuild the package(s) next: packaging/arch/build.sh"
