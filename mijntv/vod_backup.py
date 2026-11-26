"""Match canonical VOD titles to a separate provider without sharing stream IDs."""
import difflib
import json
import logging
import re
import threading
import time

from .catchup import Catchup
from .account_health import usable
from .discovery import identity, normalized
from .vod import VOD, REFRESH_SECONDS, identifier


class BackupStore:
    def __init__(self, store):self.store=store
    def setting(self, key, *args):return self.store.setting('backup_source' if key=='source' else key,*args)
    def __getattr__(self, key):return getattr(self.store,key)


class BackupCatalog(VOD):
    def selection(self, kind):
        # Only a permitted canonical title can reach this internal catalogue.
        # Category IDs from different providers must never be interchanged.
        return None


def media_id(value, imdb=False):
    value=str(value or '').strip()
    pattern=r'tt[0-9]{5,12}' if imdb else r'[1-9][0-9]{0,11}'
    return value if re.fullmatch(pattern,value) else ''


def title_identity(data):
    display,year,_=identity(data['title'],data,str(data.get('id','')))
    return normalized(display),year


def confidence(reference, candidate):
    name,year=title_identity(reference);other,other_year=title_identity(candidate)
    common=False
    for field,is_imdb in (('imdb_id',True),('tmdb_id',False)):
        left,right=media_id(reference.get(field),is_imdb),media_id(candidate.get(field),is_imdb)
        if left and right:
            if left!=right:return 0,'Andere titelidentiteit'
            common=True
    if year and other_year and year!=other_year:return 0,'Ander jaartal'
    if common:return 100,'Dezelfde IMDb-/TMDB-titel'
    if name==other and year and year==other_year:return 95,'Dezelfde titel en hetzelfde jaartal'
    similarity=difflib.SequenceMatcher(None,name,other).ratio()
    if similarity<.68:return 0,'Geen passende titel'
    return round(similarity*75),'Mogelijke titel; zelf controleren'+(' (jaartal onbekend)' if not year or not other_year else '')


