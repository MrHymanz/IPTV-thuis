"""Persistent EPG recording scheduler; private input manifests keep credentials out of argv."""
import os
import re
import unicodedata
import logging
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from urllib.parse import parse_qsl, parse_qs, urlsplit
from .store import iter_m3u, Store
from .maintenance import BACKGROUND_STARTUP_DELAY
from .catchup import Catchup
from .epg import PROGRAMME_VISIBLE
from .backup import channel_key
from .matching import RecordingCatalog

MIN_FREE = 512 * 1024 * 1024
END_BUFFER_SECONDS = 5 * 60
STATES = {'queued': 'Gepland', 'recording': 'Neemt op', 'completed': 'Opgenomen',
          'failed': 'Mislukt', 'cancelled': 'Geannuleerd', 'interrupted': 'Onderbroken'}


class Recordings:
    def __init__(self, store, streams=None):
        self.store = store
        self.catchup = Catchup(store)
        self.streams = streams
        self.input_refreshing = False
        self.retry_recording = None
        self.retry_attempts = {}
        self.retry_sources = {}
        self.lock = threading.RLock()
        self.process = None
        self.active = None
        self.archive_stop = None
        self.manifest = None
        self.stopping = None
        if not store.setting('recording_directory'):
            try:
                self.validate_directory(self.directory(), create=True)
            except (ValueError, OSError):
                pass
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS recordings (
                id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, channel TEXT NOT NULL,
                title TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
                state TEXT NOT NULL, directory TEXT NOT NULL, error TEXT NOT NULL DEFAULT '')''')
            db.execute('''CREATE TABLE IF NOT EXISTS recording_channels (
                id TEXT PRIMARY KEY,name TEXT NOT NULL,group_name TEXT NOT NULL,url TEXT NOT NULL,tvg_id TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS recording_parts (
                id TEXT PRIMARY KEY,parent_id TEXT NOT NULL,part INTEGER NOT NULL,title TEXT NOT NULL)''')

    def input_settings(self):
        with self.store.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM recording_channels')]
        configured=bool(self.store.setting('recording_source'))
        favorites=self.store.favorites(playback=True)
        catalog=RecordingCatalog(rows)
        matches=[]
        for favorite in favorites:
            source=catalog.match(favorite) if configured else None
            matches.append({'id':favorite['id'],'label':favorite['label'],'matched':bool(source),
                            'source':source['name'] if source else ''})
        return {'configured':configured,'channels':len(rows),'matches':matches,
                'matched':sum(m['matched'] for m in matches),'total':len(favorites),
                'error':self.store.setting('recording_source_error')}

    def save_input(self, source):
        if not isinstance(source,str) or len(source)>16384:
            raise ValueError('Ongeldig opnameadres.')
        source=source.strip()
        with self.lock:
            if self.active:
                raise ValueError('Stop eerst de actieve opname voordat je het opnameaccount wijzigt.')
        if not source:
            with self.store.connect() as db:
                db.execute('DELETE FROM recording_channels')
                db.execute("DELETE FROM settings WHERE key IN ('recording_source','recording_source_refreshed','recording_source_error')")
            return self.input_settings()
        def identity(address):
            p=urlsplit(address); q=parse_qs(p.query)
            return (p.hostname,p.port,q.get('username',[p.username])[-1])
        primary=self.store.setting('source')
        backup=self.store.setting('backup_source')
        if any(source==other or (identity(source)[2] and identity(source)==identity(other))
               for other in (primary,backup) if other):
            raise ValueError('Gebruik een tweede account, niet hetzelfde account als voor live-tv of de backup.')
        self._import_input(source)
        return self.input_settings()

    def _import_input(self, source):
        previous=self.store.setting('recording_source')
        favorites=self.store.favorites(playback=True)
        epg={f['epg_id'].strip().casefold() for f in favorites if f.get('epg_id')}
        names={channel_key(c.get('name') or f['label'],c.get('group_name') or '')
               for f in favorites for c in [f]+f.get('sources',[])}
        rows=[]
        with self.store.provider_playlist(source) as text:
            for row in iter_m3u(text):
                if row[4].strip().casefold() in epg or channel_key(row[1],row[2]) in names:
                    rows.append((row[0],row[1],row[2],row[3],row[4].strip()))
        if not rows:
            raise ValueError('Geen passende zenders gevonden voor de favorieten. De vorige opname-ingang blijft behouden.')
        with self.lock, self.store.connect() as db:
            if self.store.setting('recording_source')!=previous or (self.active and source!=previous):
                raise ValueError('De opname-ingang is ondertussen gewijzigd of er is een opname gestart. Probeer opnieuw.')
            db.execute('DELETE FROM recording_channels')
            db.executemany('INSERT INTO recording_channels VALUES (?,?,?,?,?)',rows)
            for k,v in [('recording_source',source),('recording_source_refreshed',str(time.time())),('recording_source_error','')]:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(k,v))

    def refresh_input_background(self):
        source=self.store.setting('recording_source')
        try:
            due=time.time()-float(self.store.setting('recording_source_refreshed','0'))>=21600
        except ValueError:
            due=True
        if not source or not due or self.input_refreshing:
            return
        self.input_refreshing=True
        def refresh():
            try:
                self._import_input(source)
            except Exception:
                if self.store.setting('recording_source')==source:
                    self.store.set_setting('recording_source_error','Verversen van de opname-ingang mislukt; de vorige lijst blijft behouden.')
                    self.store.set_setting('recording_source_refreshed',str(time.time()-21600+900))
            finally:
                self.input_refreshing=False
        threading.Thread(target=refresh,daemon=True).start()

    def recording_source(self, channel):
        if not self.store.setting('recording_source'):
            return 'primary',(channel.get('sources') or [channel])[0]
        with self.store.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM recording_channels')]
        source=RecordingCatalog(rows).match(channel)
        if source:
            return 'secondary',source
        raise ValueError('Deze zender ontbreekt of is dubbelzinnig in het opnameaccount. Ververs de opname-ingang in het beheer.')

    def required_channel(self):
        required=self.streams.required_channel() if self.streams else None
        if required:
            return required
        if self.retry_recording and self.retry_recording['slot']=='primary':
            return dict(self.retry_recording['channel'],stream_token=None)
        return None

    def viewing_url(self, channel, source):
        if self.retry_recording and self.retry_recording['slot']=='primary':
            if channel['id']!=self.retry_recording['channel']['id']:
                raise ValueError('De opname wordt hersteld. Stop de opname om naar een andere zender te gaan.')
            source=self.retry_sources.get(self.retry_recording['id'],source)
        if urlsplit(source['url'].partition('|')[0]).scheme not in ('http','https'):
            return source['url']
        return self.streams.view(channel,source) if self.streams else source['url']

    def reserve_archive(self, stop):
        with self.lock:
            if isinstance(self.required_channel(), dict) or (self.active and not self.store.setting('recording_source')):
                raise ValueError('Er loopt een opname op dit account. Terugkijken is daarna weer beschikbaar.')
            self.stop_view()
            self.archive_stop = stop

    def release_archive(self):
        with self.lock:
            self.archive_stop = None

    def stop_view(self):
        if self.streams:
            self.streams.stop_view()

    def viewing_source_id(self):
        if self.streams:
            with self.streams.lock:
                return self.streams.viewer.source['id'] if self.streams.viewer else None

    def viewing_token(self):
        if self.streams:
            with self.streams.lock:
                return self.streams.viewer.token if self.streams.viewer else None

    def stop_for_zap(self):
        with self.lock:
            identifier=self.active or (self.retry_recording['id'] if self.retry_recording else None)
            if identifier:
                self.cancel(identifier)

    def queue_continuation(self, row, now=None):
        """Preserve the old file and resume in a new part, with bounded retries."""
        now=time.time() if now is None else now
        if not self.streams or row['end']-now<=5:
            return False
        with self.store.connect() as db:
            part=db.execute('SELECT parent_id,part,title FROM recording_parts WHERE id=?',(row['id'],)).fetchone()
            if db.execute("SELECT 1 FROM recordings WHERE id<>? AND state IN ('queued','recording') AND start<? AND end>?",
                          (row['id'],row['end'],now)).fetchone():
                return False
        parent,number,title=tuple(part) if part else (row['id'],1,row['title'])
        attempts=[t for t in self.retry_attempts.get(parent,[]) if now-t<600]
        if len(attempts)>=5:
            logging.getLogger('mijntv.stream').info('recording_retry_limit parent_id=%s',parent)
            return False
        delay=min(30,2**(len(attempts)+1))
        if now+delay>=row['end']-5:
            return False
        channel=next((f for f in self.store.favorites(playback=True) if f['id']==row['channel_id']),None)
        if not channel:
            return False
        slot,source=self.recording_source(channel)
        with self.streams.lock:
            feed=self.streams.slots.get(slot)
            if feed and feed.channel['id']==channel['id']:
                source=feed.source
                if feed.closed:
                    if slot=='primary':
                        choices=channel.get('sources') or [channel]
                    else:
                        with self.store.connect() as db:
                            choices=[dict(r) for r in db.execute('SELECT * FROM recording_channels WHERE tvg_id=?',(channel.get('epg_id',''),))] if channel.get('epg_id') else [source]
                    if choices:
                        position=next((i for i,s in enumerate(choices) if s['id']==feed.source['id']),-1)
                        source=choices[(position+1)%len(choices)]
        identifier=uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute('INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?)',
                (identifier,row['channel_id'],row['channel'],f'{title} (deel {number+1})',int(now+delay),row['end'],'queued',row['directory'],''))
            db.execute('INSERT INTO recording_parts VALUES (?,?,?,?)',(identifier,parent,number+1,title))
            db.execute("UPDATE recordings SET state='interrupted',error=? WHERE id=?",
                ('Stream onderbroken; opname wordt automatisch voortgezet in een volgend deel.',row['id']))
        self.retry_attempts[parent]=attempts+[now]
        self.retry_sources[identifier]=source
        self.retry_recording={'id':identifier,'channel':channel,'slot':slot}
        logging.getLogger('mijntv.stream').info('recording_retry parent_id=%s part=%s delay=%s source_id=%s',parent,number+1,delay,source['id'])
        return True

    def resume(self, identifier):
        with self.lock:
            with self.store.connect() as db:
                row=db.execute('SELECT * FROM recordings WHERE id=?',(identifier,)).fetchone()
            if not row or row['state'] not in ('failed','interrupted'):
                raise ValueError('Alleen een onderbroken of mislukte opname kan worden hervat.')
            self.validate_directory(Path(row['directory']))
            if shutil.disk_usage(row['directory']).free<MIN_FREE:
                raise ValueError('Er is onvoldoende vrije ruimte om de opname te hervatten.')
            if not self.queue_continuation(row):
                raise ValueError('Hervatten is niet mogelijk: het programma is afgelopen, er loopt al een opname of de herstelgrens is bereikt.')
            return self.retry_recording['id']

    def directory(self):
        default = os.environ.get('IPTV_RECORDING_DIR', str(Path(self.store.path).parent / 'recordings'))
        return Path(self.store.setting('recording_directory', default))

    @staticmethod
    def validate_directory(path, create=False):
        if not path.is_absolute() or '..' in path.parts or len(str(path)) > 1000:
            raise ValueError('Gebruik een absoluut pad voor de opnamemap.')
        # A disconnected removable drive must never fall back to the system disk.
        if path.parts[:3] == ('/', 'var', 'media'):
            if len(path.parts) < 4 or not os.path.ismount(Path(*path.parts[:4])):
                raise ValueError('Deze externe schijf is niet aangekoppeld.')
        if create and not path.exists() and path.parent.is_dir():
            path.mkdir(mode=0o700)
        if not path.is_dir():
            raise ValueError('De opnamemap is niet bereikbaar. Controleer de schijf en het pad.')
        if create:
            try:
                with tempfile.TemporaryFile(dir=path):
                    pass
            except OSError:
                raise ValueError('De opnamemap is niet schrijfbaar.') from None
        return path

    def storage(self):
        path = self.directory()
        result = {'directory': str(path), 'available': False, 'free': None, 'total': None,
                  'error': '', 'recorder_available': bool(shutil.which('ffmpeg'))}
        try:
            self.validate_directory(path)
            usage = shutil.disk_usage(path)
            result.update(available=True, free=usage.free, total=usage.total)
        except (ValueError, OSError) as error:
            result['error'] = str(error) if isinstance(error, ValueError) else 'Opslag niet bereikbaar.'
        return result

    def save_storage(self, value):
        if not isinstance(value, str):
            raise ValueError('Ongeldige opnamemap.')
        path = Path(value.strip())
        with self.lock:
            self.validate_directory(path, create=True)
            self.store.set_setting('recording_directory', str(path))
        return self.storage()

    def guide(self, channel_id):
        with self.store.connect() as db:
            channel = db.execute('''SELECT f.label,COALESCE(e.epg_id,c.tvg_id) AS tvg_id FROM favorites f
                                    LEFT JOIN channels c ON c.id=f.id
                                    LEFT JOIN favorite_epg e ON e.id=f.id WHERE f.id=?''', (channel_id,)).fetchone()
            if not channel:
                raise ValueError('Kies een zender uit de favorieten.')
            rows = db.execute(f'''SELECT start,end,title FROM epg_programmes p
                WHERE epg_id=? AND end>? AND {PROGRAMME_VISIBLE} ORDER BY start LIMIT 300''',
                (channel['tvg_id'].strip(), int(time.time())))
            return {'channel': channel['label'], 'programmes': [dict(row) for row in rows]}

    def schedule(self, channel_id, programme_start=None):
        if not shutil.which('ffmpeg'):
            raise ValueError('De opnamefunctie is nog niet geïnstalleerd op deze server.')
        now = int(time.time())
        with self.lock:
            path = self.validate_directory(self.directory(), create=True)
            if shutil.disk_usage(path).free < MIN_FREE:
                raise ValueError('Er is minder dan 512 MB vrij op de opnameschijf.')
            channel = next((f for f in self.store.favorites(playback=True) if f['id']==channel_id),None)
            with self.store.connect() as db:
                if not channel or not channel['url']:
                    raise ValueError('Deze zender is niet beschikbaar.')
                if programme_start is None:
                    programme = db.execute(f'''SELECT start,end,title FROM epg_programmes p
                        WHERE epg_id=? AND start<=? AND end>? AND {PROGRAMME_VISIBLE} ORDER BY start DESC LIMIT 1''',
                        (channel['epg_id'].strip(), now, now)).fetchone()
                    if programme is None:
                        start, end, title = now, now + 3600, 'Handmatige opname (1 uur)'
                    else:
                        start, end, title = programme['start'], programme['end'], programme['title']
                else:
                    if isinstance(programme_start, bool) or not isinstance(programme_start, int):
                        raise ValueError('Ongeldig programma.')
                    programme = self.catchup.recording_programme(channel_id, programme_start) or db.execute(f'''SELECT start,end,title FROM epg_programmes p
                        WHERE epg_id=? AND start=? AND end>? AND {PROGRAMME_VISIBLE}''',
                        (channel['epg_id'].strip(), programme_start, now)).fetchone()
                    if not programme:
                        raise ValueError('Dit programma staat niet meer in de gids of is al afgelopen.')
                    start, end, title = programme['start'], programme['end'], programme['title']
                start = max(now, start)
                end += END_BUFFER_SECONDS
                conflict = db.execute('''SELECT id FROM recordings WHERE state IN ('queued','recording')
                    AND start<? AND end>?''', (end, start)).fetchone()
                if conflict:
                    raise ValueError('Er is al een opname in dit tijdvak, inclusief vijf minuten uitloop. Er kan één opname tegelijk lopen.')
                identifier = uuid.uuid4().hex
                db.execute('INSERT INTO recordings VALUES (?,?,?,?,?,?,?, ?,?)',
                           (identifier, channel_id, channel['label'], title, start, end, 'queued', str(path), ''))
        return identifier

    @staticmethod
    def file(row):
        directory = Path(row['directory'])
        legacy = directory / (row['id'] + '.ts')
        if legacy.exists():
            return legacy
        def component(value):
            value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
            return re.sub(r'[^A-Za-z0-9]+', '_', value).strip('_')[:70] or 'Opname'
        stamp = time.strftime('%Y-%m-%d_%H-%M', time.localtime(row['start']))
        name = f"{stamp}_{component(row['channel'])}_{component(row['title'])}_{row['id'][:8]}.ts"
        return directory / name

    def list(self):
        with self.store.connect() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM recordings ORDER BY start DESC')]
        for row in rows:
            row.pop('directory')
            row['status'] = STATES[row['state']]
            try:
                path = self.playback(row['id'])
                row['bytes'] = path.stat().st_size
                row['playable'] = row['bytes'] > 0
            except (OSError, ValueError):
                row['bytes'], row['playable'] = 0, False
        return rows

    def playback(self, identifier):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM recordings WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('Opname niet gevonden.')
        self.validate_directory(Path(row['directory']))
        path = self.file(row)
        if not path.is_file() or path.is_symlink():
            raise ValueError('Opnamebestand niet beschikbaar.')
        return path

    def update(self, identifier, state, error=''):
        with self.store.connect() as db:
            db.execute('UPDATE recordings SET state=?,error=? WHERE id=?', (state, error, identifier))

    def cancel(self, identifier):
        with self.lock:
            with self.store.connect() as db:
                row = db.execute('SELECT state FROM recordings WHERE id=?', (identifier,)).fetchone()
                if not row or row['state'] not in ('queued', 'recording'):
                    raise ValueError('Deze opname is niet gepland of actief.')
            if identifier == self.active:
                self.stop_process('cancelled')
            else:
                self.update(identifier, 'cancelled')
                if self.retry_recording and self.retry_recording['id']==identifier:
                    self.retry_recording=None
                    self.retry_sources.pop(identifier,None)

    def delete(self, identifier):
        with self.lock:
            with self.store.connect() as db:
                row = db.execute('SELECT * FROM recordings WHERE id=?', (identifier,)).fetchone()
                if not row or row['state'] in ('queued', 'recording'):
                    raise ValueError('Annuleer of stop de opname voordat je deze verwijdert.')
                self.validate_directory(Path(row['directory']))
                self.file(row).unlink(missing_ok=True)
                db.execute('DELETE FROM recordings WHERE id=?', (identifier,))
                db.execute('DELETE FROM recording_parts WHERE id=?',(identifier,))

    def stop_process(self, state, error=''):
        if self.process and self.stopping is None:
            self.stopping = (state, error, time.monotonic())
            if self.process.poll() is None:
                self.process.send_signal(signal.SIGINT)

    def start(self, row):
        manifest = None
        pinned = False
        try:
            path = self.validate_directory(Path(row['directory']), create=True)
            if shutil.disk_usage(path).free < MIN_FREE:
                raise ValueError('Opnameschijf bijna vol; opname niet gestart.')
            channel = next((f for f in self.store.favorites(playback=True) if f['id']==row['channel_id']),None)
            if not channel:
                raise ValueError('Zender niet meer beschikbaar.')
            slot, source = self.recording_source(channel)
            source=self.retry_sources.pop(row['id'],source)
            if slot == 'primary' and self.archive_stop:
                stop, self.archive_stop = self.archive_stop, None
                stop()
            address, _, raw_headers = source['url'].partition('|')
            if self.streams:
                feed = self.streams.acquire(slot,channel,source,recording=True)
                pinned = True
                address, raw_headers = self.streams.url(feed), ''
            if urlsplit(address).scheme not in ('http', 'https') or '\n' in address or '\r' in address:
                raise ValueError('Dit streamtype kan niet worden opgenomen.')
            def quote(value):
                return "'" + value.replace("'", "'\\''") + "'"
            headers = ''.join(f'{key}: {value}\r\n' for key, value in parse_qsl(raw_headers)
                              if '\r' not in key+value and '\n' not in key+value)
            manifest = tempfile.NamedTemporaryFile(mode='w', prefix='iptv-record-', suffix='.ffconcat', delete=False)
            manifest.write('ffconcat version 1.0\nfile ' + quote(address) + '\noption rw_timeout 50000000\n')
            if headers:
                # ffconcat options cannot contain literal line breaks.
                raise ValueError('Opnemen van streams met aangepaste headers wordt nog niet ondersteund.')
            manifest.close()
            process = subprocess.Popen(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'quiet',
                '-f', 'concat', '-safe', '0', '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
                '-i', manifest.name, '-map', '0:v:0?', '-map', '0:a?', '-c', 'copy',
                '-f', 'mpegts', '-n', str(self.file(row))],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.process, self.active, self.manifest = process, row['id'], manifest.name
            self.stopping = None
            self.update(row['id'], 'recording')
            if self.retry_recording and self.retry_recording['id']==row['id']:
                self.retry_recording=None
        except Exception as error:
            if self.retry_recording and self.retry_recording['id']==row['id']:
                self.retry_recording=None
            if pinned:
                self.streams.release_recording()
            if manifest:
                manifest.close()
                Path(manifest.name).unlink(missing_ok=True)
            self.update(row['id'], 'failed', str(error) if isinstance(error, ValueError) else 'De opname kon niet starten.')

    def step(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if self.process:
                with self.store.connect() as db:
                    row = db.execute('SELECT * FROM recordings WHERE id=?', (self.active,)).fetchone()
                if self.process.poll() is None:
                    try:
                        self.validate_directory(Path(row['directory']))
                        if shutil.disk_usage(row['directory']).free < MIN_FREE:
                            self.stop_process('failed', 'Opname gestopt: minder dan 512 MB vrije ruimte.')
                    except (ValueError, OSError):
                        self.stop_process('failed', 'Opnameschijf is niet meer bereikbaar.')
                    if now >= row['end']:
                        self.stop_process('completed')
                    if self.stopping and time.monotonic()-self.stopping[2] > 8:
                        self.process.kill()
                    return
                size = self.file(row).stat().st_size if self.file(row).is_file() else 0
                if self.stopping:
                    state, error, _ = self.stopping
                    if state == 'completed' and not size:
                        state, error = 'failed', 'De stream leverde geen opname op.'
                elif size and self.process.returncode == 0 and now >= row['end']-5:
                    state, error = 'completed', ''
                else:
                    state, error = 'failed', 'De stream is voortijdig gestopt. Een gedeeltelijke opname blijft bewaard.'
                self.update(self.active, state, error)
                if not self.stopping and now<row['end']-5:
                    logging.getLogger('mijntv.stream').info('recording_stream_ended recording_id=%s exit_code=%s bytes=%s remaining_seconds=%s',
                        row['id'],self.process.returncode,size,int(row['end']-now))
                    try:
                        self.queue_continuation(row,now)
                    except (OSError,ValueError):
                        logging.getLogger('mijntv.stream').info('recording_retry_unavailable recording_id=%s',row['id'])
                Path(self.manifest).unlink(missing_ok=True)
                if self.streams:
                    self.streams.release_recording()
                self.process, self.active, self.manifest, self.stopping = None, None, None, None
            with self.store.connect() as db:
                db.execute("UPDATE recordings SET state='failed',error='Starttijd gemist: de app stond uit.' WHERE state='queued' AND end<=?", (now,))
                row = db.execute("SELECT * FROM recordings WHERE state='queued' AND start<=? AND end>? ORDER BY start LIMIT 1", (now, now)).fetchone()
            if row:
                self.start(row)

    def run(self, stop):
        # Active processes cannot survive application/container restart. Keep partial files.
        with self.store.connect() as db:
            db.execute("UPDATE recordings SET state='interrupted',error='Onderbroken door een herstart; het gedeeltelijke bestand blijft bewaard.' WHERE state='recording'")
        maintenance_at=time.monotonic()+BACKGROUND_STARTUP_DELAY
        while not stop.is_set():
            try:
                self.step()
                if time.monotonic()>=maintenance_at:
                    self.refresh_input_background()
            except Exception:
                print('Opnamecontrole mislukt; wordt opnieuw geprobeerd.', flush=True)
            stop.wait(2)
        self.close()

    def close(self):
        with self.lock:
            if self.process:
                self.stop_process('interrupted', 'Onderbroken doordat de app werd afgesloten.')
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
                state, error, _ = self.stopping
                self.update(self.active, state, error)
                Path(self.manifest).unlink(missing_ok=True)
                if self.streams:
                    self.streams.release_recording()
                self.process, self.active, self.manifest, self.stopping = None, None, None, None
