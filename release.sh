#!/bin/bash
# Release script for MDRender
# Usage: ./release.sh [patch|minor|major] [version_code] [--pre-release] [--publish-internal-testing]
#   If version_code is provided, it will be used instead of auto-incrementing
#   If --pre-release is provided, creates a pre-release version (e.g., v2.9.9-rc.0)
#   If --publish-internal-testing is provided, publishes to Google Play Internal Testing

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
BUMP_TYPE=""
SPECIFIED_CODE=""
PRE_RELEASE=false
AUTO_PUBLISH=false

# First positional argument is bump type
if [[ -n "$1" ]] && [[ ! "$1" =~ ^-- ]]; then
    BUMP_TYPE=$1
    shift
fi

# Second positional argument might be version code
if [[ -n "$1" ]] && [[ ! "$1" =~ ^-- ]] && [[ "$1" =~ ^[0-9]+$ ]]; then
    SPECIFIED_CODE=$1
    shift
fi

# Check for flags
for arg in "$@"; do
    case "$arg" in
        --pre-release)
            PRE_RELEASE=true
            ;;
        --publish-internal-testing|--publish)
            AUTO_PUBLISH=true
            ;;
        *)
            echo -e "${RED}Error: Unknown flag '${arg}'${NC}"
            echo "Usage: ./release.sh [patch|minor|major] [version_code] [--pre-release] [--publish-internal-testing]"
            exit 1
            ;;
    esac
done

# Default to patch if no bump type specified
BUMP_TYPE=${BUMP_TYPE:-patch}

echo -e "${GREEN}=== MDRender Release Script ===${NC}"
echo ""

# 1. Check if git working directory is clean
echo "1. Checking git status..."
if [[ -n $(git status -s) ]]; then
    echo -e "${RED}Error: Git working directory is not clean!${NC}"
    echo "Please commit or stash your changes first:"
    git status -s
    exit 1
fi
echo -e "${GREEN}✓ Git working directory is clean${NC}"
echo ""

# 2. Read current version
echo "2. Reading current version..."
source version.properties
CURRENT_VERSION="${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}"
CURRENT_PRERELEASE="${VERSION_PRERELEASE:-}"
CURRENT_CODE=${VERSION_CODE}

if [[ -n "$CURRENT_PRERELEASE" ]]; then
    CURRENT_VERSION_FULL="${CURRENT_VERSION}-${CURRENT_PRERELEASE}"
else
    CURRENT_VERSION_FULL="${CURRENT_VERSION}"
fi

echo "   Current version: ${CURRENT_VERSION_FULL} (code ${CURRENT_CODE})"
echo ""

# 3. Calculate new version
echo "3. Calculating new version..."
NEW_MAJOR=$VERSION_MAJOR
NEW_MINOR=$VERSION_MINOR
NEW_PATCH=$VERSION_PATCH
NEW_PRERELEASE=""

# Use specified code if provided, otherwise increment
if [[ -n "$SPECIFIED_CODE" ]]; then
    NEW_CODE=$SPECIFIED_CODE
    echo -e "   ${YELLOW}Using specified version code: ${NEW_CODE}${NC}"
else
    NEW_CODE=$((VERSION_CODE + 1))
fi

# Handle pre-release versioning
if [[ "$PRE_RELEASE" == true ]]; then
    if [[ -n "$CURRENT_PRERELEASE" ]]; then
        # Increment existing pre-release version
        # Extract rc number (e.g., "rc.0" -> 0)
        if [[ "$CURRENT_PRERELEASE" =~ rc\.([0-9]+)$ ]]; then
            RC_NUM=${BASH_REMATCH[1]}
            NEW_RC_NUM=$((RC_NUM + 1))
            NEW_PRERELEASE="rc.${NEW_RC_NUM}"
            # Keep same base version
        else
            # Start new rc sequence
            NEW_PRERELEASE="rc.0"
        fi
    else
        # First pre-release, bump version and add rc.0
        case $BUMP_TYPE in
            major)
                NEW_MAJOR=$((VERSION_MAJOR + 1))
                NEW_MINOR=0
                NEW_PATCH=0
                ;;
            minor)
                NEW_MINOR=$((VERSION_MINOR + 1))
                NEW_PATCH=0
                ;;
            patch)
                NEW_PATCH=$((VERSION_PATCH + 1))
                ;;
            *)
                echo -e "${RED}Error: Invalid bump type '${BUMP_TYPE}'. Use: patch, minor, or major${NC}"
                exit 1
                ;;
        esac
        NEW_PRERELEASE="rc.0"
    fi
