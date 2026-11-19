import json
import unittest
from unittest.mock import Mock

from mijntv.library import Library
from tests import test_vod as vod_fixture


class SharedLibraryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=vod_fixture.VODTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store,self.vod=self.fixture.store,self.fixture.vod
        self.fixture.catalog();self.fixture.catalog('series');self.vod.detail('series','7')
        self.recordings=Mock();self.recordings.list.return_value=[]
        self.library=Library(self.store,self.vod,self.recordings,Mock())
        self.signature=self.vod.configuration()[2][:16]

    def player(self, token='first', kind='movie', key='7', profile='home'):
        player={'token':token,'kind':kind,'id':key}
        self.library.bind(player,profile)
        return player

    def progress(self, token='first', sequence=1, position=120, duration=6480, **extra):
        return self.library.progress(dict(token=token,sequence=sequence,position=position,duration=duration,**extra))

    def test_watchlist_and_progress_survive_a_new_service_instance(self):
        self.library.save({'signature':self.signature,'kind':'movie','id':'7','saved':True})
        self.player();self.progress()
        restarted=Library(self.store,self.vod,self.recordings,Mock())
        state=restarted.snapshot()
        self.assertEqual(state['saved'][0]['id'],'7')
        self.assertEqual(state['history'][0]['position'],120)
        self.assertNotIn('private-cover.test',json.dumps(state))
        self.assertEqual(state['profile']['name'],'Thuis')

    def test_old_requests_cannot_overwrite_newer_playback_or_sequence(self):
        self.player();self.progress(sequence=2,position=500)
        self.assertTrue(self.progress(sequence=1,position=100)['ignored'])
        self.player('second')
        self.assertTrue(self.progress(sequence=3,position=600)['ignored'])
        self.progress('second',position=800)
        self.assertEqual(self.library.snapshot()['history'][0]['position'],800)

    def test_new_session_wins_even_before_either_session_has_reported_progress(self):
        self.player('old');self.player('new')
        self.assertTrue(self.progress('old',position=700)['ignored'])
        self.progress('new',position=200)
        self.assertEqual(self.library.snapshot()['history'][0]['position'],200)

    def test_migration_never_restores_deleted_saved_items_or_overwrites_progress(self):
        self.library.save({'signature':self.signature,'kind':'movie','id':'7','saved':False})
        self.player();self.progress(position=500)
        data={'signature':self.signature,'client':'browser-old','saved':[{'kind':'movie','id':'7'}],
              'progress':[{'kind':'movie','id':'7','position':100,'duration':6480}]}
        state=self.library.import_local(data)
        self.assertEqual(state['saved'],[])
        self.assertEqual(state['history'][0]['position'],500)
        self.assertEqual(self.library.import_local(data)['history'][0]['position'],500)

    def test_profiles_have_independent_library_progress_and_defaults(self):
        person=self.library.add_profile('Later persoon')
        self.player(profile=person['id']);self.progress()
        self.assertEqual(self.library.snapshot()['history'],[])
        self.assertEqual(len(self.library.snapshot(person['id'])['history']),1)
        self.assertTrue(person['preferences']['autoplay_next'])
        self.assertEqual(person['preferences']['subtitle_language'],'nl')
        self.assertEqual(person['preferences']['audio_language'],'original')

    def test_next_episode_crosses_seasons_and_completed_series_suggests_next(self):
        self.assertEqual(self.library.next_episode('100')['episode']['id'],'101')
        self.assertEqual(self.library.next_episode('101')['episode']['id'],'202')
        self.assertIsNone(self.library.next_episode('202'))
        player=self.player(kind='episode',key='100')
        self.assertEqual(player['next_episode']['episode']['id'],'101')
        self.progress(position=2520,duration=2520,ended=True)
        state=self.library.snapshot()
        self.assertEqual(state['history'][0]['episode']['id'],'101')
        self.assertTrue(state['history'][0]['next_episode'])
        self.assertTrue(state['positions'][self.signature+'-episode-100']['watched'])

    def test_hidden_titles_are_omitted_and_profile_changes_reject_stale_scope(self):
        self.player();self.progress()
        self.store.set_setting('mobile_vod_selection:'+self.vod.configuration()[2],json.dumps({'movie':[],'series':[]}))
        self.assertEqual(self.library.snapshot()['history'],[])
        self.store.set_setting('source','https://new.test/get.php?username=other&password=other')
        self.assertTrue(self.progress(sequence=2)['ignored'])

    def test_invalid_inputs_and_unknown_profiles_are_rejected(self):
        with self.assertRaises(ValueError):self.library.profile('missing')
        self.player()
        for invalid in (float('nan'),float('inf'),-1,True):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):self.progress(position=invalid)
        with self.assertRaises(ValueError):self.library.preferences('home',{'autoplay_next':'yes'})

    def dismiss(self, kind='movie', key='7', **extra):
        return self.library.remove_continue(dict(signature=self.signature,kind=kind,id=key,**extra))

    def test_dismiss_movie_preserves_resume_watchlist_and_survives_restart(self):
        self.library.save({'signature':self.signature,'kind':'movie','id':'7','saved':True})
        self.player();self.progress(position=500)
        state=self.dismiss()
        self.assertEqual(state['history'],[])
        self.assertEqual(state['positions'][self.signature+'-movie-7']['position'],500)
        self.assertEqual(len(state['saved']),1)
        self.progress(sequence=2,position=600)
        restarted=Library(self.store,self.vod,self.recordings,Mock())
        self.assertEqual(restarted.snapshot()['history'],[])
        self.player('new');self.progress('new',position=700)
        self.assertEqual(self.library.snapshot()['history'][0]['position'],700)

    def test_dismiss_series_hides_next_episode_and_older_episodes(self):
        self.player('older',kind='episode',key='100');self.progress('older')
        self.player('latest',kind='episode',key='101');self.progress('latest',position=2520,duration=2520,ended=True)
        self.assertTrue(self.library.snapshot()['history'][0]['next_episode'])
        self.assertEqual(self.dismiss('series')['history'],[])
        self.assertTrue(self.library.snapshot()['positions'][self.signature+'-episode-101']['watched'])
        self.player('new',kind='episode',key='202');self.progress('new')
        self.assertEqual(self.library.snapshot()['history'][0]['episode']['id'],'202')

    def test_dismiss_is_profile_scoped_and_old_import_cannot_resurrect(self):
        person=self.library.add_profile('Ander')
        self.player('other',profile=person['id']);self.progress('other')
        self.dismiss()
        state=self.library.import_local({'signature':self.signature,'client':'old-browser',
            'progress':[{'kind':'movie','id':'7','position':500,'duration':6480}]})
        self.assertEqual(state['history'],[])
        self.assertEqual(len(self.library.snapshot(person['id'])['history']),1)

    def test_dismiss_rejects_invalid_scope_without_mutating_history(self):
        self.player();self.progress()
        for data in ({'kind':'episode','id':'100','signature':self.signature},
                     {'kind':'movie','id':'7','signature':'wrong'},
                     {'kind':'movie','id':'7','signature':self.signature,'profile':'missing'}):
            with self.subTest(data=data),self.assertRaises(ValueError):self.library.remove_continue(data)
        self.assertEqual(len(self.library.snapshot()['history']),1)
