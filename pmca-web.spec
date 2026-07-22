# Run `pyinstaller pmca-web.spec` to generate an executable

import os

input = 'pmca-web.py'
output = 'pmca-web'
console = os.environ.get('PMCA_BUILD_CONSOLE') == '1'

with open('build.spec') as f:
 exec(f.read())
