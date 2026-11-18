"""Small live snapshots from existing local feeds, never a new provider login."""
import concurrent.futures
import queue
import subprocess
import threading
import time


class LivePreviews:
    def __init__(self, store, streams):
        self.store, self.streams = store, streams
        self.lock = threading.Lock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.images, self.pending, self.failed = {}, set(), {}
        self.closed = False

    def current_channel(self):
        with self.streams.lock:
            feed = self.streams.viewer
            return feed.channel['id'] if feed and feed.started and not feed.closed else ''

    def feed(self, identifier):
        if not any(item['id'] == identifier for item in self.store.favorites()):
            raise ValueError('Deze zender staat niet in je favorieten.')
        with self.streams.lock:
            candidates = [self.streams.viewer, *self.streams.slots.values()]
            return next((feed for feed in candidates if feed and not feed.closed and feed.started
                         and feed.channel['id'] == identifier and time.monotonic()-feed.last_data < 15), None)

    def get(self, identifier):
        feed = self.feed(identifier)
        if feed is None:
            raise ValueError('Geen bestaande stream voor deze momentopname.')
        key = (identifier, feed.token)
        now = time.monotonic()
        with self.lock:
            cached = self.images.get(key)
            if cached and now-cached[0] < 30:
                return cached[1]
            if not self.closed and key not in self.pending and len(self.pending) < 2 and self.failed.get(key, 0) <= now:
                self.pending.add(key)
                self.pool.submit(self.generate, key, feed)
        raise ValueError('Momentopname wordt voorbereid.')

    def generate(self, key, feed):
        consumer = None
        try:
            # Subscribe only while the source is already running. subscribe()
            # must never start an idle feed on behalf of a preview request.
            with feed.lock:
                if feed.closed or not feed.started:
                    return
                consumer = feed.subscribe()
            if consumer is None:
                return
            parts, size = [], 0
            deadline = time.monotonic()+2.5
            while time.monotonic() < deadline and size < 6*1024*1024:
                try:
                    part = consumer.get(timeout=.2)
                except queue.Empty:
                    continue
                if part is None:
                    break
                parts.append(part)
                size += len(part)
            if not parts:
                raise ValueError()
            result = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-threads', '1', '-f', 'mpegts', '-i', 'pipe:0', '-frames:v', '1', '-an',
                '-vf', 'scale=640:-2', '-threads', '1', '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1'],
                input=b''.join(parts), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True)
            if not result.stdout.startswith(b'\xff\xd8\xff') or len(result.stdout) > 512*1024:
                raise ValueError()
            with self.lock:
                self.images = {k: v for k, v in self.images.items() if time.monotonic()-v[0] < 60}
                if not self.closed:
                    self.images[key] = (time.monotonic(), result.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            with self.lock:
                self.failed[key] = time.monotonic()+60
        finally:
            if consumer is not None:
                feed.unsubscribe(consumer)
            with self.lock:
                self.pending.discard(key)

    def close(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)
