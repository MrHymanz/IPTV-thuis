"""Playlist and favorite storage; only Python's standard library is required."""
import hashlib
import io
import json
import os
import re
import sqlite3
import uuid
import threading
import tempfile
import time
from contextlib import contextmanager
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

MAX_PLAYLIST = 512 * 1024 * 1024
MAX_LINE = 1024 * 1024
ATTR = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"')


def xtream_playlist_url(server, username, password):
    """Build the standard Xtream M3U endpoint from separate login fields."""
    if any(not isinstance(value, str) or not value.strip() for value in (server, username, password)):
        raise ValueError('Vul het serveradres, de gebruikersnaam en het wachtwoord in.')
    if any(len(value) > 4096 or '\r' in value or '\n' in value for value in (server, username, password)):
        raise ValueError('Ongeldige IPTV-inloggegevens.')
    try:
        parsed = urlsplit(server.strip())
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
            raise ValueError()
        parsed.port  # Reject malformed port numbers without exposing input.
    except ValueError:
        raise ValueError('Geef alleen het http- of https-serveradres op, zonder inloggegevens of queryparameters.') from None
    path = parsed.path.rstrip('/')
    if path.endswith(('/get.php', '/player_api.php')):
        path = path.rsplit('/', 1)[0]
    query = urlencode({'username': username, 'password': password, 'type': 'm3u_plus', 'output': 'ts'})
    return urlunsplit((parsed.scheme, parsed.netloc, path + '/get.php', query, ''))


def stream_url(value):
    value = str(value).strip()
    # Kodi supports request headers after |, which must survive import.
    parsed = urlsplit(value.split('|', 1)[0])
    if parsed.scheme not in ('http', 'https', 'rtsp', 'rtmp', 'udp') or not parsed.netloc:
        raise ValueError('Geef een geldig streamadres op (http, https, rtsp, rtmp of udp).')
    if '\n' in value or '\r' in value:
        raise ValueError('Ongeldig streamadres.')
    return value


def iter_m3u(text):
    """Stable IDs exclude stream tokens. Exact identity duplicates are retained by occurrence."""
    pending, occurrences, count = None, {}, 0
    source = io.StringIO(text) if isinstance(text, str) else text
    lines = iter(lambda: source.readline(MAX_LINE + 1), '') if hasattr(source, 'readline') else iter(source)
    for line in lines:
        if len(line) > MAX_LINE:
            raise ValueError('Een regel in de M3U is groter dan 1 MB. Controleer het playlistformaat.')
        line = line.strip().lstrip('\ufeff')
        if line.startswith('#EXTINF:'):
            # A comma inside a quoted attribute is not the name delimiter.
            quoted = False
            delimiter = None
            for i, char in enumerate(line):
                if char == '"':
                    quoted = not quoted
                elif char == ',' and not quoted:
                    delimiter = i
                    break
            if delimiter is None:
                pending = None
                continue
            attributes = dict(ATTR.findall(line[:delimiter]))
            pending = (line[delimiter + 1:].strip()[:300], attributes)
        elif line and not line.startswith('#') and pending:
            name, attributes = pending
            pending = None
            try:
                url = stream_url(line)
            except ValueError:
                continue
            if not name:
                continue
            group = attributes.get('group-title', '')[:300]
            identity = json.dumps([attributes.get('tvg-id', ''), group, name], ensure_ascii=False)
            occurrence = occurrences.get(identity, 0)
            occurrences[identity] = occurrence + 1
            channel_id = hashlib.sha256((identity + ':' + str(occurrence)).encode()).hexdigest()[:32]
            count += 1
            yield (channel_id, name, group, url, attributes.get('tvg-id', ''))
    if not count:
        raise ValueError('Geen geldige zenders gevonden. Upload een uitgebreide M3U-lijst met EXTINF-regels.')


def parse_m3u(text):
    """Convenience helper; database imports use the streaming iterator directly."""
    return list(iter_m3u(text))


class PlaylistTooLarge(ValueError):
    def __init__(self):
        super().__init__('De M3U is groter dan 512 MB.')


