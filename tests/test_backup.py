import io
import tempfile
import unittest
from unittest.mock import patch

from mijntv.backup import BackupAccount, channel_key
from mijntv.store import Store


def playlist(*rows):
    return '#EXTM3U\n' + ''.join(
        f'#EXTINF:-1 tvg-id="{epg}" group-title="{group}",{name}\nhttps://media.test/{i}.ts\n'
        for i, (name, epg, group) in enumerate(rows))


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.import_playlist(playlist(
            ('NL| NPO 1 HD', 'NPO1.nl', 'NL| TV'),
            ('NL| NJAM! FHD', '', 'NL| TV'),
            ('NL| Unmatched', 'missing.nl', 'NL| TV')),
            'https://first.test/get.php?username=one&password=secret')
        for c in self.store.catalog()['items']:
            self.store.add_favorite(c['id'])
        self.backup = BackupAccount(self.store)

    def save(self, text, source='https://second.test/get.php?username=two&password=private'):
        with patch('mijntv.backup.live_playlist', return_value=io.StringIO(text.replace('https://media.test/', 'https://backup-media.test/'))):
            return self.backup.save(source)

    def test_match_epg_then_country_and_name_preserving_favorites(self):
        before = [(f['id'], f['label'], f['position']) for f in self.store.favorites()]
        status = self.save(playlist(
            ('NPO Een', 'npo1.NL', 'NL| PUBLIC'),
            ('NL| NJAM! HD', '', 'NL| FOOD'),
            ('BE| NJAM! HD', '', 'BE| FOOD')))
        self.assertEqual(status['matched'], 2)
        self.assertEqual(before, [(f['id'], f['label'], f['position']) for f in self.store.favorites()])
        items = {f['name']: f for f in self.store.favorites(playback=True)}
        self.assertEqual(items['NL| NPO 1 HD']['sources'][-1]['name'], 'NPO Een')
        self.assertEqual(items['NL| NJAM! FHD']['sources'][-1]['name'], 'NL| NJAM! HD')
        self.assertEqual(items['NL| Unmatched']['source_count'], 1)
        self.assertNotIn('private', str(status))
        self.assertNotIn('media.test', str(status))
        self.assertNotIn('private', str(self.store.favorites()))

    def test_promotion_is_global_persistent_and_can_fail_back(self):
        self.save(playlist(('NPO One', 'NPO1.nl', 'NL| TV'), ('NL| NJAM! HD', '', 'NL| TV')))
        first = next(f for f in self.store.favorites(playback=True) if f['epg_id'] == 'NPO1.nl')
        backup = next(s for s in first['sources'] if s['account'] == 'backup')
        self.assertEqual(first['sources'][0]['account'], 'primary')
        self.store.remember_source(first['id'], backup['id'])
        reloaded = Store(self.temp.name)
        for f in reloaded.favorites(playback=True):
            if any(s['account'] == 'backup' for s in f['sources']):
                self.assertEqual(f['sources'][0]['account'], 'backup')
        self.assertEqual(self.backup.status()['preferred'], 'backup')
        primary = next(s for s in first['sources'] if s['account'] == 'primary')
        self.store.remember_source(first['id'], primary['id'])
        self.assertEqual(self.backup.status()['preferred'], 'primary')

    def test_invalid_preference_cannot_promote(self):
        self.save(playlist(('NPO One', 'NPO1.nl', 'NL| TV')))
        self.store.remember_source(self.store.favorites()[0]['id'], 'backup:unrelated')
        self.assertEqual(self.backup.status()['preferred'], 'primary')

    def test_failed_import_preserves_old_backup_and_primary(self):
        self.save(playlist(('NPO One', 'NPO1.nl', 'NL| TV')))
        before = self.store.favorites(playback=True)
        with self.assertRaises(ValueError):
            self.save('not a playlist')
        self.assertEqual(self.store.favorites(playback=True), before)
        self.assertEqual(self.store.setting('source'), 'https://first.test/get.php?username=one&password=secret')

    def test_disabled_xtream_api_uses_m3u_catalog(self):
        with patch('mijntv.backup.live_playlist', side_effect=ValueError('API geweigerd')), patch(
                'mijntv.backup.fetch_playlist', return_value=io.StringIO(
                    playlist(('NL| NPO 1 HD', 'NPO1.nl', 'NL| TV')))) as fallback:
            status = self.backup.save('http://second.test/get.php?username=two&password=private')
        self.assertEqual(status['matched'], 1)
        fallback.assert_called_once()

    def test_live_api_skips_full_playlist_download(self):
        with patch('mijntv.backup.live_playlist', return_value=io.StringIO(
                playlist(('NL| NPO 1 HD', 'NPO1.nl', 'NL| TV')))), patch(
                'mijntv.backup.fetch_playlist') as download:
            self.backup.save('http://second.test/get.php?username=two&password=private')
        download.assert_not_called()

    def test_refresh_rejects_loss_of_existing_matches(self):
        self.save(playlist(('NPO One', 'NPO1.nl', 'NL| TV'), ('NL| NJAM! HD', '', 'NL| TV')))
        before = self.store.favorites(playback=True)
        with patch('mijntv.backup.live_playlist', return_value=io.StringIO(playlist(
                ('Unrelated Movie', '', 'Movies')))):
            with self.assertRaisesRegex(ValueError, 'mist te veel'):
                self.backup.refresh()
        self.assertEqual(self.store.favorites(playback=True), before)
        with self.store.connect() as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'backup_import_%'").fetchone())

    def test_removing_backup_keeps_favorites_and_clears_preference(self):
        self.save(playlist(('NPO One', 'NPO1.nl', 'NL| TV')))
        f = next(f for f in self.store.favorites(playback=True) if f['epg_id'] == 'NPO1.nl')
        self.store.remember_source(f['id'], f['sources'][-1]['id'])
        self.backup.save('')
        self.assertFalse(self.backup.status()['configured'])
        self.assertEqual(len(self.store.favorites()), 3)
        self.assertTrue(all(s['account'] == 'primary' for f in self.store.favorites(playback=True) for s in f['sources']))
        self.assertEqual(self.store.setting('preferred_account'), '')

    def test_new_favorites_match_without_downloading_again(self):
        self.save(playlist(('NL| Food Network HD', 'food.uk', 'NL| TV')))
        self.store.manual('Food Network', 'https://manual.test/stream')
        # A manual favorite has no country: do not guess an NL channel.
        self.assertFalse(self.backup.status()['matches'][-1]['matched'])
        self.store.import_playlist(playlist(('NL| Food Network FHD', '', 'NL| TV')))
        self.store.add_favorite(next(c['id'] for c in self.store.catalog()['items'] if c['name'].startswith('NL| Food')))
        self.assertTrue(self.backup.status()['matches'][-1]['matched'])

    def test_ambiguous_name_is_not_guessed(self):
        status = self.save(playlist(('NL| NJAM! HD', 'schedule1', 'NL| A'),
                                    ('NL| NJAM! FHD', 'schedule2', 'NL| B')))
        njam = next(m for m in status['matches'] if 'NJAM' in m['label'])
        self.assertFalse(njam['matched'])

    def test_reject_same_primary_or_recording_account(self):
        for source in ('https://first.test/get.php?username=one&password=new',
                       'https://record.test/get.php?username=three&password=private'):
            self.store.set_setting('recording_source', 'https://record.test/get.php?username=three&password=old')
            with self.assertRaises(ValueError):
                self.backup.save(source)

    def test_quality_decoration_normalizes_without_merging_numbered_channels(self):
        self.assertEqual(channel_key('NL| NPO 1 ᴴᴰ ᴿᴬᵂ', ''), ('NL', 'NPO1'))
        self.assertNotEqual(channel_key('NL| NPO 1 HD'), channel_key('NL| NPO 2 HD'))
        self.assertEqual(channel_key('NL: NPO 1 ᴴᴰ ◉'), channel_key('NL| NPO 1 HD'))
