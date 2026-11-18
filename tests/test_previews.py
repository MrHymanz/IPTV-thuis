from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mijntv.previews import LivePreviews


class LivePreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=Mock()
        self.store.favorites.return_value=[{'id':'one'},{'id':'two'}]
        self.tv=Mock()
        self.streams=SimpleNamespace(viewer=self.tv,slots={'primary':self.tv},lock=threading.RLock())
        self.previews=LivePreviews(self.store,self.streams)
        self.addCleanup(self.previews.close)
        self.mobile=SimpleNamespace(current='mobile',sessions={'mobile':{'kind':'live','id':'one','state':'playing','output':Path(self.temp.name)}})
        self.previews.mobile=self.mobile

    def test_tv_feed_is_never_used_for_mobile_home(self):
        with self.assertRaises(ValueError):self.previews.get('one')
        self.tv.subscribe.assert_not_called()
        self.assertFalse(self.previews.pending)

    def test_cached_mobile_image_remains_after_player_closes_but_requires_favorite(self):
        self.previews.images['one']=(time.monotonic(),b'jpeg')
        self.mobile.sessions.clear()
        self.assertEqual(self.previews.get('one'),b'jpeg')
        self.store.favorites.return_value=[]
        with self.assertRaises(ValueError):self.previews.get('one')

    def test_pending_requests_capture_only_selected_mobile_channel(self):
        (Path(self.temp.name)/'segment000001.ts').write_bytes(b'video')
        with patch.object(self.previews.pool,'submit') as submit:
            for _ in range(4):
                with self.assertRaises(ValueError):self.previews.get('one')
            self.assertEqual(submit.call_count,1)
            with self.assertRaises(ValueError):self.previews.get('two')
            self.assertEqual(submit.call_count,1)

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required for real snapshot')
    def test_real_mobile_snapshot_uses_existing_segment_bytes(self):
        source=subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=25',
            '-t','1','-threads','1','-c:v','mpeg2video','-f','mpegts','pipe:1'],check=True,capture_output=True).stdout
        self.previews.generate('one',source)
        self.mobile.sessions.clear()
        self.assertTrue(self.previews.get('one').startswith(b'\xff\xd8\xff'))
        self.tv.subscribe.assert_not_called()
