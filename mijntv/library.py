"""Profile-aware shared watchlists and ordered playback progress."""
import hashlib
import json
import math
import secrets
import time

from .discovery import identity
from .vod import identifier

DEFAULTS = {'autoplay_next':True, 'audio_language':'original', 'subtitle_language':'nl'}


class Library:
    def __init__(self, store, vod, recordings, mobile):
        self.store, self.vod, self.recordings, self.mobile = store, vod, recordings, mobile
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS library_profiles (
                id TEXT PRIMARY KEY,name TEXT NOT NULL,preferences TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS library_saved (
                profile TEXT NOT NULL,signature TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,
                saved INTEGER NOT NULL,updated REAL NOT NULL,PRIMARY KEY(profile,signature,kind,id));
                CREATE TABLE IF NOT EXISTS library_progress (
                profile TEXT NOT NULL,signature TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,
                parent TEXT NOT NULL,position REAL NOT NULL,duration REAL NOT NULL,completed INTEGER NOT NULL,
                watched INTEGER NOT NULL,started INTEGER NOT NULL,sequence INTEGER NOT NULL,updated REAL NOT NULL,
                PRIMARY KEY(profile,signature,kind,id));
                CREATE TABLE IF NOT EXISTS library_dismissed (
                profile TEXT NOT NULL,signature TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,
                started INTEGER NOT NULL,PRIMARY KEY(profile,signature,kind,id));
                CREATE TABLE IF NOT EXISTS library_context (
                token TEXT PRIMARY KEY,profile TEXT NOT NULL,signature TEXT NOT NULL,kind TEXT NOT NULL,
                id TEXT NOT NULL,parent TEXT NOT NULL,started INTEGER NOT NULL,expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS library_imports (
                profile TEXT NOT NULL,signature TEXT NOT NULL,client TEXT NOT NULL,
                PRIMARY KEY(profile,signature,client));''')
            db.execute('INSERT OR IGNORE INTO library_profiles VALUES (?,?,?)',('home','Thuis',json.dumps(DEFAULTS)))

    def profile(self, key='home'):
        if not isinstance(key,str):raise ValueError('Onbekend kijkprofiel.')
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM library_profiles WHERE id=?',(key,)).fetchone()
        if not row:raise ValueError('Onbekend kijkprofiel.')
        return {'id':row['id'],'name':row['name'],'preferences':dict(DEFAULTS,**json.loads(row['preferences']))}

    def add_profile(self, name):
        if not isinstance(name,str) or not 1<=len(name.strip())<=40:raise ValueError('Geef het profiel een naam.')
        key=secrets.token_hex(12)
        with self.store.connect() as db:db.execute('INSERT INTO library_profiles VALUES (?,?,?)',(key,name.strip(),json.dumps(DEFAULTS)))
        return self.profile(key)

    def preferences(self, profile, values):
        current=self.profile(profile)
        if not isinstance(values,dict) or set(values)-set(DEFAULTS):raise ValueError('Ongeldige kijkvoorkeur.')
        preferences=dict(current['preferences'],**values)
        if not isinstance(preferences['autoplay_next'],bool) or preferences['audio_language']!='original' or preferences['subtitle_language'] not in ('nl','off'):
            raise ValueError('Ongeldige kijkvoorkeur.')
        with self.store.connect() as db:db.execute('UPDATE library_profiles SET preferences=? WHERE id=?',(json.dumps(preferences),profile))
        return self.profile(profile)

    def signature(self):
        try:return self.vod.configuration()[2]
        except ValueError:return ''

    def resolve(self, kind, key):
        key=identifier(key) if kind!='recording' else str(key)
        if kind=='recording':
            row=next((row for row in self.recordings.list() if row['id']==key and row['playable']),None)
            if not row:raise ValueError('Deze opname is niet beschikbaar.')
            return 'local','',row
        if kind not in ('movie','series','episode'):raise ValueError('Ongeldig afspeeltype.')
        configuration=self.vod.configuration()
        row=self.vod._item(configuration,kind,key)
        return configuration[2],row['category'] if kind=='episode' else '',row

    def canonical(self, kind, key):
        signature,parent,row=self.resolve(kind,key)
        if kind=='recording':return dict(row,kind=kind)
        item=self.vod.public(row,bool(self.store.setting('tmdb_key')))
        with self.store.connect() as db:
            cached=db.execute('SELECT data FROM vod_details WHERE signature=? AND kind=? AND id=?',(signature,kind,key)).fetchone()
        details=json.loads(cached['data']) if cached else {}
        for field in ('year','genre','rating','duration'):
            if details.get(field):item[field]=details[field]
        item['display_title']=identity(item['title'],item,str(key))[0]
        item['signature']=signature[:16]
        return item

    def next_episode(self, key):
        signature,parent,_=self.resolve('episode',key)
        series=self.canonical('series',parent)
        with self.store.connect() as db:
            rows=db.execute("SELECT data FROM vod_items WHERE signature=? AND kind='episode' AND category=?",(signature,parent)).fetchall()
        episodes=sorted((json.loads(row['data']) for row in rows),key=lambda ep:(ep['season'],ep['number'],ep['id']))
        current=next((index for index,ep in enumerate(episodes) if ep['id']==str(key)),None)
        if current is None or current+1>=len(episodes):return None
        episode=episodes[current+1]
        return {'library':series,'episode':{k:episode[k] for k in ('id','title','season','number','duration')},'signature':signature[:16]}

    def bind(self, player, profile='home'):
        preferences=self.profile(profile)['preferences']
        player['preferences']=preferences
        if player['kind'] not in ('movie','episode','recording'):return
        signature,parent,_=self.resolve(player['kind'],player['id'])
        token=hashlib.sha256(player['token'].encode()).hexdigest()
        started=time.time_ns()
        with self.store.connect() as db:
            db.execute('DELETE FROM library_context WHERE expires<?',(time.time(),))
            db.execute('INSERT INTO library_context VALUES (?,?,?,?,?,?,?,?)',
                (token,profile,signature,player['kind'],str(player['id']),parent,started,time.time()+86400))
            db.execute('UPDATE library_progress SET started=?,sequence=-1 WHERE profile=? AND signature=? AND kind=? AND id=? AND started<?',
                (started,profile,signature,player['kind'],str(player['id']),started))
            initial=player.get('initial_position',player.get('position'))
            if initial is not None:
                initial=self.seconds(initial)
                db.execute('UPDATE library_progress SET position=?,completed=0,updated=? WHERE profile=? AND signature=? AND kind=? AND id=? AND started=?',
                    (initial,time.time(),profile,signature,player['kind'],str(player['id']),started))
            db.execute('INSERT OR IGNORE INTO library_progress VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (profile,signature,player['kind'],str(player['id']),parent,initial or 0,player.get('duration') or 0,0,0,started,-1,time.time()))
        if player['kind']=='episode':player['next_episode']=self.next_episode(str(player['id']))

    @staticmethod
    def seconds(value):
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or not 0<=value<=7*86400:
            raise ValueError('Ongeldige afspeelpositie.')
        return value

    def progress(self, data):
        token=data.get('token')
        if not isinstance(token,str):raise ValueError('Deze kijksessie is verlopen.')
        sequence=data.get('sequence')
        if isinstance(sequence,bool) or not isinstance(sequence,int) or not 0<=sequence<=1_000_000_000:raise ValueError('Ongeldige voortgang.')
        position=self.seconds(data.get('position'))
        duration=self.seconds(data.get('duration') or 0)
        completed=data.get('ended') is True or (duration>0 and position>=duration-10)
        digest=hashlib.sha256(token.encode()).hexdigest()
        with self.store.connect() as db:
            context=db.execute('SELECT * FROM library_context WHERE token=? AND expires>?',(digest,time.time())).fetchone()
            if not context:raise ValueError('Deze kijksessie is verlopen.')
            if context['signature']!='local' and context['signature']!=self.signature():return {'ignored':True}
            old=db.execute('SELECT * FROM library_progress WHERE profile=? AND signature=? AND kind=? AND id=?',
                           (context['profile'],context['signature'],context['kind'],context['id'])).fetchone()
            if old and (old['started']>context['started'] or (old['started']==context['started'] and old['sequence']>=sequence)):
                return {'ignored':True}
            db.execute('INSERT OR REPLACE INTO library_progress VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (context['profile'],context['signature'],context['kind'],context['id'],context['parent'],position,duration,
                 int(completed),int(completed or bool(old and old['watched'])),context['started'],sequence,time.time()))
            db.execute('UPDATE library_context SET expires=? WHERE token=?',(time.time()+86400,digest))
        return {'ignored':False}

    def save(self, data):
        profile=data.get('profile','home');self.profile(profile)
        signature,_,_=self.resolve(self.vod.kind(data.get('kind')),data.get('id'))
        if data.get('signature')!=signature[:16] or not isinstance(data.get('saved'),bool):raise ValueError('Ongeldige kijklijst.')
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO library_saved VALUES (?,?,?,?,?,?)',
                       (profile,signature,data['kind'],str(data['id']),int(data['saved']),time.time()))
        return self.snapshot(profile)

    def remove_continue(self, data):
        profile=data.get('profile','home');self.profile(profile)
        kind=data.get('kind')
        if kind not in ('movie','series'):raise ValueError('Kies een film of serie.')
        signature,_,_=self.resolve(kind,data.get('id'))
        if data.get('signature')!=signature[:16]:raise ValueError('Ongeldige bibliotheek.')
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO library_dismissed VALUES (?,?,?,?,?)',
                       (profile,signature,kind,str(data['id']),time.time_ns()))
        return self.snapshot(profile)

    def snapshot(self, profile='home'):
        current=self.profile(profile);signature=self.signature()
        with self.store.connect() as db:
            saved=db.execute('SELECT * FROM library_saved WHERE profile=? AND signature=? AND saved=1 ORDER BY updated DESC LIMIT 300',(profile,signature)).fetchall()
            progress=db.execute("SELECT * FROM library_progress WHERE profile=? AND signature IN (?, 'local') ORDER BY started DESC,updated DESC LIMIT 3000",(profile,signature)).fetchall()
            dismissed={(row['kind'],row['id']):row['started'] for row in db.execute(
                'SELECT * FROM library_dismissed WHERE profile=? AND signature=?',(profile,signature))}
        positions,history,watchlist,seen={},[],[],set()
        for row in saved:
            try:watchlist.append(self.canonical(row['kind'],row['id']))
            except ValueError:pass
        for row in progress:
            try:
                self.resolve(row['kind'],row['id'])
                key=row['id'] if row['kind']=='recording' else signature[:16]+'-'+row['kind']+'-'+row['id']
                positions[key]={'position':0 if row['completed'] else row['position'],'duration':row['duration'],
                                'completed':bool(row['completed']),'watched':bool(row['watched'])}
                if row['kind']=='recording':continue
                kind='series' if row['kind']=='episode' else 'movie';identifier=row['parent'] if kind=='series' else row['id']
                if row['started']<=dismissed.get((kind,identifier),-1):continue
                if (kind,identifier) in seen:continue
                seen.add((kind,identifier))
                if row['completed']:
                    if kind!='series':continue
                    next_item=self.next_episode(row['id'])
                    if not next_item:continue
                    item=next_item['library'];item.update(episode=next_item['episode'],position=0,duration=next_item['episode']['duration'],next_episode=True)
                else:
                    if row['position']<=10:continue
                    item=self.canonical(kind,identifier)
                    item.update(position=row['position'],duration=row['duration'])
                    if kind=='series':
                        _,_,episode=self.resolve('episode',row['id']);raw=json.loads(episode['data'])
                        item['episode']={k:raw[k] for k in ('id','title','season','number')}
                history.append(item)
            except (ValueError,KeyError):continue
        return {'profile':current,'signature':signature[:16],'saved':watchlist,'history':history[:100],'positions':positions}

    def import_local(self, data):
        profile=data.get('profile','home');self.profile(profile);signature=self.signature()
        client=data.get('client')
        if not isinstance(client,str) or not 8<=len(client)<=80 or data.get('signature')!=signature[:16]:raise ValueError('Ongeldige lokale kijklijst.')
        saved=data.get('saved',[]);progress=data.get('progress',[])
        if not isinstance(saved,list) or not isinstance(progress,list) or len(saved)>300 or len(progress)>1000:raise ValueError('De lokale kijklijst is te groot.')
        with self.store.connect() as db:
            if db.execute('SELECT 1 FROM library_imports WHERE profile=? AND signature=? AND client=?',(profile,signature,client)).fetchone():return self.snapshot(profile)
        entries=[];favorites=[]
        for item in saved:
            try:
                scope,_,_=self.resolve(self.vod.kind(item['kind']),item['id'])
                if scope==signature:favorites.append((profile,signature,item['kind'],str(item['id']),1,time.time()))
            except (KeyError,TypeError,ValueError):pass
        for index,item in enumerate(progress):
            try:
                scope,parent,_=self.resolve(item['kind'],item['id'])
                if scope not in (signature,'local'):continue
                position=self.seconds(item['position']);duration=self.seconds(item.get('duration') or 0)
                complete=duration>0 and position>=duration-10
                entries.append((profile,scope,item['kind'],str(item['id']),parent,position,duration,int(complete),int(complete),0,-1,time.time()-1-index*.001))
            except (KeyError,TypeError,ValueError):pass
        with self.store.connect() as db:
            db.executemany('INSERT OR IGNORE INTO library_saved VALUES (?,?,?,?,?,?)',favorites)
            db.executemany('INSERT OR IGNORE INTO library_progress VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',entries)
            db.execute('INSERT OR IGNORE INTO library_imports VALUES (?,?,?)',(profile,signature,client))
        return self.snapshot(profile)
