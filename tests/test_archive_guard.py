import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mijntv.recordings import Recordings
from mijntv.store import Store


class ArchiveGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store=Store(self.directory.name)
        self.store.import_playlist('#EXTM3U\n#EXTINF:-1 tvg-id="one.nl",NPO 1\nhttps://example.test/live.ts')
        self.channel=self.store.catalog()['items'][0]['id']
        self.store.add_favorite(self.channel)
        self.streams=Mock();self.streams.required_channel.return_value=None
        self.recordings=Recordings(self.store,self.streams)

    def test_running_primary_recording_rejects_archive_without_stopping_it(self):
        self.streams.required_channel.return_value={'id':self.channel,'label':'NPO 1'}
        callback=Mock()
        with self.assertRaisesRegex(ValueError,'opname'):
            self.recordings.reserve_archive(callback)
        callback.assert_not_called()
        self.streams.stop_view.assert_not_called()
        self.assertIsNone(self.recordings.archive_stop)

    def test_scheduled_primary_recording_releases_archive_before_connecting(self):
        order=[]
        callback=Mock(side_effect=lambda:order.append('stop archive'))
        self.recordings.reserve_archive(callback)
        self.streams.acquire.side_effect=lambda *args,**kwargs:order.append('connect recorder') or Mock()
        self.streams.url.return_value='http://127.0.0.1:9000/stream/local-token'
        now=int(time.time())
        with self.store.connect() as db:
            db.execute('INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?)',
                       ('planned',self.channel,'NPO 1','Programma',now,now+1800,'queued',self.directory.name,''))
            row=db.execute('SELECT * FROM recordings WHERE id=?',('planned',)).fetchone()
        with patch('mijntv.recordings.subprocess.Popen',side_effect=lambda *args,**kwargs:order.append('spawn recorder') or Mock()):
            self.recordings.start(row)
        self.assertEqual(order,['stop archive','connect recorder','spawn recorder'])
        self.assertIsNone(self.recordings.archive_stop)
        self.assertEqual(self.recordings.list()[0]['state'],'recording')
        Path(self.recordings.manifest).unlink(missing_ok=True)

    def test_leaving_archive_clears_reservation(self):
        self.recordings.reserve_archive(Mock())
        self.recordings.release_archive()
        self.assertIsNone(self.recordings.archive_stop)


if __name__=='__main__':unittest.main()
