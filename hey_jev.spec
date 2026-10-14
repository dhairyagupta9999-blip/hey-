# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for Hey Jev on Windows 10/11 x64.

Produces a windowed, one-dir distribution (`dist/Hey Jev/Hey Jev.exe`)
without console window popping up on launch.
"""
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

datas = [
    ('apps.json', '.'),
    ('vocabulary.example.json', '.'),
    ('assets', 'assets'),
]

# Sound clips if present
if os.path.exists('assets/sounds'):
    datas.append(('assets/sounds', 'assets/sounds'))

hiddenimports = [
    'pycaw',
    'pycaw.pycaw',
    'pycaw.constants',
    'comtypes',
    'comtypes.client',
    'pystray',
    'PIL',
    'PIL.Image',
    'pywinauto',
    'pywinauto.application',
    'pywinauto.keyboard',
    'pywinauto.mouse',
    'sounddevice',
    'soundfile',
    'numpy',
    'faster_whisper',
    'ctranslate2',
    'keyring',
    'keyring.backends',
    'keyring.backends.Windows',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'requests',
    'backend',
    'actions_win',
    'audio_io',
    'stt',
    'tts',
    'timers',
    'logger',
    'config',
    'dictation',
    'bubble',
    'assistant_ui',
    'secrets_store',
    'hotkey',
    'autostart',
]

try:
    datas += collect_data_files('faster_whisper')
except Exception:
    pass
try:
    datas += collect_data_files('soundfile')
except Exception:
    pass

icon_path = 'assets/icon.ico' if os.path.exists('assets/icon.ico') else None

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'torchaudio', 'transformers',
        'scipy', 'matplotlib', 'tkinter', 'unittest'
    ],
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
    name='Hey Jev',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Hey Jev',
)
