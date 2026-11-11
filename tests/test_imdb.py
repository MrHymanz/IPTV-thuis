import json
import io
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from mijntv.imdb import IMDbChart, parse_chart, validate
from mijntv.store import Store


class IMDbTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.chart = IMDbChart(Store(self.directory.name))

    def test_official_structured_chart_keeps_rank_and_title_identity(self):
        chart = {'@type': 'ItemList', 'itemListElement': [
            {'position': 2, 'item': {'url': 'https://www.imdb.com/title/tt0000002/', 'name': 'Second', 'datePublished': '2000-01-01'}},
            {'position': 1, 'item': {'url': 'https://www.imdb.com/title/tt0000001/', 'name': 'First &amp; Last', 'datePublished': '1994-01-01'}}]}
        entries = parse_chart('<script type="application/ld+json">'+json.dumps(chart)+'</script>')
        self.assertEqual([e['rank'] for e in entries], [1, 2])
        self.assertEqual(entries[0]['title'], 'First & Last')
        self.chart.save(entries)
        ranker = self.chart.ranker()
        self.assertEqual(ranker(json.dumps({'title': 'Andere titel', 'imdb_id': 'tt0000001'})), 1)
        self.assertEqual(ranker(json.dumps({'title': 'NL| First & Last (1994) HD', 'year': '1994'})), 1)
        self.assertEqual(ranker(json.dumps({'title': 'NL - FIRST & LAST 4K (1994)'})), 1)
        self.assertIsNone(ranker(json.dumps({'title': 'First & Last', 'year': '2020'})))
        self.assertIsNone(ranker(json.dumps({'title': 'Niet in de lijst', 'rating': '10'})))

    def test_blocked_chart_never_replaces_previous_verified_list(self):
        self.chart.save([{'id': 'tt0000001', 'rank': 1}])
        self.chart.store.set_setting('imdb_top250_at', '0')
        with patch('mijntv.imdb.urlopen', side_effect=OSError('Blocked')) as request:
            self.chart.refresh()
            self.chart.refresh()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(self.chart.entries()), 1)
        self.assertIn('officiële', self.chart.notice)

    def test_rank_import_rejects_duplicates_out_of_range_and_bad_ids(self):
        for value in ([], [{'id': '../bad', 'rank': 1}], [{'id': 'tt0000001', 'rank': 251}],
                      [{'id': 'tt0000001', 'rank': 1}, {'id': 'tt0000001', 'rank': 2}]):
            with self.assertRaises(ValueError):
                validate(value)

    def api_payload(self, stale=False):
        return {'data': [{'id': 'tt'+str(1000000+i), 'rank': i, 'title': 'Film '+str(i), 'year': 2000} for i in range(1, 251)],
                'stale': stale, 'updatedAt': '2026-09-01T00:00:00Z' if stale else datetime.now(timezone.utc).isoformat()}

    def test_fresh_api_chart_is_used_without_requesting_blocked_imdb_html(self):
        payload = self.api_payload()
        with patch('mijntv.imdb.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())) as request:
            self.chart.refresh()
        self.assertEqual(len(self.chart.entries()), 250)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0].full_url, 'https://imdb-top250.mmdju.workers.dev/top250')
        self.assertFalse(self.chart.metadata()['stale'])

    def test_stale_seed_with_missing_ids_is_usable_but_requires_title_and_year(self):
        payload = self.api_payload(stale=True)
        payload['data'][0]['id'] = None
        with patch('mijntv.imdb.urlopen', side_effect=[io.BytesIO(json.dumps(payload).encode()), OSError('Challenge')]):
            self.chart.refresh()
        self.assertEqual(len(self.chart.entries()), 250)
        self.assertTrue(self.chart.metadata()['stale'])
        self.assertIn('2026-09-01', self.chart.notice)
        ranker = self.chart.ranker()
        self.assertIsNone(ranker(json.dumps({'title': 'Unrelated', 'imdb_id': ''})))
        self.assertIsNone(ranker(json.dumps({'title': 'Film 1'})))
        self.assertIsNone(ranker(json.dumps({'title': 'Film 1', 'year': '2020'})))
        self.assertEqual(ranker(json.dumps({'title': 'Film 1', 'year': '2000'})), 1)
        self.assertEqual(ranker(json.dumps({'title': 'Film 1 (2000) HD'})), 1)
        restarted = IMDbChart(self.chart.store)
        restarted.refresh()
        self.assertIn('oudere momentopname', restarted.notice)

    def test_older_seed_does_not_overwrite_a_manually_managed_chart(self):
        self.chart.save([{'id': 'tt0000001', 'rank': 1}])
        self.chart.store.set_setting('imdb_top250_at', '0')
        payload = self.api_payload(stale=True)
        with patch('mijntv.imdb.urlopen', side_effect=[io.BytesIO(json.dumps(payload).encode()), OSError('Challenge')]):
            self.chart.refresh()
        self.assertEqual(self.chart.entries(), [{'id': 'tt0000001', 'rank': 1, 'title': '', 'year': ''}])
        self.assertIn('bestaande ranglijst', self.chart.notice)

    def test_incomplete_or_duplicate_api_charts_are_not_cached(self):
        for kind in ('incomplete', 'duplicate'):
            payload = self.api_payload()
            if kind == 'incomplete':
                payload['data'].pop()
            else:
                payload['data'][1]['id'] = payload['data'][0]['id']
            self.chart.retry_at = 0
            with patch('mijntv.imdb.urlopen', side_effect=[io.BytesIO(json.dumps(payload).encode()), OSError('Challenge')]):
                self.chart.refresh()
            self.assertEqual(self.chart.entries(), [])
