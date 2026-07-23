# This file is used by other spec files

import os, shutil, subprocess, sys

excludes = ['bz2', 'cffi', 'Crypto', 'doctest', 'ftplib', 'gmpy', 'gmpy2', 'lzma', 'M2Crypto', 'numpy', 'plistlib', 'py_compile', 'tack', 'tarfile', 'tracemalloc']
if sys.platform == 'win32':
 # pywebview's Windows (Edge/WinForms) backend needs these at runtime.
 excludes.remove('cffi')
 excludes.remove('plistlib')
else:
 excludes.append('pmca.usb.driver.windows')
if sys.platform != 'darwin':
 excludes.append('pmca.usb.driver.osx')

# Get version from git (--dirty marks builds from a modified working tree)
version = subprocess.check_output(['git', 'describe', '--always', '--tags', '--dirty']).decode('ascii').strip()
with open('frozenversion.py', 'w') as f:
 f.write('version = "%s"' % version)

# Generate filename
suffix = {'linux': '-linux', 'win32': '-win', 'darwin': '-osx'}
output += '-' + version + suffix.get(sys.platform, '')

# Analyze files
try:
 from PyInstaller.utils.hooks import collect_submodules
 hiddenimports = collect_submodules('pmca.plugins')
except Exception:
 hiddenimports = []
a = Analysis([input], excludes=excludes, datas=[('certs/*', 'certs')], hiddenimports=hiddenimports)
a.datas = [d for d in a.datas if not (d[0].startswith('certifi') and not d[0].endswith('cacert.pem'))]
a.datas += Tree('updatershell/fdat', 'updatershell/fdat')
if os.path.isdir('assets'):
 a.datas += Tree('assets', 'assets')
if os.path.isfile('icon.png'):
 a.datas += [('icon.png', 'icon.png', 'DATA')]
# Non-Python plugin assets (frontend JS, docs) for optional drop-in plugins.
if os.path.isdir('pmca/plugins'):
 a.datas += Tree('pmca/plugins', 'pmca/plugins', excludes=['*.py', '*.pyc', '__pycache__'])

# Generate executable
pyz = PYZ(a.pure, a.zipped_data)
if sys.platform == 'darwin' and not console:
 exe = EXE(pyz, a.scripts, exclude_binaries=True, name=output, console=console)
 coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name=output)
 app = BUNDLE(coll, name=output+'.app')
else:
 exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, name=output, console=console)

os.remove('frozenversion.py')
