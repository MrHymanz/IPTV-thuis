"""Local, indexed movie discovery; no provider requests or artwork downloads."""
import json
import re
import threading
import unicodedata

PREFIX = re.compile(r'^\s*(?:NL|EN|UK|US|FR|DE|BE|ES|IT|PT|TR|AR)\s*[|:\-]\s*', re.I)
QUALITY = re.compile(r'\b(?:4K|8K|UHD|FHD|HD|SD|HDR10\+?|HDR|HEVC|H\.?26[45]|X26[45]|2160P|1080[PI]|720P|480P|DOLBY VISION)\b', re.I)


def normalized(value):
    value = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode().casefold()
    return ' '.join(re.findall(r'[a-z0-9]+', value))


def search_query(value):
    return normalized(QUALITY.sub(' ', PREFIX.sub('', str(value))))


def identity(title, data, identifier):
    year = str(data.get('year') or '')[:4]
    if not re.fullmatch(r'(?:19|20)\d{2}', year):
        match = re.search(r'\(((?:19|20)\d{2})\)', title)
        year = match[1] if match else ''
    display = PREFIX.sub('', title)
    display = re.sub(r'\((?:19|20)\d{2}\)', '', display)
    display = QUALITY.sub('', display)
    display = re.sub(r'\[\s*\]|\(\s*\)', '', display).strip(' -|:[]')
    display = ' '.join(display.split()) or title
    name = normalized(display)
    # Unknown dates are not enough to merge unrelated films with the same name.
    tmdb = str(data.get('tmdb_id') or '')
    imdb = str(data.get('imdb_id') or '')
    if year:
        group = 'name:'+name+'|'+year
    elif re.fullmatch(r'[1-9][0-9]{0,11}', tmdb):
        group = 'tmdb:'+tmdb
    elif re.fullmatch(r'tt[0-9]{5,12}', imdb):
        group = 'imdb:'+imdb
    else:
        group = 'single:'+identifier
    return display, year, group


def number(value):
    try:
        result = float(str(value).replace(',', '.'))
        return result if 0 <= result <= 10 else None
    except (ValueError, TypeError):
        return None


