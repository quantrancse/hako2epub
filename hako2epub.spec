# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path


def _pw_root():
    env = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
    if env:
        return Path(env)
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA')
        if local:
            return Path(local) / 'ms-playwright'
    return Path.home() / '.cache' / 'ms-playwright'


_root = _pw_root()
_datas = []
if _root.exists():
    for d in sorted(_root.iterdir()):
        # Only what the tool launches: Chromium + its headless shell.
        if d.is_dir() and d.name.startswith('chromium'):
            _datas.append((str(d), os.path.join('ms-playwright', d.name)))

a = Analysis(['hako2epub.py'],
             pathex=[],
             binaries=[],
             datas=_datas,
             hiddenimports=[],
             hookspath=[],
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=None,
             noarchive=False)
pyz = PYZ(a.pure, a.zipped_data,
          cipher=None)
exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          [],
          name='hako2epub',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          upx_exclude=[],
          runtime_tmpdir=None,
          console=True,
          icon='images/favicon.ico')
