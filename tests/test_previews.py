import queue
import shutil
import subprocess
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mijntv.previews import LivePreviews


class LivePreviewTests(unittest.TestCase):
    def setUp(self):
        self.feed = SimpleNamespace(channel={'id':'one'}, token='private-feed-token', closed=False,
                                    started=True, last_data=time.monotonic(), lock=threading.RLock(),
                                    subscribe=Mock(), unsubscribe=Mock())
        self.store = Mock()
        self.store.favorites.return_value = [{'id':'one'}, {'id':'two'}]
        self.streams = SimpleNamespace(viewer=self.feed, slots={'primary':self.feed}, lock=threading.RLock())
        self.previews = LivePreviews(self.store, self.streams)
        self.addCleanup(self.previews.close)

    def test_preview_never_opens_an_idle_or_other_channel(self):
        with self.assertRaises(ValueError):
            self.previews.get('two')
        self.feed.started = False
        with self.assertRaises(ValueError):
            self.previews.get('one')
        self.assertEqual(self.previews.current_channel(), '')
        self.feed.subscribe.assert_not_called()

    def test_cache_remains_bound_to_feed_and_current_favorites(self):
        key = ('one', self.feed.token)
        self.previews.images[key] = (time.monotonic(), b'jpeg')
        self.assertEqual(self.previews.get('one'), b'jpeg')
        self.store.favorites.return_value = []
        with self.assertRaises(ValueError):
            self.previews.get('one')
        self.feed.subscribe.assert_not_called()

    def test_pending_requests_share_one_snapshot(self):
        with patch.object(self.previews.pool, 'submit') as submit:
            for _ in range(4):
                with self.assertRaises(ValueError):
                    self.previews.get('one')
            self.assertEqual(submit.call_count, 1)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required for real snapshot')
    def test_real_snapshot_uses_only_buffered_stream_and_releases_consumer(self):
        source = subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=25',
                                 '-t','1','-threads','1','-c:v','mpeg2video','-f','mpegts','pipe:1'],
                                check=True, capture_output=True).stdout
        consumer = queue.Queue()
        consumer.put(source)
        consumer.put(None)
        self.feed.subscribe.return_value = consumer
        key = ('one', self.feed.token)
        self.previews.pending.add(key)
        self.previews.generate(key, self.feed)
        self.assertTrue(self.previews.get('one').startswith(b'\xff\xd8\xff'))
        self.feed.unsubscribe.assert_called_once_with(consumer)
        self.assertNotIn(key, self.previews.pending)
