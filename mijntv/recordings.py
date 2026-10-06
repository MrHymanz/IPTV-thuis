"""Persistent EPG recording scheduler; private input manifests keep credentials out of argv."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from urllib.parse import parse_qsl, urlsplit

MIN_FREE = 512 * 1024 * 1024
STATES = {'queued': 'Gepland', 'recording': 'Neemt op', 'completed': 'Opgenomen',
          'failed': 'Mislukt', 'cancelled': 'Geannuleerd', 'interrupted': 'Onderbroken'}


class Recordings:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.process = None
        self.active = None
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
            channel = db.execute('''SELECT f.label,c.tvg_id FROM favorites f JOIN channels c ON c.id=f.id
                                    WHERE f.id=?''', (channel_id,)).fetchone()
            if not channel:
                raise ValueError('Kies een zender uit de favorieten.')
            rows = db.execute('''SELECT start,end,title FROM epg_programmes
                WHERE epg_id=? AND end>? ORDER BY start LIMIT 300''',
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
            with self.store.connect() as db:
                channel = db.execute('''SELECT f.label,c.url,c.tvg_id FROM favorites f
                    JOIN channels c ON c.id=f.id WHERE f.id=?''', (channel_id,)).fetchone()
                if not channel or not channel['url']:
                    raise ValueError('Deze zender is niet beschikbaar.')
                if programme_start is None:
                    programme = db.execute('''SELECT start,end,title FROM epg_programmes
                        WHERE epg_id=? AND start<=? AND end>? ORDER BY start DESC LIMIT 1''',
                        (channel['tvg_id'].strip(), now, now)).fetchone()
                    if programme is None:
                        start, end, title = now, now + 3600, 'Handmatige opname (1 uur)'
                    else:
                        start, end, title = programme['start'], programme['end'], programme['title']
                else:
                    if isinstance(programme_start, bool) or not isinstance(programme_start, int):
                        raise ValueError('Ongeldig programma.')
                    programme = db.execute('''SELECT start,end,title FROM epg_programmes
                        WHERE epg_id=? AND start=? AND end>?''',
                        (channel['tvg_id'].strip(), programme_start, now)).fetchone()
                    if not programme:
                        raise ValueError('Dit programma staat niet meer in de gids of is al afgelopen.')
                    start, end, title = programme['start'], programme['end'], programme['title']
                start = max(now, start)
                conflict = db.execute('''SELECT id FROM recordings WHERE state IN ('queued','recording')
                    AND start<? AND end>?''', (end, start)).fetchone()
                if conflict:
                    raise ValueError('Er is al een opname in dit tijdvak. Er kan één opname tegelijk lopen.')
                identifier = uuid.uuid4().hex
                db.execute('INSERT INTO recordings VALUES (?,?,?,?,?,?,?, ?,?)',
                           (identifier, channel_id, channel['label'], title, start, end, 'queued', str(path), ''))
        return identifier

    @staticmethod
    def file(row):
        return Path(row['directory']) / (row['id'] + '.ts')

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

    def delete(self, identifier):
        with self.lock:
            with self.store.connect() as db:
                row = db.execute('SELECT * FROM recordings WHERE id=?', (identifier,)).fetchone()
                if not row or row['state'] in ('queued', 'recording'):
                    raise ValueError('Annuleer of stop de opname voordat je deze verwijdert.')
                self.validate_directory(Path(row['directory']))
                self.file(row).unlink(missing_ok=True)
                db.execute('DELETE FROM recordings WHERE id=?', (identifier,))

    def stop_process(self, state, error=''):
        if self.process and self.stopping is None:
            self.stopping = (state, error, time.monotonic())
            if self.process.poll() is None:
                self.process.send_signal(signal.SIGINT)

    def start(self, row):
        manifest = None
        try:
            path = self.validate_directory(Path(row['directory']), create=True)
            if shutil.disk_usage(path).free < MIN_FREE:
                raise ValueError('Opnameschijf bijna vol; opname niet gestart.')
            with self.store.connect() as db:
                channel = db.execute('SELECT url FROM channels WHERE id=?', (row['channel_id'],)).fetchone()
            if not channel:
                raise ValueError('Zender niet meer beschikbaar.')
            address, _, raw_headers = channel['url'].partition('|')
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
        except Exception as error:
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
                Path(self.manifest).unlink(missing_ok=True)
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
        while not stop.is_set():
            try:
                self.step()
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
                self.process, self.active, self.manifest, self.stopping = None, None, None, None
