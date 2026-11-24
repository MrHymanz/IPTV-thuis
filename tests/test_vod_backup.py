import json
import unittest
from unittest.mock import patch

from mijntv.vod_backup import BackupMatches, confidence
from tests import test_vod as vod_fixture
from tests import test_mobile as mobile_fixture


class BackupMatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture=vod_fixture.VODTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store,self.primary=self.fixture.store,self.fixture.vod
        self.store.set_setting('backup_source','https://backup.test/get.php?username=other&password=backup-private')
        self.fixture.movies[0].update(name='NL - The Test Film 4K (2026)',year='2026',imdb_id='tt1234567')
        self.fixture.movie_info['info'].update(releasedate='2026-01-01',imdb_id='tt1234567')
        self.fixture.series[0].update(name='The Test Series',year='2026',tmdb_id='1234')
        self.primary.catalog('movie');self.primary.catalog('series');self.primary.detail('series','7')
        self.matches=BackupMatches(self.store,self.primary);self.addCleanup(self.matches.close)
        self.matches.closed.set();self.matches.scheduler.join(1)
        self.movies=[{'stream_id':'900','name':'EN | The Test Film HD (2026)','year':'2026','imdb_id':'tt1234567','category_id':'b','container_extension':'mp4'}]
        self.series=[{'series_id':'901','name':'The Test Series','year':'2026','tmdb_id':'1234','category_id':'b'}]
        self.episodes={'1':[{'id':'5001','season':1,'episode_num':1,'title':'Begin','container_extension':'mkv'},
                            {'id':'5002','season':1,'episode_num':2,'title':'Vervolg'}],
                       '2':[{'id':'6001','season':2,'episode_num':1,'title':'Terugkeer'}]}
        self.patch=patch.object(self.matches.catalog.provider,'request',side_effect=self.response);self.patch.start();self.addCleanup(self.patch.stop)
        self.configuration=self.matches.catalog.configuration()
        self.matches.refresh(self.configuration,'movie');self.matches.refresh(self.configuration,'series')

    def response(self,configuration,action,**params):
        return {'get_vod_categories':[{'category_id':'b','category_name':'EN movies'}],
            'get_series_categories':[{'category_id':'b','category_name':'EN series'}],
            'get_vod_streams':self.movies,'get_series':self.series,
            'get_vod_info':{'info':{'releasedate':'2026-01-01','imdb_id':'tt1234567','duration_secs':6480},'movie_data':{'container_extension':'mp4'}},
            'get_series_info':{'info':{'releaseDate':'2026-01-01','tmdb_id':'1234'},'episodes':self.episodes}}[action]

    def test_separate_catalogues_keep_canonical_ids_and_use_backup_credentials_only_privately(self):
        offers=self.matches.offers('movie','7')
        self.assertEqual(offers['automatic'],'900')
        self.assertNotIn('backup-private',json.dumps(offers));self.assertNotIn('backup.test',json.dumps(offers))
        playback=self.matches.playback('movie','7')
        self.assertIn('/movie/other/backup-private/900.mp4',playback['url'])
        self.assertEqual(self.primary.detail('movie','7')['id'],'7')
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(DISTINCT signature) FROM vod_items WHERE kind='movie'").fetchone()[0],2)
        with self.store.connect() as db:db.execute("DELETE FROM vod_meta WHERE signature=? AND kind='movie'",(self.primary.configuration()[2],))
        self.primary._refresh(self.primary.configuration(),'movie')
        self.assertEqual(self.matches.offers('movie','7')['automatic'],'900')

    def test_series_mapping_uses_season_and_number_instead_of_provider_episode_ids(self):
        self.assertIn('/series/other/backup-private/5001.mkv',self.matches.playback('episode','100')['url'])
        self.assertIn('/series/other/backup-private/6001.mp4',self.matches.playback('episode','202')['url'])
        with self.store.connect() as db:db.execute("DELETE FROM vod_details WHERE signature=? AND kind='series'",(self.configuration[2],))
        self.episodes['2']=[]
        with self.assertRaisesRegex(ValueError,'niet eenduidig'):self.matches.playback('episode','202')

    def test_yearless_or_similar_titles_require_an_explicit_choice(self):
        with self.store.connect() as db:
            data=json.loads(db.execute("SELECT data FROM vod_items WHERE signature=? AND kind='movie' AND id='7'",(self.primary.configuration()[2],)).fetchone()[0])
            data.update(title='The Test Film',year='',imdb_id='')
            db.execute("UPDATE vod_items SET title=?,data=? WHERE signature=? AND kind='movie' AND id='7'",(data['title'],json.dumps(data),self.primary.configuration()[2]))
        offers=self.matches.offers('movie','7');self.assertIsNone(offers['automatic']);self.assertEqual(offers['items'][0]['id'],'900')
        with self.assertRaisesRegex(ValueError,'Kies eerst'):self.matches.playback('movie','7')
        self.assertIn('/900.mp4',self.matches.playback('movie','7','900',offers['backup_signature'])['url'])
        with self.assertRaises(ValueError):self.matches.playback('movie','7','900','old-source')
        with self.assertRaises(ValueError):self.matches.playback('movie','7','999',offers['backup_signature'])

    def test_large_backup_catalogue_can_be_indexed_by_category(self):
        from mijntv.catchup import ProviderRequestError
        original=self.response
        def large(configuration,action,**params):
            if action=='get_vod_streams' and not params.get('category_id'):
                raise ProviderRequestError('too_large','Lijst te groot')
            return original(configuration,action,**params)
        with self.store.connect() as db:
            db.execute("DELETE FROM vod_meta WHERE signature=? AND kind='movie'",(self.configuration[2],))
            db.execute("DELETE FROM backup_vod_sync WHERE signature=? AND kind='movie'",(self.configuration[2],))
        self.matches.closed.clear()
        try:
            with patch.object(self.matches.catalog.provider,'request',side_effect=large):
                self.assertTrue(self.matches.refresh(self.configuration,'movie'))
        finally:self.matches.closed.set()
        self.assertEqual(self.matches.offers('movie','7')['automatic'],'900')
        self.assertEqual(self.primary.detail('movie','7')['id'],'7')

    def test_hidden_primary_titles_and_changed_accounts_cannot_be_matched(self):
        self.store.set_setting('mobile_vod_selection:'+self.primary.configuration()[2],json.dumps({'movie':[],'series':[]}))
        with self.assertRaisesRegex(ValueError,'niet vrijgegeven'):self.matches.offers('movie','7')
        self.store.set_setting('mobile_vod_selection:'+self.primary.configuration()[2],'')
        signature=self.matches.offers('movie','7')['backup_signature']
        self.store.set_setting('backup_source','https://new.test/get.php?username=new&password=another-private')
        with self.assertRaises(ValueError):self.matches.playback('movie','7','900',signature)

    def test_remakes_or_conflicting_ids_never_autoplay(self):
        base={'title':'The Film','year':'1994','imdb_id':'tt1234567'}
        self.assertEqual(confidence(base,dict(base,year='2020'))[0],0)
        self.assertEqual(confidence(base,dict(base,imdb_id='tt9999999'))[0],0)
        self.assertLess(confidence({'title':'The Film'}, {'title':'The Film'})[0],95)


class MobileBackupPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.fixture=mobile_fixture.MobileTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.signature=self.fixture.vod_fixture();self.mobile=self.fixture.mobile
        self.mobile.vod_backups.closed.set();self.mobile.vod_backups.scheduler.join(1)
        with self.fixture.store.connect() as db:
            row=db.execute("SELECT data FROM vod_items WHERE signature=? AND kind='movie' AND id='77'",(self.mobile.vod.configuration()[2],)).fetchone()
            data=json.loads(row[0]);data.update(year='2026')
            db.execute("UPDATE vod_items SET data=? WHERE signature=? AND kind='movie' AND id='77'",(json.dumps(data),self.mobile.vod.configuration()[2]))
        def response(configuration,action,**params):
            return {'get_vod_categories':[{'category_id':'b','category_name':'Films'}],
                'get_vod_streams':[{'stream_id':'999','name':'Testfilm','year':'2026','category_id':'b','container_extension':'mp4'}],
                'get_vod_info':{'info':{'releasedate':'2026-01-01','duration_secs':3600},'movie_data':{'container_extension':'mp4'}}}[action]
        patcher=patch.object(self.mobile.vod_backups.catalog.provider,'request',side_effect=response);patcher.start();self.addCleanup(patcher.stop)
        self.mobile.vod_backups.refresh(self.mobile.vod_backups.catalog.configuration(),'movie')

    def test_busy_tv_uses_backup_and_preserves_requested_title_identity(self):
        tv=self.fixture.tv()
        player=self.mobile.start({'kind':'movie','id':'77','signature':self.signature,'position':123})
        self.assertEqual(player['id'],'77');self.assertEqual(player['position'],123)
        self.assertEqual(player['account_role'],'backup');self.assertFalse(tv.closed)
        session=self.mobile.sessions[player['token']]
        manifest=(__import__('pathlib').Path(session['directory'].name)/'input.ffconcat').read_text()
        self.assertIn('/movie/alternate/backup-secret/999.mp4',manifest)
        self.assertNotIn('backup-secret',json.dumps(player))
        self.assertEqual(session['account'],self.fixture.hub.account_key('backup'))

    def test_free_primary_still_uses_primary_without_a_backup_lookup(self):
        with patch.object(self.mobile.vod_backups,'playback') as match:
            player=self.mobile.start({'kind':'movie','id':'77','signature':self.signature})
        self.assertEqual(player['account_role'],'primary');match.assert_not_called()

    def test_no_backup_connection_is_started_when_both_accounts_are_busy(self):
        self.fixture.tv();self.fixture.hub.reserve_account('other','backup')
        with patch.object(self.mobile.vod_backups,'playback') as match:
            with self.assertRaisesRegex(ValueError,'kijkaccounts zijn bezet'):
                self.mobile.start({'kind':'movie','id':'77','signature':self.signature})
        match.assert_not_called()
