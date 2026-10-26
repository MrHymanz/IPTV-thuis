import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

from mijntv.store import Store
from mijntv.recordings import Recordings

spec = importlib.util.spec_from_file_location('tv_updater', Path(__file__).resolve().parents[1] / 'deploy/libreelec/update.py')
updates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updates)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = Store(self.base / 'data')
        self.recorder = Recordings(self.store)
        self.addCleanup(self.recorder.close)
        updates.write_json(self.base / 'updates.json', updates.DEFAULTS)
        (self.base / 'enabled').touch()
        self.updater = updates.Updater(self.base, run=Mock(return_value=''))
        self.revision = 'a' * 40

    def recording(self, state, start, end):
        with self.store.connect() as db:
            db.execute('INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?)',
                       ('test','channel','Channel','Test',start,end,state,str(self.base),''))

    def test_active_or_imminent_recordings_block_update(self):
        for state,start,end in [('recording',0,2000),('queued',1100,2000)]:
            with self.subTest(state=state):
                with self.store.connect() as db:
                    db.execute('DELETE FROM recordings')
                self.recording(state,start,end)
                self.assertFalse(updates.claim_update(self.store.path, now=1000))
                self.assertEqual(self.store.setting('update_lease_until'),'')
        with self.store.connect() as db:
            db.execute("UPDATE recordings SET start=2000,state='queued'")
        self.assertTrue(updates.claim_update(self.store.path, now=1000))
        self.assertEqual(float(self.store.setting('update_lease_until')),1300)

    def test_recording_start_waits_for_lease_without_losing_plan(self):
        self.recording('queued',0,9999999999)
        self.store.set_setting('update_lease_until','9999999999')
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM recordings').fetchone()
        with patch('mijntv.recordings.subprocess.Popen') as process:
            self.recorder.start(row)
        process.assert_not_called()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM recordings').fetchone()[0],'queued')

    def test_new_recording_plan_is_temporarily_rejected_during_update(self):
        self.store.manual('Test','https://example.test/stream')
        self.store.set_setting('update_lease_until','9999999999')
        identifier=self.store.favorites()[0]['id']
        with patch('mijntv.recordings.shutil.which',return_value='/usr/bin/ffmpeg'):
            with self.assertRaisesRegex(ValueError,'bijgewerkt'):
                self.recorder.schedule(identifier)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recordings').fetchone()[0],0)

    def test_healthcheck_allows_container_a_moment_to_start(self):
        states=[{'Running':False},{'Running':True,'Health':{'Status':'starting'}},
                {'Running':True,'Health':{'Status':'healthy'}}]
        self.updater.docker=Mock(side_effect=[json.dumps([{'State':s}]) for s in states])
        clock=[0]
        def sleep(seconds):
            clock[0]+=seconds
        with patch.object(updates.time,'monotonic',side_effect=lambda:clock[0]), patch.object(
                updates.time,'sleep',side_effect=sleep):
            self.assertTrue(self.updater.healthy())

    def test_stopped_tv_service_is_not_opened_by_updater(self):
        self.updater.run=Mock(return_value='inactive')
        self.updater.candidate=Mock()
        with self.assertRaises(updates.Deferred):
            self.updater.update()
        self.updater.candidate.assert_not_called()

    def test_already_claimed_recording_blocks_update(self):
        self.recording('recording',0,9999999999)
        self.assertFalse(updates.claim_update(self.store.path))
        self.assertEqual(self.store.setting('update_lease_until'),'')

    def test_only_successful_push_workflow_for_exact_commit_is_accepted(self):
        good={'head_sha':self.revision,'head_branch':'main','event':'push',
              'path':updates.DEFAULTS['workflow'],'status':'completed','conclusion':'success'}
        for changed in [{'conclusion':'failure'},{'event':'pull_request'},{'head_sha':'b'*40},
                        {'path':'unrelated.yml'},{'head_branch':'other'}]:
            self.updater.get=Mock(side_effect=[json.dumps({'sha':self.revision}).encode(),
                                               json.dumps({'workflow_runs':[dict(good,**changed)]}).encode()])
            with self.assertRaises(updates.Deferred):
                self.updater.candidate()
        self.updater.get=Mock(side_effect=[json.dumps({'sha':self.revision}).encode(),
                                           json.dumps({'workflow_runs':[good]}).encode()])
        self.assertEqual(self.updater.candidate(),self.revision)

    def test_failed_revision_is_not_reinstalled_automatically(self):
        self.updater.state={'revision':'b'*40,'failed_revision':self.revision}
        self.updater.get=Mock(return_value=json.dumps({'sha':self.revision}).encode())
        with self.assertRaises(updates.Deferred):
            self.updater.candidate()
        self.updater.get.assert_called_once()

    def test_untrusted_archive_paths_and_links_are_rejected(self):
        for name,kind in [('../escape',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),
                          ('root/link',tarfile.SYMTYPE)]:
            content=io.BytesIO()
            with tarfile.open(fileobj=content,mode='w:gz') as archive:
                member=tarfile.TarInfo(name);member.type=kind
                archive.addfile(member)
            with self.assertRaises(ValueError):
                updates.unpack_archive(content.getvalue(),self.base/'archive')

    def test_normal_archive_is_unpacked_under_single_root(self):
        content=io.BytesIO()
        with tarfile.open(fileobj=content,mode='w:gz') as archive:
            member=tarfile.TarInfo('repo-commit/mijntv/app.py')
            member.size=4
            archive.addfile(member,io.BytesIO(b'test'))
        updates.unpack_archive(content.getvalue(),self.base/'archive')
        self.assertEqual((self.base/'archive/mijntv/app.py').read_bytes(),b'test')

    def setup_install(self):
        folder=self.base/'source'
        (folder/'deploy/libreelec').mkdir(parents=True)
        for name in ['update.py','run-tv.sh']:
            (folder/'deploy/libreelec'/name).write_text('new code')
        def command(args,timeout=180):
            if 'inspect' in args:
                return json.dumps([{'Config':{'Env':['IPTV_AUDIO_DEVICE=alsa/custom']}}])
            return ''
        self.updater.run=Mock(side_effect=command)
        return folder

    def test_second_guard_prevents_restart_when_recording_appears_during_build(self):
        self.recording('recording',0,9999999999)
        with self.assertRaises(updates.Deferred):
            self.updater.install(self.revision,self.base/'source','image')
        self.updater.run.assert_not_called()

    def test_failed_new_image_restores_container_database_and_old_revision(self):
        folder=self.setup_install()
        self.updater.state={'revision':'b'*40}
        def health():
            if self.updater.healthy.call_count==1:
                self.store.set_setting('migration_marker','new database')
                return False
            return True
        self.updater.healthy=Mock(side_effect=health)
        with patch.object(updates.subprocess,'run'), self.assertRaises(RuntimeError):
            self.updater.install(self.revision,folder,'image')
        calls=[call.args[0] for call in self.updater.run.call_args_list]
        self.assertTrue(any(args[-3:]==['rename','iptv-thuis-tv-previous','iptv-thuis-tv'] for args in calls))
        self.assertEqual(self.store.setting('migration_marker'),'')
        self.assertEqual(self.store.setting('update_lease_until'),'')
        state=json.loads(self.updater.state_path.read_text())
        self.assertEqual(state['revision'],'b'*40)
        self.assertEqual(state['failed_revision'],self.revision)

    def test_healthy_image_saves_revision_and_keeps_private_data(self):
        folder=self.setup_install()
        self.store.set_setting('private_account','local only')
        self.updater.healthy=Mock(return_value=True)
        with patch.object(updates.subprocess,'run') as create:
            self.updater.install(self.revision,folder,'image')
        self.assertEqual(create.call_args.kwargs['env']['IPTV_AUDIO_DEVICE'],'alsa/custom')
        self.assertEqual(create.call_args.kwargs['env']['IPTV_IMAGE'],'image')
        self.assertEqual(json.loads(self.updater.state_path.read_text())['revision'],self.revision)
        self.assertEqual(self.store.setting('private_account'),'local only')
        self.assertEqual(self.store.setting('update_lease_until'),'')
