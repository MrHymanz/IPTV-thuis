import json
import tempfile
import unittest
from unittest.mock import patch

from mijntv.catchup import Catchup, ProviderRequestError
from mijntv.store import Store
from mijntv.vod import VOD, duration

SOURCE = 'https://provider.test/panel/get.php?username=viewer%26name&password=secret%2Bpass'


class VODTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.store.set_setting('source', SOURCE)
        self.provider = Catchup(self.store)
        self.vod = VOD(self.store, self.provider)
        self.movies = [{'stream_id': 7, 'name': 'Een film', 'category_id': '1',
                        'stream_icon': 'https://private-cover.test/secret+pass.jpg',
                        'container_extension': 'mkv', 'rating': '8.1',
                        'direct_source': 'https://private-stream.test/secret+pass'}]
        self.series = [{'series_id': 7, 'name': 'Een serie', 'category_id': '1',
                        'cover': 'https://private-cover.test/series.jpg', 'releaseDate': '2026-01-01'}]
        self.movie_info = {'info': {'plot': '<p>Een verhaal.</p>', 'duration': '01:48:00', 'genre': 'Drama'},
                           'movie_data': {'container_extension': 'mkv', 'direct_source': 'https://private.test/secret'}}
        self.series_info = {'info': {'plot': 'Een serieverhaal.'}, 'episodes': {
            '2': [{'id': '202', 'episode_num': 1, 'season': 2, 'title': 'Terugkeer', 'container_extension': 'mp4',
                   'info': {'duration_secs': 2400, 'movie_image': 'https://private.test/episode.jpg'}}],
            '1': [{'id': '101', 'episode_num': 2, 'season': 1, 'title': 'Vervolg', 'container_extension': 'mkv'},
                  {'id': '100', 'episode_num': 1, 'season': 1, 'title': 'Begin', 'info': {'duration': '00:42:00'},
                   'direct_source': 'https://private.test/secret'}]}}
        self.calls = []
        self.patcher = patch.object(self.provider, 'request', side_effect=self.request)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def request(self, configuration, action, **params):
        self.calls.append((action, params))
        return {'get_vod_categories': [{'category_id': '1', 'category_name': 'HBO Films'}],
                'get_series_categories': [{'category_id': '1', 'category_name': 'HBO Series'}],
                'get_vod_streams': self.movies, 'get_series': self.series,
                'get_vod_info': self.movie_info, 'get_series_info': self.series_info}[action]

    def catalog(self, kind='movie', **kwargs):
        return self.vod.catalog(kind, **kwargs)

    def test_same_category_and_title_ids_stay_separate_for_films_and_series(self):
        movies, series = self.catalog(), self.catalog('series')
        self.assertEqual(movies['categories'], [{'id': '1', 'name': 'HBO Films'}])
        self.assertEqual(series['categories'], [{'id': '1', 'name': 'HBO Series'}])
        self.assertEqual(movies['items'][0]['title'], 'Een film')
        self.assertEqual(series['items'][0]['title'], 'Een serie')
        self.assertEqual(self.catalog(category='not-present')['total'], 0)
        self.assertEqual(self.catalog(query='FILM')['total'], 1)
        self.assertEqual(len(self.calls), 4)

    def test_catalog_and_details_only_expose_local_cover_urls(self):
        result = self.catalog()
        self.assertTrue(result['items'][0]['cover'].startswith('/api/mobile/vod/cover?'))
        detail = self.vod.detail('movie', '7')
        self.assertEqual(detail['duration'], 6480)
        self.assertEqual(detail['description'], 'Een verhaal.')
        public = json.dumps([result, detail])
        for secret in ('private-cover.test', 'private-stream.test', 'secret', 'direct_source', 'viewer'):
            self.assertNotIn(secret, public)
        self.assertEqual(self.vod.cover_url('movie', '7'), self.movies[0]['stream_icon'])
        self.vod.detail('movie', '7')
        self.assertEqual(len(self.calls), 3)

    def test_series_seasons_are_sorted_and_episode_urls_are_assembled_on_server(self):
        result = self.catalog('series')
        detail = self.vod.detail('series', '7')
        self.assertEqual([(e['season'], e['number']) for e in detail['episodes']], [(1, 1), (1, 2), (2, 1)])
        public = json.dumps(detail)
        self.assertNotIn('direct_source', public)
        self.assertNotIn('private.test', public)
        playback = self.vod.playback('episode', '100', result['signature'])
        self.assertEqual(playback['url'], 'https://provider.test/panel/series/viewer%26name/secret%2Bpass/100.mp4')
        self.assertEqual(playback['duration'], 2520)
        self.assertEqual(playback['label'], 'Een serie')
        with self.assertRaises(ValueError):
            self.vod.playback('episode', '999', result['signature'])
        with self.assertRaises(ValueError):
            self.vod.playback('series', '7', result['signature'])

    def test_movie_playback_preserves_actual_container_and_ignores_direct_source(self):
        result = self.catalog()
        item = self.vod.playback('movie', '7', result['signature'])
        self.assertEqual(item['url'], 'https://provider.test/panel/movie/viewer%26name/secret%2Bpass/7.mkv')
        self.assertEqual(item['duration'], 6480)

    def test_source_change_never_reuses_old_ids_covers_or_details(self):
        result = self.catalog()
        self.vod.detail('movie', '7')
        self.store.set_setting('source', SOURCE.replace('secret%2Bpass', 'new-pass'))
        with self.assertRaisesRegex(ValueError, 'gewijzigd'):
            self.vod.playback('movie', '7', result['signature'])
        with self.assertRaises(ValueError):
            self.vod.cover_url('movie', '7')
        with self.assertRaises(ValueError):
            self.vod.detail('movie', '7')

    def test_changed_configuration_during_fetch_does_not_publish_old_metadata(self):
        def changed(configuration, action, **params):
            result = self.request(configuration, action, **params)
            self.store.set_setting('source', SOURCE.replace('secret%2Bpass', 'new-pass'))
            return result
        self.provider.request.side_effect = changed
        with self.assertRaisesRegex(ValueError, 'gewijzigd'):
            self.catalog()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM vod_items').fetchone()[0], 0)

    def test_failed_refresh_retains_cached_catalog_and_backs_off(self):
        self.catalog()
        with self.store.connect() as db:
            db.execute('UPDATE vod_meta SET updated=0')
        self.provider.request.side_effect = ValueError('Provider niet bereikbaar.')
        result = self.catalog()
        self.assertEqual(result['items'][0]['title'], 'Een film')
        self.assertIn('eerder', result['notice'])
        before = len(self.provider.request.call_args_list)
        self.catalog()
        self.assertEqual(len(self.provider.request.call_args_list), before)

    def test_empty_catalog_is_valid_but_broken_catalog_does_not_erase_cache(self):
        self.movies = []
        self.assertEqual(self.catalog()['total'], 0)
        with self.store.connect() as db:
            db.execute('UPDATE vod_meta SET updated=0')
        self.movies = [{'invalid': True}]
        self.assertIn('eerder', self.catalog()['notice'])

    def test_pagination_search_and_category_filter_work_on_large_catalog(self):
        self.movies = [dict(self.movies[0], stream_id=i, name=f'Film {i:04}', category_id=str(i % 2)) for i in range(135)]
        first, second = self.catalog(), self.catalog(offset=60)
        self.assertEqual(first['total'], 135)
        self.assertEqual(len(first['items']), 60)
        self.assertFalse({i['id'] for i in first['items']} & {i['id'] for i in second['items']})
        self.assertEqual(self.catalog(category='1')['total'], 67)
        self.assertEqual(self.catalog(query='0134')['items'][0]['title'], 'Film 0134')

    def test_missing_xtream_and_invalid_ids_have_clear_errors(self):
        with self.assertRaises(ValueError):
            self.vod.catalog('live')
        self.catalog()
        for key in ('../x', True, '-1', 'https://example.test'):
            with self.assertRaises(ValueError):
                self.vod.detail('movie', key)
        self.store.set_setting('source', 'https://provider.test/playlist.m3u')
        with self.assertRaisesRegex(ValueError, 'Xtream'):
            self.catalog()

    def test_details_failure_retains_previous_details(self):
        self.catalog()
        self.vod.detail('movie', '7')
        with self.store.connect() as db:
            db.execute('UPDATE vod_details SET updated=0')
        self.provider.request.side_effect = ValueError('Provider niet bereikbaar.')
        detail = self.vod.detail('movie', '7')
        self.assertEqual(detail['description'], 'Een verhaal.')
        self.assertIn('eerder', detail['notice'])

    def test_bad_covers_and_extensions_are_not_used_as_urls_or_manifest_lines(self):
        self.movies[0].update(stream_icon='javascript:alert(1)', container_extension="mp4\nfile '/etc/passwd'")
        self.catalog()
        self.assertEqual(self.vod.cover_url('movie', '7'), '')
        self.movie_info['movie_data']['container_extension'] = '../bad'
        signature = self.catalog()['signature']
        self.assertTrue(self.vod.playback('movie', '7', signature)['url'].endswith('/7.mp4'))
        self.assertEqual(duration({'duration': 'broken'}), 0)
        self.assertEqual(duration({'duration_secs': 'nan'}), 0)
        self.assertEqual(duration({'duration': '42:00'}), 2520)

    def test_large_or_slow_full_catalog_falls_back_to_individual_categories(self):
        self.movies.append(dict(self.movies[0], stream_id=8, name='Andere film', category_id='2'))
        def response(configuration, action, **params):
            if action == 'get_vod_categories':
                return [{'category_id': '1', 'category_name': 'HBO'}, {'category_id': '2', 'category_name': 'Netflix'}]
            if action == 'get_vod_streams':
                if not params.get('category_id'):
                    raise ProviderRequestError('too_large', 'De providerlijst is te groot.')
                return [i for i in self.movies if i['category_id'] == params['category_id']]
            return self.request(configuration, action, **params)
        self.provider.request.side_effect = response
        initial = self.catalog()
        self.assertTrue(initial['requires_category'])
        self.assertEqual(len(initial['categories']), 2)
        self.assertEqual(initial['items'], [])
        calls = self.provider.request.call_count
        self.assertTrue(self.catalog()['requires_category'])
        self.assertEqual(self.provider.request.call_count, calls)
        first = self.catalog(category='1')
        self.assertFalse(first['requires_category'])
        self.assertEqual([i['title'] for i in first['items']], ['Een film'])
        self.provider.request.assert_called_with(self.vod.configuration(), 'get_vod_streams', category_id='1')
        second = self.catalog(category='2')
        self.assertEqual([i['title'] for i in second['items']], ['Andere film'])
        calls = self.provider.request.call_count
        self.assertEqual(self.catalog(category='1')['total'], 1)
        self.assertEqual(self.provider.request.call_count, calls)
        self.assertEqual(self.catalog('series')['items'][0]['title'], 'Een serie')

    def test_category_failure_is_reported_without_creating_a_successful_empty_cache(self):
        def response(configuration, action, **params):
            if action == 'get_vod_streams':
                raise ProviderRequestError('timeout', 'De provider reageerde niet binnen twintig seconden.')
            return self.request(configuration, action, **params)
        self.provider.request.side_effect = response
        self.assertTrue(self.catalog()['requires_category'])
        with self.assertRaisesRegex(ValueError, 'twintig seconden'):
            self.catalog(category='1')
        with self.store.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM vod_meta WHERE kind='movie:category:1'").fetchone())

    def test_category_fetch_does_not_accept_unrelated_provider_items(self):
        self.movies.append(dict(self.movies[0], stream_id=8, name='Andere film', category_id='2'))
        result = self.catalog(category='1')
        self.assertEqual([i['id'] for i in result['items']], ['7'])
