# server/app/releases.py
"""The latest mdrender-send release assets, for the Clients page download links.

Best-effort and cached: the GitHub API is rate-limited and may be unreachable,
so callers always get a dict (falling back to the releases page URL).
"""
import json
import time
import urllib.request

_API = "https://api.github.com/repos/{repo}/releases/latest"
_TTL_SECONDS = 1800
_cache = {"at": 0.0, "data": None}

# filename suffix/pattern -> human platform label
_PLATFORMS = (
    ("setup", "Windows"),
    (".exe", "Windows"),
    (".pkg.tar", "Arch Linux"),
    (".deb", "Debian / Ubuntu"),
    (".rpm", "Fedora / openSUSE"),
    (".apk", "Alpine"),
    (".pkg", "macOS"),
    (".dmg", "macOS"),
)


def _platform(name: str) -> str:
    lowered = name.lower()
    for needle, label in _PLATFORMS:
        if needle in lowered:
            return label
    return "Other"


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "mdrender-push"})
    with urllib.request.urlopen(req, timeout=6) as resp:
        return json.load(resp)


def latest(config, *, now=None) -> dict:
    """Return {"page", "tag", "assets": [{"name", "url", "platform"}]}."""
    page = getattr(config, "RELEASES_URL", "")
    repo = getattr(config, "RELEASE_REPO", "")
    result = {"page": page, "tag": "", "assets": []}
    if not repo:
        return result
    now = now if now is not None else time.time()
    if _cache["data"] is not None and now - _cache["at"] < _TTL_SECONDS:
        return _cache["data"]
    try:
        release = _fetch(_API.format(repo=repo))
        result["tag"] = release.get("tag_name", "")
        result["page"] = release.get("html_url") or page
        for asset in release.get("assets", []):
            name = asset.get("name", "")
            result["assets"].append({
                "name": name,
                "url": asset.get("browser_download_url", ""),
                "platform": _platform(name),
            })
    except Exception:  # noqa: BLE001 - fall back to the releases page link
        pass
    _cache.update(at=now, data=result)
    return result
