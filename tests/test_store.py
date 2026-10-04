import tempfile
import io
import threading
import time
import unittest
from unittest.mock import patch

from mijntv.store import Store, parse_m3u, xtream_playlist_url
from urllib.parse import parse_qs, urlsplit


def playlist(token='old'):
    return '#EXTM3U\n#EXTINF:-1 tvg-id="one" group-title="NL, HD",NPO 1\nhttps://example.test/' + token + '/1\n#EXTINF:-1 tvg-id="two" group-title="NL",NPO 2\nhttps://example.test/' + token + '/2\n'


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)

    def test_refresh_preserves_renamed_favorites_and_updates_tokens(self):
        self.store.import_playlist(playlist(), 'https://example.test/list')
        rows = self.store.catalog()['items']
        self.store.add_favorite(rows[0]['id'])
        self.store.add_favorite(rows[1]['id'])
        self.store.rename(rows[0]['id'], 'Nederland 1')
        self.store.reorder([rows[1]['id'], rows[0]['id']])
        self.store.import_playlist(playlist('new'), 'https://example.test/list')
        favorites = self.store.favorites(playback=True)
        self.assertEqual([f['label'] for f in favorites], ['NPO 2', 'Nederland 1'])
        self.assertTrue(all('/new/' in f['url'] for f in favorites))
        self.assertNotIn('url', self.store.favorites()[0])

    def test_missing_favorite_returns_when_channel_returns(self):
        self.store.import_playlist(playlist())
        row = self.store.catalog()['items'][0]
        self.store.add_favorite(row['id'])
        self.store.import_playlist('#EXTM3U\n#EXTINF:-1,Other\nhttps://example.test/other')
        self.assertFalse(self.store.favorites()[0]['available'])
        self.store.import_playlist(playlist('new'))
        self.assertTrue(self.store.favorites()[0]['available'])

    def test_bad_import_preserves_catalog_source_and_manual_channel(self):
        self.store.import_playlist(playlist(), 'https://example.test/list')
        self.store.manual('Eigen zender', 'https://example.test/manual')
        with self.assertRaises(ValueError):
            self.store.import_playlist('not a playlist')
        self.assertEqual(self.store.catalog()['total'], 3)
        self.assertEqual(self.store.setting('source'), 'https://example.test/list')
        self.store.import_playlist(playlist('new'))
        self.assertEqual(self.store.catalog()['total'], 3)

    def test_duplicate_identity_and_quoted_commas(self):
        text = playlist() + '#EXTINF:-1 tvg-id="one" group-title="NL, HD",NPO 1\nhttps://example.test/duplicate'
        rows = parse_m3u('\ufeff' + text)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][2], 'NL, HD')
        self.assertEqual(len({r[0] for r in rows}), 3)

    def test_50000_channels_pagination_and_search(self):
        text = '#EXTM3U\n' + ''.join(f'#EXTINF:-1 tvg-id="{i}" group-title="NL",Zender {i:05d}\nhttps://example.test/{i}\n' for i in range(50000))
        started = time.monotonic()
        self.assertEqual(self.store.import_playlist(text), 50000)
        self.assertEqual(self.store.catalog(offset=49900)['total'], 50000)
        self.assertEqual(len(self.store.catalog(offset=49900)['items']), 100)
        self.assertEqual(self.store.catalog(search='Zender 12345')['total'], 1)
        print(f'\n50.000 zenders importeren en doorzoeken: {time.monotonic()-started:.2f}s')

    def test_literal_search_and_invalid_order(self):
        self.store.import_playlist(playlist())
        self.assertEqual(self.store.catalog(search='%')['total'], 0)
        row = self.store.catalog()['items'][0]
        self.store.add_favorite(row['id'])
        with self.assertRaises(ValueError):
            self.store.reorder([])
        with self.assertRaises(ValueError):
            self.store.manual('Test', 'file:///etc/passwd')

    def test_refresh_failure_and_concurrent_source_change(self):
        self.store.import_playlist(playlist(), 'https://example.test/old')
        self.store.add_favorite(self.store.catalog()['items'][0]['id'])
        with patch('mijntv.store.fetch_playlist', side_effect=ValueError('Netwerkfout')):
            with self.assertRaises(ValueError):
                self.store.refresh_source()
        self.assertEqual(self.store.catalog()['total'], 2)
        def replace_source(source, directory=None):
            self.store.import_playlist(playlist('fresh'), 'https://example.test/new')
            return io.StringIO(playlist('outdated'))
        with patch('mijntv.store.fetch_playlist', side_effect=replace_source):
            with self.assertRaises(ValueError):
                self.store.refresh_source()
        self.assertEqual(self.store.setting('source'), 'https://example.test/new')
        self.assertIn('/fresh/', self.store.favorites(playback=True)[0]['url'])

    def test_xtream_special_characters_and_server_paths(self):
        for base in ('https://example.test:8443/portal/', 'https://example.test:8443/portal/get.php',
                     'https://example.test:8443/portal/player_api.php'):
            parsed = urlsplit(xtream_playlist_url(base, 'user+&é', ' pass?#&+ '))
            self.assertEqual(parsed.path, '/portal/get.php')
            self.assertEqual(parse_qs(parsed.query), {'username':['user+&é'], 'password':[' pass?#&+ '],
                                                     'type':['m3u_plus'], 'output':['ts']})

    def test_xtream_rejects_invalid_input_without_exposing_secrets(self):
        for base in ('file:///etc/passwd', 'https://user:secret@example.test',
                     'https://example.test?password=secret', 'https://example.test:bad', 'https://[bad'):
            with self.assertRaises(ValueError) as error:
                xtream_playlist_url(base, 'user', 'secret')
            self.assertNotIn('secret', str(error.exception))
        for values in (('https://example.test','','secret'),('https://example.test','user',''),
                       ('https://example.test','user',None)):
            with self.assertRaises(ValueError):
                xtream_playlist_url(*values)

    def test_favorites_remain_writable_during_background_import(self):
        self.store.import_playlist(playlist())
        ids = [row['id'] for row in self.store.catalog()['items']]
        for channel_id in ids:
            self.store.add_favorite(channel_id)
        staging_ready, proceed = threading.Event(), threading.Event()
        errors = []
        def slow_playlist():
            # Pause after the first batch was inserted, as a large parser would.
            for i in range(1000):
                yield f'#EXTINF:-1,Nieuw {i}\n'
                yield f'https://example.test/{i}\n'
            staging_ready.set()
            if not proceed.wait(10):
                raise RuntimeError('Test timeout')
            yield from playlist('fresh').splitlines(keepends=True)
        def import_list():
            try:
                self.store.import_playlist(slow_playlist(),'https://example.test/source')
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=import_list)
        thread.start()
        try:
            self.assertTrue(staging_ready.wait(5))
            self.store.reorder(ids[::-1])
            self.store.rename(ids[0],'Nieuwe naam')
            self.assertEqual(self.store.catalog()['total'],2, 'Oude catalogus blijft tijdens import actief')
            self.assertEqual([f['id'] for f in self.store.favorites()],ids[::-1])
        finally:
            proceed.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[])
        self.assertEqual(self.store.catalog()['total'],1002)
        self.assertEqual([f['id'] for f in self.store.favorites()],ids[::-1])
        self.assertEqual(self.store.favorites()[1]['label'],'Nieuwe naam')


if __name__ == '__main__':
    unittest.main()
