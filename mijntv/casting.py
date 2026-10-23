"""Optional YouTube DIAL child process. Only validated commands cross into Tk."""
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time


def number(value, maximum=86400):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Ongeldige castpositie.')
    return max(0, min(maximum, value))


def validate(message):
    command = message.get('command')
    if command == 'play':
        if not isinstance(message.get('video'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', message['video']):
            raise ValueError('Ongeldige YouTube-video.')
        number(message.get('position', 0))
    elif command == 'seek':
        number(message.get('position'))
    elif command == 'volume':
        volume = message.get('volume')
        if not isinstance(volume, dict) or not isinstance(volume.get('muted'), bool):
            raise ValueError('Ongeldig castvolume.')
        number(volume.get('level'), 100)
    elif command not in ('pause', 'resume', 'stop', 'status'):
        raise ValueError('Onbekende castopdracht.')
    return command


class YouTubeReceiver:
    def __init__(self, root, directory, handler, notify):
        self.root, self.handler, self.notify = root, handler, notify
        self.commands = queue.Queue(maxsize=64)
        self.process = None
        self.closed = False
        self.ready = False
        self.code = None
        script = os.environ.get('IPTV_CAST_SCRIPT', '/opt/iptv-cast/receiver.mjs')
        if os.environ.get('IPTV_YOUTUBE_CAST') != '1' or not shutil.which('node') or not Path(script).is_file():
            return
        work = Path(directory) / 'youtube-cast'
        work.mkdir(mode=0o700, exist_ok=True)
        try:
            self.process = subprocess.Popen(['node', script], cwd=work, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            text=True, bufsize=1)
        except OSError:
            self.notify('error', None)
            return
        threading.Thread(target=self.read, daemon=True).start()
        self.root.after(100, self.poll)

    def read(self):
        try:
            for line in self.process.stdout:
                if len(line) > 16384:
                    continue
                try:
                    message = json.loads(line)
                    if isinstance(message, dict):
                        self.commands.put_nowait((time.monotonic(), message))
                except (ValueError, queue.Full):
                    continue
        except (OSError, ValueError):
            pass

    def send(self, message):
        try:
            if self.process and self.process.poll() is None:
                self.process.stdin.write(json.dumps(message)+'\n')
                self.process.stdin.flush()
        except (OSError, ValueError):
            pass

    def poll(self):
        if self.closed:
            return
        for _ in range(32):
            try:
                received, message = self.commands.get_nowait()
            except queue.Empty:
                break
            if 'event' in message:
                event = message['event']
                if event == 'ready':
                    self.ready = True
                if event == 'pairing':
                    code = message.get('code', '')
                    if not isinstance(code, str) or not re.fullmatch(r'[0-9 -]{6,30}', code):
                        continue
                    self.code = code
                self.notify(event, self.code)
                continue
            identifier = message.get('id')
            if not isinstance(identifier, int) or isinstance(identifier, bool):
                continue
            result = False
            try:
                if time.monotonic()-received < 10:
                    validate(message)
                    result = self.handler(message)
            except (ValueError, OSError, RuntimeError):
                self.notify('play-error', None)
            self.send({'id': identifier, 'result': result})
        if self.process.poll() is not None:
            if self.ready:
                self.ready = False
                self.notify('error', None)
            return
        self.root.after(100, self.poll)

    def pair(self):
        if not self.ready:
            return False
        self.send({'command': 'pair'})
        return True

    def close(self):
        self.closed = True
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
            self.process.stdin.close()
            self.process.stdout.close()