class BackupMatches:
    def __init__(self, store, primary):
        self.store,self.primary=store,primary
        self.catalog=BackupCatalog(store,Catchup(BackupStore(store)))
        self.closed=threading.Event();self.lock=threading.Lock();self.workers={};self.retry={}
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS backup_vod_identity (
                signature TEXT,kind TEXT,id TEXT,name TEXT,year TEXT,imdb TEXT,tmdb TEXT,preference INTEGER,
                PRIMARY KEY(signature,kind,id));
                CREATE INDEX IF NOT EXISTS backup_vod_name ON backup_vod_identity(signature,kind,name);
                CREATE INDEX IF NOT EXISTS backup_vod_imdb ON backup_vod_identity(signature,kind,imdb);
                CREATE INDEX IF NOT EXISTS backup_vod_tmdb ON backup_vod_identity(signature,kind,tmdb);
                CREATE TABLE IF NOT EXISTS backup_vod_sync (signature TEXT,kind TEXT,updated REAL,complete INTEGER,
                PRIMARY KEY(signature,kind));''')
        self.scheduler=threading.Thread(target=self._schedule,daemon=True);self.scheduler.start()

    def _schedule(self):
        if self.closed.wait(10):return
        while not self.closed.is_set():
            for kind in ('movie','series'):self.warm(kind)
            self.closed.wait(300)

    def warm(self, kind):
        configuration=self.catalog.provider.configuration()
        if not configuration or self.closed.is_set() or not usable(self.store,'backup'):return
        key=(configuration[2],kind)
        with self.lock:
            if key in self.workers or self.retry.get(key,0)>time.monotonic():return
            with self.store.connect() as db:
                done=db.execute('SELECT updated,complete FROM backup_vod_sync WHERE signature=? AND kind=?',key).fetchone()
            if done and done['complete'] and time.time()-done['updated']<REFRESH_SECONDS:return
            def run():
                success=False
                try:success=self.refresh(configuration,kind)
                except Exception:
                    logging.getLogger('mijntv.stream').info('backup_vod_catalog kind=%s state=unavailable',kind)
                finally:
                    with self.lock:
                        self.workers.pop(key,None);self.retry[key]=time.monotonic()+(300 if not success else 30)
            worker=threading.Thread(target=run,daemon=True);self.workers[key]=worker;worker.start()

    def _index(self, configuration, kind, category=None, complete=False):
        self.catalog.check(configuration)
        where='signature=? AND kind=?';args=[configuration[2],kind]
        if category is not None:where+=' AND category=?';args.append(category)
        with self.store.connect() as db:
            self.catalog.discovery.ensure(db,configuration[2],kind,where,args)
            rows=db.execute('SELECT v.id,v.data,i.preference FROM (SELECT * FROM vod_items WHERE '+where+') v JOIN vod_discovery i USING(signature,kind,id)',args).fetchall()
            records=[]
            for row in rows:
                data=json.loads(row['data']);name,year=title_identity(data)
                records.append((configuration[2],kind,row['id'],name,year,media_id(data.get('imdb_id'),True),media_id(data.get('tmdb_id')),row['preference']))
            self.catalog.check(configuration)
            db.execute('DELETE FROM backup_vod_identity WHERE signature<>?',(configuration[2],))
            db.execute('DELETE FROM backup_vod_identity WHERE signature=? AND kind=? AND id NOT IN (SELECT id FROM vod_items WHERE signature=? AND kind=?)',(configuration[2],kind,configuration[2],kind))
            db.executemany('INSERT OR REPLACE INTO backup_vod_identity VALUES (?,?,?,?,?,?,?,?)',records)
            db.execute('INSERT OR REPLACE INTO backup_vod_sync VALUES (?,?,?,?)',(configuration[2],kind,time.time(),int(complete)))
        return len(records)

    def refresh(self, configuration, kind):
        _,by_category=self.catalog._refresh(configuration,kind)
        complete=True;count=0
        if by_category:
            with self.store.connect() as db:
                categories=[dict(r) for r in db.execute('SELECT id,name FROM vod_categories WHERE signature=? AND kind=?',(configuration[2],kind))]
                fresh={r['kind'].partition(':category:')[2] for r in db.execute('SELECT kind FROM vod_meta WHERE signature=? AND updated>? AND kind LIKE ?',(configuration[2],time.time()-REFRESH_SECONDS,kind+':category:%'))}
            def priority(category):
                text=category['name'].upper()
                return 0 if re.search(r'\b(NL|DUTCH|NEDERLAND)\b',text) else 1 if re.search(r'\b(EN|UK|US|USA|ENGLISH)\b',text) else 2
            attempts=0
            for category in sorted(categories,key=priority):
                if self.closed.is_set():return False
                self.catalog.check(configuration)
                if category['id'] in fresh:continue
                if attempts>=50:complete=False;break
                attempts+=1
                try:
                    notice,_=self.catalog._refresh(configuration,kind,category['id'])
                    if notice:complete=False
                    self._index(configuration,kind,category['id'])
                except ValueError:complete=False
                if self.closed.wait(.2):return False
        count=self._index(configuration,kind,complete=complete)
        logging.getLogger('mijntv.stream').info('backup_vod_catalog kind=%s state=%s titles=%s',kind,'ready' if complete else 'partial',count)
        return complete

    def reference(self, kind, key):
        if kind not in ('movie','series','episode'):raise ValueError('Ongeldig afspeeltype.')
        configuration=self.primary.configuration();key=identifier(key)
        row=self.primary._item(configuration,kind,key);episode=None
        if kind=='episode':
            episode=json.loads(row['data']);kind='series';key=row['category']
            row=self.primary._item(configuration,kind,key)
        data=json.loads(row['data'])
        with self.store.connect() as db:
            detail=db.execute('SELECT data FROM vod_details WHERE signature=? AND kind=? AND id=?',(configuration[2],kind,key)).fetchone()
        if detail:data.update(json.loads(detail[0]))
        return configuration,kind,data,episode

    def offers(self, kind, key):
        primary,kind,reference,episode=self.reference(kind,key)
        configuration=self.catalog.provider.configuration()
        base={'items':[],'automatic':None,'signature':primary[2][:16],'backup_signature':''}
        if not configuration:return dict(base,state='unconfigured',notice='Het backupaccount heeft geen bruikbare Xtream-login.')
        if not usable(self.store,'backup'):return dict(base,state='inactive',notice='Het backupaccount is verlopen of geblokkeerd en wordt overgeslagen.')
        self.warm(kind);signature=configuration[2];name,year=title_identity(reference)
        with self.store.connect() as db:
            sync=db.execute('SELECT complete FROM backup_vod_sync WHERE signature=? AND kind=?',(signature,kind)).fetchone()
            condition=['name=?'];args=[name]
            for field,is_imdb,column in (('imdb_id',True,'imdb'),('tmdb_id',False,'tmdb')):
                value=media_id(reference.get(field),is_imdb)
                if value:condition.append(column+'=?');args.append(value)
            candidates=db.execute('SELECT v.*,i.preference FROM backup_vod_identity i JOIN vod_items v USING(signature,kind,id) WHERE i.signature=? AND i.kind=? AND ('+' OR '.join(condition)+') ORDER BY i.preference,v.id LIMIT 80',(signature,kind,*args)).fetchall()
            if not candidates:
                words=[w for w in name.split() if len(w)>2][:3]
                if words:
                    # Token overlap narrows a bounded suggestion set; no fuzzy autostart.
                    candidates=db.execute('SELECT v.*,i.preference FROM backup_vod_identity i JOIN vod_items v USING(signature,kind,id) WHERE i.signature=? AND i.kind=? AND ('+' OR '.join('i.name LIKE ?' for _ in words)+') ORDER BY i.preference,v.id LIMIT 120',(signature,kind,*('%'+word+'%' for word in words))).fetchall()
        items=[]
        for row in candidates:
            data=json.loads(row['data']);score,reason=confidence(reference,data)
            if not score:continue
            items.append({'id':row['id'],'title':row['title'],'year':title_identity(data)[1],'score':score,'reason':reason,'preference':row['preference']})
        items.sort(key=lambda item:(-item['score'],item['preference'],item['id']))
        strong=[item for item in items if item['score']>=95]
        # Multiple quality copies are fine; conflicting known identities are not.
        identities=set()
        for row in candidates:
            if row['id'] in {item['id'] for item in strong}:
                data=json.loads(row['data']);identities.add((media_id(data.get('imdb_id'),True),media_id(data.get('tmdb_id'))))
        automatic=strong[0]['id'] if strong and len({x for x in identities if any(x)})<=1 else None
        self.primary.check(primary);self.catalog.check(configuration)
        with self.lock:unavailable=not sync and (signature,kind) not in self.workers and self.retry.get((signature,kind),0)>time.monotonic()
        state='unavailable' if unavailable else 'ready' if sync and sync['complete'] else 'partial' if sync else 'loading'
        notice='De backup-provider kon het aanbod niet leveren. Probeer het later opnieuw.' if unavailable else 'De backupcatalogus wordt op de achtergrond geladen. Probeer het zo opnieuw.' if state=='loading' else 'De backupcatalogus is nog niet volledig; gevonden titels zijn alvast beschikbaar.' if state=='partial' else 'Geen passende titel gevonden op het backupaccount.' if not items else 'Sterke matches kunnen automatisch worden gebruikt. Controleer andere suggesties zelf.'
        return dict(base,state=state,notice=notice,items=items[:6],automatic=automatic,backup_signature=signature[:16])

    def playback(self, kind, key, chosen=None, signature=None):
        offers=self.offers(kind,key)
        selected=chosen if chosen is not None else offers['automatic']
        if chosen is not None and signature!=offers['backup_signature']:raise ValueError('Het backupaccount is gewijzigd. Open de suggesties opnieuw.')
        if selected is None:
            raise ValueError(offers['notice'] if not offers['items'] else 'Er zijn mogelijke matches op het backupaccount. Kies eerst zelf de juiste titel onder Backupaanbod.')
        if str(selected) not in [item['id'] for item in offers['items']]:raise ValueError('Deze backuptitel is niet meer beschikbaar als passende suggestie.')
        primary,match_kind,reference,episode=self.reference(kind,key)
        configuration=self.catalog.configuration();matched=self.catalog.detail(match_kind,str(selected))
        score,_=confidence(reference,matched)
        if chosen is None and score<95:raise ValueError('De detailgegevens wijken af. Controleer eerst de suggesties onder Backupaanbod.')
        target=str(selected)
        if episode:
            matches=[ep for ep in matched.get('episodes',[]) if ep['season']==episode['season'] and ep['number']==episode['number']]
            if len(matches)!=1:raise ValueError('Deze aflevering is niet eenduidig beschikbaar bij de backup-provider. Er wordt geen andere aflevering gestart.')
            target=matches[0]['id']
        result=self.catalog.playback(kind,target,configuration[2][:16])
        self.primary.check(primary);self.catalog.check(configuration)
        return result

    def close(self):
        self.closed.set();self.scheduler.join(timeout=1)
        self.catalog.close()
