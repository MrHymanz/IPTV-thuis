"""Loopback-only fan-out: one provider connection, independent local consumers."""
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import tempfile
import threading
from urllib.parse import parse_qsl, urlsplit
from urllib.request import Request, urlopen


class RecordingConflict(ValueError):
    pass


class Feed:
    def __init__(self, channel, source):
        self.channel = channel
        self.source = source
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.consumers = set()
        self.recent = deque(maxlen=16)
        self.started = False
        self.closed = False
        self.recording = False
        self.response = None
        self.process = None

    def subscribe(self):
        with self.lock:
            if self.closed:
                return None
            consumer = queue.Queue(maxsize=128)
            for chunk in self.recent:
                consumer.put_nowait(chunk)
            self.consumers.add(consumer)
            if not self.started:
                self.started = True
                threading.Thread(target=self.read, daemon=True).start()
            return consumer

    def unsubscribe(self, consumer):
        with self.lock:
            self.consumers.discard(consumer)
            if not self.consumers and not self.recording:
                self.close()

    def publish(self, chunk):
        with self.lock:
            if self.closed:
                return
            self.recent.append(chunk)
            for consumer in list(self.consumers):
                try:
                    consumer.put_nowait(chunk)
                except queue.Full:
                    # Disconnect a slow consumer without interrupting the others.
                    self.consumers.discard(consumer)
                    while True:
                        try:
                            consumer.get_nowait()
                        except queue.Empty:
                            break
                    consumer.put_nowait(None)

    def read(self):
        manifest = None
        try:
            address, _, raw_headers = self.source['url'].partition('|')
            headers = {'User-Agent': 'mpv', 'Accept-Encoding': 'identity'}
            headers.update({k:v for k,v in parse_qsl(raw_headers) if '\r' not in k+v and '\n' not in k+v})
            if urlsplit(address).scheme not in ('http', 'https'):
                raise ValueError('Gedeelde streams vereisen HTTP of HTTPS.')
            response = urlopen(Request(address, headers=headers), timeout=20)
            with self.lock:
                if self.closed:
                    response.close()
                    return
                self.response = response
            first = response.read1(65536)
            if first.lstrip().startswith(b'#EXTM3U'):
                # Remux HLS packets only; credentials remain in a private manifest.
                response.close()
                if raw_headers:
                    raise ValueError('HLS met aangepaste headers wordt niet ondersteund.')
                manifest = tempfile.NamedTemporaryFile(mode='w', prefix='iptv-feed-', suffix='.ffconcat', delete=False)
                Path(manifest.name).chmod(0o600)
                manifest.write("ffconcat version 1.0\nfile '"+address.replace("'", "'\\''")+"'\noption rw_timeout 20000000\n")
                manifest.close()
                with self.lock:
                    if self.closed:
                        return
                    self.process = subprocess.Popen(['ffmpeg','-nostdin','-hide_banner','-loglevel','quiet',
                        '-f','concat','-safe','0','-protocol_whitelist','file,http,https,tcp,tls,crypto',
                        '-i',manifest.name,'-map','0:v:0?','-map','0:a?','-c','copy','-flush_packets','1',
                        '-f','mpegts','pipe:1'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                reader = self.process.stdout
            else:
                if first:
                    self.publish(first)
                reader = response
            while not self.closed:
                chunk = reader.read1(65536)
                if not chunk:
                    break
                self.publish(chunk)
        except Exception:
            # Never log exception strings containing provider URLs/credentials.
            pass
        finally:
            self.close()
            if manifest:
                Path(manifest.name).unlink(missing_ok=True)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for consumer in self.consumers:
                if consumer.full():
                    try:
                        consumer.get_nowait()
                    except queue.Empty:
                        pass
                consumer.put_nowait(None)
            self.consumers.clear()
            response, process = self.response, self.process
        if response:
            try:
                response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError):
                pass
            # Closing a blocking read must not hold the GUI or hub lock.
            threading.Thread(target=response.close, daemon=True).start()
        if process and process.poll() is None:
            process.terminate()
            def reap():
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            threading.Thread(target=reap, daemon=True).start()


class StreamHub:
    def __init__(self):
        self.lock = threading.RLock()
        self.feeds = {}
        self.slots = {}
        self.viewer = None
        hub = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                with hub.lock:
                    feed = hub.feeds.get(self.path.removeprefix('/stream/')) if self.path.startswith('/stream/') else None
                    consumer = feed.subscribe() if feed else None
                if consumer is None:
                    self.send_error(404)
                    return
                self.connection.settimeout(5)
                try:
                    self.send_response(200)
                    self.send_header('Content-Type','video/mp2t')
                    self.send_header('Cache-Control','no-store')
                    self.send_header('Connection','close')
                    self.end_headers()
                    while True:
                        try:
                            chunk = consumer.get(timeout=1)
                        except queue.Empty:
                            continue
                        if chunk is None:
                            break
                        self.wfile.write(chunk)
                except (OSError, ValueError):
                    pass
                finally:
                    feed.unsubscribe(consumer)
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever,daemon=True).start()

    def url(self, feed):
        return f'http://127.0.0.1:{self.server.server_port}/stream/{feed.token}'

    def acquire(self, slot, channel, source, recording=False):
        with self.lock:
            current = self.slots.get(slot)
            if current and current.recording:
                if current.channel['id'] != channel['id']:
                    raise RecordingConflict('Er loopt een opname op '+current.channel['label']+'.')
                if current.closed:
                    raise RecordingConflict('De opnamebron is onderbroken. Stop de opname voordat je opnieuw verbindt.')
                return current
            if current and not current.closed and current.channel['id'] == channel['id'] and (recording or current.source['id'] == source['id']):
                current.recording = recording
                return current
            if current:
                current.close()
                self.feeds.pop(current.token,None)
            feed = Feed(channel,source)
            feed.recording = recording
            self.slots[slot] = feed
            self.feeds[feed.token] = feed
            return feed

    def view(self, channel, source):
        with self.lock:
            feed = self.acquire('primary',channel,source)
            self.viewer = feed
            return self.url(feed)

    def stop_view(self):
        with self.lock:
            if self.viewer and not self.viewer.recording:
                self.viewer.close()
            self.viewer = None

    def release_recording(self):
        with self.lock:
            for feed in self.slots.values():
                if feed.recording:
                    feed.recording = False
                    if feed is not self.viewer:
                        feed.close()

    def required_channel(self):
        with self.lock:
            feed = self.slots.get('primary')
            return dict(feed.channel) if feed and feed.recording else None

    def close(self):
        with self.lock:
            for feed in self.slots.values():
                feed.close()
        self.server.shutdown()
        self.server.server_close()
