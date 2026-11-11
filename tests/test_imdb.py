import json
import tempfile
import unittest
from unittest.mock import patch

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
        self.assertIsNone(ranker(json.dumps({'title': 'First & Last', 'year': '2020'})))
        self.assertIsNone(ranker(json.dumps({'title': 'Niet in de lijst', 'rating': '10'})))

    def test_blocked_chart_never_replaces_previous_verified_list(self):
        self.chart.save([{'id': 'tt0000001', 'rank': 1}])
        self.chart.store.set_setting('imdb_top250_at', '0')
        with patch('mijntv.imdb.urlopen', side_effect=OSError('Blocked')) as request:
            self.chart.refresh()
            self.chart.refresh()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(self.chart.entries()), 1)
        self.assertIn('officiële', self.chart.notice)

    def test_rank_import_rejects_duplicates_out_of_range_and_bad_ids(self):
        for value in ([], [{'id': '../bad', 'rank': 1}], [{'id': 'tt0000001', 'rank': 251}],
                      [{'id': 'tt0000001', 'rank': 1}, {'id': 'tt0000001', 'rank': 2}]):
            with self.assertRaises(ValueError):
                validate(value)
