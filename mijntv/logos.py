"""Local raster logo cache. Provider addresses never reach the browser."""
import hashlib
import math
import re
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
    def __init__(self, directory, store=None):
        self.directory = Path(directory) / 'logos'
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.lock = threading.Lock()
        self.locks, self.failed = {}, {}
        self.store = store
        self.entries, self.touched = {}, {}
        self.indexed = False
        self.closed, self.wake = threading.Event(), threading.Event()
        self.worker = None
        try:
            self.limit = int(float(store.setting('image_cache_gb', '2'))*1_000_000_000) if store else 2_000_000_000
        except (TypeError, ValueError):
            self.limit = 2_000_000_000
        self.limit = max(500_000_000, min(20_000_000_000, self.limit))

    def start(self):
        if self.worker is None:
            self.worker = threading.Thread(target=self.maintain, daemon=True)
            self.worker.start()

    def index(self):
        # One metadata scan after boot, rather than rereading the entire cache
        # on every catalogue refresh. Never follow files outside this cache.
        with os.scandir(self.directory) as files:
            for entry in files:
                if not re.fullmatch(r'[a-f0-9]{64}', entry.name) or not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                    with self.lock:
                        self.entries.setdefault(entry.name, (stat.st_size, stat.st_mtime))
                        self.touched.setdefault(entry.name, stat.st_mtime)
                except OSError:
                    pass
        self.indexed = True

    def status(self):
        with self.lock:
            return {'bytes': sum(size for size, _ in self.entries.values()), 'files': len(self.entries),
                    'limit_gb': self.limit/1_000_000_000, 'indexing': not self.indexed}

    def configure(self, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not .5 <= value <= 20:
            raise ValueError('Kies een cachelimiet tussen 0,5 en 20 GB.')
        if self.store:
            self.store.set_setting('image_cache_gb', str(value))
        self.limit = int(value*1_000_000_000)
        self.wake.set()
        return self.status()

    def note(self, key, path, size):
        now = time.time()
        with self.lock:
            touched = self.touched.get(key, 0)
            self.entries[key] = (size, now)
            if now-touched >= 3600:
                self.touched[key] = now
        # Persist recency at most once an hour per file, not on every view.
        if now-touched >= 3600:
            try:
                os.utime(path, None)
            except OSError:
                pass
        self.wake.set()

    def prune(self):
        if not self.indexed:
            self.index()
        with self.lock:
            total = sum(size for size, _ in self.entries.values())
            candidates = sorted(self.entries.items(), key=lambda pair: pair[1][1]) if total > self.limit else []
        for key, (size, used) in candidates:
            if self.closed.is_set() or total <= self.limit*.95:
                break
            with self.lock:
                lock = self.locks.setdefault(key, threading.Lock())
            if not lock.acquire(blocking=False):
                continue
            try:
                with self.lock:
                    current = self.entries.get(key)
                    if current != (size, used):
                        continue  # Accessed while pruning; retain the newer image.
                try:
                    (self.directory/key).unlink(missing_ok=True)
                except OSError:
                    continue
                with self.lock:
                    self.entries.pop(key, None)
                total -= size
            finally:
                lock.release()

    def maintain(self):
        while not self.closed.is_set():
            try:
                self.prune()
            except OSError:
                pass
            self.wake.wait(60)
            self.wake.clear()
            # Batch writes/downloads so maintenance never runs for every image.
            self.closed.wait(5)

    def close(self):
        self.closed.set()
        self.wake.set()

    def get(self, url):
        key = hashlib.sha256(url.encode()).hexdigest()
        with self.lock:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            path = self.directory / key
            if path.exists():
                data = path.read_bytes()
                self.note(key, path, len(data))
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
                self.note(key, path, len(data))
                return data, mime
            except Exception:
                self.failed[key] = time.monotonic() + 300
                raise ValueError('Logo niet beschikbaar.') from None
