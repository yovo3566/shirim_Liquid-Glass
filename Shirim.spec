# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = [('web', 'web'), ('build-updates', 'updates'), ('desktop/shirim-filtered.ico', '.')]
hiddenimports = ['webview', 'pywebview', 'certifi', 'yt_dlp', 'requests', 'qrcode', 'ytmusicapi', 'mutagen', 'mutagen.id3', 'mutagen.mp4', 'mutagen.flac', 'mutagen.ogg', 'websockets', 'Cryptodome', 'brotli', 'curl_cffi', 'urllib3']
datas += collect_data_files('ytmusicapi')
datas += collect_data_files('yt_dlp_ejs')
hiddenimports += collect_submodules('qrcode')


a = Analysis(
    ['desktop/shirim_desktop.py'],
    pathex=['core'],
    binaries=[('vendor/win/ffmpeg.exe', 'bin'), ('vendor/win/deno.exe', 'bin')],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Shirim',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['desktop/shirim.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Shirim',
)
