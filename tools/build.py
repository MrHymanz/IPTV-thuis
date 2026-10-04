"""Build a source zip without streams, runtime data, or credentials."""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parent.parent
output = root / 'dist' / 'TPTV-thuis-0.1.0.zip'
output.parent.mkdir(exist_ok=True)
files = [root/'README.md', root/'pyproject.toml', root/'.gitignore', root/'index.html']
for directory in ('mijntv', 'tools', 'tests', 'docs'):
    files.extend(path for path in (root/directory).rglob('*')
                 if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc')
with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path, 'TPTV-thuis/' + str(path.relative_to(root)))
print(output)
