#!/bin/sh
# Build the distro packages for mdrender-send.
#
#   .deb / .rpm / .apk  -> packaging/dist/   (via nfpm)
#   Arch .pkg.tar.zst   -> packaging/arch/    (via makepkg, on Arch)
#
# nfpm must be on PATH, or point NFPM at the binary:
#   NFPM=/tmp/nfpm ./packaging/build-packages.sh
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
cd "$repo"

nfpm="${NFPM:-$(command -v nfpm || true)}"
if [ -n "$nfpm" ]; then
  mkdir -p packaging/dist
  for p in deb rpm apk; do
    "$nfpm" package -f packaging/nfpm.yaml -p "$p" -t packaging/dist
  done
else
  echo "nfpm not found; install it or set NFPM=/path/to/nfpm" >&2
fi

if command -v makepkg >/dev/null; then
  "$here/arch/build.sh"
fi

echo
echo "artifacts:"
ls -1 "$here"/dist/* "$here"/arch/*.pkg.tar.* 2>/dev/null || true
