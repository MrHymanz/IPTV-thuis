"""On-demand movie/series metadata; provider URLs never leave the server."""
import hashlib
import html
import json
import re
import threading
import time
from urllib.parse import quote, urlsplit, urlunsplit
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .catchup import Catchup, ProviderRequestError
from .imdb import IMDbChart, title_key

REFRESH_SECONDS = 6 * 3600
PAGE_SIZE = 60


def clean(value, limit=6000):
    return ' '.join(html.unescape(re.sub(r'<[^>]*>', ' ', str(value or ''))).split())[:limit]


def identifier(value):
    value = str(value)
    if not re.fullmatch(r'[0-9]{1,20}', value):
        raise ValueError('Ongeldige film of serie.')
    return value


def extension(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9]{1,8}', value) else 'mp4'


def duration(info):
    try:
        seconds = int(float(info.get('duration_secs') or 0))
        if seconds <= 0:
            pieces = str(info.get('duration') or '').split(':')
            seconds = 0
            if len(pieces) in (2, 3):
                for piece in pieces:
                    seconds = seconds * 60 + int(piece)
        return seconds if 0 < seconds <= 7 * 86400 else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def cover(value):
    try:
        parsed = urlsplit(value) if isinstance(value, str) else None
        return value if parsed and parsed.scheme in ('http', 'https') and parsed.hostname else ''
    except ValueError:
        return ''


