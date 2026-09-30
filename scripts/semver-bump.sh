#!/bin/bash
# Compute the next release version from conventional commits, apply it
# everywhere, write the release announcement, commit and tag.
#
#   scripts/semver-bump.sh [--auto|major|minor|patch] [--dry-run]
#
#   --auto       derive the bump from commits since the last version bump
#                (default): BREAKING CHANGE / `type!: ` -> major,
#                feat -> minor, fix/perf -> patch. Anything else means there
#                is nothing to release.
#   major|minor|patch
#                force that bump, whatever the commits say.
#   --dry-run    write version.properties, the packaging copies and the
#                announcement, but do not commit or tag.
#
# Pre-releases: if version.properties carries VERSION_PRERELEASE the release
# graduates instead of bumping (same rule as ./release.sh).
#
# When $GITHUB_OUTPUT is set, emits step outputs:
#   should_release, version, code, tag, sha, dry_run
#
# Pushes the release commit and tag only when running in GitHub Actions and
# --dry-run was not given.
set -euo pipefail

cd "$(dirname "$0")/.."

BUMP=auto
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --auto) BUMP=auto ;;
        --dry-run) DRY_RUN=true ;;
        major|minor|patch) BUMP=$arg ;;
        -h|--help)
            sed -n '2,22p' "$0"
            exit 0
            ;;
        *)
            echo "semver-bump: unknown argument '$arg'" >&2
            exit 2
            ;;
    esac
done

emit() { # key=value
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        printf '%s\n' "$1" >>"$GITHUB_OUTPUT"
    fi
}

# ---------------------------------------------------------------- current
# shellcheck disable=SC1091
source version.properties
BASE_MAJOR=$VERSION_MAJOR
BASE_MINOR=$VERSION_MINOR
BASE_PATCH=$VERSION_PATCH
BASE_CODE=$VERSION_CODE
PRERELEASE="${VERSION_PRERELEASE:-}"
BASE_VERSION="${BASE_MAJOR}.${BASE_MINOR}.${BASE_PATCH}"
if [[ -n "$PRERELEASE" ]]; then
    BASE_VERSION_FULL="${BASE_VERSION}-${PRERELEASE}"
else
    BASE_VERSION_FULL="${BASE_VERSION}"
fi

# ---------------------------------------------------------- commit window
# The last commit that touched version.properties *is* the last release, so
# the changelog window never depends on tags being in sync with the version
# (they are not: the repo only carries v1.0.11).
SINCE=$(git log -1 --format=%H -- version.properties 2>/dev/null || true)
if [[ -n "$SINCE" ]]; then
    window="${SINCE}..HEAD"
else
    window="--max-count=50"
fi

subjects=$(git log "$window" --pretty=format:'%s' 2>/dev/null || true)
bodies=$(git log "$window" --pretty=format:'%b' 2>/dev/null || true)

detected=none
if [[ -n "$subjects" ]]; then
    if grep -qE '^[a-z]+(\([^)]*\))?!:' <<<"$subjects" ||
        grep -qE '^BREAKING CHANGE:' <<<"$bodies"; then
        detected=major
    elif grep -qE '^feat(\(|:|!)' <<<"$subjects"; then
        detected=minor
    elif grep -qE '^(fix|perf)(\(|:|!)' <<<"$subjects"; then
        detected="patch"
    fi
fi

if [[ -n "$PRERELEASE" ]]; then
    # Graduating from a pre-release: keep the numbers, drop the suffix.
    effective=graduate
elif [[ "$BUMP" != auto ]]; then
    effective=$BUMP
else
    effective=$detected
fi

if [[ "$effective" == none ]]; then
    echo "semver-bump: no feat/fix/perf/BREAKING CHANGE commits since the last release"
    echo "semver-bump: nothing to release (current version ${BASE_VERSION_FULL})"
    emit "should_release=false"
    emit "version=${BASE_VERSION}"
    emit "code=${BASE_CODE}"
    emit "tag=v${BASE_VERSION}"
    emit "sha=$(git rev-parse HEAD)"
    emit "dry_run=${DRY_RUN}"
    exit 0
fi

# ------------------------------------------------------------- new version
case "$effective" in
    graduate) NEW_MAJOR=$BASE_MAJOR; NEW_MINOR=$BASE_MINOR; NEW_PATCH=$BASE_PATCH ;;
    major) NEW_MAJOR=$((BASE_MAJOR + 1)); NEW_MINOR=0; NEW_PATCH=0 ;;
    minor) NEW_MAJOR=$BASE_MAJOR; NEW_MINOR=$((BASE_MINOR + 1)); NEW_PATCH=0 ;;
    patch) NEW_MAJOR=$BASE_MAJOR; NEW_MINOR=$BASE_MINOR; NEW_PATCH=$((BASE_PATCH + 1)) ;;
