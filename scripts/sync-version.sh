#!/bin/sh
# Propagate version.properties into the packaging files that keep a hard-coded
# copy of the version.
#
#   scripts/sync-version.sh            rewrite the files in place
#   scripts/sync-version.sh --check    exit 1 if any file is out of date
#
# version.properties is the single source of truth (it drives the Android
# versionName/versionCode); everything else is derived from it.
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
cd "$repo"

check=false
if [ "${1:-}" = "--check" ]; then
  check=true
fi

if [ ! -f version.properties ]; then
  echo "sync-version: version.properties not found" >&2
  exit 1
fi

# shellcheck disable=SC1091
. ./version.properties

: "${VERSION_MAJOR:?}" "${VERSION_MINOR:?}" "${VERSION_PATCH:?}"
VERSION="${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}"

failed=false

# rewrite <file> <awk-regex> <replacement line>
rewrite() {
    file=$1
    re=$2
    replacement=$3
    tmp=$(mktemp)

    if ! awk -v re="$re" -v rep="$replacement" '
        BEGIN { done = 0 }
        !done && $0 ~ re { print rep; done = 1; next }
        { print }
        END { if (!done) exit 1 }
    ' "$file" >"$tmp"; then
        rm -f "$tmp"
        echo "sync-version: no line matching /$re/ in $file" >&2
        exit 1
    fi

    if $check; then
        if cmp -s "$file" "$tmp"; then
            rm -f "$tmp"
        else
            rm -f "$tmp"
            echo "sync-version: out of date: $file (want version $VERSION)" >&2
            failed=true
        fi
    else
        if cmp -s "$file" "$tmp"; then
            rm -f "$tmp"
            echo "in sync: $file"
        else
            # cat, not mv: keeps the file's mode and inode (build-pkg.sh is +x)
            cat "$tmp" >"$file"
            rm -f "$tmp"
            echo "updated: $file -> $VERSION"
        fi
    fi
}

rewrite packaging/nfpm.yaml '^version: ' "version: \"$VERSION\""
rewrite packaging/arch/PKGBUILD '^pkgver=' "pkgver=$VERSION"
rewrite packaging/windows/mdrender-send.iss '^#define AppVersion ' "#define AppVersion \"$VERSION\""
rewrite packaging/macos/build-pkg.sh '^VERSION=' "VERSION=\"$VERSION\""

if $check; then
    if $failed; then
        echo "sync-version: packaging versions differ from version.properties ($VERSION)" >&2
        exit 1
    fi
    echo "sync-version: packaging versions match version.properties ($VERSION)"
fi
