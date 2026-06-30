# -*- mode: python ; coding: utf-8 -*-
import os
import sys

block_cipher = None

# Paths to bundled external binaries
poppler_dir = os.path.join('bundled', 'poppler')
unrar_dir = os.path.join('bundled', 'unrar')

# Collect all poppler binaries
poppler_bins = [(os.path.join(poppler_dir, f), 'poppler')
                for f in os.listdir(poppler_dir)]

# Collect UnRAR
unrar_bins = [(os.path.join(unrar_dir, 'UnRAR.exe'), 'unrar')]

a = Analysis(
    ['comic_cruncher.py'],
    pathex=[],
    binaries=poppler_bins + unrar_bins,
    datas=[],
    hiddenimports=[
        'PyQt6.QtWidgets',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PIL',
        'PIL.Image',
        'PIL.WebPImagePlugin',
        'pdf2image',
        'rarfile',
        'cv2',
        'numpy',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
        'pandas',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
    ],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ComicCruncher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No console window - GUI app
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ComicCruncher',
)
