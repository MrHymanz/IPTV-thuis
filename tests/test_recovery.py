import importlib
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch
try:
    from mijntv.tv import TV
except ModuleNotFoundError as error:
    if error.name != 'tkinter': raise
    with patch.dict(sys.modules, {'tkinter':ModuleType('tkinter')}):
        TV=importlib.import_module('mijntv.tv').TV

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tv=TV.__new__(TV)
        self.tv.watching=True;self.tv.playing_recording=None
        self.tv.channels=[{'url':'https://private.example/secret'}];self.tv.selected=0
        self.tv.recovery_attempts=[];self.tv.recovery_pending=None
        self.tv.player=Mock();self.tv.show_error=Mock();self.tv.start_stream=Mock()

    def recover(self, now):
        with patch('mijntv.tv.time.monotonic',return_value=now):
            self.tv.recover_stream('stalled',{'time-pos':0})

    def test_restart_uses_same_channel_and_closes_old_player(self):
        player=self.tv.player
        self.recover(100)
        player.close.assert_called_once()
        self.tv.start_stream.assert_called_once_with(self.tv.channels[0])
        self.assertEqual(self.tv.recovery_attempts,[100])

    def test_retry_cooldown_and_budget(self):
        self.recover(100);self.recover(110)
        self.assertEqual(self.tv.start_stream.call_count,1)
        self.assertIsNotNone(self.tv.recovery_pending)
        self.recover(160);self.recover(220);self.recover(280)
        self.assertEqual(self.tv.start_stream.call_count,3)
        self.recover(900)
        self.assertEqual(self.tv.start_stream.call_count,4)

    def test_fallback_advances_without_changing_favorite(self):
        self.tv.channels[0]['sources']=[{'id':'primary','url':'one'},
                                        {'id':'backup','url':'two','group_name':'4K'},
                                        {'id':'third','url':'three','group_name':'HD'}]
        self.tv.active_source_id='primary';self.tv.tried_sources={'primary'}
        self.recover(100)
        self.assertEqual(self.tv.active_source_id,'backup')
        self.assertEqual(self.tv.selected,0)
        self.tv.tried_sources.add('backup')
        self.recover(160)
        self.assertEqual(self.tv.active_source_id,'third')

    def test_actual_start_uses_selected_backup_url(self):
        self.tv.channels[0]['sources']=[{'id':'primary','url':'one'}, {'id':'backup','url':'two'}]
        self.tv.active_source_id='backup';self.tv.tried_sources={'primary'}
        self.tv.volume=100;self.tv.muted=False;self.tv.root=Mock();self.tv.video=Mock()
        self.tv.player=None
        player=Mock()
        with patch('mijntv.tv.Player',return_value=player):
            TV.start_stream(self.tv,self.tv.channels[0])
        player.play.assert_called_once_with('two')
        self.assertEqual(self.tv.tried_sources,{'primary','backup'})
        self.assertEqual(self.tv.selected,0)

    def test_only_advancing_healthy_source_is_remembered(self):
        self.tv.store=Mock(); self.tv.active_source_id='backup'
        self.tv.channels=[{'id':'favorite','sources':[{'id':'primary'},{'id':'backup'}]}]
        sample={'time-pos':10,'pause':False,'core-idle':False,'seeking':False,
                'paused-for-cache':False,'aid':1,'current-ao':'alsa','audio-out-params/samplerate':48000}
        self.tv.remember_healthy_source(sample)
        self.tv.remember_healthy_source(sample)
        self.tv.store.remember_source.assert_not_called()
        self.tv.remember_healthy_source(dict(sample, **{'time-pos':25}))
        self.tv.store.remember_source.assert_called_once_with('favorite','backup')
        self.assertEqual(self.tv.channels[0]['sources'][0]['id'],'backup')
        self.tv.remembered_source=None
        self.tv.remember_healthy_source(dict(sample, **{'time-pos':40,'current-ao':None}))
        self.tv.remember_healthy_source(dict(sample, **{'time-pos':55}))
        self.assertEqual(self.tv.store.remember_source.call_count,1)

    def test_home_and_recordings_are_never_restarted(self):
        self.tv.watching=False;self.recover(100)
        self.tv.watching=True;self.tv.playing_recording='recording';self.recover(200)
        self.tv.start_stream.assert_not_called()
