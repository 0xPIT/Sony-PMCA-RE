# Run `pyinstaller pmca-web.spec` to generate an executable

input = 'pmca-web.py'
output = 'pmca-web'
console = False

with open('build.spec') as f:
 exec(f.read())
