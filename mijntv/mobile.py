"""One mobile HLS player; TV and recordings always have provider priority."""
import json
import logging
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
from .vod import VOD
from .seekable import SeekableVOD
from . import tracks
from .vod_backup import BackupMatches


class MobilePlayback:
    def __init__(self, store, recordings, streams, vod=None):
        self.store, self.recordings, self.streams = store, recordings, streams
        self.vod = vod or VOD(store, recordings.catchup)
        self.sessions = {}
        self.seekable = SeekableVOD(self)
        self.current = None
        self.vod_backups = BackupMatches(store,self.vod)
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
        logging.getLogger('mijntv.stream').info('mobile_stopped kind=%s state=%s', session['kind'], state)
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
        free, reasons = [], []
        for source in sorted(candidates, key=cost):
            account = source.get('account', 'primary')
            if archive and account != 'primary':
                continue
            key = self.streams.account_key(account)
            reasons.append(account+(':busy' if key in occupied else ':cooldown' if self.avoid.get(key, 0) > now else ':available'))
            if key not in occupied:
                free.append((source, key))
            if key not in occupied and self.avoid.get(key, 0) <= now:
                logging.getLogger('mijntv.stream').info('mobile_account_selected role=%s recovery=False', account)
                return source, key
        # Cooldown is a preference after failure, not a reservation. If all
        # genuinely free accounts are cooling down, make a bounded retry.
        if free:
            source, key = min(free, key=lambda item: self.avoid.get(item[1], 0))
            logging.getLogger('mijntv.stream').info('mobile_account_selected role=%s recovery=True', source.get('account', 'primary'))
            return source, key
        logging.getLogger('mijntv.stream').info('mobile_accounts_unavailable reasons=%s', ','.join(sorted(set(reasons))))
        if archive:
            raise RecordingConflict('Het hoofdaccount voor terugkijken is nu niet beschikbaar. De tv en opnames houden voorrang. Een vrij backupaccount kan dit archief niet overnemen.')
        raise RecordingConflict('Geen vrij kijkaccount voor deze zender. De tv en opnames houden voorrang. Probeer het later opnieuw.')

    def start(self, data):
        requested_at = time.monotonic()
        if self.closed.is_set():
            raise ValueError('De mobiele speler wordt afgesloten. Probeer later opnieuw.')
        if not shutil.which('ffmpeg'):
            raise ValueError('De mobiele videospeler is nog niet beschikbaar op deze server.')
        kind = data.get('kind', 'live')
        offset = data.get('position', 0)
        if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset) or offset < 0:
            raise ValueError('Ongeldige afspeelpositie.')
        if kind not in ('live', 'recording', 'archive', 'movie', 'episode'):
            raise ValueError('Ongeldig afspeeltype.')
        if offset > 7 * 86400 or (kind == 'live' and offset):
            raise ValueError('Ongeldige afspeelpositie.')
        for field in ('audio_track','subtitle_track'):
            if field in data and data[field] is not None and (isinstance(data[field],bool) or not isinstance(data[field],int) or not 0<=data[field]<=10000):
                raise ValueError('Ongeldige taalkeuze.')
        native_vod = data.get('native_vod') is True and kind in ('movie', 'episode')
        initial_position = offset
        if native_vod:
            offset = 0  # Discover the exact source duration before seeking a resumed VOD.
        local_recording = None
        vod = self.vod.playback(kind, data.get('id'), data.get('signature')) if kind in ('movie', 'episode') else None
        if kind == 'recording':
            row = next((r for r in self.recordings.list() if r['id'] == data.get('id')), None)
            if not row or not row['playable'] or row['state'] in ('queued', 'recording'):
                raise ValueError('Deze opname is nog niet beschikbaar om af te spelen.')
            address = str(self.recordings.playback(row['id']))
            local_recording = (row, address, self.duration(address) or max(1, row['end'] - row['start']))
        account = None
        vod_role='primary';alternate=None
        if vod:
            with self.streams.lock:
                occupied=self.streams.occupied_accounts()
                primary_busy=self.streams.account_key('primary') in occupied
                backup_busy=self.streams.account_key('backup') in occupied
            logging.getLogger('mijntv.stream').info('mobile_vod_accounts kind=%s primary_busy=%s backup_busy=%s explicit_backup=%s',kind,primary_busy,backup_busy,data.get('backup_id') is not None)
            if primary_busy or data.get('backup_id') is not None:
                if backup_busy:
                    raise RecordingConflict('De kijkaccounts zijn bezet door de tv of een opname. Probeer het later opnieuw.')
                try:alternate=self.vod_backups.playback(kind,str(data.get('id')),data.get('backup_id'),data.get('backup_signature'))
                except ValueError as error:raise RecordingConflict(str(error)) from None
                vod_role='backup'
        with self.streams.lock:
            if self.closed.is_set():
                raise ValueError('De mobiele speler wordt afgesloten.')
            # Reap abandoned sessions before deciding whether a phone is active.
            self.expire()
            current = self.sessions.get(self.current)
            if current and current['process'].poll() is not None:
                self._cleanup(current)
                current = None
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
            elif vod is not None:
                self.vod.check(vod['configuration'])
                identifier = str(data.get('id'))
                try:
                    if alternate:self.vod_backups.catalog.check(alternate['configuration'])
                    source, account = self._source({'url': (alternate or vod)['url'], 'account': vod_role})
                except RecordingConflict:
                    raise RecordingConflict('Het hoofdaccount voor films en series is bezet of tijdelijk niet beschikbaar. De tv en opnames houden voorrang. Probeer later opnieuw.') from None
                address = (alternate or vod)['url']
                title, label, duration = vod['title'], vod['label'], (alternate or vod)['duration']
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
                                    (('duration ' + str(duration) + '\n') if duration and not native_vod else ''))
                manifest.chmod(0o600)
                input_args = ['-f', 'concat', '-safe', '0', '-protocol_whitelist',
                              'file,http,https,tcp,tls,crypto', *(['-segment_time_metadata', '1'] if native_vod else []), '-i', str(manifest)]
            # One-second VOD segments reduce Safari's initial HLS buffer;
            # keep the same ninety-second seek window and bitrate limits.
            fast_start = kind in ('movie', 'episode')
            segment_seconds = 1 if fast_start else 2
            output = root/'run-0' if native_vod else root
            if native_vod:
                output.mkdir(mode=0o700)
            metadata_filter = ("metadata=mode=print:key=lavf.concatdec.duration:enable='eq(n,0)',metadata=mode=print:key=lavf.concat.duration:enable='eq(n,0)'," if native_vod else '')
            command = ['ffmpeg', '-nostdin', '-hide_banner', '-nostats', '-loglevel', 'info' if fast_start else 'error',
                       '-progress', 'pipe:1', '-stats_period', '0.5',
                       *(['-ss', str(offset)] if offset else []), '-re', '-threads', '2', *input_args,
                       '-map', '0:v:0?', '-map', '0:a:0?', '-sn', '-dn',
                       '-vf', metadata_filter+"scale=w='min(1280,iw)':h=-2,fps=25", '-c:v', 'libx264', '-preset', 'veryfast',
                       *(['-tune', 'zerolatency'] if fast_start else []),
                       '-profile:v', 'main', '-pix_fmt', 'yuv420p', '-threads', '2',
                       '-b:v', '1800k', '-maxrate', '2200k', '-bufsize', '4400k',
                       '-force_key_frames', 'expr:gte(t,n_forced*'+str(segment_seconds)+')',
                       '-c:a', 'aac', '-b:a', '128k', '-ac', '2',
                       *(['-output_ts_offset', str(offset), '-start_number', str(offset)] if native_vod else []),
                       '-f', 'hls', '-hls_time', str(segment_seconds), '-hls_list_size', str(90//segment_seconds),
                       '-hls_delete_threshold', '2', '-hls_flags', 'delete_segments+independent_segments+temp_file',
                       '-hls_segment_filename', str(output / 'segment%06d.ts'), str(output / 'index.m3u8')]
            try:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE)
            except OSError:
                directory.cleanup()
                raise ValueError('De mobiele videospeler kon niet starten.') from None
            session = dict(token=token, directory=directory, process=process, state='loading', message='',
                           kind=kind, id=identifier, title=title, channel=label, duration=duration,
                           position=offset, account=account, account_role=(source or {}).get('account'), seen=time.monotonic(), start=data.get('start'), paused=False,
                           requested_at=requested_at, transcoder_at=time.monotonic(), ready_logged=False, failure='unknown', decoded=False, native_vod=native_vod, duration_verified=False,
                           initial_position=initial_position, output=output, command=command, first_segment=offset,
                           last_served=offset-1, seek_generation=0, tracks={'audio':[], 'subtitles':[]},
                           tracks_ready=False, subtitle_language=data.get('subtitle_language','nl'),
                           track_choice={key:data[key] for key in ('audio_track','subtitle_track') if key in data})
            logging.getLogger('mijntv.stream').info('mobile_start kind=%s preparation_seconds=%.2f',
                                                   kind, session['transcoder_at']-requested_at)
            self._observe(session)
            self.sessions[token] = session
            self.current = token
            if account:
                def preempt():
                    self._stop(session, 'preempted', 'De tv of opname heeft dit account nodig. Mobiel probeert een ander account.')
                session['preempt'] = preempt
                self.streams.mobile = {'owner': token, 'key': account, 'stop': preempt}
            return self.status(token)

    def _observe(self, session):
        readers = [threading.Thread(target=target, args=(session,), daemon=True)
                   for target in (self._diagnose, self._progress)]
        session['readers'] = readers
        for reader in readers:
            reader.start()

    @staticmethod
    def _progress(session):
        stream = getattr(session['process'], 'stdout', None)
        if stream is None:
            return
        try:
            with stream:
                while True:
                    line = stream.readline(256)
                    if not line:
                        break
                    if line.startswith(b'frame='):
                        try:
                            session['decoded'] = int(line.split(b'=', 1)[1]) > 0
                        except ValueError:
                            pass
        except OSError:
            pass

    @staticmethod
    def failure_details(category):
        messages = {
            'provider_denied': 'De aanbieder weigert deze stream. Controleer je kijkaccount in het beheer of probeer later opnieuw.',
            'title_unavailable': 'Deze titel is niet beschikbaar bij de aanbieder. Kies een andere film of aflevering.',
            'provider_error': 'De aanbieder heeft een storing bij deze stream. Probeer het later opnieuw.',
            'source_timeout': 'De aanbieder reageert te langzaam. Probeer het later opnieuw.',
            'source_connection': 'De verbinding met de aanbieder is onderbroken.',
            'unsupported_codec': 'Het videoformaat van deze titel wordt nog niet ondersteund.',
            'conversion_error': 'Deze video kon niet worden omgezet voor je telefoon.',
            'invalid_source': 'De aanbieder levert voor deze titel geen leesbare video. Kies een andere titel.'}
        return messages.get(category, 'Deze bron kon niet worden afgespeeld. Probeer opnieuw of kies een andere titel.'), category not in {
            'provider_denied', 'title_unavailable', 'unsupported_codec', 'conversion_error', 'invalid_source'}

    @staticmethod
    def _diagnose(session):
        """Drain stderr without retaining or logging credential-bearing raw text."""
        stream = getattr(session['process'], 'stderr', None)
        if stream is None:
            return
        try:
            with stream:
                while True:
                    line = stream.readline(4096)
                    if not line:
                        break
                    text = line.decode('utf-8', errors='replace').lower()
                    tracks.observe(session,text)
                    metadata = re.search(r'lavf\.concat(?:dec)?\.duration=([0-9]+)', text)
                    if session.get('native_vod') and metadata:
                        seconds = int(metadata[1])/1_000_000
                        if 0 < seconds <= 7*86400:
                            session.update(duration=seconds, duration_verified=True)
                    category = None
                    status = re.search(r'(?:http error|server returned|http/[0-9.]+)\s+([1-5][0-9]{2})\b', text)
                    code = status[1] if status else ''
                    if code in ('401', '403'):
                        category = 'provider_denied'
                    elif code == '404':
                        category = 'title_unavailable'
                    elif code.startswith('5'):
                        category = 'provider_error'
                    elif 'timed out' in text or 'timeout' in text:
                        category = 'source_timeout'
                    elif any(value in text for value in ('connection refused', 'connection reset', 'network is unreachable', 'failed to resolve')):
                        category = 'source_connection'
                    elif 'invalid data found when processing input' in text:
                        category = 'invalid_source'
                    elif any(value in text for value in ('unknown decoder', 'decoder not found', 'unsupported codec')):
                        category = 'unsupported_codec'
                    elif any(value in text for value in ('error initializing output', 'error while opening encoder', 'failed to configure output pad')):
                        category = 'conversion_error'
                    if category and session['failure'] == 'unknown':
                        session['failure'] = category
        except OSError:
            pass

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
            if s.get('tracks_ready') and not s.get('tracks_configured') and (not s['native_vod'] or s['duration_verified'] or (s['output']/'index.m3u8').exists()) and s['state'] not in ('stopped','preempted','error'):
                tracks.configure(self,s,automatic=True,position=s['initial_position'])
            if self.seekable.enabled(s) and s['state'] not in ('stopped', 'preempted', 'error') and s['initial_position'] > 0 and not s.get('initial_seek_prepared'):
                s['initial_seek_prepared'] = True
                s['initial_position'] = min(s['initial_position'], max(0, s['duration']-.1))
                self.seekable.restart(s, math.floor(s['initial_position']))
            ready = (s['output'] / ('index_0.m3u8' if s.get('subtitle_id') is not None else 'index.m3u8')).exists()
            if ready and s['native_vod'] and not s['duration_verified'] and s['duration'] and s['initial_position'] > 0 and not s.get('initial_seek_prepared') and s['state'] not in ('stopped', 'preempted', 'error'):
                s.update(native_vod=False, initial_seek_prepared=True, position=math.floor(s['initial_position']))
                self.seekable.restart(s, s['position'])
                ready = False
            if ready and not s['ready_logged']:
                s['ready_logged'] = True
                now = time.monotonic()
                logging.getLogger('mijntv.stream').info(
                    'mobile_ready kind=%s account=%s role_version=2 preparation_seconds=%.2f stream_seconds=%.2f total_seconds=%.2f',
                    s['kind'], s.get('account_role') or 'none', s['transcoder_at']-s['requested_at'], now-s['transcoder_at'], now-s['requested_at'])
            if s['state'] not in ('stopped', 'preempted', 'error'):
                code = s['process'].poll()
                if code is not None:
                    if code == 0 and ready:
                        s['state'] = 'ended'
                    else:
                        logging.getLogger('mijntv.stream').info(
                            'mobile_failed kind=%s account=%s role_version=2 category=%s exit_code=%s stream_seconds=%.2f',
                            s['kind'], s.get('account_role') or 'none', s['failure'], code, time.monotonic()-s['transcoder_at'])
                        self._stop(s, 'error', self.failure_details(s['failure'])[0])
                elif ready:
                    s['state'] = 'paused' if s['paused'] else 'playing'
            return {k: s[k] for k in ('token', 'state', 'message', 'kind', 'id', 'title', 'channel', 'duration', 'position')} | {
                'ready': ready, 'playlist': '/api/mobile/hls/' + token + '/index.m3u8',
                'phase': 'buffering' if ready else 'preparing' if s['decoded'] else 'connecting',
                'retryable': self.failure_details(s['failure'])[1], 'failure': s['failure'],
                'native_vod': self.seekable.enabled(s), 'initial_position':s['initial_position'],
                'position':0 if self.seekable.enabled(s) else s['position'], 'tracks':tracks.options(s),'account_role':s.get('account_role')}

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

    def select_tracks(self, data):
        position=data.get('position',0)
        if isinstance(position,bool) or not isinstance(position,(int,float)) or not math.isfinite(position) or not 0<=position<=7*86400:
            raise ValueError('Ongeldige afspeelpositie.')
        with self.streams.lock:
            session=self._session(data.get('token'))
            if session['kind'] not in ('movie','episode') or session['state'] in ('stopped','preempted','error'):
                raise ValueError('Taalkeuze is voor deze video niet beschikbaar.')
            position=min(position,max(0,session['duration']-.1)) if session['duration'] else position
            tracks.configure(self,session,data.get('audio'),data.get('subtitle'),position=position)
            return self.status(session['token'])

    def asset(self, token, name):
        if not re.fullmatch(r'(?:index|video|captions)\.m3u8|segment[0-9]{6,12}\.ts|caption[0-9]{6,12}\.vtt', name):
            raise ValueError('Ongeldig videobestand.')
        caption=re.fullmatch(r'caption([0-9]{6,12})\.vtt',name)
        with self.streams.lock:
            s = self._session(token)
            if s['state'] in ('preempted', 'stopped', 'error'):
                raise ValueError('De mobiele speler is gestopt.')
            s['seen'] = time.monotonic()
            native = self.seekable.enabled(s)
            subtitles=s.get('subtitle_id') is not None
            if name=='index.m3u8' and subtitles:return tracks.master(s)
            if (name in ('captions.m3u8','video.m3u8') or caption) and not subtitles:
                raise ValueError('Deze ondertiteling is niet beschikbaar.')
            if native and name.endswith('.m3u8'):
                return self.seekable.playlist(s, captions=name=='captions.m3u8')
            if not native:
                filename=('index_0_vtt.m3u8' if name=='captions.m3u8' else 'index_0.m3u8' if subtitles and name.endswith('.m3u8') else
                          'index_0'+str(int(caption[1]))+'.vtt' if caption else name)
                path = s['output']/filename
                if not path.is_file() or path.is_symlink() or (caption and not self.seekable.caption_ready(s,filename)):
                    raise ValueError('Videosegment wordt nog voorbereid.')
                try:data = path.read_bytes()
                except OSError:raise ValueError('Dit videosegment is verlopen. Ga terug naar live.') from None
                if name=='captions.m3u8':
                    data=re.sub(rb'index_0([0-9]+)\.vtt',lambda m:('caption'+format(int(m[1]),'06d')+'.vtt').encode(),data)
                if caption:return self.caption_data(data), 'text/vtt'
                return data, 'application/vnd.apple.mpegurl' if name.endswith('.m3u8') else 'video/mp2t'
        return self.seekable.segment(token, int(caption[1]) if caption else int(name[7:-3]),caption=bool(caption))

    @staticmethod
    def caption_data(data):
        # FFmpeg's MPEG-TS muxer adds 1.4 seconds to the source timestamps.
        return data.replace(b'WEBVTT\n',b'WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:126000\n',1)

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
        self.vod_backups.close()
        with self.streams.lock:
            for s in list(self.sessions.values()):
                self._cleanup(s)
