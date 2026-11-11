from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from mijntv.thumbnails import RecordingThumbnails


class ThumbnailTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'local.ts'
        self.path.write_bytes(b'fixture')
        self.recordings = Mock()
        self.recordings.list.return_value = [{'id': 'one', 'playable': True, 'state': 'completed'}]
        self.recordings.playback.return_value = self.path
        self.thumbs = RecordingThumbnails(self.recordings, self.directory.name)
        self.addCleanup(self.thumbs.close)

    def test_background_generation_uses_local_recording_and_cache(self):
        entered, release = threading.Event(), threading.Event()
        def generate(*args, **kwargs):
            entered.set(); release.wait(3)
            return Mock(stdout=b'\xff\xd8\xfffixture')
        with patch('mijntv.thumbnails.shutil.which', return_value='ffmpeg'), patch('mijntv.thumbnails.subprocess.run', side_effect=generate) as run:
            with self.assertRaises(ValueError):
                self.thumbs.get('one')
            self.assertTrue(entered.wait(2))
            with self.assertRaises(ValueError):
                self.thumbs.get('one')
            release.set()
            self.thumbs.pool.shutdown(wait=True)
            self.assertEqual(self.thumbs.get('one'), b'\xff\xd8\xfffixture')
            self.assertEqual(run.call_count, 1)
            self.assertIn(str(self.path), run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs['timeout'], 12)

    def test_active_missing_and_invalid_recordings_are_not_processed(self):
        with self.assertRaises(ValueError):
            self.thumbs.get('../private')
        self.recordings.list.return_value = [{'id': 'one', 'playable': True, 'state': 'recording'}]
        with self.assertRaises(ValueError):
            self.thumbs.get('one')
        self.recordings.playback.assert_not_called()