else
    # Regular release (not pre-release)
    if [[ -n "$CURRENT_PRERELEASE" ]]; then
        # Graduating from pre-release to stable
        echo -e "   ${BLUE}Graduating from pre-release to stable${NC}"
        # Keep the version numbers, just remove pre-release suffix
        NEW_PRERELEASE=""
    else
        # Normal version bump
        case $BUMP_TYPE in
            major)
                NEW_MAJOR=$((VERSION_MAJOR + 1))
                NEW_MINOR=0
                NEW_PATCH=0
                ;;
            minor)
                NEW_MINOR=$((VERSION_MINOR + 1))
                NEW_PATCH=0
                ;;
            patch)
                NEW_PATCH=$((VERSION_PATCH + 1))
                ;;
            *)
                echo -e "${RED}Error: Invalid bump type '${BUMP_TYPE}'. Use: patch, minor, or major${NC}"
                exit 1
                ;;
        esac
    fi
fi

NEW_VERSION="${NEW_MAJOR}.${NEW_MINOR}.${NEW_PATCH}"
if [[ -n "$NEW_PRERELEASE" ]]; then
    NEW_VERSION_FULL="${NEW_VERSION}-${NEW_PRERELEASE}"
else
    NEW_VERSION_FULL="${NEW_VERSION}"
fi

echo "   New version: ${NEW_VERSION_FULL} (code ${NEW_CODE})"
if [[ "$PRE_RELEASE" == true ]]; then
    echo -e "   ${BLUE}Pre-release mode enabled${NC}"
fi
echo ""

# 4. Confirm with user
read -p "Continue with release ${NEW_VERSION_FULL}? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Release cancelled.${NC}"
    exit 0
fi
echo ""

# 5. Update version.properties
echo "4. Updating version.properties..."
cat > version.properties << EOF
# Version configuration for MDRender
# Update these values to bump version across all build files

VERSION_MAJOR=${NEW_MAJOR}
VERSION_MINOR=${NEW_MINOR}
VERSION_PATCH=${NEW_PATCH}
VERSION_CODE=${NEW_CODE}
EOF

# Add pre-release suffix if present
if [[ -n "$NEW_PRERELEASE" ]]; then
    echo "VERSION_PRERELEASE=${NEW_PRERELEASE}" >> version.properties
fi

echo -e "${GREEN}✓ Version file updated${NC}"
echo ""

# 6. Commit version bump
echo "5. Committing version bump..."
git add version.properties
if [[ "$PRE_RELEASE" == true ]]; then
    git commit -m "chore: Bump version to ${NEW_VERSION_FULL} (code ${NEW_CODE}) [pre-release]"
else
    git commit -m "chore: Bump version to ${NEW_VERSION_FULL} (code ${NEW_CODE})"
fi
echo -e "${GREEN}✓ Version committed${NC}"
echo ""

# 7. Create git tag
echo "6. Creating git tag..."
TAG_NAME="v${NEW_VERSION_FULL}"
if [[ "$PRE_RELEASE" == true ]]; then
    git tag -a "$TAG_NAME" -m "Pre-release version ${NEW_VERSION_FULL}"
else
    git tag -a "$TAG_NAME" -m "Release version ${NEW_VERSION_FULL}"
fi
echo -e "${GREEN}✓ Tag ${TAG_NAME} created${NC}"
echo ""

# 8. Build signed release
echo "7. Building signed release AAB..."
./gradlew clean bundleRelease
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Build successful${NC}"
else
    echo -e "${RED}Error: Build failed!${NC}"
    echo "Rolling back..."
    git tag -d "$TAG_NAME"
    git reset --hard HEAD~1
    exit 1
fi
echo ""

# 9. Verify AAB exists
AAB_PATH="app/build/outputs/bundle/release/app-release.aab"
if [ -f "$AAB_PATH" ]; then
    AAB_SIZE=$(ls -lh "$AAB_PATH" | awk '{print $5}')
    echo -e "${GREEN}✓ AAB created: ${AAB_PATH} (${AAB_SIZE})${NC}"
else
    echo -e "${RED}Error: AAB file not found!${NC}"
    exit 1
fi
echo ""

# 10. Generate release notes
echo "8. Generating release notes..."
RELEASE_DATE=$(date +%Y-%m-%d)
BUILD_DATE=$(date "+%Y-%m-%d %H:%M:%S")

# Get commits since last tag
LAST_TAG=$(git describe --tags --abbrev=0 HEAD~1 2>/dev/null || echo "")
if [[ -n "$LAST_TAG" ]]; then
    COMMIT_LOG=$(git log ${LAST_TAG}..HEAD --pretty=format:"• %s" --no-merges)
else
    COMMIT_LOG=$(git log --pretty=format:"• %s" --no-merges -10)
fi

RELEASE_TYPE="RELEASE"
if [[ "$PRE_RELEASE" == true ]]; then
    RELEASE_TYPE="PRE-RELEASE"
fi

cat > RELEASE_NOTES.txt << EOF
===============================================================================
MDRender - Release Notes
Version ${NEW_VERSION_FULL} (Build ${NEW_CODE}) [${RELEASE_TYPE}]
Release Date: ${RELEASE_DATE}
Build Date: ${BUILD_DATE}
===============================================================================

CHANGES IN THIS RELEASE:
------------------------
${COMMIT_LOG}