class VOD:
    def __init__(self, store, provider=None):
        self.store = store
        self.provider = provider or Catchup(store)
        self.lock = threading.RLock()
        self.failed = {}
        self.chart = IMDbChart(store)
        self.closed = threading.Event()
        self.jobs_lock = threading.Lock()
        self.workers, self.sync_errors, self.sync_retry = {}, {}, {}
        self.featured_lock, self.featured_cache = threading.Lock(), {}
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS vod_items (
                    signature TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                    category TEXT NOT NULL, title TEXT NOT NULL, search TEXT NOT NULL,
                    cover TEXT NOT NULL, extension TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(signature,kind,id));
                CREATE INDEX IF NOT EXISTS vod_category ON vod_items(signature,kind,category,title);
                CREATE TABLE IF NOT EXISTS vod_categories (
                    signature TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
                    PRIMARY KEY(signature,kind,id));
                CREATE TABLE IF NOT EXISTS vod_meta (
                    signature TEXT NOT NULL, kind TEXT NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY(signature,kind));
                CREATE TABLE IF NOT EXISTS vod_backdrops (
                    signature TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, url TEXT NOT NULL,
                    PRIMARY KEY(signature,kind,id));
                CREATE TABLE IF NOT EXISTS vod_details (
                    signature TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                    updated REAL NOT NULL, data TEXT NOT NULL, PRIMARY KEY(signature,kind,id));
            ''')

    def configuration(self):
        configuration = self.provider.configuration()
        if not configuration:
            raise ValueError('Films en series zijn beschikbaar met een Xtream-login. Stel deze in bij het beheer van je hoofdaccount.')
        return configuration

    @staticmethod
    def kind(kind):
        if kind not in ('movie', 'series'):
            raise ValueError('Kies Films of Series.')
        return kind

    def check(self, configuration):
        if self.provider.configuration() != configuration:
            raise ValueError('De providerinstellingen zijn gewijzigd. Open het aanbod opnieuw.')

    def selection(self, kind):
        raw = self.store.setting('mobile_vod_selection:'+self.configuration()[2], '')
        if not raw:
            return None
        try:
            value = json.loads(raw).get(kind, [])
            return [str(i) for i in value] if isinstance(value, list) else []
        except (ValueError, AttributeError):
            return []

    def settings(self, refresh=False):
        configuration = self.configuration()
        result = {'kinds': {}, 'imdb_count': len(self.chart.entries())}
        for kind, action in [('movie', 'get_vod_categories'), ('series', 'get_series_categories')]:
            with self.store.connect() as db:
                known = db.execute('SELECT id,name FROM vod_categories WHERE signature=? AND kind=? ORDER BY name COLLATE NOCASE', (configuration[2], kind)).fetchall()
            if refresh or not known:
                categories = self.provider.request(configuration, action)
                if not isinstance(categories, list):
                    raise ValueError('De provider stuurde geen bruikbare categorieën.')
                self._save_categories(configuration, kind, categories)
                with self.store.connect() as db:
                    known = db.execute('SELECT id,name FROM vod_categories WHERE signature=? AND kind=? ORDER BY name COLLATE NOCASE', (configuration[2], kind)).fetchall()
            selected = self.selection(kind)
            result['kinds'][kind] = {'configured': selected is not None,
                                    'categories': [dict(row, enabled=selected is None or row['id'] in selected) for row in known]}
        return result

    def save_selection(self, selections):
        if not isinstance(selections, dict) or set(selections) != {'movie', 'series'}:
            raise ValueError('Sla films en series afzonderlijk op.')
        configuration = self.configuration()
        normalized = {}
        with self.store.connect() as db:
            for kind in ('movie', 'series'):
                values = selections[kind]
                allowed = {row[0] for row in db.execute('SELECT id FROM vod_categories WHERE signature=? AND kind=?', (configuration[2], kind))}
                if not isinstance(values, list) or any(not isinstance(v, str) or v not in allowed for v in values):
                    raise ValueError('Onbekende categorie; haal de beheerlijst opnieuw op.')
                normalized[kind] = list(dict.fromkeys(values))
            self.check(configuration)
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                       ('mobile_vod_selection:'+configuration[2], json.dumps(normalized)))
        for kind in ('movie', 'series'):
            self.start_sync(kind)

    def start_sync(self, kind):
        selected = self.selection(kind)
        if selected is None or not selected or self.closed.is_set():
            return
        with self.jobs_lock:
            if kind in self.workers or self.sync_retry.get(kind, 0) > time.monotonic():
                return
            configuration = self.configuration()
            def run():
                errors = 0
                try:
                    for category in selected:
                        if self.closed.is_set() or self.provider.configuration() != configuration or self.selection(kind) != selected:
                            return
                        try:
                            notice, _ = self._refresh(configuration, kind, category)
                            if notice:
                                errors += 1
                        except Exception:
                            errors += 1
                    self.sync_errors[kind] = errors
                finally:
                    with self.jobs_lock:
                        self.workers.pop(kind, None)
                        self.sync_retry[kind] = time.monotonic() + (60 if errors else 30)
            worker = threading.Thread(target=run, daemon=True)
            self.workers[kind] = worker
            worker.start()

    def close(self):
        self.closed.set()

    def account_allowed(self, row):
        if row['kind'] == 'episode':
            self._item(self.configuration(), 'series', row['category'])
            return
        selected = self.selection(row['kind'])
        if selected is not None and row['category'] not in selected:
            raise ValueError('Deze categorie is niet vrijgegeven voor mobiel.')

    def _save_categories(self, configuration, kind, categories):
        self.check(configuration)
        rows = [(configuration[2], kind, str(c['category_id']), clean(c.get('category_name'), 300))
                for c in categories if isinstance(c, dict) and c.get('category_id') is not None]
        with self.store.connect() as db:
            db.execute('DELETE FROM vod_categories WHERE signature=? AND kind=?', (configuration[2], kind))
            db.executemany('INSERT OR REPLACE INTO vod_categories VALUES (?,?,?,?)', rows)
        return rows

    def _refresh(self, configuration, kind, category=''):
        signature = configuration[2]
        scope = kind+':category:'+category if category else kind
        with self.store.connect() as db:
            full = db.execute('SELECT updated FROM vod_meta WHERE signature=? AND kind=?', (signature, kind)).fetchone()
            mode = db.execute('SELECT updated FROM vod_meta WHERE signature=? AND kind=?', (signature, kind+':category-mode')).fetchone()
            row = db.execute('SELECT updated FROM vod_meta WHERE signature=? AND kind=?', (signature, scope)).fetchone()
        if full and time.time()-full[0] < REFRESH_SECONDS:
            return '', False
        if not category and mode and time.time()-mode[0] < REFRESH_SECONDS:
            return 'Het volledige aanbod kon niet worden opgehaald. Kies een bron of categorie om de titels afzonderlijk te laden.', True
        if row and time.time()-row[0] < REFRESH_SECONDS:
            return '', False
        if row and self.failed.get((signature, scope), 0) > time.monotonic():
            return 'Vernieuwen is niet gelukt. Je ziet het eerder opgehaalde aanbod.', False
        try:
            category_action = 'get_vod_categories' if kind == 'movie' else 'get_series_categories'
            item_action = 'get_vod_streams' if kind == 'movie' else 'get_series'
            with self.store.connect() as db:
                known = [dict(row) for row in db.execute('SELECT id AS category_id,name AS category_name FROM vod_categories WHERE signature=? AND kind=?', (signature, kind))]
            categories = known if category and mode and known else self.provider.request(configuration, category_action)
            if not isinstance(categories, list):
                raise ValueError('De provider stuurde geen bruikbare categorieën.')
            if category and not any(str(c.get('category_id')) == category for c in categories if isinstance(c, dict)):
                raise ValueError('Deze categorie is niet meer beschikbaar. Open het aanbod opnieuw.')
            try:
                items = self.provider.request(configuration, item_action, **({'category_id': category} if category else {}))
            except ProviderRequestError as error:
                if category or row or not categories:
                    raise
                if not self._save_categories(configuration, kind, categories):
                    raise
                with self.store.connect() as db:
                    db.execute('INSERT OR REPLACE INTO vod_meta VALUES (?,?,?)', (signature, kind+':category-mode', time.time()))
                return str(error)+' Kies een bron of categorie om de titels afzonderlijk te laden.', True
            if not isinstance(items, list):
                raise ValueError('De provider stuurde geen bruikbaar film- of serieaanbod.')
            rows = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    key = identifier(item.get('stream_id' if kind == 'movie' else 'series_id'))
                except ValueError:
                    continue
                # Some panels ignore category_id and send the whole catalogue.
                # Never put unrelated titles into a selected category.
                if category and str(item.get('category_id') or category) != category:
                    continue
                title = clean(item.get('name'), 500)
                if not title:
                    continue
                data = {'id': key, 'kind': kind, 'title': title, 'category': str(item.get('category_id') or category),
                        'year': clean(item.get('year') or str(item.get('releaseDate') or item.get('release_date') or '')[:4], 10),
                        'rating': clean(item.get('rating'), 10), 'imdb_id': clean(item.get('imdb_id') or item.get('imdb'), 20), 'tmdb_id': clean(item.get('tmdb_id') or item.get('tmdb'), 20)}
                rows[key] = (signature, kind, key, str(item.get('category_id') or category), title, title.casefold(),
                             cover(item.get('stream_icon') if kind == 'movie' else item.get('cover')),
                             extension(item.get('container_extension')), json.dumps(data))
            if items and not rows:
                raise ValueError('De provider stuurde geen bruikbare titels.')
            catrows = [(signature, kind, str(c['category_id']), clean(c.get('category_name'), 300))
                       for c in categories if isinstance(c, dict) and c.get('category_id') is not None]
            self.check(configuration)
            self.failed.pop((signature, scope), None)
            with self.store.connect() as db:
                for table in ('vod_items', 'vod_categories', 'vod_meta', 'vod_details', 'vod_backdrops'):
                    db.execute('DELETE FROM '+table+' WHERE signature<>?', (signature,))
                if category:
                    db.execute('DELETE FROM vod_items WHERE signature=? AND kind=? AND category=?', (signature, kind, category))
                else:
                    db.execute('DELETE FROM vod_items WHERE signature=? AND kind=?', (signature, kind))
                    db.execute('DELETE FROM vod_meta WHERE signature=? AND kind=?', (signature, kind+':category-mode'))
                db.execute('DELETE FROM vod_categories WHERE signature=? AND kind=?', (signature, kind))
                db.executemany('INSERT OR REPLACE INTO vod_items VALUES (?,?,?,?,?,?,?,?,?)', rows.values())
                db.executemany('INSERT OR REPLACE INTO vod_categories VALUES (?,?,?,?)', catrows)
                db.execute('INSERT OR REPLACE INTO vod_meta VALUES (?,?,?)', (signature, scope, time.time()))
            return '', False
        except ValueError:
            if not row:
                raise
            self.check(configuration)
            self.failed[(signature, scope)] = time.monotonic() + 60
            return 'Vernieuwen is niet gelukt. Je ziet het eerder opgehaalde aanbod.', False

    @staticmethod
    def public(row, artwork_enabled=False):
        item = json.loads(row['data'])
        item['cover'] = ('/api/mobile/vod/cover?kind='+row['kind']+'&id='+row['id']+
                         '&v='+hashlib.sha256((row['signature']+row['cover']).encode()).hexdigest()[:12]) if row['cover'] or (artwork_enabled and row['kind'] in ('movie', 'series')) else ''
        item['backdrop'] = '/api/mobile/vod/cover?kind='+row['kind']+'&id='+row['id']+'&asset=backdrop' if artwork_enabled or item.get('has_backdrop') else ''
        return item

    @staticmethod
    def featured_rows(db, where, args, day):
        # Daily deterministic mix across the whole permitted library, not just
        # the first results page. Keep one quality/language version per title.
        def daily(raw):
            item = json.loads(raw)
            return hashlib.sha256((day+title_key(item['title'])).encode()).hexdigest()
        db.create_function('daily_feature', 1, daily)
        rating = "CAST(replace(COALESCE(json_extract(data,'$.rating'),''),',','.') AS REAL)"
        quality = "CASE WHEN cover LIKE 'https://image.tmdb.org/%' THEN 0 WHEN cover<>'' THEN 1 ELSE 2 END"
        rated = db.execute('SELECT * FROM vod_items WHERE '+where+' ORDER BY '+rating+' DESC,title,id LIMIT 200', args).fetchall()
        rated = sorted(rated, key=lambda row: (daily(row['data']), 0 if row['cover'].startswith('https://image.tmdb.org/') else 1, row['id']))
        varied = db.execute('SELECT * FROM vod_items WHERE '+where+' ORDER BY daily_feature(data),'+quality+',id LIMIT 160', args).fetchall()
        picks, seen = [], set()
        for index in range(max(len(rated), len(varied))):
            for pool in (rated, varied):
                if index >= len(pool):
                    continue
                row = pool[index]
                identity = title_key(row['title'])
                if identity not in seen:
                    seen.add(identity)
                    picks.append(row)
                    if len(picks) == 8:
                        return picks
        return picks

    def catalog(self, kind, category='', query='', offset=0, sort='title'):
        self.kind(kind)
        if sort not in ('title', 'rating', 'imdb250') or (kind != 'movie' and sort == 'imdb250'):
            raise ValueError('Ongeldige sorteervolgorde.')
        configuration = self.configuration()
        selected = self.selection(kind)
        offset = max(0, int(offset))
        loading = False
        if selected is None:
            with self.lock:
                notice, requires_category = self._refresh(configuration, kind, str(category))
        else:
            if category and category not in selected:
                raise ValueError('Deze categorie is niet vrijgegeven voor mobiel.')
            self.start_sync(kind)
            loading = kind in self.workers
            requires_category = False
            notice = ('De gekozen categorieën worden op de achtergrond bijgewerkt.' if loading else
                      'Sommige categorieën konden niet worden vernieuwd. Een nieuwe poging volgt automatisch.' if self.sync_errors.get(kind) else '')
            if not selected:
                notice = 'Er zijn nog geen categorieën vrijgegeven voor mobiel. Kies ze in het beheer.'
        self.check(configuration)
        where, args = 'signature=? AND kind=?', [configuration[2], kind]
        if requires_category:
            where += ' AND 0'
        if selected is not None:
            where += ' AND category IN ('+','.join('?' for _ in selected)+')' if selected else ' AND 0'
            args.extend(selected)
        if category:
            where += ' AND category=?'
            args.append(str(category))
        if query.strip():
            where += ' AND instr(search,?)>0'
            args.append(query.strip().casefold()[:300])
        feature_where, feature_args = where, list(args)
        try:
            featured_day = datetime.now(ZoneInfo('Europe/Amsterdam')).date().isoformat()
        except ZoneInfoNotFoundError:
            featured_day = time.strftime('%Y-%m-%d')
        if sort == 'imdb250':
            self.chart.refresh()
            notice = ' '.join(filter(None, [notice, self.chart.notice]))
        with self.store.connect() as db:
            db.create_function('imdb_rank', 1, self.chart.ranker())
            ordering = 'title COLLATE NOCASE,id'
            if sort == 'rating':
                ordering = "CAST(replace(COALESCE(json_extract(data,'$.rating'),''),',','.') AS REAL) DESC,"+ordering
            elif sort == 'imdb250':
                where += ' AND imdb_rank(data) IS NOT NULL'
                ordering = 'imdb_rank(data),'+ordering
            categories = [dict(row) for row in db.execute('SELECT id,name FROM vod_categories WHERE signature=? AND kind=? ORDER BY name COLLATE NOCASE', (configuration[2], kind))
                          if selected is None or row['id'] in selected]
            total = db.execute('SELECT count(*) FROM vod_items WHERE '+where, args).fetchone()[0]
            rows = db.execute('SELECT * FROM vod_items WHERE '+where+' ORDER BY '+ordering+' LIMIT ? OFFSET ?', (*args, PAGE_SIZE, offset)).fetchall()
            featured = self.daily_featured(db, kind, feature_where, feature_args, featured_day) if not category and not query.strip() and not requires_category else []
        artwork_enabled = bool(self.store.setting('tmdb_key'))
        items = [self.public(row, artwork_enabled) for row in rows]
        if sort == 'imdb250':
            ranker = self.chart.ranker()
            for item, row in zip(items, rows):
                item['imdb_rank'] = ranker(row['data'])
        return {'items': items, 'categories': categories, 'featured': [self.public(row, artwork_enabled) for row in featured], 'featured_day': featured_day,
                'total': total, 'offset': offset, 'limit': PAGE_SIZE, 'notice': notice,
                'signature': configuration[2][:16], 'requires_category': requires_category,
                'loading': loading, 'allowed_categories': selected}

    def daily_featured(self, db, kind, where, args, day):
        stamp = db.execute('SELECT max(updated) FROM vod_meta WHERE signature=? AND (kind=? OR kind LIKE ?)',
                           (args[0], kind, kind+':category:%')).fetchone()[0]
        key = (day, where, tuple(args), stamp)
        with self.featured_lock:
            cached = self.featured_cache.get(kind)
            if cached and cached[0] == key:
                ids = cached[1]
                if not ids:
                    return []
                rows = db.execute('SELECT * FROM vod_items WHERE '+where+' AND id IN ('+','.join('?' for _ in ids)+')', (*args, *ids)).fetchall()
                by_id = {row['id']: row for row in rows}
                return [by_id[key] for key in ids if key in by_id]
            rows = self.featured_rows(db, where, args, day)
            self.featured_cache[kind] = (key, [row['id'] for row in rows])
            return rows

    def _item(self, configuration, kind, key):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM vod_items WHERE signature=? AND kind=? AND id=?', (configuration[2], kind, key)).fetchone()
        if not row:
            raise ValueError('Deze film of serie staat niet meer in het aanbod. Open het overzicht opnieuw.')
        self.account_allowed(row)
        return row

    def detail(self, kind, key):
        self.kind(kind)
        key = identifier(key)
        configuration = self.configuration()
        with self.lock:
            row = self._item(configuration, kind, key)
            with self.store.connect() as db:
                cached = db.execute('SELECT updated,data FROM vod_details WHERE signature=? AND kind=? AND id=?', (configuration[2], kind, key)).fetchone()
            notice = ''
            if cached and time.time()-cached['updated'] < REFRESH_SECONDS:
                data = json.loads(cached['data'])
            else:
                try:
                    response = self.provider.request(configuration, 'get_vod_info' if kind == 'movie' else 'get_series_info',
                                                     **({'vod_id': key} if kind == 'movie' else {'series_id': key}))
                    if not isinstance(response, dict) or not isinstance(response.get('info'), dict):
                        raise ValueError('Details zijn momenteel niet beschikbaar bij de provider.')
                    info = response['info']
                    data = dict(json.loads(row['data']), description=clean(info.get('plot') or info.get('description')),
                                genre=clean(info.get('genre'), 300), cast=clean(info.get('cast'), 1000),
                                director=clean(info.get('director'), 300), duration=duration(info))
                    data['year'] = clean(info.get('releasedate') or info.get('releaseDate') or info.get('release_date') or data.get('year'), 10)[:4]
                    data['rating'] = clean(info.get('rating') or data.get('rating'), 10)
                    data['imdb_id'] = clean(info.get('imdb_id') or info.get('imdb') or data.get('imdb_id'), 20)
                    data['tmdb_id'] = clean(info.get('tmdb_id') or info.get('tmdb') or data.get('tmdb_id'), 20)
                    episodes = []
                    episode_rows = []
                    if kind == 'series':
                        groups = response.get('episodes')
                        if not isinstance(groups, dict):
                            raise ValueError('De provider stuurde geen bruikbare afleveringen.')
                        for season_key, entries in groups.items():
                            if not isinstance(entries, list):
                                continue
                            for index, ep in enumerate(entries):
                                if not isinstance(ep, dict):
                                    continue
                                try:
                                    ep_id = identifier(ep.get('id'))
                                    season = max(0, int(ep.get('season', season_key)))
                                    number = max(0, int(ep.get('episode_num', index+1)))
                                except (TypeError, ValueError, OverflowError):
                                    continue
                                epinfo = ep.get('info') if isinstance(ep.get('info'), dict) else {}
                                episode = {'id': ep_id, 'kind': 'episode', 'series_id': key, 'series_title': data['title'],
                                           'season': season, 'number': number, 'title': clean(ep.get('title'), 500) or 'Aflevering '+str(number),
                                           'duration': duration(epinfo), 'description': clean(epinfo.get('plot'))}
                                episodes.append(episode)
                                episode_rows.append((configuration[2], 'episode', ep_id, key, episode['title'], episode['title'].casefold(),
                                                     cover(epinfo.get('movie_image')), extension(ep.get('container_extension')), json.dumps(episode)))
                        data['episodes'] = sorted(episodes, key=lambda e: (e['season'], e['number'], e['id']))
                    self.check(configuration)
                    movie_data = response.get('movie_data') if isinstance(response.get('movie_data'), dict) else {}
                    new_cover = cover(info.get('movie_image') or info.get('cover_big') or info.get('cover')) or row['cover']
                    backdrops = info.get('backdrop_path') or []
                    backdrops = backdrops if isinstance(backdrops, list) else [backdrops]
                    backdrop = next((cover(value) for value in backdrops if cover(value)), '')
                    data['has_backdrop'] = bool(backdrop)
                    with self.store.connect() as db:
                        db.execute('INSERT OR REPLACE INTO vod_backdrops VALUES (?,?,?,?)', (configuration[2], kind, key, backdrop))
                        if kind == 'series':
                            db.execute("DELETE FROM vod_items WHERE signature=? AND kind='episode' AND category=?", (configuration[2], key))
                            db.executemany('INSERT OR REPLACE INTO vod_items VALUES (?,?,?,?,?,?,?,?,?)', episode_rows)
                        db.execute('UPDATE vod_items SET cover=?,extension=? WHERE signature=? AND kind=? AND id=?',
                                   (new_cover, extension(movie_data.get('container_extension') or row['extension']), configuration[2], kind, key))
                        db.execute('INSERT OR REPLACE INTO vod_details VALUES (?,?,?,?,?)', (configuration[2], kind, key, time.time(), json.dumps(data)))
                except ValueError:
                    if not cached:
                        raise
                    self.check(configuration)
                    data = json.loads(cached['data'])
                    notice = 'De details konden niet worden vernieuwd. Je ziet de eerder opgehaalde informatie.'
            self.check(configuration)
            row = self._item(configuration, kind, key)
            if data.get('imdb_id') or data.get('tmdb_id') or data.get('has_backdrop'):
                base = json.loads(row['data'])
                base.update({k:data[k] for k in ('imdb_id', 'tmdb_id', 'has_backdrop') if k in data})
                with self.store.connect() as db:
                    db.execute('UPDATE vod_items SET data=? WHERE signature=? AND kind=? AND id=?', (json.dumps(base), configuration[2], kind, key))
            public = self.public(row, bool(self.store.setting('tmdb_key')))
            return dict(data, cover=public['cover'], backdrop=('/api/mobile/vod/cover?kind='+kind+'&id='+key+'&asset=backdrop') if data.get('has_backdrop') else public['backdrop'], notice=notice, signature=configuration[2][:16])

    def cover_url(self, kind, key):
        if kind not in ('movie', 'series', 'episode'):
            raise ValueError('Ongeldige cover.')
        return self._item(self.configuration(), kind, identifier(key))['cover']

    def backdrop_url(self, kind, key):
        configuration = self.configuration()
        self._item(configuration, self.kind(kind), identifier(key))
        with self.store.connect() as db:
            row = db.execute('SELECT url FROM vod_backdrops WHERE signature=? AND kind=? AND id=?', (configuration[2], kind, key)).fetchone()
        return row['url'] if row else ''

    def artwork_metadata(self, kind, key):
        configuration = self.configuration()
        row = self._item(configuration, self.kind(kind), identifier(key))
        raw = json.loads(row['data'])
        # Only public title identity is ever sent to TMDB, never provider URLs.
        return configuration, {k:raw.get(k) for k in ('kind', 'title', 'year', 'imdb_id', 'tmdb_id')}

    def playback(self, kind, key, signature):
        if kind not in ('movie', 'episode'):
            raise ValueError('Kies een film of een aflevering.')
        configuration = self.configuration()
        if signature != configuration[2][:16]:
            raise ValueError('Het provideraccount is gewijzigd. Open de titel opnieuw.')
        if kind == 'movie':
            data = self.detail(kind, key)
        else:
            row = self._item(configuration, kind, identifier(key))
            # Episode IDs belong to this provider's currently known series.
            self.detail('series', row['category'])
            data = json.loads(self._item(configuration, kind, identifier(key))['data'])
        self.check(configuration)
        row = self._item(configuration, kind, identifier(key))
        parsed, auth, _ = configuration
        path = parsed.path.rsplit('/', 1)[0] + ('/movie/' if kind == 'movie' else '/series/')
        path += quote(auth['username'], safe='')+'/'+quote(auth['password'], safe='')+'/'+row['id']+'.'+row['extension']
        return {'url': urlunsplit((parsed.scheme, parsed.netloc, path, '', '')), 'duration': data.get('duration', 0),
                'title': data['title'], 'label': data.get('series_title') or 'Film', 'configuration': configuration}
