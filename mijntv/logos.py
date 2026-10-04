"""Local raster logo cache. Provider addresses never reach the browser."""
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.request import Request, urlopen

MAX_LOGO = 2 * 1024 * 1024


def image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'image/webp'
    raise ValueError('Geen ondersteund zenderlogo.')


class LogoCache:
    def __init__(self, directory):
        self.directory = Path(directory) / 'logos'
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.lock = threading.Lock()
        self.locks, self.failed = {}, {}

    def get(self, url):
        key = hashlib.sha256(url.encode()).hexdigest()
        with self.lock:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            path = self.directory / key
            if path.exists():
                data = path.read_bytes()
                return data, image_type(data)
            if self.failed.get(key, 0) > time.monotonic():
                raise ValueError('Logo tijdelijk niet beschikbaar.')
            try:
                with urlopen(Request(url, headers={'User-Agent':'Mozilla/5.0 (IPTV-thuis)'}), timeout=8) as response:
                    data = response.read(MAX_LOGO + 1)
                if len(data) > MAX_LOGO:
                    raise ValueError('Logo te groot.')
                mime = image_type(data)
                with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as file:
                    temporary = file.name
                    file.write(data)
                try:
                    os.replace(temporary, path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                return data, mime
            except Exception:
                self.failed[key] = time.monotonic() + 300
                raise ValueError('Logo niet beschikbaar.') from None
