# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main_app.py', 'app.py'],
    pathex=[],
    binaries=[('bin/ffmpeg', 'bin'), ('bin/ffprobe', 'bin')],
    datas=[('static', 'static'), ('select_file.py', '.')],
    hiddenImports=['PySide6', 'PySide6.QtWidgets', 'PySide6.QtCore', 'PySide6.QtGui'],
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
    name='Tapeless VTR Editor',
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
    icon=['insertcut_icon_1024.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Tapeless VTR Editor',
)
app = BUNDLE(
    coll,
    name='Tapeless VTR Editor.app',
    icon='insertcut_icon_1024.icns',
    bundle_identifier='com.manus.tapeless-vtr-editor',
    info_plist={
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': 'True',
    },
)