def copy_playlist(source, destination, length=None):
    """Copy bounded chunks to disk. Reject truncated uploads and over-limit downloads."""
    if length is not None and not 0 <= length <= MAX_PLAYLIST:
        raise PlaylistTooLarge()
    total = 0
    while length is None or total < length:
        chunk = source.read(min(1024 * 1024, length - total) if length is not None else 1024 * 1024)
        if not chunk:
            if length is not None and total != length:
                raise ValueError('Het uploaden is onderbroken. Probeer opnieuw.')
            break
        total += len(chunk)
        if total > MAX_PLAYLIST:
            raise PlaylistTooLarge()
        destination.write(chunk)
    destination.seek(0)
    return total


def fetch_playlist(url, directory=None):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('Het M3U-adres moet een http- of https-adres zijn.')
    temporary = tempfile.TemporaryFile(mode='w+b', dir=directory)
    try:
        with urlopen(Request(url, headers={'User-Agent': 'IPTV-thuis/0.1', 'Accept-Encoding': 'identity'}), timeout=90) as response:
            if urlsplit(response.geturl()).scheme not in ('http', 'https'):
                raise ValueError('Ongeldige omleiding.')
            size = response.headers.get('Content-Length')
            if size and int(size) > MAX_PLAYLIST:
                raise PlaylistTooLarge()
            copy_playlist(response, temporary, int(size) if size else None)
    except PlaylistTooLarge:
        temporary.close()
        raise
    except Exception:
        temporary.close()
        raise ValueError('De M3U kon niet worden opgehaald. Controleer adres, inloggegevens en netwerk.') from None
    return io.TextIOWrapper(temporary, encoding='utf-8-sig', errors='replace')


