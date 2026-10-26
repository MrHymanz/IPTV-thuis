"""One mobile HLS player; TV and recordings always have provider priority."""
import json
import math
from pathlib import Path
import re
import secrets
import signal
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata

from .streams import RecordingConflict


class MobilePlayback:
    def __init__(self, store, recordings, streams):
        self.store, self.recordings, self.streams = store, recordings, streams
        self.sessions = {}
        self.current = None
        self.avoid = {}
        self.closed = threading.Event()
        self.worker = threading.Thread(target=self.monitor, daemon=True)
        self.worker.start()

    def _session(self, token):
        if not isinstance(token, str) or token not in self.sessions:
            raise ValueError('Deze mobiele kijksessie is verlopen. Open de zender opnieuw.')
        return self.sessions[token]

    def _stop(self, session, state='stopped', message=''):
        session.update(state=state, message=message)
        process = session.get('process')
        if process and process.poll() is None:
            # Kill before giving a provider login to TV/recorder. No extra connection.
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                raise RuntimeError('De mobiele stream kon nog niet worden vrijgegeven.')
        mobile = self.streams.mobile
        if mobile and mobile['owner'] == session['token']:
            self.streams.mobile = None
        if state in ('preempted', 'error') and session.get('account'):
            self.avoid[session['account']] = time.monotonic() + (2 if state == 'preempted' else 45)

    def _cleanup(self, session):
        self._stop(session)
        session['directory'].cleanup()
        self.sessions.pop(session['token'], None)
        if self.current == session['token']:
            self.current = None

    def _source(self, channel, archive=False):
        occupied = self.streams.occupied_accounts()
        now = time.monotonic()
        candidates = channel.get('sources') or [channel]
        def cost(source):
            name = unicodedata.normalize('NFKC', source.get('name', '')).upper()
            return bool(re.search(r'\b(?:4K|8K|UHD)\b', name)), bool(re.search(r'\b(?:HEVC|H265)\b', name))
        # Prefer manageable HD inputs over explicit 4K/HEVC versions on a phone.
        for source in sorted(candidates, key=cost):
            account = source.get('account', 'primary')
            if archive and account != 'primary':
                continue
            key = self.streams.account_key(account)
            if key not in occupied and self.avoid.get(key, 0) <= now:
                return source, key
        raise RecordingConflict('Geen vrij kijkaccount voor deze zender. De tv en opnames houden voorrang. Probeer het later opnieuw.')

    def start(self, data):
        if self.closed.is_set():
            raise ValueError('De mobiele speler wordt afgesloten. Probeer later opnieuw.')
        if not shutil.which('ffmpeg'):
            raise ValueError('De mobiele videospeler is nog niet beschikbaar op deze server.')
        kind = data.get('kind', 'live')
        offset = data.get('position', 0)
        if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset) or offset < 0:
            raise ValueError('Ongeldige afspeelpositie.')
        if kind not in ('live', 'recording', 'archive'):
            raise ValueError('Ongeldig afspeeltype.')
        if offset > 7 * 86400 or (kind == 'live' and offset):
            raise ValueError('Ongeldige afspeelpositie.')
        local_recording = None
        if kind == 'recording':
            row = next((r for r in self.recordings.list() if r['id'] == data.get('id')), None)
            if not row or not row['playable'] or row['state'] in ('queued', 'recording'):
                raise ValueError('Deze opname is nog niet beschikbaar om af te spelen.')
            address = str(self.recordings.playback(row['id']))
            local_recording = (row, address, self.duration(address) or max(1, row['end'] - row['start']))
        account = None
        with self.streams.lock:
            if self.closed.is_set():
                raise ValueError('De mobiele speler wordt afgesloten.')
            current = self.sessions.get(self.current)
            if current and current['state'] not in ('stopped', 'preempted', 'error', 'ended'):
                if data.get('session') != current['token']:
                    raise RecordingConflict('Er is al een mobiele speler actief. Sluit die eerst.')
            if (current and data.get('retry') is True and current.get('account')
                    and current['state'] != 'preempted'
                    and (current['state'] == 'loading' or current['process'].poll() is not None)):
                self.avoid[current['account']] = time.monotonic() + 45
            if kind == 'recording':
                row, address, duration = local_recording
                identifier = row['id']
                title, label = row['title'], row['channel']
                source = None
            else:
                identifier = data.get('id')
                channel = next((f for f in self.store.favorites(playback=True) if f['id'] == identifier), None)
                if not channel:
                    raise ValueError('Deze zender staat niet in je favorieten.')
                source, account = self._source(channel, kind == 'archive')
                address = source['url'].partition('|')[0]
                if source['url'] != address:
                    raise ValueError('Deze bron gebruikt aangepaste headers en is niet beschikbaar op mobiel.')
                label = channel['label']
                title = ((channel.get('epg') or {}).get('now') or {}).get('title') or label
                duration = 0
                if kind == 'archive':
                    start = data.get('start')
                    if isinstance(start, bool) or not isinstance(start, int):
                        raise ValueError('Ongeldig programma.')
                    address = self.recordings.catchup.playback_url(identifier, start)
                    with self.store.connect() as db:
                        row = db.execute('SELECT title,end FROM catchup_programmes WHERE channel_id=? AND start=?',
                                         (identifier, start)).fetchone()
                    if not row:
                        raise ValueError('Programma niet gevonden.')
                    title, duration = row['title'], row['end'] - start
            if duration and offset >= duration:
                offset = max(0, duration - 1)
            if current:
                self._cleanup(current)
            token = secrets.token_urlsafe(24)
            directory = tempfile.TemporaryDirectory(prefix='iptv-mobile-')
            root = Path(directory.name)
            input_args = []
            if kind == 'recording':
                input_args = ['-i', address]
            else:
                # Keep provider secrets out of process arguments and HTTP responses.
                manifest = root / 'input.ffconcat'
                manifest.write_text("ffconcat version 1.0\nfile '" + address.replace("'", "'\\''") +
                                    "'\noption rw_timeout 15000000\n" +
                                    (('duration ' + str(duration) + '\n') if duration else ''))
                manifest.chmod(0o600)
                input_args = ['-f', 'concat', '-safe', '0', '-protocol_whitelist',
                              'file,http,https,tcp,tls,crypto', '-i', str(manifest)]
            command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                       *(['-ss', str(offset)] if offset else []), '-re', '-threads', '2', *input_args,
                       '-map', '0:v:0?', '-map', '0:a:0?', '-sn', '-dn',
                       '-vf', "scale=w='min(1280,iw)':h=-2,fps=25", '-c:v', 'libx264', '-preset', 'veryfast',
                       '-profile:v', 'main', '-pix_fmt', 'yuv420p', '-threads', '2',
                       '-b:v', '1800k', '-maxrate', '2200k', '-bufsize', '4400k',
                       '-force_key_frames', 'expr:gte(t,n_forced*2)',
                       '-c:a', 'aac', '-b:a', '128k', '-ac', '2',
                       '-f', 'hls', '-hls_time', '2', '-hls_list_size', '45',
                       '-hls_delete_threshold', '2', '-hls_flags', 'delete_segments+independent_segments+temp_file',
                       '-hls_segment_filename', str(root / 'segment%06d.ts'), str(root / 'index.m3u8')]
            try:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
            except OSError:
                directory.cleanup()
                raise ValueError('De mobiele videospeler kon niet starten.') from None
            session = dict(token=token, directory=directory, process=process, state='loading', message='',
                           kind=kind, id=identifier, title=title, channel=label, duration=duration,
                           position=offset, account=account, seen=time.monotonic(), start=data.get('start'), paused=False)
            self.sessions[token] = session
            self.current = token
            if account:
                def preempt():
                    self._stop(session, 'preempted', 'De tv of opname heeft dit account nodig. Mobiel probeert een ander account.')
                self.streams.mobile = {'owner': token, 'key': account, 'stop': preempt}
            return self.status(token)

    @staticmethod
    def duration(path):
        try:
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                     '-of', 'json', path], capture_output=True, timeout=6, check=True)
            duration = float(json.loads(result.stdout)['format']['duration'])
            return duration if math.isfinite(duration) and duration > 0 else 0
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            return 0

    def status(self, token):
        with self.streams.lock:
            s = self._session(token)
            s['seen'] = time.monotonic()
            ready = (Path(s['directory'].name) / 'index.m3u8').exists()
            if s['state'] not in ('stopped', 'preempted', 'error'):
                code = s['process'].poll()
                if code is not None:
                    if code == 0 and ready:
                        s['state'] = 'ended'
                    else:
                        self._stop(s, 'error', 'Deze bron kon niet worden afgespeeld. Probeer een andere zender of opnieuw.')
                elif ready:
                    s['state'] = 'paused' if s['paused'] else 'playing'
            return {k: s[k] for k in ('token', 'state', 'message', 'kind', 'id', 'title', 'channel', 'duration', 'position')} | {
                'ready': ready, 'playlist': '/api/mobile/hls/' + token + '/index.m3u8'}

    def pause(self, token, paused):
        if not isinstance(paused, bool):
            raise ValueError('Ongeldige pauze-opdracht.')
        with self.streams.lock:
            s = self._session(token)
            if s['state'] in ('stopped', 'preempted', 'error'):
                raise ValueError('De mobiele speler is gestopt.')
            if s['paused'] != paused and s['kind'] != 'live' and s['process'].poll() is None:
                s['process'].send_signal(signal.SIGSTOP if paused else signal.SIGCONT)
            s['paused'] = paused
            s['seen'] = time.monotonic()
            return self.status(token)

    def stop(self, token):
        with self.streams.lock:
            s = self._session(token)
            self._cleanup(s)

    def asset(self, token, name):
        if not re.fullmatch(r'index\.m3u8|segment[0-9]{6,12}\.ts', name):
            raise ValueError('Ongeldig videobestand.')
        with self.streams.lock:
            s = self._session(token)
            if s['state'] in ('preempted', 'stopped', 'error'):
                raise ValueError('De mobiele speler is gestopt.')
            s['seen'] = time.monotonic()
            path = Path(s['directory'].name) / name
            if not path.is_file() or path.is_symlink():
                raise ValueError('Videosegment wordt nog voorbereid.')
            try:
                data = path.read_bytes()
            except OSError:
                raise ValueError('Dit videosegment is verlopen. Ga terug naar live.') from None
            return data, 'application/vnd.apple.mpegurl' if name.endswith('.m3u8') else 'video/mp2t'

    def expire(self):
        with self.streams.lock:
            for s in list(self.sessions.values()):
                if time.monotonic() - s['seen'] > 45:
                    self._cleanup(s)
                elif s['process'].poll() is not None and s['state'] not in ('preempted', 'stopped', 'error'):
                    if self.streams.mobile and self.streams.mobile['owner'] == s['token']:
                        self.streams.mobile = None

    def monitor(self):
        while not self.closed.wait(5):
            self.expire()

    def close(self):
        self.closed.set()
        with self.streams.lock:
            for s in list(self.sessions.values()):
                self._cleanup(s)
