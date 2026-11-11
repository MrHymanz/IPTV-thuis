"""Optional TMDB artwork fallback. Credentials and provider URLs stay server-side."""
import hashlib
import json
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .imdb import title_key

API = 'https://api.themoviedb.org/3/'
IMAGE = 'https://image.tmdb.org/t/p/'


def image_url(path, asset):
    if not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)', path, re.I):
        return ''
    return IMAGE+('w500' if asset == 'poster' else 'w780')+path


def search_title(title):
    title = re.sub(r'^\s*[A-Z]{2,3}\s*[|:]\s*', '', title)
    return re.sub(r'\(\d{4}\)|\b(?:FHD|UHD|HD|4K|1080p|720p)\b', '', title, flags=re.I).strip()


class Artwork:
    def __init__(self, store, vod, logos):
        self.store, self.vod, self.logos = store, vod, logos
        self.lock = threading.Lock()
        self.locks = {}
        self.error = ''
        self.retry_at = 0
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS tmdb_artwork (
                key TEXT PRIMARY KEY, data TEXT NOT NULL, expires REAL NOT NULL)''')

    def status(self):
        return {'configured': bool(self.store.setting('tmdb_key')), 'error': self.error}

    def configure(self, value):
        if not isinstance(value, str):
            raise ValueError('Ongeldige TMDB-sleutel.')
        value = value.strip()
        if value.startswith('Bearer '):
            value = value[7:].strip()
        if value and not re.fullmatch(r'[A-Za-z0-9._-]{20,4096}', value):
            raise ValueError('Gebruik een TMDB API-key of API Read Access Token.')
        self.store.set_setting('tmdb_key', value)
        self.error = ''
        self.retry_at = 0
        with self.store.connect() as db:
            db.execute('DELETE FROM tmdb_artwork')
        return self.status()

    def request(self, path, key, **params):
        if self.retry_at > time.monotonic():
            raise ValueError(self.error or 'TMDB is tijdelijk niet beschikbaar.')
        headers = {'Accept': 'application/json', 'User-Agent': 'IPTV-thuis/0.1'}
        if re.fullmatch(r'[A-Fa-f0-9]{32}', key):
            params['api_key'] = key
        else:
            headers['Authorization'] = 'Bearer '+key
        address = API+path+'?'+urlencode(params)
        try:
            with urlopen(Request(address, headers=headers), timeout=8) as response:
                raw = response.read(2*1024*1024+1)
            if len(raw) > 2*1024*1024:
                raise ValueError()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            if self.store.setting('tmdb_key') == key:
                self.error = ''
            return result
        except HTTPError as error:
            message = 'TMDB weigert de sleutel. Controleer de API-instelling.' if error.code in (401, 403) else 'TMDB is tijdelijk niet beschikbaar.'
        except Exception:
            message = 'TMDB kon niet worden bereikt. Bestaande covers blijven beschikbaar.'
        if self.store.setting('tmdb_key') == key:
            self.error = message
            self.retry_at = time.monotonic()+300
        raise ValueError(message) from None

    def test(self):
        key = self.store.setting('tmdb_key')
        if not key:
            raise ValueError('Stel eerst een TMDB-sleutel in.')
        self.retry_at = 0
        self.request('configuration', key)
        return {'ok': True}

    def lookup(self, item, key):
        kind = 'movie' if item['kind'] == 'movie' else 'tv'
        tmdb = str(item.get('tmdb_id') or '')
        imdb = str(item.get('imdb_id') or '')
        if re.fullmatch(r'[0-9]{1,12}', tmdb):
            return self.request(kind+'/'+tmdb, key, language='nl-NL')
        if re.fullmatch(r'tt[0-9]{5,12}', imdb):
            result = self.request('find/'+imdb, key, external_source='imdb_id', language='nl-NL')
            matches = result.get('movie_results' if kind == 'movie' else 'tv_results', [])
        else:
            year = str(item.get('year') or '')
            if not re.fullmatch(r'\d{4}', year):
                found = re.search(r'\((\d{4})\)', item['title'])
                year = found[1] if found else ''
            params = {'query': search_title(item['title']), 'language': 'nl-NL', 'include_adult': 'false'}
            if year:
                params['primary_release_year' if kind == 'movie' else 'first_air_date_year'] = year
            result = self.request('search/'+kind, key, **params)
            matches = []
            results = result.get('results', [])
            if not isinstance(results, list):
                return {}
            for candidate in results:
                if not isinstance(candidate, dict):
                    continue
                names = [candidate.get(k, '') for k in ('title', 'original_title', 'name', 'original_name')]
                date = str(candidate.get('release_date' if kind == 'movie' else 'first_air_date') or '')[:4]
                if any(title_key(n) == title_key(item['title']) for n in names if isinstance(n, str)) and (not year or year == date):
                    matches.append(candidate)
        # Never take the first search result when remakes or names are ambiguous.
        if not isinstance(matches, list) or len(matches) != 1 or not isinstance(matches[0], dict):
            return {}
        return matches[0]

    def fallback(self, kind, identifier, asset='poster'):
        if kind not in ('movie', 'series') or asset not in ('poster', 'backdrop'):
            raise ValueError('Geen aanvullende afbeelding.')
        configuration, item = self.vod.artwork_metadata(kind, identifier)
        key = self.store.setting('tmdb_key')
        if not key:
            raise ValueError('TMDB is niet ingesteld.')
        identity = [configuration[2], kind, str(identifier), item.get('title'), item.get('year'), item.get('tmdb_id'), item.get('imdb_id'), key]
        cache_key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        with self.lock:
            lock = self.locks.setdefault(cache_key, threading.Lock())
        with lock:
            with self.store.connect() as db:
                row = db.execute('SELECT data,expires FROM tmdb_artwork WHERE key=?', (cache_key,)).fetchone()
            if row and row['expires'] > time.time():
                data = json.loads(row['data'])
            else:
                try:
                    result = self.lookup(item, key)
                    data = {'poster': image_url(result.get('poster_path'), 'poster'),
                            'backdrop': image_url(result.get('backdrop_path'), 'backdrop')}
                    lifetime = 30*86400 if any(data.values()) else 86400
                except ValueError:
                    data, lifetime = {}, 300
                self.vod.check(configuration)
                if self.store.setting('tmdb_key') != key:
                    raise ValueError('TMDB-instelling gewijzigd.')
                with self.store.connect() as db:
                    db.execute('INSERT OR REPLACE INTO tmdb_artwork VALUES (?,?,?)', (cache_key, json.dumps(data), time.time()+lifetime))
            address = data.get(asset)
            if not address:
                raise ValueError('Geen eenduidige TMDB-afbeelding beschikbaar.')
            result = self.logos.get(address)
            self.vod.check(configuration)
            self.vod.artwork_metadata(kind, identifier)  # Respect a newly hidden category.
            if self.store.setting('tmdb_key') != key:
                raise ValueError('TMDB-instelling gewijzigd.')
            return result
