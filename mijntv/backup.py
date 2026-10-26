"""Separate live-TV failover catalog; account secrets stay in private storage."""
import re
import time
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .store import fetch_playlist, iter_m3u
from .xtream import is_xtream_source, live_playlist


def identity(address):
    p = urlsplit(address)
    q = parse_qs(p.query)
    return p.hostname, p.port, q.get('username', [p.username])[-1]


def channel_key(name, group=''):
    text = unicodedata.normalize('NFKC', name).upper().strip()
    region = re.match(r'^([A-Z]{2,3})\s*[|:]', text)
    group_region = re.match(r'^([A-Z]{2,3})\s*[|:]', group.upper())
    country = (region or group_region).group(1) if region or group_region else ''
    text = re.sub(r'^[A-Z]{2,3}\s*[|:]\s*', '', text)
    text = re.sub(r'\b(?:FHD|UHD|HD|SD|4K|8K|HEVC|H264|H265|RAW|VIP|GOLD|60FPS|50FPS)\b', '', text)
    return country, re.sub(r'[^\w]', '', text, flags=re.UNICODE)


class BackupAccount:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def create_tables(db):
        db.execute("""CREATE TABLE IF NOT EXISTS backup_channels (
            id TEXT PRIMARY KEY,name TEXT NOT NULL,group_name TEXT NOT NULL,
            url TEXT NOT NULL,tvg_id TEXT NOT NULL,country TEXT NOT NULL,name_key TEXT NOT NULL)""")
        db.execute('CREATE INDEX IF NOT EXISTS backup_epg ON backup_channels(tvg_id)')
        db.execute('CREATE INDEX IF NOT EXISTS backup_name ON backup_channels(name_key,country)')

    def matches(self, db, favorite):
        epg = (favorite.get('epg_id') or '').strip().casefold()
        rows = []
        method = ''
        if epg:
            rows = db.execute('SELECT * FROM backup_channels WHERE tvg_id=? ORDER BY id', (epg,)).fetchall()
            if rows:
                method = 'epg'
        if not rows:
            country, key = channel_key(favorite.get('name') or favorite['label'], favorite.get('group_name') or '')
            if key:
                rows = db.execute('SELECT * FROM backup_channels WHERE name_key=? AND country=? ORDER BY id',
                                  (key, country)).fetchall()
                # Different EPG identities behind the same name require a human choice.
                if len({r['tvg_id'] for r in rows if r['tvg_id']}) > 1:
                    rows = []
                if rows:
                    method = 'name'
        sources = [dict(id=r['id'], name=r['name'], group_name=r['group_name'], url=r['url'], account='backup') for r in rows]
        region, _ = channel_key(favorite.get('name') or favorite['label'], favorite.get('group_name') or '')
        sources.sort(key=lambda r: (bool(region) and channel_key(r['name'], r['group_name'])[0] != region,
                                    'FHD' not in r['name'].upper(), 'HD' not in r['name'].upper(), r['id']))
        return sources, method

    @staticmethod
    def favorite_rows(db):
        return db.execute("""SELECT f.id,f.label,c.name,c.group_name,COALESCE(e.epg_id,'') AS epg_id
                FROM favorites f LEFT JOIN channels c ON c.id=f.id
                LEFT JOIN favorite_epg e ON e.id=f.id ORDER BY f.position""").fetchall()

    def status(self):
        with self.store.connect() as db:
            favorites = self.favorite_rows(db)
            matches = []
            for row in favorites:
                sources, method = self.matches(db, dict(row))
                matches.append({'id': row['id'], 'label': row['label'], 'matched': bool(sources),
                                'method': method, 'source': sources[0]['name'] if sources else '',
                                'sources': len(sources)})
            count = db.execute('SELECT count(*) FROM backup_channels').fetchone()[0]
            settings = dict(db.execute("SELECT key,value FROM settings WHERE key LIKE 'backup_%' OR key='preferred_account'"))
        return {'configured': bool(settings.get('backup_source')), 'channels': count,
                'preferred': settings.get('preferred_account', 'primary'),
                'refreshed_at': float(settings.get('backup_refreshed_at', '0')),
                'error': settings.get('backup_error', ''), 'matches': matches,
                'matched': sum(m['matched'] for m in matches), 'total': len(matches)}

    def save(self, source):
        if not isinstance(source, str) or len(source) > 16384:
            raise ValueError('Ongeldig backupadres.')
        source = source.strip()
        with self.store.import_lock:
            if not source:
                with self.store.connect() as db:
                    db.execute('DELETE FROM backup_channels')
                    db.execute("DELETE FROM settings WHERE key IN ('backup_source','backup_refreshed_at','backup_error','preferred_account')")
                    db.execute("DELETE FROM favorite_source WHERE source_id LIKE 'backup:%'")
                return self.status()
            primary = self.store.setting('source')
            recording = self.store.setting('recording_source')
            if any(source == other or (identity(source)[2] and identity(source) == identity(other))
                   for other in (primary, recording) if other):
                raise ValueError('Gebruik een ander account dan het kijkaccount of het aparte opnameaccount.')
            self._import(source)
            return self.status()

    def _import(self, source, expected=None):
        table = 'backup_import_' + uuid.uuid4().hex
        text = None
        if is_xtream_source(source):
            try:
                # Fetch live TV only; the provider's full playlist may exceed 512 MB.
                text = live_playlist(source)
            except ValueError:
                pass
        if text is None:
            text = fetch_playlist(source, directory=str(Path(self.store.path).parent))
        with text:
            try:
                with self.store.connect() as db:
                    db.execute(f'CREATE TABLE {table} AS SELECT * FROM backup_channels WHERE 0')
                    batch = []
                    for row in iter_m3u(text):
                        country, key = channel_key(row[1], row[2])
                        batch.append(('backup:' + row[0], row[1], row[2], row[3], row[4].strip().casefold(), country, key))
                        if len(batch) >= 1000:
                            db.executemany(f'INSERT INTO {table} VALUES (?,?,?,?,?,?,?)', batch)
                            db.commit()
                            batch.clear()
                    db.executemany(f'INSERT INTO {table} VALUES (?,?,?,?,?,?,?)', batch)
                    # A failed fetch/parse never removes the previous catalog.
                    if expected is not None and self.store.setting('backup_source') != expected:
                        raise ValueError('Het backupaccount is ondertussen gewijzigd.')
                    old_source = db.execute("SELECT value FROM settings WHERE key='backup_source'").fetchone()
                    if not old_source or old_source[0] != source:
                        db.execute("DELETE FROM settings WHERE key='preferred_account'")
                        db.execute("DELETE FROM favorite_source WHERE source_id LIKE 'backup:%'")
                    favorites = [dict(f) for f in self.favorite_rows(db)]
                    old_matched = sum(bool(self.matches(db, f)[0]) for f in favorites)
                    db.execute('DELETE FROM backup_channels')
                    db.execute(f'INSERT INTO backup_channels SELECT * FROM {table}')
                    new_matched = sum(bool(self.matches(db, f)[0]) for f in favorites)
                    if old_source and old_source[0] == source and old_matched and new_matched * 2 < old_matched:
                        raise ValueError('De nieuwe backuplijst mist te veel favorieten. De vorige lijst blijft behouden.')
                    for k, v in [('backup_source', source), ('backup_refreshed_at', str(time.time())), ('backup_error', '')]:
                        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (k, v))
            finally:
                with self.store.connect() as db:
                    db.execute(f'DROP TABLE IF EXISTS {table}')

    def refresh(self):
        with self.store.import_lock:
            source = self.store.setting('backup_source')
            if not source:
                raise ValueError('Stel eerst een backupaccount in.')
            self._import(source, expected=source)
            return self.status()

    def run(self, stop):
        from .maintenance import BACKGROUND_STARTUP_DELAY
        if stop.wait(BACKGROUND_STARTUP_DELAY):
            return
        retry_at = 0
        previous = None
        while not stop.is_set():
            source = self.store.setting('backup_source')
            if source != previous:
                retry_at, previous = 0, source
            try:
                due = time.time() - float(self.store.setting('backup_refreshed_at', '0')) >= 21600
            except ValueError:
                due = True
            if source and due and time.monotonic() >= retry_at:
                try:
                    self.refresh()
                    retry_at = 0
                except Exception:
                    if self.store.setting('backup_source') == source:
                        self.store.set_setting('backup_error', 'Verversen mislukt; de vorige backuplijst blijft behouden.')
                    retry_at = time.monotonic() + 900
            if stop.wait(60):
                return
