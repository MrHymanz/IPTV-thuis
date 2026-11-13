import io
import hashlib
import json
import tempfile
import unittest
import time
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from mijntv.artwork import Artwork, image_url
from mijntv.store import Store

KEY = 'a'*32


class ArtworkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.vod = Mock()
        self.item = {'kind': 'movie', 'title': 'NL| A Film (2020) HD', 'year': '2020', 'tmdb_id': '42', 'imdb_id': ''}
        self.vod.artwork_metadata.return_value = (('provider-url', {}, 'provider-signature'), self.item)
        self.logos = Mock()
        self.logos.get.return_value = (b'fixture', 'image/jpeg')
        self.art = Artwork(self.store, self.vod, self.logos)
        self.art.configure(KEY)

    def test_direct_tmdb_identity_downloads_poster_and_backdrop_and_caches_one_lookup(self):
        with patch.object(self.art, 'request', return_value={'poster_path': '/poster.jpg', 'backdrop_path': '/backdrop.jpg'}) as request:
            self.assertEqual(self.art.fallback('movie', '7'), (b'fixture', 'image/jpeg'))
            self.art.fallback('movie', '7', 'backdrop')
            self.assertEqual(request.call_count, 1)
            request.assert_called_once_with('movie/42', KEY, language='nl-NL')
        self.assertEqual(self.logos.get.call_args_list[0].args, ('https://image.tmdb.org/t/p/w500/poster.jpg',))
        self.assertEqual(self.logos.get.call_args_list[1].args, ('https://image.tmdb.org/t/p/w780/backdrop.jpg',))

    def test_series_imdb_lookup_uses_tv_results_not_an_identical_movie(self):
        self.item.update(kind='series', tmdb_id='', imdb_id='tt0000001')
        with patch.object(self.art, 'request', return_value={'movie_results': [{'poster_path': '/wrong.jpg'}], 'tv_results': [{'poster_path': '/correct.jpg'}]}) as request:
            self.art.fallback('series', '7')
        request.assert_called_once_with('find/tt0000001', KEY, external_source='imdb_id', language='nl-NL')
        self.logos.get.assert_called_once_with('https://image.tmdb.org/t/p/w500/correct.jpg')

    def test_title_search_matches_name_and_year_instead_of_first_popular_result(self):
        self.item.update(tmdb_id='')
        data = {'results': [{'title': 'Different Film', 'release_date': '2020-01-01', 'poster_path': '/wrong.jpg'},
                            {'title': 'A Film', 'release_date': '1990-01-01', 'poster_path': '/old.jpg'},
                            {'title': 'A Film', 'release_date': '2020-01-01', 'poster_path': '/correct.jpg'}]}
        with patch.object(self.art, 'request', return_value=data) as request:
            self.art.fallback('movie', '7')
        self.assertEqual(request.call_args.kwargs['query'], 'A Film')
        self.assertEqual(request.call_args.kwargs['primary_release_year'], '2020')
        self.logos.get.assert_called_once_with('https://image.tmdb.org/t/p/w500/correct.jpg')

    def test_provider_hyphen_prefix_and_4k_label_are_removed_for_real_world_titles(self):
        self.item.update(title='NL - THE DARK KNIGHT 4K (2008)', year='', tmdb_id='')
        response = {'results': [{'title': 'The Dark Knight', 'release_date': '2008-07-16', 'poster_path': '/correct.jpg'}]}
        with patch.object(self.art, 'request', return_value=response) as request:
            self.art.fallback('movie', '7')
        self.assertEqual(request.call_args.kwargs['query'], 'THE DARK KNIGHT')
        self.assertEqual(request.call_args.kwargs['primary_release_year'], '2008')
        self.logos.get.assert_called_once_with('https://image.tmdb.org/t/p/w500/correct.jpg')

    def test_failed_search_from_before_prefix_fix_does_not_hide_new_matches(self):
        identity = ['provider-signature', 'movie', '7', self.item['title'], self.item['year'], self.item['tmdb_id'], self.item['imdb_id'], KEY]
        old_key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        with self.store.connect() as db:
            db.execute('INSERT INTO tmdb_artwork VALUES (?,?,?)', (old_key, '{}', time.time()+86400))
        with patch.object(self.art, 'request', return_value={'poster_path': '/correct.jpg'}) as request:
            self.art.fallback('movie', '7')
            request.assert_called_once()
        self.logos.get.assert_called_once_with('https://image.tmdb.org/t/p/w500/correct.jpg')

    def test_international_title_uses_provider_detail_identity_when_search_cannot_match(self):
        self.item.update(title='NL - Your Mother Should Know', year='', tmdb_id='')
        enriched = dict(self.item, tmdb_id='42')
        configuration = ('provider-url', {}, 'provider-signature')
        self.vod.artwork_metadata.side_effect = [(configuration, self.item), (configuration, enriched), (configuration, enriched)]
        with patch.object(self.art, 'request', side_effect=[{'results': []}, {'poster_path': '/matchmaker.jpg'}]) as request:
            self.art.fallback('movie', '7')
        self.vod.detail.assert_called_once_with('movie', '7')
        self.assertEqual(request.call_args_list[1].args[0], 'movie/42')
        self.logos.get.assert_called_once_with('https://image.tmdb.org/t/p/w500/matchmaker.jpg')

    def test_ambiguous_matches_and_missing_images_are_negatively_cached(self):
        self.item.update(title='A Film', year='', tmdb_id='')
        data = {'results': [{'title': 'A Film', 'release_date': '1990', 'poster_path': '/old.jpg'},
                            {'title': 'A Film', 'release_date': '2020', 'poster_path': '/new.jpg'}]}
        with patch.object(self.art, 'request', return_value=data) as request:
            for asset in ('poster', 'backdrop'):
                with self.assertRaises(ValueError):
                    self.art.fallback('movie', '7', asset)
            self.assertEqual(request.call_count, 1)
        self.logos.get.assert_not_called()

    def test_disabled_or_hidden_items_never_call_tmdb(self):
        self.art.configure('')
        with patch.object(self.art, 'request') as request:
            with self.assertRaises(ValueError):
                self.art.fallback('movie', '7')
            request.assert_not_called()
        self.art.configure(KEY)
        self.vod.artwork_metadata.side_effect = ValueError('Verborgen categorie')
        with patch.object(self.art, 'request') as request:
            with self.assertRaises(ValueError):
                self.art.fallback('movie', '7')
            request.assert_not_called()

    def test_status_never_returns_key_and_key_changes_invalidate_metadata_cache(self):
        self.assertNotIn(KEY, json.dumps(self.art.status()))
        with patch.object(self.art, 'request', return_value={'poster_path': '/one.jpg'}) as request:
            self.art.fallback('movie', '7')
            self.art.configure('b'*32)
            self.art.fallback('movie', '7')
            self.assertEqual(request.call_count, 2)
        with self.assertRaises(ValueError):
            self.art.configure('bad\nheader')

    def test_http_errors_hide_credentials_and_apply_global_backoff(self):
        failure = HTTPError('https://private.test/'+KEY, 401, KEY, {}, None)
        with patch('mijntv.artwork.urlopen', side_effect=failure) as request:
            for _ in range(2):
                with self.assertRaises(ValueError) as caught:
                    self.art.request('configuration', KEY)
                self.assertNotIn(KEY, str(caught.exception))
            self.assertEqual(request.call_count, 1)
        self.assertNotIn(KEY, json.dumps(self.art.status()))

    def test_bearer_token_is_only_in_authorization_header(self):
        key = 'eyJ.'+'x'*40+'.signature'
        self.art.configure(key)
        with patch('mijntv.artwork.urlopen', return_value=io.BytesIO(b'{}')) as request:
            self.art.request('configuration', key)
        req = request.call_args.args[0]
        self.assertEqual(req.get_header('Authorization'), 'Bearer '+key)
        self.assertNotIn(key, req.full_url)

    def test_api_key_request_contains_only_public_title_identity(self):
        with patch('mijntv.artwork.urlopen', return_value=io.BytesIO(b'{"results":[]}')) as request:
            self.art.request('search/movie', KEY, query='A Film', primary_release_year='2020')
        req = request.call_args.args[0]
        parsed = urlsplit(req.full_url)
        self.assertEqual(parsed.hostname, 'api.themoviedb.org')
        self.assertEqual(set(parse_qs(parsed.query)), {'api_key', 'query', 'primary_release_year'})
        self.assertEqual(request.call_args.kwargs['timeout'], 8)

    def test_external_image_paths_and_changed_configuration_are_rejected(self):
        self.assertEqual(image_url('https://evil.test/one.jpg', 'poster'), '')
        self.assertEqual(image_url('/../private.jpg', 'poster'), '')
        self.assertEqual(image_url('/one.svg', 'poster'), '')
        def changed(*args, **kwargs):
            self.store.set_setting('tmdb_key', 'b'*32)
            return {'poster_path': '/one.jpg'}
        with patch.object(self.art, 'request', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'gewijzigd'):
                self.art.fallback('movie', '7')
        self.logos.get.assert_not_called()


class ArtworkWarmingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.vod, self.logos = Mock(), Mock()
        self.vod.configuration.return_value = ('provider', {}, 'signature')
        self.vod.cover_url.return_value = 'https://provider.test/broken.jpg'
        self.art = Artwork(self.store, self.vod, self.logos)

    def test_working_alternative_survives_restart_without_retrying_broken_provider(self):
        self.logos.get.side_effect = ValueError('offline')
        with patch.object(self.art, 'fallback', return_value=(b'jpeg', 'image/jpeg')):
            self.assertEqual(self.art.get('movie', '7'), (b'jpeg', 'image/jpeg'))
        restarted = Artwork(self.store, self.vod, self.logos)
        self.logos.get.reset_mock()
        with patch.object(restarted, 'fallback', return_value=(b'jpeg', 'image/jpeg')):
            self.assertEqual(restarted.get('movie', '7'), (b'jpeg', 'image/jpeg'))
        self.logos.get.assert_not_called()
        # Access restrictions are checked even for a previously warmed image.
        self.vod.cover_url.side_effect = ValueError('hidden')
        with self.assertRaises(ValueError):
            restarted.get('movie', '7')

    def test_changed_provider_image_is_tried_again(self):
        self.logos.get.side_effect = ValueError('offline')
        with patch.object(self.art, 'fallback', return_value=(b'jpeg', 'image/jpeg')):
            self.art.get('movie', '7')
        self.vod.cover_url.return_value = 'https://provider.test/new.jpg'
        self.logos.get.side_effect = None
        self.logos.get.return_value = (b'new', 'image/jpeg')
        self.assertEqual(self.art.get('movie', '7'), (b'new', 'image/jpeg'))

    def test_queue_prioritizes_carousel_deduplicates_and_backs_off_failures(self):
        from mijntv.artwork import ArtworkWarmer
        self.vod.artwork_candidates.return_value = [
            ('signature', 'movie', '1', 'poster', 5),
            ('signature', 'series', '2', 'backdrop', 0),
            ('signature', 'series', '2', 'backdrop', 0)]
        with patch('mijntv.artwork.threading.Thread.start'):
            warmer = ArtworkWarmer(self.art)
        self.addCleanup(warmer.close)
        warmer.fill()
        warmer.fill()
        self.assertEqual(warmer.tasks.qsize(), 2)
        processed = []
        def fail(kind, identifier, asset):
            processed.append((kind, identifier, asset))
            warmer.close()
            raise ValueError('offline')
        with patch.object(self.art, 'get', side_effect=fail):
            warmer.run()
        self.assertEqual(processed, [('series', '2', 'backdrop')])
        self.assertGreater(warmer.retry[('signature', 'series', '2', 'backdrop')], time.monotonic()+290)

    def test_queue_is_bounded(self):
        from mijntv.artwork import ArtworkWarmer
        self.vod.artwork_candidates.return_value = [('signature', 'movie', str(i), 'poster', 5) for i in range(5000)]
        with patch('mijntv.artwork.threading.Thread.start'):
            warmer = ArtworkWarmer(self.art)
        self.addCleanup(warmer.close)
        warmer.fill()
        self.assertEqual(warmer.tasks.qsize(), 4096)
