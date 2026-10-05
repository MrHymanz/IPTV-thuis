"""Build a source zip from tracked project files, excluding Git metadata and local data."""
from pathlib import Path
import subprocess
import zipfile

root = Path(__file__).resolve().parent.parent
output = root / 'dist' / 'IPTV-thuis-0.1.0.zip'
output.parent.mkdir(exist_ok=True)
try:
    tracked = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z']).decode().split('\0')
except (OSError, subprocess.CalledProcessError):
    raise SystemExit('Maak het bronarchief vanuit een Git-checkout met Git geïnstalleerd.') from None
files = [root / name for name in tracked if name and (root / name).is_file()]
with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path, 'IPTV-thuis/' + str(path.relative_to(root)))
print(output)