class Discovery:
    def __init__(self, store):
        self.store, self.lock = store, threading.Lock()
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS vod_discovery (
                signature TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                display_title TEXT NOT NULL, search TEXT NOT NULL, year TEXT NOT NULL,
                group_key TEXT NOT NULL, genres TEXT NOT NULL, genre_keys TEXT NOT NULL,
                rating REAL, seconds REAL NOT NULL, preference INTEGER NOT NULL,
                PRIMARY KEY(signature,kind,id));
                CREATE INDEX IF NOT EXISTS vod_discovery_group ON vod_discovery(signature,kind,group_key);''')

    @staticmethod
    def invalidate(db, signature, kind, keys):
        db.executemany('DELETE FROM vod_discovery WHERE signature=? AND kind=? AND id=?',
                       ((signature, kind, key) for key in keys))

    def ensure(self, db, signature, kind, where=None, args=None):
        with self.lock:
            scope = 'SELECT * FROM vod_items WHERE '+(where or 'signature=? AND kind=?')
            rows = db.execute('''SELECT v.id,v.title,v.data,d.data AS detail FROM ('''+scope+''') v
                LEFT JOIN vod_details d USING(signature,kind,id)
                LEFT JOIN vod_discovery i USING(signature,kind,id)
                WHERE i.id IS NULL''', args if args is not None else (signature, kind)).fetchall()
            records = []
            for row in rows:
                data = json.loads(row['data'])
                if row['detail']:
                    data.update(json.loads(row['detail']))
                display, year, group = identity(row['title'], data, row['id'])
                genres = sorted({value.strip() for value in re.split(r'[,;/|]', str(data.get('genre') or '')) if value.strip()})
                seconds = data.get('duration') or 0
                if kind == 'series' and not seconds:
                    lengths = sorted(ep['duration'] for ep in data.get('episodes', []) if isinstance(ep, dict)
                                     and isinstance(ep.get('duration'), (int, float)) and ep['duration'] > 0)
                    seconds = lengths[len(lengths)//2] if lengths else 0
                if not isinstance(seconds, (int, float)) or not 0 < seconds <= 7*86400:
                    seconds = 0
                preference = 2 if re.search(r'\b(?:4K|8K|UHD|2160P)\b', row['title'], re.I) else 0 if re.search(r'\b(?:HD|FHD|1080[PI]|720P)\b', row['title'], re.I) else 1
                records.append((signature, kind, row['id'], display, normalized(display+' '+year), year, group,
                                json.dumps(genres), '|'+ '|'.join(normalized(g) for g in genres)+'|',
                                number(data.get('rating')), seconds, preference))
            if records:
                db.executemany('INSERT OR REPLACE INTO vod_discovery VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', records)
                db.commit()  # Publish before another request can enter the index lock.

    def query(self, db, where, args, query, sort, offset, limit, genre='', year='', min_rating='', length='', kind='movie'):
        if genre and (not isinstance(genre, str) or len(genre) > 100):
            raise ValueError('Ongeldig genre.')
        if year and not re.fullmatch(r'(?:19|20)\d{2}', str(year)):
            raise ValueError('Ongeldig jaar.')
        rating = number(min_rating) if min_rating != '' else None
        if min_rating != '' and rating is None:
            raise ValueError('Kies een beoordeling tussen 0 en 10.')
        if length not in ('', 'short', 'medium', 'long'):
            raise ValueError('Ongeldige speelduur.')
        base = 'FROM (SELECT * FROM vod_items WHERE '+where+') v JOIN vod_discovery i USING(signature,kind,id)'
        years = [row[0] for row in db.execute('SELECT DISTINCT i.year '+base+" WHERE i.year<>'' ORDER BY i.year DESC", args)]
        genres = sorted({g for row in db.execute('SELECT DISTINCT i.genres '+base, args) for g in json.loads(row[0])}, key=str.casefold)
        filters, extra = [], []
        for term in search_query(query).split()[:20]:
            filters.append('instr(i.search,?)>0');extra.append(term)
        if genre:
            filters.append('instr(i.genre_keys,?)>0');extra.append('|'+normalized(genre)+'|')
        if year:
            filters.append('i.year=?');extra.append(str(year))
        if rating is not None:
            filters.append('i.rating>=?');extra.append(rating)
        if length:
            short, medium = (1800, 3600) if kind == 'series' else (5400, 7200)
            if length == 'short':filters.append('i.seconds>0 AND i.seconds<?');extra.append(short)
            elif length == 'medium':filters.append('i.seconds>=? AND i.seconds<=?');extra.extend((short, medium))
            else:filters.append('i.seconds>?');extra.append(medium)
        if sort == 'imdb250':filters.append('imdb_rank(v.data) IS NOT NULL')
        sql = '''WITH eligible AS (SELECT v.*,i.display_title,i.group_key,i.year AS discovery_year,
            i.rating AS discovery_rating,i.seconds AS discovery_seconds,i.preference '''+base
        if filters:sql += ' WHERE '+' AND '.join(filters)
        sql += '''), grouped AS (SELECT *,row_number() OVER (PARTITION BY group_key
            ORDER BY preference,CASE WHEN cover<>'' THEN 0 ELSE 1 END,id) AS choice,
            count(*) OVER (PARTITION BY group_key) AS variant_count,
            max(discovery_rating) OVER (PARTITION BY group_key) AS group_rating FROM eligible) '''
        ordering = 'display_title COLLATE NOCASE,id'
        if sort == 'rating':ordering = 'group_rating DESC,'+ordering
        elif sort == 'imdb250':ordering = 'imdb_rank(data),'+ordering
        params = [*args, *extra]
        count_sql = 'SELECT count(DISTINCT i.group_key) '+base
        if filters:count_sql += ' WHERE '+' AND '.join(filters)
        total = db.execute(count_sql, params).fetchone()[0]
        rows = db.execute(sql+'SELECT * FROM grouped WHERE choice=1 ORDER BY '+ordering+' LIMIT ? OFFSET ?', (*params, limit, offset)).fetchall()
        return rows, total, {'genres':genres, 'years':years}
