"""Bounded XMLTV import for favorites; provider credentials stay on the server."""
import gzip
import hashlib
import io
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

MAX_EPG = 512 * 1024 * 1024
REFRESH_SECONDS = 4 * 60 * 60


def epg_url(source):
    parsed = urlsplit(source)
    query = parse_qs(parsed.query)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            not parsed.path.endswith('/get.php') or
            not all(query.get(key) for key in ('username', 'password'))):
        return ''
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rsplit('/', 1)[0] + '/xmltv.php',
                      urlencode({key: query[key][0] for key in ('username', 'password')}), ''))


def xmltv_time(value):
    match = re.fullmatch(r'(\d{12}|\d{14})(?:\s+([+-]\d{4}|Z|UTC|GMT))?', value.strip())
    if not match:
        raise ValueError('Ongeldige EPG-tijd.')
    digits, offset = match.groups()
    digits = digits.ljust(14, '0')
    offset = '+0000' if offset in (None, 'Z', 'UTC', 'GMT') else offset
    return int(datetime.strptime(digits + ' ' + offset, '%Y%m%d%H%M%S %z').timestamp())


class LimitedReader:
    def __init__(self, stream, limit=MAX_EPG):
        self.stream, self.limit, self.total = stream, limit, 0

    def read(self, size=-1):
        data = self.stream.read(min(size if size >= 0 else 65536, self.limit-self.total+1))
        self.total += len(data)
        if self.total > self.limit:
            raise ValueError('De EPG is groter dan 512 MB.')
        return data


def parse_xmltv(stream, channel_ids, now=None):
    now = time.time() if now is None else now
    channel_ids = set(channel_ids)
    context = ET.iterparse(LimitedReader(stream), events=('start', 'end'))
    try:
        _, root = next(context)
    except StopIteration:
        raise ValueError('Lege EPG.') from None
    if root.tag != 'tv':
        raise ValueError('Geen XMLTV-programmagids.')
    programmes = {}
    for event, element in context:
        if event != 'end' or element.tag not in ('channel', 'programme'):
            continue
        if element.tag == 'programme' and element.get('channel', '').strip() in channel_ids:
            channel_id = element.get('channel').strip()
            try:
                start = xmltv_time(element.get('start', ''))
                end = xmltv_time(element.get('stop')) if element.get('stop') else None
            except (ValueError, OverflowError):
                root.clear()
                continue
            titles = element.findall('title')
            title = next((e for e in titles if e.get('lang', '').lower() in ('nl', 'nl_nl')), titles[0] if titles else None)
            title = ' '.join(''.join(title.itertext()).split())[:500] if title is not None else ''
            if (title and start <= now + 7*86400 and (end or start+86400) > now-86400 and
                    (end is None or end > start)):
                programmes[(channel_id, start)] = (channel_id, start, end, title)
                if len(programmes) > 100000:
                    raise ValueError('Te veel EPG-programma’s voor de favorieten.')
        root.clear()
    rows = sorted(programmes.values(), key=lambda row: (row[0], row[1]))
    result = []
    for index, (channel_id, start, end, title) in enumerate(rows):
        # XMLTV stop is optional. Infer it only from the next programme of this channel.
        if end is None and index+1 < len(rows) and rows[index+1][0] == channel_id:
            end = rows[index+1][1]
        if end is not None and end > start:
            result.append((channel_id, start, end, title))
    return result


class EPG:
    def __init__(self, store):
        self.store, self.lock = store, threading.Lock()

    def configuration(self):
        with self.store.connect() as db:
            ids = {row[0].strip() for row in db.execute('''SELECT DISTINCT c.tvg_id FROM favorites f
                          JOIN channels c ON f.id=c.id WHERE trim(c.tvg_id)<>'' ''')}
        source = self.store.setting('source')
        signature = hashlib.sha256(json.dumps([source, sorted(ids)]).encode()).hexdigest()
        return epg_url(source), ids, signature

    def refresh(self, stream=None, now=None):
        if not self.lock.acquire(blocking=False):
            raise ValueError('De EPG wordt al vernieuwd.')
        try:
            url, ids, signature = self.configuration()
            if not ids:
                return 0
            if stream is not None:
                rows = parse_xmltv(stream, ids, now)
            else:
                if not url:
                    raise ValueError('Deze playlist heeft geen automatische Xtream-EPG.')
                with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0 (IPTV-thuis)',
                                                   'Accept-Encoding': 'identity'}), timeout=90) as response:
                    if urlsplit(response.geturl()).scheme not in ('http', 'https'):
                        raise ValueError('Ongeldige EPG-omleiding.')
                    with io.BufferedReader(response) as buffered:
                        reader = gzip.GzipFile(fileobj=buffered) if buffered.peek(2)[:2] == b'\x1f\x8b' else buffered
                        rows = parse_xmltv(reader, ids, now)
            if not rows:
                raise ValueError('Geen passende programmagegevens ontvangen.')
            if signature != self.configuration()[2]:
                raise ValueError('De favorieten of aanbieder zijn ondertussen gewijzigd.')
            with self.store.connect() as db:
                db.execute('DELETE FROM epg_programmes')
                db.executemany('INSERT INTO epg_programmes VALUES (?,?,?,?)', rows)
                db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',
                               [('epg_refreshed_at', str(time.time())), ('epg_signature', signature)])
            return len(rows)
        except Exception as error:
            if isinstance(error, ValueError):
                raise
            raise ValueError('De EPG kon niet worden opgehaald; bestaande gegevens blijven bewaard.') from None
        finally:
            self.lock.release()

    def run(self, stop):
        while not stop.is_set():
            try:
                url, ids, signature = self.configuration()
                refreshed = float(self.store.setting('epg_refreshed_at', '0'))
                if url and ids and (time.time()-refreshed >= REFRESH_SECONDS or
                                   signature != self.store.setting('epg_signature')):
                    self.refresh()
            except Exception:
                print('EPG vernieuwen mislukt; bestaande programmagegevens blijven bewaard.', flush=True)
                if stop.wait(600):
                    return
            if stop.wait(60):
                return
