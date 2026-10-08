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


def logo_url(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ('http', 'https') and parsed.hostname and len(value) <= 4096:
            return value
    except ValueError:
        pass
    return ''


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
            yield (channel_id, name, group, url, attributes.get('tvg-id', ''), logo_url(attributes.get('tvg-logo', '')))
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
                CREATE TABLE IF NOT EXISTS favorite_epg (id TEXT PRIMARY KEY, epg_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS favorite_source (id TEXT PRIMARY KEY, source_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS favorite_backups (
                    favorite_id TEXT NOT NULL, channel_id TEXT NOT NULL,
                    PRIMARY KEY(favorite_id,channel_id)
                );
                CREATE TABLE IF NOT EXISTS epg_programmes (
                    epg_id TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL, title TEXT NOT NULL,
                    PRIMARY KEY(epg_id,start)
                );
            ''')
            if 'logo' not in {row[1] for row in db.execute('PRAGMA table_info(channels)')}:
                db.execute("ALTER TABLE channels ADD COLUMN logo TEXT NOT NULL DEFAULT ''")
            indexed = db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND tbl_name='channels' AND sql LIKE '%trim(tvg_id)%'").fetchone()
            if not indexed:
                db.execute('CREATE INDEX IF NOT EXISTS channels_epg_lookup ON channels(trim(tvg_id))')
            self._merge_favorites(db)
        os.chmod(self.path, 0o600)

    @staticmethod
    def _merge_favorites(db):
        db.execute("""INSERT OR IGNORE INTO favorite_epg SELECT f.id,trim(c.tvg_id)
                      FROM favorites f JOIN channels c ON c.id=f.id
                      WHERE trim(c.tvg_id)<>'' AND c.manual=0""")
        seen = {}
        rows = db.execute('''SELECT f.id,e.epg_id FROM favorites f LEFT JOIN favorite_epg e ON e.id=f.id
                             ORDER BY f.position,f.rowid''').fetchall()
        for row in rows:
            identity = row['epg_id']
            if identity and identity in seen:
                canonical = seen[identity]
                db.execute('INSERT OR IGNORE INTO favorite_backups VALUES (?,?)', (canonical,row['id']))
                db.execute('INSERT OR IGNORE INTO favorite_backups SELECT ?,channel_id FROM favorite_backups WHERE favorite_id=?',
                           (canonical,row['id']))
                db.execute('DELETE FROM favorites WHERE id=?', (row['id'],))
                db.execute('DELETE FROM favorite_epg WHERE id=?', (row['id'],))
                db.execute('DELETE FROM favorite_backups WHERE favorite_id=?', (row['id'],))
            elif identity:
                seen[identity] = row['id']

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

    def cleanup_imports(self):
        """Recover staging tables after a killed process, before importing starts."""
        with self.import_lock, self.connect() as db:
            names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                     if re.fullmatch(r'channels_(?:import|previous)_[0-9a-f]{32}', row[0])]
            db.execute('PRAGMA synchronous=NORMAL')
            db.execute('PRAGMA secure_delete=OFF')
            db.execute('PRAGMA cache_size=-32768')
            for name in names:
                db.execute(f'DROP TABLE "{name}"')
                db.commit()
            return len(names)

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
                        id TEXT NOT NULL, name TEXT NOT NULL, group_name TEXT NOT NULL,
                        url TEXT NOT NULL, tvg_id TEXT NOT NULL, manual INTEGER NOT NULL DEFAULT 0,
                        logo TEXT NOT NULL DEFAULT '')''')
                # Keep one writer open so closing each batch does not checkpoint
                # the WAL repeatedly. NORMAL applies only to this rebuildable import.
                with self.connect() as db:
                    db.execute('PRAGMA synchronous=NORMAL')
                    db.execute('PRAGMA cache_size=-32768')
                    db.execute('PRAGMA wal_autocheckpoint=4096')
                    batch = []
                    for row in iter_m3u(text):
                        batch.append(row)
                        count += 1
                        if len(batch) == 1000:
                            db.executemany(f'INSERT INTO {staging}(id,name,group_name,url,tvg_id,logo) VALUES (?,?,?,?,?,?)', batch)
                            db.commit()
                            batch.clear()
                    db.executemany(f'INSERT INTO {staging}(id,name,group_name,url,tvg_id,logo) VALUES (?,?,?,?,?,?)', batch)
                    # Build the ID index once after loading instead of updating
                    # random index pages for every row on a mechanical disk.
                    db.execute(f'CREATE UNIQUE INDEX {staging}_id ON {staging}(id)')
                    db.execute(f'CREATE INDEX {staging}_epg ON {staging}(trim(tvg_id))')
                    db.execute(f'INSERT INTO {staging} SELECT * FROM channels WHERE manual=1')
                    db.execute(f'ALTER TABLE channels RENAME TO {previous}')
                    db.execute(f'ALTER TABLE {staging} RENAME TO channels')
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('source', source))
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('refreshed_at', str(time.time())))
                    self._merge_favorites(db)
                return count
            finally:
                with self.connect() as db:
                    db.execute('PRAGMA synchronous=NORMAL')
                    db.execute('PRAGMA secure_delete=OFF')
                    db.execute('PRAGMA cache_size=-32768')
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

    def channel_logo(self, channel_id):
        with self.connect() as db:
            row = db.execute('SELECT logo FROM channels WHERE id=?', (channel_id,)).fetchone()
            return row[0] if row else ''

    def backfill_favorite_logos(self):
        """Enrich existing favorites without rebuilding the large channel catalog."""
        source = self.setting('source')
        if not source:
            return 0
        remaining = {item['id'] for item in self.favorites()}
        found = []
        with fetch_playlist(source, directory=os.path.dirname(self.path)) as text:
            for channel_id, name, group, url, tvg_id, logo in iter_m3u(text):
                if channel_id in remaining:
                    if logo:
                        found.append((logo, channel_id))
                    remaining.remove(channel_id)
                    if not remaining:
                        break
        with self.import_lock, self.connect() as db:
            if self.setting('source') != source:
                raise ValueError('Het playlistadres is ondertussen gewijzigd.')
            db.executemany('UPDATE channels SET logo=? WHERE id=?', found)
        return len(found)

    def remember_source(self, favorite_id, source_id):
        favorite = next((f for f in self.favorites(playback=True) if f['id'] == favorite_id), None)
        if not favorite or source_id not in {source['id'] for source in favorite['sources']}:
            return
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO favorite_source VALUES (?,?)', (favorite_id, source_id))

    def favorites(self, playback=False):
        with self.connect() as db:
            rows = db.execute('''SELECT f.id,f.label,f.position,c.name,c.group_name,c.url,c.logo,
                                 COALESCE(e.epg_id,'') AS epg_id
                                 FROM favorites f LEFT JOIN channels c ON f.id=c.id
                                 LEFT JOIN favorite_epg e ON e.id=f.id ORDER BY f.position''').fetchall()
            keys = sorted({row['epg_id'] for row in rows if row['epg_id']})
            candidates = {}
            if keys:
                for row in db.execute('SELECT id,group_name,url,trim(tvg_id) AS epg_id FROM channels WHERE trim(tvg_id) IN ('+
                                      ','.join('?' for _ in keys)+') ORDER BY group_name,id', keys):
                    candidates.setdefault(row['epg_id'], []).append(dict(row))
            pins = {}
            for row in db.execute('SELECT favorite_id,channel_id FROM favorite_backups ORDER BY rowid'):
                pins.setdefault(row['favorite_id'], []).append(row['channel_id'])
            remembered = dict(db.execute('SELECT id,source_id FROM favorite_source').fetchall())
            result = []
            for row in rows:
                item = dict(row)
                preferred = pins.get(item['id'], [])
                alternatives = [c for c in candidates.get(item['epg_id'], [])
                                if c['id'] != item['id'] and (c['group_name'] != item['group_name']
                                                             or c['id'] in preferred)]
                alternatives.sort(key=lambda c: (preferred.index(c['id']) if c['id'] in preferred else len(preferred),
                                                  c['group_name'],c['id']))
                sources, used = [], set()
                primary = {'id':item['id'], 'group_name':item['group_name'], 'url':item['url']}
                for source in [primary]+alternatives:
                    if source['url'] and source['url'] not in used:
                        used.add(source['url'])
                        sources.append(source)
                saved = remembered.get(item['id'])
                sources.sort(key=lambda source: source['id'] != saved)
                item['available'] = bool(sources)
                item['source_count'] = len(sources)
                item['source_groups'] = list(dict.fromkeys(source['group_name'] for source in sources))
                if playback:
                    item['sources'] = sources
                    item['url'] = sources[0]['url'] if sources else ''
                else:
                    del item['url']
                    if item['logo']:
                        version = hashlib.sha256(item['logo'].encode()).hexdigest()[:12]
                        item['logo'] = '/api/logos/' + item['id'] + '?v=' + version
                result.append(item)
        guide = self.programmes()
        for item in result:
            item['epg'] = guide.get(item['id'], {'now': None, 'next': None})
        return result

    def programmes(self, now=None):
        now = time.time() if now is None else now
        result = {}
        with self.connect() as db:
            for kind, condition, direction in [('now', 'start<=? AND end>?', 'DESC'),
                                                ('next', 'start>?', 'ASC')]:
                arguments = (now, now) if kind == 'now' else (now,)
                rows = db.execute(f'''SELECT f.id,p.start,p.end,p.title FROM favorites f
                    LEFT JOIN channels c ON c.id=f.id
                    LEFT JOIN epg_programmes p ON p.rowid=(SELECT rowid FROM epg_programmes
                       WHERE epg_id=COALESCE((SELECT epg_id FROM favorite_epg WHERE id=f.id),trim(c.tvg_id)) AND {condition} ORDER BY start {direction} LIMIT 1)''', arguments)
                for row in rows:
                    item = result.setdefault(row['id'], {'now': None, 'next': None})
                    if row['title'] is not None:
                        item[kind] = {'start': row['start'], 'end': row['end'], 'title': row['title']}
        return result

    def add_favorite(self, channel_id, label=''):
        with self.connect() as db:
            channel = db.execute('SELECT name,trim(tvg_id) AS epg_id,manual FROM channels WHERE id=?', (channel_id,)).fetchone()
            if not channel:
                raise ValueError('Deze zender bestaat niet meer in de lijst.')
            self.label(label or channel['name'])
            if channel['epg_id'] and not channel['manual']:
                existing = db.execute('SELECT f.id FROM favorites f JOIN favorite_epg e ON e.id=f.id WHERE e.epg_id=? ORDER BY f.position LIMIT 1', (channel['epg_id'],)).fetchone()
                if existing:
                    if existing['id'] != channel_id:
                        db.execute('INSERT OR IGNORE INTO favorite_backups VALUES (?,?)', (existing['id'],channel_id))
                    return
            position = db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM favorites').fetchone()[0]
            db.execute('INSERT OR IGNORE INTO favorites VALUES (?,?,?)',
                       (channel_id, self.label(label or channel[0]), position))
            self._merge_favorites(db)

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
            db.execute('DELETE FROM favorite_epg WHERE id=?', (channel_id,))
            db.execute('DELETE FROM favorite_backups WHERE favorite_id=?', (channel_id,))
            db.execute('DELETE FROM favorite_source WHERE id=?', (channel_id,))
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
            db.execute('INSERT INTO channels(id,name,group_name,url,tvg_id,manual) VALUES (?,?,?,?,?,1)', (channel_id, label, 'Handmatig', url, ''))
            position = db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM favorites').fetchone()[0]
            db.execute('INSERT INTO favorites VALUES (?,?,?)', (channel_id, label, position))
        return channel_id
