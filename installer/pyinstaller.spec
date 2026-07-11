# PyInstaller specification for the yt-dlp Dropper frontend application.
# Build command:
# pyinstaller --onefile --name ytdlp-dropper app.py

block_cipher = None

from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

analysis = Analysis(
    ["app.py"],
    pathex=[".."],
    binaries=[],
    datas=[],
    hiddenimports=["socketio", "requests"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ytdlp-dropper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    strip=False,
    upx=False,
    name="ytdlp-dropper",
)
