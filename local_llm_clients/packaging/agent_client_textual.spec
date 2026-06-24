# -*- mode: python ; coding: utf-8 -*-

"""PyInstaller spec for the local file-agent Textual launcher."""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from pathlib import Path


ROOT = Path(SPECPATH).resolve().parents[1]


block_cipher = None

hiddenimports = collect_submodules("textual") + collect_submodules("rich")

datas = [
    (str(ROOT / "local_llm_clients" / "config"), "local_llm_clients/config"),
    (str(ROOT / "local_llm_clients" / "docs"), "local_llm_clients/docs"),
]
datas += collect_data_files("textual")
datas += collect_data_files("rich")

a = Analysis(
    [str(ROOT / "agent_client_textual.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="agent_client_textual",
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="agent_client_textual",
)
