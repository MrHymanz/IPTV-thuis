import tempfile
import unittest
from mijntv.store import Store

PLAYLIST='''#EXTM3U
#EXTINF:-1 tvg-id="npo1.nl" group-title="Algemeen",NPO 1 FHD
https://example.test/primary
#EXTINF:-1 tvg-id="npo1.nl" group-title="4K",NPO1 4K
https://example.test/backup
#EXTINF:-1 tvg-id="npo1.nl" group-title="HD",NPO 1 HD
https://example.test/third
#EXTINF:-1 tvg-id="npo1extra.nl" group-title="4K",NPO 1 Extra
https://example.test/extra
#EXTINF:-1 group-title="Anders",NPO 1
https://example.test/unknown
'''

class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.store=Store(self.directory.name);self.store.import_playlist(PLAYLIST)
        with self.store.connect() as db:
            self.ids={r['name']:r['id'] for r in db.execute('SELECT id,name FROM channels')}
        self.primary=self.ids['NPO 1 FHD'];self.backup=self.ids['NPO1 4K']
        self.store.add_favorite(self.primary,'NPO 1')

    def test_failed_preference_removal_does_not_clear_newer_choice(self):
        self.store.remember_source(self.primary,self.backup)
        self.store.forget_source(self.primary,self.primary)
        self.assertEqual(self.store.favorites(playback=True)[0]['sources'][0]['id'],self.backup)
        self.store.forget_source(self.primary,self.backup)
        self.assertEqual(self.store.favorites(playback=True)[0]['sources'][0]['id'],self.primary)

    def test_startup_merging_uses_channel_id_lookup_after_import(self):
        from unittest.mock import Mock
        probe=Mock()
        probe.execute.return_value.fetchall.return_value=[]
        Store._merge_favorites(probe)
        query=probe.execute.call_args_list[0].args[0]
        with self.store.connect() as db:
            plan=[row[3] for row in db.execute('EXPLAIN QUERY PLAN '+query)]
        self.assertTrue(any('SEARCH c USING INDEX' in step for step in plan), plan)
        self.assertFalse(any('SCAN c' in step for step in plan), plan)

    def test_preference_survives_restart_and_missing_source(self):
        self.store.remember_source(self.primary, self.backup)
        reopened = Store(self.directory.name)
        self.assertEqual(reopened.favorites(playback=True)[0]['sources'][0]['id'], self.backup)
        reopened.import_playlist(PLAYLIST.replace('https://example.test/backup', 'https://example.test/updated'))
        self.assertEqual(reopened.favorites(playback=True)[0]['url'], 'https://example.test/updated')
        with reopened.connect() as db:
            db.execute('DELETE FROM channels WHERE id=?', (self.backup,))
        self.assertEqual(reopened.favorites(playback=True)[0]['sources'][0]['id'], self.primary)
        reopened.remember_source(self.primary, 'unrelated')
        self.assertEqual(reopened.favorites(playback=True)[0]['sources'][0]['id'], self.primary)

    def test_same_epg_id_is_one_favorite_with_private_sources(self):
        self.store.add_favorite(self.backup)
        items=self.store.favorites(playback=True)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['label'],'NPO 1')
        self.assertEqual([s['id'] for s in items[0]['sources']][:2],[self.primary,self.backup])
        self.assertEqual(items[0]['source_count'],3)
        public=self.store.favorites()[0]
        self.assertNotIn('sources',public);self.assertNotIn('url',public)
        self.assertNotIn('https://',str(public))

    def test_other_epg_and_missing_epg_do_not_merge(self):
        self.store.add_favorite(self.ids['NPO 1 Extra'])
        self.store.add_favorite(self.ids['NPO 1'])
        self.assertEqual(len(self.store.favorites()),3)

    def test_migration_keeps_first_label_and_pins_duplicate(self):
        with self.store.connect() as db:
            db.execute('INSERT INTO favorites VALUES (?,?,?)',(self.backup,'Later toegevoegd',99))
        migrated=Store(self.directory.name)
        items=migrated.favorites(playback=True)
        self.assertEqual(len(items),1);self.assertEqual(items[0]['label'],'NPO 1')
        self.assertEqual(items[0]['sources'][1]['id'],self.backup)

    def test_missing_primary_recovers_and_refreshed_urls_are_used(self):
        without_primary=PLAYLIST.replace('#EXTINF:-1 tvg-id="npo1.nl" group-title="Algemeen",NPO 1 FHD\nhttps://example.test/primary\n','')
        self.store.import_playlist(without_primary.replace('/backup','/new-token'))
        item=self.store.favorites(playback=True)[0]
        self.assertEqual(item['id'],self.primary)
        self.assertTrue(item['available'])
        self.assertIn('/new-token',item['url'])
        self.store.import_playlist(PLAYLIST)
        self.assertEqual(self.store.favorites(playback=True)[0]['sources'][0]['id'],self.primary)

    def test_identical_urls_and_same_group_not_counted_twice(self):
        extended=PLAYLIST+'#EXTINF:-1 tvg-id="npo1.nl" group-title="Clone",Clone\nhttps://example.test/backup\n'
        extended+='#EXTINF:-1 tvg-id="npo1.nl" group-title="Algemeen",Same group\nhttps://example.test/samegroup\n'
        self.store.import_playlist(extended)
        self.assertEqual(self.store.favorites()[0]['source_count'],3)

    def test_explicitly_added_same_group_backup_is_retained(self):
        extended=PLAYLIST+'#EXTINF:-1 tvg-id="npo1.nl" group-title="Algemeen",Second feed\nhttps://example.test/second\n'
        self.store.import_playlist(extended)
        with self.store.connect() as db:
            second=db.execute("SELECT id FROM channels WHERE name='Second feed'").fetchone()[0]
        self.store.add_favorite(second)
        item=self.store.favorites(playback=True)[0]
        self.assertEqual(item['sources'][1]['id'],second)
        self.assertEqual(item['source_count'],4)
        self.assertEqual(len(self.store.favorites()),1)
