"""Mobile-only live snapshots, without opening another provider connection."""
import concurrent.futures
import subprocess
import threading
import time


class LivePreviews:
    def __init__(self, store, streams):
        self.store, self.streams = store, streams
        self.mobile = None
        self.lock = threading.Lock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.images, self.pending, self.failed = {}, set(), {}
        self.closed = False

    def get(self, identifier):
        if not any(item['id'] == identifier for item in self.store.favorites()):
            raise ValueError('Deze zender staat niet in je favorieten.')
        now = time.monotonic()
        with self.lock:
            cached = self.images.get(identifier)
        if cached and now-cached[0] < 30:
            return cached[1]
        data = None
        # Only read already produced mobile segments. Never consult the TV feed.
        with self.streams.lock:
            session = self.mobile.sessions.get(self.mobile.current) if self.mobile else None
            if session and session['kind'] == 'live' and session['id'] == identifier and session['state'] not in ('stopped','preempted','error'):
                parts = sorted(session['output'].glob('segment*.ts'))
                if parts:
                    try:
                        path = parts[-1]
                        if not path.is_symlink() and path.stat().st_size <= 6*1024*1024:
                            data = path.read_bytes()
                    except OSError:
                        pass
        with self.lock:
            if data and not self.closed and identifier not in self.pending and self.failed.get(identifier, 0) <= now:
                self.pending.add(identifier)
                self.pool.submit(self.generate, identifier, data)
            if cached and now-cached[0] < 86400:
                return cached[1]
        raise ValueError('Nog geen mobiel beeld voor deze zender.')

    def generate(self, identifier, data):
        try:
            result = subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-threads','1',
                '-f','mpegts','-i','pipe:0','-frames:v','1','-an','-vf','scale=640:-2','-threads','1',
                '-f','image2pipe','-vcodec','mjpeg','pipe:1'],input=data,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,timeout=5,check=True)
            if not result.stdout.startswith(b'\xff\xd8\xff') or len(result.stdout)>512*1024:
                raise ValueError()
            with self.lock:
                if not self.closed:
                    self.images[identifier] = (time.monotonic(),result.stdout)
                    while len(self.images)>8:
                        self.images.pop(min(self.images,key=lambda key:self.images[key][0]))
        except (OSError,ValueError,subprocess.SubprocessError):
            with self.lock:self.failed[identifier]=time.monotonic()+60
        finally:
            with self.lock:self.pending.discard(identifier)

    def close(self):
        with self.lock:self.closed=True
        self.pool.shutdown(wait=False,cancel_futures=True)
