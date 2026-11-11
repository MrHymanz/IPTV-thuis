"""Cache the actual IMDb Top 250; never substitute provider ratings for rank."""
import html
import json
import re
import time
import unicodedata
from urllib.request import Request, urlopen

URL = 'https://www.imdb.com/chart/top/'


def title_key(value):
    value = re.sub(r'^\s*[A-Z]{2,3}\s*[|:]\s*', '', value)
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
        key = str(entry.get('id', ''))
        try:
            rank = int(entry.get('rank', index))
        except (ValueError, TypeError):
            raise ValueError('Ongeldige IMDb-rang.') from None
        if not re.fullmatch(r'tt\d{5,12}', key) or not 1 <= rank <= 250 or key in used_ids or rank in used_ranks:
            raise ValueError('IMDb-IDs en rangen moeten geldig en uniek zijn.')
        used_ids.add(key); used_ranks.add(rank)
        result.append({'id': key, 'rank': rank, 'title': str(entry.get('title', ''))[:500], 'year': str(entry.get('year', ''))[:4]})
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

    def save(self, entries):
        entries = validate(entries)
        with self.store.connect() as db:
            db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',
                           [('imdb_top250', json.dumps(entries)), ('imdb_top250_at', str(time.time()))])
        self.notice = ''
        return len(entries)

    def refresh(self):
        if self.retry_at > time.monotonic() or time.time()-float(self.store.setting('imdb_top250_at', '0')) < 86400:
            return
        self.retry_at = time.monotonic()+3600
        try:
            with urlopen(Request(URL, headers={'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.8'}), timeout=8) as response:
                raw = response.read(4*1024*1024+1)
            if len(raw) > 4*1024*1024:
                raise ValueError()
            entries = parse_chart(raw.decode('utf-8'))
            if len(entries) != 250:
                raise ValueError()
            self.save(entries)
        except Exception:
            self.notice = 'De officiële IMDb Top 250 kon niet worden vernieuwd. Importeer de ranglijst in het beheer.'

    def ranker(self):
        by_id, by_name = {}, {}
        for entry in self.entries():
            by_id[entry['id']] = entry['rank']
            if entry['title']:
                by_name.setdefault(title_key(entry['title']), []).append(entry)
        def rank(raw):
            item = json.loads(raw)
            if item.get('imdb_id') in by_id:
                return by_id[item['imdb_id']]
            matches = by_name.get(title_key(item['title']), [])
            year = item.get('year')
            matches = [m for m in matches if not year or not m['year'] or year == m['year']]
            return matches[0]['rank'] if len(matches) == 1 else None
        return rank
