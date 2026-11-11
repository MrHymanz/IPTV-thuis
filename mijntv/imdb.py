"""Cache the actual IMDb Top 250; never substitute provider ratings for rank."""
import html
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from urllib.request import Request, urlopen

URL = 'https://www.imdb.com/chart/top/'
API_URL = 'https://imdb-top250.mmdju.workers.dev/top250'


def title_key(value):
    value = re.sub(r'^\s*[A-Z]{2,3}\s*[|:\-]\s*', '', value)
    value = re.sub(r'\([^)]*\d{4}[^)]*\)|\b(?:FHD|UHD|HD|4K|1080p|720p)\b', '', value, flags=re.I)
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().lower())


def parse_chart(document):
    entries = []
    for raw in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', document, re.S | re.I):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for block in data if isinstance(data, list) else [data]:
            if not isinstance(block, dict):
                continue
            for index, entry in enumerate(block.get('itemListElement', []), 1):
                item = entry.get('item', {})
                match = re.search(r'tt\d{5,12}', str(item.get('url', '')))
                if match:
                    entries.append({'id': match[0], 'rank': int(entry.get('position', index)),
                                    'title': html.unescape(str(item.get('name', ''))),
                                    'year': str(item.get('datePublished', ''))[:4]})
    return validate(entries)


def validate(entries):
    if not isinstance(entries, list) or not 1 <= len(entries) <= 250:
        raise ValueError('Gebruik een lijst met maximaal 250 IMDb-titels.')
    result, used_ids, used_ranks = [], set(), set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ValueError('Elke IMDb-titel moet een id en rang hebben.')
        key = str(entry.get('id') or '')
        title = str(entry.get('title') or '')[:500]
        year = str(entry.get('year') or '')[:4]
        try:
            rank = int(entry.get('rank', index))
        except (ValueError, TypeError):
            raise ValueError('Ongeldige IMDb-rang.') from None
        valid_identity = bool(re.fullmatch(r'tt\d{5,12}', key)) if key else bool(title.strip() and re.fullmatch(r'\d{4}', year))
        if not valid_identity or not 1 <= rank <= 250 or (key and key in used_ids) or rank in used_ranks:
            raise ValueError('IMDb-IDs en rangen moeten geldig en uniek zijn.')
        if key:
            used_ids.add(key)
        used_ranks.add(rank)
        result.append({'id': key, 'rank': rank, 'title': title, 'year': year})
    return sorted(result, key=lambda e: e['rank'])


class IMDbChart:
    def __init__(self, store):
        self.store = store
        self.retry_at = 0
        self.notice = ''

    def entries(self):
        try:
            return validate(json.loads(self.store.setting('imdb_top250', '[]')))
        except (ValueError, TypeError):
            return []

    def metadata(self):
        try:
            value = json.loads(self.store.setting('imdb_top250_meta', '{}'))
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    def status_notice(self):
        meta = self.metadata()
        if meta.get('stale'):
            date = str(meta.get('updated_at') or '')[:10]
            return 'IMDb Top 250: oudere momentopname'+(' van '+date if date else '')+' via mmdju API; dit is geen actuele ranglijst.'
        return ''

    def save(self, entries, metadata=None):
        entries = validate(entries)
        with self.store.connect() as db:
            db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',
                           [('imdb_top250', json.dumps(entries)), ('imdb_top250_at', str(time.time())),
                            ('imdb_top250_meta', json.dumps(metadata or {'source': 'manual', 'updated_at': '', 'stale': False}))])
        self.notice = ''
        return len(entries)

    @staticmethod
    def read(url, limit):
        with urlopen(Request(url, headers={'User-Agent': 'IPTV-thuis/0.1', 'Accept': 'application/json' if url == API_URL else 'text/html',
                                          'Accept-Language': 'en-US,en;q=0.8'}), timeout=8) as response:
            raw = response.read(limit+1)
        if len(raw) > limit:
            raise ValueError('IMDb-antwoord te groot.')
        return raw

    def refresh(self):
        if self.retry_at > time.monotonic() or time.time()-float(self.store.setting('imdb_top250_at', '0')) < 86400:
            self.notice = self.status_notice() or self.notice
            return
        self.retry_at = time.monotonic()+3600
        candidate = None
        try:
            payload = json.loads(self.read(API_URL, 1024*1024))
            entries = validate(payload.get('data'))
            if len(entries) != 250 or {e['rank'] for e in entries} != set(range(1, 251)):
                raise ValueError('Onvolledige API-ranglijst.')
            updated = str(payload.get('updatedAt') or '')
            try:
                age = time.time()-datetime.fromisoformat(updated.replace('Z', '+00:00')).timestamp()
            except (ValueError, OverflowError):
                age = float('inf')
            metadata = {'source': 'mmdju API', 'updated_at': updated, 'stale': payload.get('stale') is not False or age > 2*86400 or age < -86400}
            candidate = (entries, metadata)
            if not metadata['stale']:
                self.save(*candidate)
                return
        except Exception:
            pass
        try:
            entries = parse_chart(self.read(URL, 4*1024*1024).decode('utf-8'))
            if len(entries) != 250:
                raise ValueError('Onvolledige IMDb-ranglijst.')
            self.save(entries, {'source': 'IMDb', 'updated_at': datetime.now(timezone.utc).isoformat(), 'stale': False})
            return
        except Exception:
            pass
        if candidate:
            current = self.metadata()
            # Do not replace a manually curated or newer list with old seed data.
            if self.entries() and (not current.get('updated_at') or current['updated_at'] > candidate[1]['updated_at']):
                self.notice = 'De API levert een oudere IMDb-momentopname. De bestaande ranglijst blijft behouden.'
                return
            self.save(*candidate)
            self.notice = self.status_notice()
        else:
            self.notice = 'De officiële IMDb Top 250 kon niet worden vernieuwd. Importeer de ranglijst in het beheer.'

    def ranker(self):
        by_id, by_name = {}, {}
        for entry in self.entries():
            if entry['id']:
                by_id[entry['id']] = entry['rank']
            if entry['title']:
                by_name.setdefault(title_key(entry['title']), []).append(entry)
        def rank(raw):
            item = json.loads(raw)
            if item.get('imdb_id') and item['imdb_id'] in by_id:
                return by_id[item['imdb_id']]
            matches = by_name.get(title_key(item['title']), [])
            year = item.get('year')
            if not year:
                match = re.search(r'\((\d{4})\)', item['title'])
                year = match[1] if match else ''
            matches = [m for m in matches if (m['id'] or year) and (not year or not m['year'] or year == m['year'])]
            return matches[0]['rank'] if len(matches) == 1 else None
        return rank
