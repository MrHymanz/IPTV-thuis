import hashlib
import json
import tempfile
import time
import unittest
from unittest.mock import patch

from mijntv.account_health import AccountHealth,health,usable
from mijntv.backup import BackupAccount
from mijntv.store import Store
from tests import test_mobile as mobile_fixture


class AccountHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name)
        self.source='https://backup.test/get.php?username=viewer&password=private-health'
        self.store.set_setting('backup_source',self.source)
        self.checker=AccountHealth.__new__(AccountHealth);self.checker.store=self.store

    def record(self,status,expires=None):
        self.store.set_setting('account_health:backup',json.dumps({'signature':hashlib.sha256(self.source.encode()).hexdigest(),
            'status':status,'expires_at':expires,'checked_at':time.time()}))

    def test_verified_expiry_blocks_but_unknown_status_and_new_login_do_not(self):
        self.assertTrue(usable(self.store,'backup'))
        self.record('expired');self.assertFalse(usable(self.store,'backup'))
        self.store.set_setting('backup_source',self.source+'2')
        self.assertTrue(usable(self.store,'backup'))
        self.store.set_setting('backup_source',self.source)
        self.record('active',time.time()-1);self.assertFalse(usable(self.store,'backup'))

    def test_renewed_account_is_enabled_again_without_removing_credentials(self):
        self.record('expired')
        with patch('mijntv.catchup.Catchup.request',return_value={'user_info':{'auth':1,'status':'Active','exp_date':str(int(time.time()+86400))}}), patch('mijntv.backup.BackupAccount.refresh') as refresh:
            self.checker.refresh('backup')
        refresh.assert_called_once_with()
        self.assertTrue(usable(self.store,'backup'))
        self.assertEqual(self.store.setting('backup_source'),self.source)
        self.assertNotIn('private-health',json.dumps(health(self.store,'backup')))

    def test_failed_probe_cannot_erase_a_verified_expiry(self):
        self.record('expired')
        with patch('mijntv.catchup.Catchup.request',side_effect=ValueError('unavailable')):
            with self.assertRaises(ValueError):self.checker.refresh('backup')
        self.assertFalse(usable(self.store,'backup'))

    def test_provider_rejecting_auth_is_not_reported_as_active(self):
        with patch('mijntv.catchup.Catchup.request',return_value={'user_info':{'auth':0,'status':'Active'}}):
            self.checker.refresh('backup')
        self.assertEqual(health(self.store,'backup')['status'],'denied')


class ExpiredSourceTests(unittest.TestCase):
    def setUp(self):
        self.f=mobile_fixture.MobileTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        source=self.f.store.setting('backup_source')
        self.f.store.set_setting('account_health:backup',json.dumps({'signature':hashlib.sha256(source.encode()).hexdigest(),
            'status':'expired','expires_at':time.time()-3600,'checked_at':time.time()}))

    def test_favorite_candidates_exclude_expired_backup_but_preserve_configuration(self):
        favorite=self.f.store.favorites(playback=True)[0]
        self.assertTrue(favorite['sources'])
        self.assertFalse(any(s['account']=='backup' for s in favorite['sources']))
        self.assertTrue(BackupAccount(self.f.store).status()['health']['blocked'])
        self.assertTrue(self.f.store.setting('backup_source'))

    def test_mobile_and_direct_stream_requests_cannot_use_cached_expired_candidates(self):
        self.f.tv()
        with self.assertRaises(ValueError):self.f.play()
        backup=next(s for s in self.f.channel['sources'] if s['account']=='backup')
        with self.assertRaisesRegex(ValueError,'verlopen'):
            self.f.hub.acquire('primary',self.f.channel,backup)