===============================================================================
PLAY STORE RELEASE NOTES (500 character limit):
===============================================================================

EDIT THIS SECTION BEFORE UPLOADING TO PLAY STORE!

New in this release:
• [Describe major new features]
• [Describe improvements]
• [Describe fixes]

Refer to the commit messages above for details.

===============================================================================
SHORT CHANGELOG:
===============================================================================

EDIT THIS SECTION BEFORE UPLOADING TO PLAY STORE!

• [Summary of changes]
• [Key improvements]
• [Important fixes]

===============================================================================

NOTE: Edit RELEASE_NOTES.txt to customize the Play Store release notes section
      based on the commit messages listed above. The Play Store section has a
      500 character limit.

EOF

echo -e "${GREEN}✓ Release notes generated: RELEASE_NOTES.txt${NC}"
echo ""

# 9. Generate Play Store release notes
echo "9. Generating Play Store release notes..."
PLAY_NOTES_DIR="app/src/main/play/release-notes/en-US"
mkdir -p "$PLAY_NOTES_DIR"

# Extract short changelog for Play Store (max 500 chars)
PLAY_NOTES_PREFIX=""
if [[ "$PRE_RELEASE" == true ]]; then
    PLAY_NOTES_PREFIX="[PRE-RELEASE] "
fi

PLAY_NOTES=$(cat <<EOF
${PLAY_NOTES_PREFIX}New in version ${NEW_VERSION_FULL}:

${COMMIT_LOG}

Bug fixes and performance improvements.
EOF
)

# Truncate to 500 characters if needed
PLAY_NOTES_SHORT=$(echo "$PLAY_NOTES" | head -c 500)

echo "$PLAY_NOTES_SHORT" > "${PLAY_NOTES_DIR}/default.txt"
echo -e "${GREEN}✓ Play Store release notes generated${NC}"
echo ""

# 10. Publish to Google Play (if requested)
if [[ "$AUTO_PUBLISH" == true ]]; then
    echo "10. Publishing to Google Play Internal Testing..."

    # Check if service account file exists
    if [[ ! -f "play-service-account.json" ]]; then
        echo -e "${RED}Error: play-service-account.json not found!${NC}"
        echo "To enable automatic publishing:"
        echo "1. Go to Google Play Console -> API Access"
        echo "2. Create or use existing service account"
        echo "3. Download JSON key and save as play-service-account.json"
        echo ""
        echo -e "${YELLOW}Skipping automatic publishing...${NC}"
    else
        # Publish using Gradle Play Publisher plugin
        echo "Publishing AAB to Internal Testing track..."
        ./gradlew publishReleaseBundle --track=internal

        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓ Successfully published to Google Play Internal Testing!${NC}"
            echo ""
        else
            echo -e "${RED}Error: Publishing failed!${NC}"
            echo "The build was successful but publishing failed."
            echo "You can manually upload the AAB file."
            echo ""
        fi
    fi
else
    echo "10. Skipping automatic publishing (use --publish-internal-testing flag to enable)"
fi
echo ""

# 11. Summary
echo -e "${GREEN}=== Release Complete ===${NC}"
echo ""
echo "Version: ${NEW_VERSION_FULL} (code ${NEW_CODE})"
if [[ "$PRE_RELEASE" == true ]]; then
    echo -e "Type: ${BLUE}Pre-release${NC}"
else
    echo "Type: Stable Release"
fi
echo "Tag: ${TAG_NAME}"
echo "AAB: ${AAB_PATH}"
echo "Release Notes: RELEASE_NOTES.txt"
if [[ "$AUTO_PUBLISH" == true ]] && [[ -f "play-service-account.json" ]]; then
    echo "Published: Yes (Internal Testing)"
else
    echo "Published: No"
fi
echo ""
echo -e "${YELLOW}Next steps:${NC}"
if [[ "$AUTO_PUBLISH" == true ]] && [[ -f "play-service-account.json" ]]; then
    echo "1. Review release in Google Play Console"
    if [[ "$PRE_RELEASE" == true ]]; then
        echo "2. Test the pre-release with internal testers"
        echo "3. Run './release.sh patch' (without --pre-release) when ready for stable release"
        echo "4. Push to remote: git push && git push --tags"
    else
        echo "2. Promote to other tracks if needed (alpha, beta, production)"
        echo "3. Push to remote: git push && git push --tags"
    fi
else
    echo "1. Review and edit RELEASE_NOTES.txt if needed"
    echo "2. Test the AAB file"
    if [[ "$PRE_RELEASE" == true ]]; then
        echo "3. Upload to Google Play Internal Testing for testing"
        echo "4. Run './release.sh patch' (without --pre-release) when ready for stable release"
    fi
    echo "3. Push to remote: git push && git push --tags"
    echo "4. Upload ${AAB_PATH} to Google Play Console manually"
    echo "   OR run: ./gradlew publishReleaseBundle --track=internal"
    echo "5. Use content from RELEASE_NOTES.txt for Play Store release notes"
fi
echo ""
