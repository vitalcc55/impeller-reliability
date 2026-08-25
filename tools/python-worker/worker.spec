# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

worker_root = Path(SPECPATH)
entry_point = worker_root / "src" / "impeller_reliability" / "worker" / "main.py"
runtime_datas = []
for distribution_name in ("impeller-reliability-worker", "pydantic", "numpy", "scipy"):
    runtime_datas += copy_metadata(distribution_name)

analysis = Analysis(
    [str(entry_point)],
    pathex=[str(worker_root / "src")],
    binaries=[],
    datas=runtime_datas,
    hiddenimports=["numpy", "scipy"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
python_archive = PYZ(analysis.pure)
executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="impeller-reliability-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=True,
)
bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    name="impeller-reliability-worker",
)
