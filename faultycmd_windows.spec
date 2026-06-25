# faultycmd_windows.spec
# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

datas = [('VERSION', '.')]
binaries = []
hiddenimports = [
    'click',
    'serial',
    'serial.tools.list_ports',
]

for package in ['textual', 'rich']:
    tmp_ret = collect_all(package)
    datas.extend(tmp_ret[0])
    binaries.extend(tmp_ret[1])
    hiddenimports.extend(tmp_ret[2])

extra_files = [
    ('README.md', '.'),
]
for src, dst in extra_files:
    if os.path.exists(src):
        datas.append((src, dst))

a = Analysis(
    ['src/faultycmd/__main__.py'],
    pathex=['src'],
    binaries=binaries,
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
    name='faultycmd',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='faultycmd',
)