class Store:
    def __init__(self, directory):
        self.import_lock = threading.RLock()
        os.makedirs(directory, mode=0o700, exist_ok=True)
        self.path = os.path.join(directory, 'mijntv.db')
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS channels (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, group_name TEXT NOT NULL,
                    url TEXT NOT NULL, tvg_id TEXT NOT NULL, manual INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS favorites (
                    id TEXT PRIMARY KEY, label TEXT NOT NULL, position INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key, default=''):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return row[0] if row else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, value))

    def import_playlist(self, text, source=''):
        # Build in short transactions: favorites must remain writable while a
        # provider list with millions of entries is parsed. Swap table names
        # only after validation succeeds, preserving the original on failure.
        suffix = uuid.uuid4().hex
        staging, previous = 'channels_import_' + suffix, 'channels_previous_' + suffix
        count = 0
        with self.import_lock:
            try:
                with self.connect() as db:
                    db.execute(f'''CREATE TABLE {staging} (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, group_name TEXT NOT NULL,
                        url TEXT NOT NULL, tvg_id TEXT NOT NULL, manual INTEGER NOT NULL DEFAULT 0)''')
                batch = []
                for row in iter_m3u(text):
                    batch.append(row)
                    count += 1
                    if len(batch) == 1000:
                        with self.connect() as db:
                            db.executemany(f'INSERT INTO {staging}(id,name,group_name,url,tvg_id) VALUES (?,?,?,?,?)', batch)
                        batch.clear()
                with self.connect() as db:
                    db.executemany(f'INSERT INTO {staging}(id,name,group_name,url,tvg_id) VALUES (?,?,?,?,?)', batch)
                    db.execute(f'INSERT INTO {staging} SELECT * FROM channels WHERE manual=1')
                    db.execute(f'ALTER TABLE channels RENAME TO {previous}')
                    db.execute(f'ALTER TABLE {staging} RENAME TO channels')
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('source', source))
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('refreshed_at', str(time.time())))
                return count
            finally:
                with self.connect() as db:
                    db.execute(f'DROP TABLE IF EXISTS {staging}')
                    db.execute(f'DROP TABLE IF EXISTS {previous}')

    def import_url(self, source):
        with fetch_playlist(source, directory=os.path.dirname(self.path)) as text:
            return self.import_playlist(text, source)

    def import_file(self, stream, length):
        with tempfile.TemporaryFile(mode='w+b', dir=os.path.dirname(self.path)) as temporary:
            copy_playlist(stream, temporary, length)
            with io.TextIOWrapper(temporary, encoding='utf-8-sig', errors='replace') as text:
                return self.import_playlist(text)

    def refresh_source(self):
        source = self.setting('source')
        if not source:
            raise ValueError('Upload opnieuw een bestand of stel een M3U-adres in.')
        with fetch_playlist(source, directory=os.path.dirname(self.path)) as text:
            with self.import_lock:
                if self.setting('source') != source:
                    raise ValueError('Het M3U-adres is ondertussen gewijzigd. De nieuwe instelling blijft behouden.')
                return self.import_playlist(text, source)

    def catalog(self, search='', group='', offset=0, limit=100):
        where, args = ['1=1'], []
        if search:
            # Literal substring, including % and _, rather than wildcard input.
            where.append("(name LIKE ? ESCAPE '\\' OR group_name LIKE ? ESCAPE '\\')")
            term = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            args.extend([term, term])
        if group:
            where.append('group_name=?')
            args.append(group)
        clause = ' AND '.join(where)
        with self.connect() as db:
            count = db.execute('SELECT COUNT(*) FROM channels WHERE ' + clause, args).fetchone()[0]
            rows = db.execute('SELECT id,name,group_name,manual FROM channels WHERE ' + clause +
                              ' ORDER BY name COLLATE NOCASE,id LIMIT ? OFFSET ?', args + [limit, offset])
            return {'total': count, 'items': [dict(row) for row in rows]}

    def groups(self):
        with self.connect() as db:
            return [row[0] for row in db.execute('SELECT DISTINCT group_name FROM channels ORDER BY group_name')]

    def favorites(self, playback=False):
        with self.connect() as db:
            rows = db.execute('''SELECT f.id,f.label,f.position,c.name,c.group_name,c.url
                                 FROM favorites f LEFT JOIN channels c ON f.id=c.id ORDER BY f.position''')
            result = []
            for row in rows:
                item = dict(row)
                item['available'] = bool(item['url'])
                if not playback:
                    del item['url']
                result.append(item)
            return result

    def add_favorite(self, channel_id, label=''):
        with self.connect() as db:
            channel = db.execute('SELECT name FROM channels WHERE id=?', (channel_id,)).fetchone()
            if not channel:
                raise ValueError('Deze zender bestaat niet meer in de lijst.')
            position = db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM favorites').fetchone()[0]
            db.execute('INSERT OR IGNORE INTO favorites VALUES (?,?,?)',
                       (channel_id, self.label(label or channel[0]), position))

    @staticmethod
    def label(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 100:
            raise ValueError('Gebruik een zendernaam van 1 tot 100 tekens.')
        return value.strip()

    def rename(self, channel_id, label):
        with self.connect() as db:
            result = db.execute('UPDATE favorites SET label=? WHERE id=?', (self.label(label), channel_id))
            if not result.rowcount:
                raise ValueError('Favoriet niet gevonden.')

    def remove(self, channel_id):
        with self.connect() as db:
            db.execute('DELETE FROM favorites WHERE id=?', (channel_id,))
            db.execute('DELETE FROM channels WHERE id=? AND manual=1', (channel_id,))

    def reorder(self, ids):
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
            raise ValueError('Ongeldige volgorde.')
        with self.connect() as db:
            current = {row[0] for row in db.execute('SELECT id FROM favorites')}
            if len(ids) != len(set(ids)) or set(ids) != current:
                raise ValueError('De favorieten zijn gewijzigd. Ververs de pagina.')
            db.executemany('UPDATE favorites SET position=? WHERE id=?', enumerate(ids))

    def manual(self, label, url):
        label, url = self.label(label), stream_url(url)
        channel_id = 'manual-' + uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO channels VALUES (?,?,?,?,?,1)', (channel_id, label, 'Handmatig', url, ''))
            position = db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM favorites').fetchone()[0]
            db.execute('INSERT INTO favorites VALUES (?,?,?)', (channel_id, label, position))
        return channel_id
