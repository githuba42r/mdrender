# server/tests/test_releases.py
"""Latest CLI release assets for the Clients page download links."""
from server.app import releases


def _reset(monkeypatch):
    monkeypatch.setattr(releases, "_cache", {"at": 0.0, "data": None})


def test_latest_classifies_assets_by_platform(config, monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(releases, "_fetch", lambda url: {
        "tag_name": "v1.0.15",
        "html_url": "https://github.com/githuba42r/mdrender/releases/tag/v1.0.15",
        "assets": [
            {"name": "mdrender-send-1.0.15-1-any.pkg.tar.zst", "browser_download_url": "u1"},
            {"name": "mdrender-send_1.0.15_all.deb", "browser_download_url": "u2"},
            {"name": "mdrender-send-1.0.15-1.noarch.rpm", "browser_download_url": "u3"},
            {"name": "mdrender-send_1.0.15_noarch.apk", "browser_download_url": "u5"},
            {"name": "mdrender-send-1.0.15.pkg", "browser_download_url": "u4"},
            {"name": "mdrender-send-Setup-1.0.15.exe", "browser_download_url": "u6"},
        ]})

    release = releases.latest(config)
    by_name = {a["name"]: a["platform"] for a in release["assets"]}
    assert release["tag"] == "v1.0.15"
    assert by_name["mdrender-send_1.0.15_all.deb"] == "Debian / Ubuntu"
    assert by_name["mdrender-send-1.0.15-1.noarch.rpm"] == "Fedora / openSUSE"
    assert by_name["mdrender-send-1.0.15-1-any.pkg.tar.zst"] == "Arch Linux"
    assert by_name["mdrender-send_1.0.15_noarch.apk"] == "Alpine"
    assert by_name["mdrender-send-1.0.15.pkg"] == "macOS"
    assert by_name["mdrender-send-Setup-1.0.15.exe"] == "Windows"


def test_latest_falls_back_to_the_releases_page_on_error(config, monkeypatch):
    _reset(monkeypatch)

    def boom(url):
        raise RuntimeError("no network")

    monkeypatch.setattr(releases, "_fetch", boom)
    release = releases.latest(config)
    assert release["assets"] == []
    assert release["page"].startswith("https://github.com/")
