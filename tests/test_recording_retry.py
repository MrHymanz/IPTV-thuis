import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from mijntv.store import Store
from mijntv.recordings import Recordings
from mijntv.streams import StreamHub

class RecordingRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name)
        output=Path(self.tmp.name)/'recordings';output.mkdir()
        self.store.set_setting('recording_directory',str(output))
        self.store.import_playlist('#EXTM3U\n#EXTINF:-1 tvg-id="one" group-title="Main",NPO 1\nhttps://example.test/main\n#EXTINF:-1 tvg-id="one" group-title="Backup",NPO 1 HD\nhttps://example.test/backup\n')
        self.channel=self.store.catalog(group='Main')['items'][0]['id'];self.store.add_favorite(self.channel)
        self.hub=StreamHub();self.addCleanup(self.hub.close)
        self.rec=Recordings(self.store,self.hub);self.addCleanup(self.rec.close)
        with patch('mijntv.recordings.shutil.which',return_value='ffmpeg'):
            self.id=self.rec.schedule(self.channel)
        self.now=int(time.time())

    def row(self,identifier):
        with self.store.connect() as d: return dict(d.execute('SELECT * FROM recordings WHERE id=?',(identifier,)).fetchone())

    def process(self):
        process=Mock();process.poll.return_value=None;process.returncode=1
        return process

    def test_disconnect_preserves_file_switches_source_and_resumes_in_new_part(self):
        first=self.process()
        with patch('mijntv.recordings.subprocess.Popen',return_value=first): self.rec.step(self.now)
        original=self.rec.file(self.row(self.id));original.write_bytes(b'original recording')
        token=self.hub.slots['primary'].token
        self.hub.slots['primary'].close('eof')
        first.poll.return_value=1
        self.rec.step(self.now+1)
        self.assertEqual(self.row(self.id)['state'],'interrupted')
        self.assertEqual(original.read_bytes(),b'original recording')
        self.assertEqual(self.rec.required_channel()['id'],self.channel)
        second_id=self.rec.retry_recording['id'];second_row=self.row(second_id)
        self.assertIn('(deel 2)',second_row['title'])
        self.assertEqual(second_row['end'],self.row(self.id)['end'])
        second=self.process()
        with patch('mijntv.recordings.subprocess.Popen',return_value=second): self.rec.step(self.now+4)
        self.assertEqual(self.row(second_id)['state'],'recording')
        self.assertNotEqual(self.hub.slots['primary'].token,token)
        self.assertTrue(self.hub.slots['primary'].source['url'].endswith('/backup'))
        self.assertNotEqual(original,self.rec.file(second_row))
        self.rec.stop_for_zap();second.poll.return_value=0;self.rec.step(self.now+5)
        self.assertEqual(self.row(second_id)['state'],'cancelled')
        self.assertIsNone(self.rec.required_channel())

    def test_waiting_continuation_can_be_cancelled_and_retry_budget_is_bounded(self):
        row=self.row(self.id)
        self.rec.update(self.id,'failed')
        for attempt in range(5):
            self.assertTrue(self.rec.queue_continuation(row,self.now+attempt))
            identifier=self.rec.retry_recording['id']
            self.rec.stop_for_zap()
            self.assertEqual(self.row(identifier)['state'],'cancelled')
            self.assertIsNone(self.rec.required_channel())
        self.assertFalse(self.rec.queue_continuation(row,self.now+6))

    def test_explicit_stop_and_disk_failure_never_auto_resume(self):
        process=self.process()
        with patch('mijntv.recordings.subprocess.Popen',return_value=process):self.rec.step(self.now)
        self.rec.stop_process('failed','Opnameschijf bijna vol.')
        process.poll.return_value=1
        self.rec.step(self.now+1)
        self.assertEqual(self.row(self.id)['state'],'failed')
        self.assertIsNone(self.rec.retry_recording)
        self.assertIsNone(self.rec.required_channel())
        self.assertEqual(len(self.rec.list()),1)

    def test_manual_resume_preserves_original_and_rejects_duplicates_or_expired_programme(self):
        self.rec.update(self.id,'failed')
        identifier=self.rec.resume(self.id)
        self.assertNotEqual(identifier,self.id)
        with self.assertRaises(ValueError):self.rec.resume(self.id)
        self.rec.cancel(identifier)
        with self.store.connect() as db:
            db.execute('UPDATE recordings SET end=? WHERE id=?',(self.now-1,self.id))
        with self.assertRaises(ValueError):self.rec.resume(self.id)
