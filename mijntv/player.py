"""mpv IPC controller. Stream credentials never go into process arguments."""
import json
import os
import queue
import socket
import subprocess
import tempfile
import threading
import time


class Player:
    def __init__(self, window_id):
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.directory = tempfile.TemporaryDirectory(prefix='iptv-')
        self.socket_path = os.path.join(self.directory.name, 'mpv.sock')
        self.process = subprocess.Popen([
            'mpv', '--no-config', '--idle=yes', '--keep-open=no', '--force-window=yes',
            '--wid=' + str(window_id), '--input-ipc-server=' + self.socket_path,
            '--input-default-bindings=no', '--input-vo-keyboard=no', '--input-terminal=no',
            '--osc=no', '--osd-level=0', '--cursor-autohide=always', '--terminal=no',
            '--cache=yes', '--demuxer-max-bytes=64MiB', '--vo=gpu,x11', '--hwdec=auto-safe',
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

    def _read(self):
        try:
            with self.connection.makefile('rb') as stream:
                for line in stream:
                    try:
                        message = json.loads(line)
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

    def play(self, url):
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
        self.command('stop')

    def close(self):
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
