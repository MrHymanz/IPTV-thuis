"""Bounded background thumbnails from local, completed recording files."""
import concurrent.futures
import hashlib
from pathlib import Path
import shutil
import subprocess
import threading
import time


class RecordingThumbnails:
    def __init__(self, recordings, directory):
        self.recordings = recordings
        self.directory = Path(directory)/'recording-thumbnails'
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.lock = threading.Lock()
        self.pending, self.failed = set(), {}
        self.closed = False

    def get(self, identifier):
        row = next((r for r in self.recordings.list() if r['id'] == identifier), None)
        if not row or not row['playable'] or row['state'] in ('queued', 'recording'):
            raise ValueError('Geen thumbnail beschikbaar.')
        path = self.recordings.playback(identifier)
        stat = path.stat()
        key = hashlib.sha256((identifier+str(stat.st_size)+str(stat.st_mtime_ns)).encode()).hexdigest()
        target = self.directory/(key+'.jpg')
        if target.exists():
            return target.read_bytes()
        with self.lock:
            if not self.closed and key not in self.pending and self.failed.get(key, 0) <= time.monotonic():
                self.pending.add(key)
                self.pool.submit(self.generate, key, path, target)
        raise ValueError('Thumbnail wordt voorbereid.')

    def generate(self, key, path, target):
        try:
            if not shutil.which('ffmpeg'):
                raise ValueError()
            for seconds in (5, 0):
                result = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                    '-threads', '1', '-ss', str(seconds), '-i', str(path), '-frames:v', '1',
                    '-vf', 'scale=320:-2', '-threads', '1', '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1'],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=12, check=True)
                if result.stdout.startswith(b'\xff\xd8\xff') and len(result.stdout) <= 2*1024*1024:
                    temporary = target.with_suffix('.tmp')
                    temporary.write_bytes(result.stdout)
                    temporary.chmod(0o600)
                    temporary.replace(target)
                    return
            raise ValueError()
        except Exception:
            with self.lock:
                self.failed[key] = time.monotonic()+300
        finally:
            with self.lock:
                self.pending.discard(key)

    def close(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)
