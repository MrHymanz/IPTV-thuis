"""On-demand Xtream archive/EPG metadata. Credentials remain in server memory."""
import base64
import hashlib
import html
import json
import math
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_RESPONSE = 64 * 1024 * 1024


def text(value, limit=6000):
    if not isinstance(value, str):
        return ''
    try:
        value = base64.b64decode(value, validate=True).decode('utf-8')
    except (ValueError, UnicodeError):
        pass
    return ' '.join(html.unescape(re.sub(r'<[^>]*>', ' ', value)).split())[:limit]


def identity(name):
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    name = name.rsplit('|', 1)[-1]
    name = re.sub(r'\b(?:fhd|hd|uhd|4k|sd|hevc|h26[45]|raw|[256]0fps)\b', '', name)
    return re.sub(r'[^a-z0-9]', '', name)


def country(name):
    match = re.match(r'^\s*([A-Za-z]{2,3})\s*[|:]', name)
    return match.group(1).lower() if match else ''


def number(value):
    try:
        return int(value) if not isinstance(value, bool) else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def phase(item, now=None):
    now = time.time() if now is None else now
    return 'past' if item['end'] <= now else 'future' if item['start'] > now else 'current'


class Catchup:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS catchup_meta (signature TEXT NOT NULL,key TEXT NOT NULL,value TEXT NOT NULL,
                    PRIMARY KEY(signature,key));
                CREATE TABLE IF NOT EXISTS catchup_sources (signature TEXT NOT NULL,stream_id TEXT NOT NULL,
                    name TEXT NOT NULL,epg_id TEXT NOT NULL,days INTEGER NOT NULL,
                    PRIMARY KEY(signature,stream_id));
                CREATE TABLE IF NOT EXISTS catchup_programmes (signature TEXT NOT NULL,channel_id TEXT NOT NULL,
                    stream_id TEXT NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,title TEXT NOT NULL,
                    description TEXT NOT NULL,PRIMARY KEY(signature,channel_id,start));
            ''')

    def configuration(self):
        source = self.store.setting('source')
        try:
            parsed = urlsplit(source)
            query = parse_qs(parsed.query)
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                    not parsed.path.endswith(('/get.php', '/player_api.php')) or
                    not query.get('username') or not query.get('password')):
                return None
            return (parsed, {key: query[key][0] for key in ('username', 'password')},
                    hashlib.sha256(source.encode()).hexdigest())
        except ValueError:
            return None

    def request(self, configuration, action=None, **params):
        parsed, auth, _ = configuration
        query = dict(auth, **params)
        if action:
            query['action'] = action
        address = urlunsplit((parsed.scheme, parsed.netloc,
                              parsed.path.rsplit('/', 1)[0]+'/player_api.php', urlencode(query), ''))
        try:
            with urlopen(Request(address, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as response:
                data = response.read(MAX_RESPONSE+1)
            if len(data) > MAX_RESPONSE:
                raise ValueError()
            return json.loads(data)
        except Exception:
            raise ValueError('De provider kon niet worden bereikt. Probeer het later opnieuw.') from None

    def metadata(self, signature):
        with self.store.connect() as db:
            return dict(db.execute('SELECT key,value FROM catchup_meta WHERE signature=?', (signature,)))

    def inventory(self, configuration):
        signature = configuration[2]
        metadata = self.metadata(signature)
        if time.time()-float(metadata.get('inventory_at', '0')) < 6*3600:
            return
        info = self.request(configuration)
        streams = self.request(configuration, 'get_live_streams')
        if not isinstance(info, dict) or not isinstance(streams, list):
            raise ValueError('De provider stuurde geen bruikbare zenderlijst.')
        if info.get('user_info', {}).get('status', 'Active') != 'Active':
            raise ValueError('Het provideraccount is niet actief.')
        zone = info.get('server_info', {}).get('timezone', 'UTC')
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            zone = 'UTC'
        rows = []
        for stream in streams:
            stream_id = str(stream.get('stream_id', ''))
            if not stream_id.isdigit():
                continue
            days = min(31, max(0, number(stream.get('tv_archive_duration')))) if number(stream.get('tv_archive')) == 1 else 0
            rows.append((signature, stream_id, str(stream.get('name', ''))[:500],
                         str(stream.get('epg_channel_id') or '').strip()[:300], days))
        if not rows:
            raise ValueError('De provider stuurde een lege zenderlijst.')
        if self.configuration() != configuration:
            raise ValueError('De providerinstellingen zijn ondertussen gewijzigd.')
        with self.store.connect() as db:
            db.execute('DELETE FROM catchup_sources')
            db.execute('DELETE FROM catchup_meta WHERE signature<>?', (signature,))
            db.execute('DELETE FROM catchup_programmes WHERE signature<>?', (signature,))
            db.executemany('INSERT OR REPLACE INTO catchup_sources VALUES (?,?,?,?,?)', rows)
            db.executemany('INSERT OR REPLACE INTO catchup_meta VALUES (?,?,?)',
                           [(signature, 'inventory_at', str(time.time())), (signature, 'timezone', zone)])

    def resolve(self, channel, configuration):
        with self.store.connect() as db:
            candidates = [dict(row) for row in db.execute('SELECT * FROM catchup_sources WHERE signature=?', (configuration[2],))]
        by_id = {row['stream_id']: row for row in candidates}
        direct = []
        parsed = configuration[0]
        for source in channel.get('sources') or [channel]:
            address = urlsplit(source.get('url', '').partition('|')[0])
            if (address.scheme, address.netloc) != (parsed.scheme, parsed.netloc):
                continue
            identifier = address.path.rsplit('/', 1)[-1].split('.')[0]
            if identifier in by_id:
                direct.append(by_id[identifier])
        archived = [row for row in direct if row['days'] > 0]
        if archived:
            return archived[0]
        epg_id = channel.get('epg_id', '').casefold()
        matching = [row for row in candidates if epg_id and row['epg_id'].casefold() == epg_id and row['days'] > 0]
        if not matching:
            name = channel.get('name') or channel['label']
            region = country(name) or country(channel.get('group_name', ''))
            matching = [row for row in candidates if row['days'] > 0 and identity(row['name']) == identity(name)
                        and (not region or country(row['name']) == region)]
            # A bare label shared between countries is not a safe archive mapping.
            if len({country(row['name']) for row in matching}) > 1:
                matching = []
        if matching:
            return sorted(matching, key=lambda row: (-row['days'], 'FHD' not in row['name'], row['stream_id']))[0]
        return direct[0] if direct else None

    def local_guide(self, channel):
        with self.store.connect() as db:
            rows = db.execute('''SELECT start,end,title FROM epg_programmes WHERE epg_id=? AND end>? AND start<? ORDER BY start LIMIT 2000''',
                              (channel.get('epg_id', ''), int(time.time())-3*86400, int(time.time())+7*86400))
            return [dict(row, description='', archive=False) for row in rows]

    def cached(self, signature, channel_id, days=0):
        now = time.time()
        with self.store.connect() as db:
            rows = db.execute('''SELECT start,end,title,description,stream_id FROM catchup_programmes
                              WHERE signature=? AND channel_id=? AND end>? AND start<? ORDER BY start''',
                              (signature, channel_id, int(now)-max(days, 1)*86400, int(now)+7*86400))
            return [dict(row, archive_days=days, archive=bool(days and row['start'] >= now-days*86400 and row['end'] <= now)) for row in rows]

    def guide(self, channel_id):
        channel = next((item for item in self.store.favorites(playback=True) if item['id'] == channel_id), None)
        if not channel:
            raise ValueError('Kies een zender uit de favorieten.')
        configuration = self.configuration()
        result = {'programmes': self.local_guide(channel), 'notice': '', 'days': 0}
        if not configuration:
            return result
        with self.lock:
            try:
                self.inventory(configuration)
                source = self.resolve(channel, configuration)
                if not source:
                    result['notice'] = 'Geen passende terugkijkbron gevonden; de gewone gids blijft beschikbaar.'
                    return result
                signature = configuration[2]
                result['days'] = source['days']
                metadata = self.metadata(signature)
                if time.time()-float(metadata.get('epg:'+channel_id, '0')) >= 15*60:
                    response = self.request(configuration, 'get_simple_data_table', stream_id=source['stream_id'])
                    entries = response.get('epg_listings') if isinstance(response, dict) else None
                    if not isinstance(entries, list):
                        raise ValueError('De provider stuurde geen bruikbare programmagids.')
                    now = time.time()
                    rows = {}
                    for item in entries[:20000]:
                        start, end = number(item.get('start_timestamp')), number(item.get('stop_timestamp'))
                        title = text(item.get('title'), 500)
                        if title and now-max(source['days'], 1)*86400 < end and start < now+7*86400 and start < end:
                            rows[start] = (signature, channel_id, source['stream_id'], start, end, title, text(item.get('description')))
                    if not rows:
                        raise ValueError('De provider heeft momenteel geen programma’s voor deze zender.')
                    if self.configuration() != configuration:
                        raise ValueError('De providerinstellingen zijn ondertussen gewijzigd.')
                    with self.store.connect() as db:
                        db.execute('DELETE FROM catchup_programmes WHERE channel_id=?', (channel_id,))
                        db.executemany('INSERT INTO catchup_programmes VALUES (?,?,?,?,?,?,?)', rows.values())
                        db.execute('INSERT OR REPLACE INTO catchup_meta VALUES (?,?,?)', (signature, 'epg:'+channel_id, str(now)))
                result['programmes'] = self.cached(signature, channel_id, source['days']) or result['programmes']
            except ValueError as error:
                result['notice'] = str(error)
                if self.configuration() == configuration:
                    source = self.resolve(channel, configuration)
                    result['days'] = source['days'] if source else 0
                    cached = self.cached(configuration[2], channel_id, result['days'])
                    if cached:
                        result['programmes'] = cached
        return result

    def recording_programme(self, channel_id, start):
        configuration = self.configuration()
        if not configuration:
            return None
        with self.store.connect() as db:
            row = db.execute('''SELECT start,end,title FROM catchup_programmes
                WHERE signature=? AND channel_id=? AND start=? AND end>?''',
                             (configuration[2], channel_id, start, int(time.time()))).fetchone()
            return dict(row) if row else None

    def playback_url(self, channel_id, start):
        configuration = self.configuration()
        if not configuration:
            raise ValueError('Deze provider ondersteunt geen terugkijken via deze app.')
        with self.store.connect() as db:
            if not db.execute('SELECT 1 FROM favorites WHERE id=?', (channel_id,)).fetchone():
                raise ValueError('Deze zender staat niet meer in je favorieten.')
            row = db.execute('''SELECT p.*,s.days FROM catchup_programmes p JOIN catchup_sources s
                ON s.signature=p.signature AND s.stream_id=p.stream_id
                WHERE p.signature=? AND p.channel_id=? AND p.start=?''', (configuration[2], channel_id, start)).fetchone()
        if not row or row['days'] <= 0 or row['end'] > time.time() or row['start'] < time.time()-row['days']*86400:
            raise ValueError('Deze uitzending is nog niet beschikbaar of valt buiten het terugkijkarchief.')
        metadata = self.metadata(configuration[2])
        try:
            zone = ZoneInfo(metadata.get('timezone', 'UTC'))
        except ZoneInfoNotFoundError:
            zone = timezone.utc
        stamp = datetime.fromtimestamp(row['start'], zone).strftime('%Y-%m-%d:%H-%M')
        parsed, auth, _ = configuration
        path = parsed.path.rsplit('/', 1)[0]+'/timeshift/'+quote(auth['username'], safe='')+'/'+quote(auth['password'], safe='')
        path += '/'+str(math.ceil((row['end']-row['start'])/60))+'/'+stamp+'/'+row['stream_id']+'.ts'
        return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))
