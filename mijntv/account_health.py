"""Persist verified provider status; never make a network request while choosing a stream."""
import hashlib
import json
import logging
import threading
import time

SETTINGS={'primary':'source','backup':'backup_source','recording':'recording_source'}


def signature(store, role):
    return hashlib.sha256(store.setting(SETTINGS[role]).encode()).hexdigest()


def health(store, role):
    try:
        data=json.loads(store.setting('account_health:'+role) or '{}')
        if data.get('signature')!=signature(store,role):data={}
    except (ValueError,TypeError):data={}
    expires=data.get('expires_at')
    expired=isinstance(expires,(int,float)) and expires>0 and expires<=time.time()
    status='expired' if expired else data.get('status','unknown')
    return {'status':status,'blocked':status in ('expired','disabled','banned','denied'),
            'checked_at':data.get('checked_at',0),'expires_at':expires}


def usable(store, role):
    return role not in SETTINGS or not health(store,role)['blocked']


class AccountStore:
    def __init__(self,store,role):self.store,self.role=store,role
    def setting(self,key,*args):return self.store.setting(SETTINGS[self.role] if key=='source' else key,*args)
    def __getattr__(self,key):return getattr(self.store,key)


class AccountHealth:
    def __init__(self,store):
        self.store=store;self.closed=threading.Event()
        self.worker=threading.Thread(target=self.run,daemon=True);self.worker.start()

    def refresh(self,role):
        from .catchup import Catchup
        source=self.store.setting(SETTINGS[role])
        if not source:return
        provider=Catchup(AccountStore(self.store,role));configuration=provider.configuration()
        if not configuration:return
        info=provider.request(configuration).get('user_info',{})
        if not isinstance(info,dict):raise ValueError('Onbekende accountstatus.')
        status=str(info.get('status','')).casefold()
        if str(info.get('auth'))=='0':status='denied'
        if status not in ('active','expired','disabled','banned','denied'):raise ValueError('Onbekende accountstatus.')
        try:expires=int(info.get('exp_date') or 0) or None
        except (TypeError,ValueError):expires=None
        if self.store.setting(SETTINGS[role])!=source:return
        before=health(self.store,role)['status']
        self.store.set_setting('account_health:'+role,json.dumps({'signature':hashlib.sha256(source.encode()).hexdigest(),
            'status':status,'expires_at':expires,'checked_at':time.time()}))
        if role=='backup' and before in ('expired','disabled','banned','denied') and not health(self.store,role)['blocked']:
            from .backup import BackupAccount
            BackupAccount(self.store).refresh()
        if before!=health(self.store,role)['status']:
            logging.getLogger('mijntv.stream').info('account_health role=%s status=%s',role,health(self.store,role)['status'])

    def run(self):
        if self.closed.wait(5):return
        while not self.closed.is_set():
            for role in SETTINGS:
                if self.closed.is_set():return
                state=health(self.store,role);interval=60 if state['blocked'] else 300
                if time.time()-state['checked_at']<interval:continue
                try:self.refresh(role)
                except Exception:
                    # Failed probes never erase a known expiry or declare a
                    # working account unusable just because its API timed out.
                    pass
            self.closed.wait(60)

    def close(self):
        self.closed.set();self.worker.join(timeout=1)
