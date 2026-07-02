#!/bin/bash
# Quick version bump utility for development
# Usage: ./bump_version.sh patch|minor|major

set -e

BUMP_TYPE=${1:-patch}

source version.properties
CURRENT="${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}"

case $BUMP_TYPE in
    major) NEW_MAJOR=$((VERSION_MAJOR + 1)); NEW_MINOR=0; NEW_PATCH=0 ;;
    minor) NEW_MAJOR=$VERSION_MAJOR; NEW_MINOR=$((VERSION_MINOR + 1)); NEW_PATCH=0 ;;
    patch) NEW_MAJOR=$VERSION_MAJOR; NEW_MINOR=$VERSION_MINOR; NEW_PATCH=$((VERSION_PATCH + 1)) ;;
    *) echo "Usage: $0 [patch|minor|major]"; exit 1 ;;
esac

NEW_VERSION="${NEW_MAJOR}.${NEW_MINOR}.${NEW_PATCH}"
NEW_CODE=$((VERSION_CODE + 1))

echo "Bumping: $CURRENT → $NEW_VERSION (code $NEW_CODE)"

git checkout "$(git rev-parse --abbrev-ref HEAD)"

cat > version.properties << EOF
VERSION_MAJOR=${NEW_MAJOR}
VERSION_MINOR=${NEW_MINOR}
VERSION_PATCH=${NEW_PATCH}
VERSION_CODE=${NEW_CODE}
EOF

git add version.properties
git commit -m "chore: bump version to ${NEW_VERSION} (code ${NEW_CODE})"
echo "Committed. Tag manually with: git tag -a v${NEW_VERSION} -m 'v${NEW_VERSION}'"