esac
NEW_CODE=$((BASE_CODE + 1))
NEW_VERSION="${NEW_MAJOR}.${NEW_MINOR}.${NEW_PATCH}"
TAG="v${NEW_VERSION}"

if ! $DRY_RUN && [[ -n "$(git status --porcelain)" ]]; then
    echo "semver-bump: working tree is not clean; commit or stash first" >&2
    git status --short >&2
    exit 1
fi

echo "semver-bump: ${BASE_VERSION_FULL} (code ${BASE_CODE}) -> ${NEW_VERSION} (code ${NEW_CODE})"
echo "semver-bump: bump=${effective} (detected=${detected}) dry_run=${DRY_RUN}"

# -------------------------------------------------------- write everything
cat >version.properties <<EOF
# Version configuration for MDRender
# Update these values to bump version across all build files

VERSION_MAJOR=${NEW_MAJOR}
VERSION_MINOR=${NEW_MINOR}
VERSION_PATCH=${NEW_PATCH}
VERSION_CODE=${NEW_CODE}
EOF

./scripts/sync-version.sh

# ------------------------------------------------------------ announcement
repo_slug="${GITHUB_REPOSITORY:-}"
if [[ -z "$repo_slug" ]]; then
    remote=$(git config --get remote.origin.url || true)
    repo_slug=$(sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##' <<<"$remote")
fi
# Only trust the slug when it actually looks like owner/repo - a local clone
# can have a filesystem path as its origin.
if [[ "$repo_slug" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    release_url="https://github.com/${repo_slug}/releases/tag/${TAG}"
    image_ref="ghcr.io/${repo_slug%%/*}/mdrender-server:${NEW_VERSION}"
else
    release_url=""
    image_ref=""
fi
released_on=$(date +%Y-%m-%d)

section() { # <title> <awk-regex>
    local title=$1 re=$2 found=false line
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        if [[ "$found" == false ]]; then
            printf '\n### %s\n\n' "$title"
            found=true
        fi
        printf '* %s\n' "$line"
    done < <(grep -E "$re" <<<"$subjects" || true)
}

announcement="docs/releases/${TAG}.md"
mkdir -p docs/releases
{
    echo "# MDRender ${NEW_VERSION}"
    echo
    echo "Released ${released_on}."
    echo
    echo "## Downloads"
    echo
    echo "* **Android**: \`mdrender-${NEW_VERSION}-release-unsigned.apk\` (sideload; not Play-signed)"
    echo "* **Linux**: \`.deb\` / \`.rpm\` / Alpine \`.apk\` / Arch \`.pkg.tar.zst\`"
    echo "* **CLI**: \`mdrender-send\` standalone binary (Linux, Windows, macOS) and installers"
    if [[ -n "$image_ref" ]]; then
        echo "* **Server**: \`docker pull ${image_ref}\`"
    fi
    echo
    if [[ -n "$release_url" ]]; then
        echo "All assets: ${release_url}"
    else
        echo "All assets: see the GitHub Releases page for this tag."
    fi
    section "Breaking changes" '^[a-z]+(\([^)]*\))?!:'
    section "Features" '^feat(\(|:|!)'
    section "Fixes" '^(fix|perf)(\(|:|!)'
    section "Maintenance" '^(chore|refactor|docs|style|test|ci|build)(\(|:|!)'
    echo
} >"$announcement"
echo "semver-bump: wrote ${announcement}"

if $DRY_RUN; then
    echo "semver-bump: dry run - nothing committed or tagged"
    emit "should_release=true"
    emit "version=${NEW_VERSION}"
    emit "code=${NEW_CODE}"
    emit "tag=${TAG}"
    emit "sha=$(git rev-parse HEAD)"
    emit "dry_run=true"
    exit 0
fi

# ------------------------------------------------------------- commit + tag
git add version.properties \
    packaging/nfpm.yaml \
    packaging/arch/PKGBUILD \
    packaging/windows/mdrender-send.iss \
    packaging/macos/build-pkg.sh \
    "$announcement"

git commit -m "chore(release): ${TAG} (code ${NEW_CODE}) [skip ci]"
git tag -a "$TAG" -m "Release ${NEW_VERSION}"
HEAD_SHA=$(git rev-parse HEAD)

if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    if ! git push origin HEAD || ! git push origin "$TAG"; then
        echo "semver-bump: push failed." >&2
        echo "  If master is protected, allow github-actions[bot] to push" >&2
        echo "  (Settings > Rules > master), or the release commit stays local." >&2
        exit 1
    fi
    echo "semver-bump: pushed release commit and ${TAG}"
else
    echo "semver-bump: created commit and ${TAG} locally - push with:"
    echo "  git push origin HEAD && git push origin ${TAG}"
fi

emit "should_release=true"
emit "version=${NEW_VERSION}"
emit "code=${NEW_CODE}"
emit "tag=${TAG}"
emit "sha=${HEAD_SHA}"
emit "dry_run=false"
