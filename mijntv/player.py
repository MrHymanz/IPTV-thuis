"""mpv IPC controller. Stream credentials never go into process arguments."""
import json
import os
import queue
import socket
import subprocess
import tempfile
import threading
import time

from .video_health import black_frame
from .audio import audio_device
from .diagnostics import PROPERTIES, StreamWatchdog, safe_snapshot


class Player:
    def __init__(self, window_id, hwdec='auto-safe'):
        if hwdec not in ('auto-safe', 'no'):
            raise ValueError('Ongeldige decoderkeuze.')
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.status_lock = threading.Lock()
        self.status = {}
        self.watchdog = StreamWatchdog()
        self.watchdog_enabled = True
        self.last_warning = 0
        self.generation = 0
        self.monitor_active = False
        self.monitor_stop = threading.Event()
        self.directory = tempfile.TemporaryDirectory(prefix='iptv-')
        self.socket_path = os.path.join(self.directory.name, 'mpv.sock')
        audio = []
        device = audio_device()
        if device:
            audio = ['--ao=alsa', '--audio-device=' + device, '--audio-channels=stereo']
        self.process = subprocess.Popen([
            'mpv', '--no-config', '--idle=yes', '--keep-open=no', '--force-window=yes',
            '--wid=' + str(window_id), '--input-ipc-server=' + self.socket_path,
            '--input-default-bindings=no', '--input-vo-keyboard=no', '--input-terminal=no',
            '--osc=no', '--osd-level=0', '--cursor-autohide=always', '--terminal=no',
            '--cache=yes', '--demuxer-max-bytes=64MiB', '--vo=gpu,x11', '--hwdec=' + hwdec,
        ] + audio, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.connection = None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and self.process.poll() is None:
            try:
                conn = socket.socket(socket.AF_UNIX)
                conn.connect(self.socket_path)
                self.connection = conn
                break
            except OSError:
                conn.close()
                time.sleep(.05)
        if self.connection is None:
            self.close()
            raise RuntimeError('De videospeler kon niet starten. Controleer mpv en de beeldscherminstellingen.')
        os.chmod(self.socket_path, 0o600)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.command('request_log_messages', 'warn')
        threading.Thread(target=self._monitor, daemon=True).start()
        threading.Thread(target=self._monitor_picture, daemon=True).start()

    def _read(self):
        try:
            with self.connection.makefile('rb') as stream:
                for line in stream:
                    try:
                        message = json.loads(line)
                        request = message.get('request_id', '')
                        if isinstance(request, str) and request.startswith('health:'):
                            key = request.split(':', 1)[1]
                            if key in PROPERTIES:
                                with self.status_lock:
                                    self.status[key] = message.get('data')
                                    self.status['_received'] = time.monotonic()
                        if message.get('event') == 'log-message':
                            text = message.get('text', '').lower()
                            category = 'decoder' if any(word in text for word in ('decode', 'corrupt', 'invalid nal')) else 'network' if any(word in text for word in ('http', 'connection', 'timed out', 'tls')) else 'player'
                            now = time.monotonic()
                            if now-self.last_warning >= 5:
                                self.last_warning = now
                                self.events.put({'event': 'diagnostic-error', 'category': category})
                        if message.get('event') in ('file-loaded', 'end-file', 'shutdown'):
                            # Do not forward error text or metadata containing provider URLs.
                            self.events.put({'event': message['event'], 'reason': message.get('reason', '')})
                    except (ValueError, UnicodeError):
                        continue
        except OSError:
            pass
        self.events.put({'event': 'disconnected'})

    def command(self, *args):
        with self.lock:
            if not self.connection or self.process.poll() is not None:
                raise RuntimeError('De videospeler is gestopt. Kies opnieuw een zender.')
            try:
                self.connection.sendall((json.dumps({'command': list(args)}) + '\n').encode())
            except OSError:
                raise RuntimeError('De videospeler reageert niet. Kies opnieuw een zender.') from None

    def snapshot(self):
        with self.status_lock:
            return safe_snapshot(self.status)

    def _monitor(self):
        while not self.monitor_stop.wait(2):
            if not self.monitor_active:
                continue
            try:
                with self.lock:
                    if not self.connection or self.process.poll() is not None:
                        continue
                    for key in PROPERTIES:
                        if key.startswith('video-'):
                            continue
                        self.connection.sendall((json.dumps({'command': ['get_property', key],
                                                            'request_id': 'health:'+key})+'\n').encode())
                with self.status_lock:
                    values = dict(self.status)
                    if self.watchdog_enabled and self.watchdog.stalled(values, time.monotonic()):
                        self.events.put({'event': 'stalled', 'reason': self.watchdog.reason, 'snapshot': safe_snapshot(values)})
            except (RuntimeError, OSError):
                return

    def check_picture(self):
        with self.status_lock:
            generation = self.generation
            if self.status.get('pause') is True or self.status.get('core-idle') is not False:
                return
        path = os.path.join(self.directory.name, 'frame.jpg')
        verified = False
        try:
            # A separate IPC connection keeps large captures out of the event reader.
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(5)
                connection.connect(self.socket_path)
                connection.sendall((json.dumps({'command': ['screenshot-to-file', path, 'video'],
                                                'request_id': 'picture'})+'\n').encode())
                with connection.makefile('rb') as stream:
                    for line in stream:
                        response = json.loads(line)
                        if response.get('request_id') == 'picture':
                            if response.get('error') != 'success':
                                return
                            break
                    else:
                        return
            black = black_frame(path)
            with self.status_lock:
                if generation == self.generation and self.monitor_active:
                    self.status['video-black'] = black
                    self.status['video-checked-at'] = time.monotonic()
                    verified = True
        except (ImportError, OSError, ValueError):
            # Unknown picture status never certifies a source as healthy.
            return
        finally:
            if not verified:
                with self.status_lock:
                    if generation == self.generation:
                        self.status.pop('video-black', None)
                        self.status.pop('video-checked-at', None)
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def _monitor_picture(self):
        while not self.monitor_stop.wait(10):
            if self.monitor_active and self.watchdog_enabled:
                self.check_picture()

    def play(self, url):
        with self.status_lock:
            self.generation += 1
            self.status.clear()
            self.watchdog.reset(time.monotonic())
        self.monitor_active = True
        address, separator, raw_headers = url.partition('|')
        headers = []
        if separator:
            from urllib.parse import parse_qsl
            headers = [f'{key}: {value}' for key, value in parse_qsl(raw_headers)
                       if '\r' not in key + value and '\n' not in key + value]
        self.command('set_property', 'http-header-fields', headers)
        self.command('loadfile', address, 'replace')
        self.command('set_property', 'pause', False)

    def stop(self):
        with self.status_lock:
            self.generation += 1
            self.monitor_active = False
        self.command('stop')

    def close(self):
        self.monitor_stop.set()
        if self.connection:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            self.connection = None
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.directory.cleanup()
