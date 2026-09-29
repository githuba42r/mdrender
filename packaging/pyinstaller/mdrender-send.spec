# PyInstaller spec: build a single-file `mdrender-send` executable.
#
# PyInstaller cannot cross-compile, so build once per target OS:
#   python -m PyInstaller --clean --noconfirm packaging/pyinstaller/mdrender-send.spec
# The result lands in dist/ (mdrender-send, or mdrender-send.exe on Windows).
import os

repo = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
script = os.path.join(repo, "tools", "localsend-send", "localsend-send.py")

a = Analysis(
    [script],
    pathex=[repo],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="mdrender-send",
    console=True,
    upx=False,
    strip=False,
)
