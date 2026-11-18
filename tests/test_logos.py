import hashlib
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from mijntv.logos import LogoCache
from mijntv.store import Store


class ImageCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = LogoCache(self.temp.name)
        self.addCleanup(self.cache.close)

    def seed(self, url, age):
        path = self.cache.directory/hashlib.sha256(url.encode()).hexdigest()
        path.write_bytes(b'\xff\xd8\xff'+b'X'*77)
        os.utime(path, (time.time()-age, time.time()-age))
        return path

    def test_prune_keeps_recently_viewed_images_and_respects_limit(self):
        old = self.seed('https://test/old', 300)
        medium = self.seed('https://test/medium', 200)
        recent = self.seed('https://test/recent', 100)
        unrelated = self.cache.directory/'unrelated.txt'
        unrelated.write_text('keep')
        self.cache.index()
        self.cache.get('https://test/old')
        self.cache.limit = 180
        self.cache.prune()
        self.assertTrue(old.exists())
        self.assertFalse(medium.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(self.cache.status()['bytes'], 160)

    def test_limit_persists_and_rejects_invalid_values(self):
        store = Store(Path(self.temp.name)/'data')
        cache = LogoCache(self.temp.name, store)
        self.addCleanup(cache.close)
        cache.configure(.5)
        restarted = LogoCache(self.temp.name, store)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.limit, 500_000_000)
        for invalid in (0, -1, 21, True, '2', float('nan'), float('inf')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                cache.configure(invalid)

    def test_images_are_downloaded_once_and_can_be_refetched_after_eviction(self):
        with patch('mijntv.logos.urlopen', side_effect=lambda *a, **k:io.BytesIO(b'\xff\xd8\xffimage')) as request:
            self.cache.get('https://test/image')
            self.cache.get('https://test/image')
            self.assertEqual(request.call_count, 1)
            self.cache.limit = 0
            self.cache.prune()
            self.cache.get('https://test/image')
            self.assertEqual(request.call_count, 2)

    def test_index_ignores_symlinks_and_non_cache_files(self):
        outside = Path(self.temp.name)/'outside'
        outside.write_text('keep')
        (self.cache.directory/('a'*64)).symlink_to(outside)
        self.cache.index()
        self.assertEqual(self.cache.status()['files'], 0)
        self.cache.limit = 0
        self.cache.prune()
        self.assertEqual(outside.read_text(), 'keep')
