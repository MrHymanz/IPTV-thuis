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
        from concurrent.futures import Future
        self.tv.player_future=Future();self.tv.player_future.set_result(player)
        with patch('mijntv.tv.Player',return_value=player):
            TV.start_stream(self.tv,self.tv.channels[0])
        player.play.assert_called_once_with('two')
        self.assertEqual(self.tv.tried_sources,{'primary','backup'})
        self.assertEqual(self.tv.selected,0)

    def test_zap_burst_only_loads_final_selection(self):
        self.tv.channels=[{'id':str(i),'available':True,'label':str(i)} for i in range(20)]
        self.tv.menu_open=False;self.tv.active_source_id=None;self.tv.pending_zap=None
        self.tv.root=Mock();callbacks={}
        def after(delay,callback):
            handle=len(callbacks)+1;callbacks[handle]=callback;return handle
        self.tv.root.after.side_effect=after
        self.tv.root.after_cancel.side_effect=lambda handle: callbacks.pop(handle)
        for name in ('error','home','banner','banner_label'):
            setattr(self.tv,name,Mock())
        self.tv.update_programme=Mock();self.tv.show_banner=Mock();self.tv.loading=True
        for index in range(1,20):
            self.tv.choose(index)
        self.tv.start_stream.assert_not_called()
        self.assertFalse(self.tv.loading)
        self.assertEqual(len(callbacks),1)
        next(iter(callbacks.values()))()
        self.tv.start_stream.assert_called_once_with(self.tv.channels[19])

    def test_startup_wait_does_not_block_latest_channel_selection(self):
        from concurrent.futures import Future
        self.tv.start_stream=TV.start_stream.__get__(self.tv,TV)
        self.tv.player=None;self.tv.player_future=Future();self.tv.root=Mock();self.tv.video=Mock()
        self.tv.start_stream({'url':'first'})
        self.tv.root.after.assert_called_once()
        self.tv.player_future.set_result(Mock())
        self.tv.volume=100;self.tv.muted=False;self.tv.tried_sources=set()
        self.tv.start_stream({'id':'latest','url':'last'})
        self.tv.player.play.assert_called_once_with('last')

    def test_saved_software_decoder_is_used_by_new_player(self):
        self.tv.player=None;self.tv.display_settings={'decoder':'software'}
        self.tv.volume=100;self.tv.muted=False;self.tv.root=Mock();self.tv.video=Mock()
        self.tv.tried_sources=set()
        def thread(target,daemon):
            worker=Mock();worker.start.side_effect=target;return worker
        with patch('mijntv.tv.threading.Thread',side_effect=thread), patch('mijntv.tv.Player') as player:
            TV.start_stream(self.tv,{'id':'channel','url':'stream'})
        player.assert_called_once_with(self.tv.video.winfo_id(),hwdec='no')
        player.return_value.play.assert_called_once_with('stream')

    def test_fps_overlay_reports_video_screen_and_drops_and_can_be_hidden(self):
        self.tv.root=Mock();self.tv.fps_label=Mock();self.tv.display_settings={'show_fps':True}
        self.tv.player.snapshot.return_value={'estimated-vf-fps':25.0,'display-fps':60.0,'frame-drop-count':3}
        self.tv.update_fps()
        self.tv.fps_label.configure.assert_called_once_with(text='Video 25.0 fps · TV 60 Hz · drops 3')
        self.tv.fps_label.lift.assert_called_once()
        self.tv.display_settings['show_fps']=False;self.tv.update_fps()
        self.tv.fps_label.place_forget.assert_called_once()

    def test_only_advancing_visible_source_is_remembered(self):
        self.tv.store=Mock(); self.tv.active_source_id='backup'
        self.tv.channels=[{'id':'favorite','sources':[{'id':'primary'},{'id':'backup'}]}]
        base={'pause':False,'core-idle':False,'seeking':False,'paused-for-cache':False,
              'aid':1,'current-ao':'alsa','audio-out-params/samplerate':48000}
        def sample(t, black=False, checked=None):
            with patch('mijntv.tv.time.monotonic',return_value=t):
                self.tv.remember_healthy_source(dict(base, **{'time-pos':t,'video-black':black,
                    'video-checked-at':t if checked is None else checked}))
        sample(0);sample(15)
        self.tv.store.remember_source.assert_not_called()
        sample(30)
        self.tv.store.remember_source.assert_called_once_with('favorite','backup')
        self.assertEqual(self.tv.channels[0]['sources'][0]['id'],'backup')
        self.tv.remembered_source=None
        sample(45,True);sample(60,True);sample(90,True)
        sample(100,checked=0);sample(120,black=None)
        self.assertEqual(self.tv.store.remember_source.call_count,1)
        sample(130);sample(145)
        self.assertEqual(self.tv.store.remember_source.call_count,1)
        sample(160)
        self.assertEqual(self.tv.store.remember_source.call_count,2)

    def test_black_picture_switches_source_and_forgets_preference(self):
        self.tv.store=Mock(); self.tv.channels[0]['id']='favorite'
        self.tv.channels[0]['sources']=[{'id':'black','url':'one'},{'id':'healthy','url':'two'}]
        self.tv.active_source_id='black';self.tv.tried_sources={'black'}
        with patch('mijntv.tv.time.monotonic',return_value=100):
            self.tv.recover_stream('black_picture',{'video-black':True})
        self.assertEqual(self.tv.active_source_id,'healthy')
        self.tv.store.forget_source.assert_called_once_with('favorite','black')
        self.tv.start_stream.assert_called_once_with(self.tv.channels[0])

    def test_black_preference_is_removed_even_during_retry_cooldown(self):
        self.tv.store=Mock();self.tv.channels[0]['id']='favorite';self.tv.active_source_id='black'
        self.tv.recovery_attempts=[100]
        with patch('mijntv.tv.time.monotonic',return_value=110):
            self.tv.recover_stream('black_picture',{})
        self.tv.store.forget_source.assert_called_once_with('favorite','black')
        self.tv.start_stream.assert_not_called()

    def test_home_and_recordings_are_never_restarted(self):
        self.tv.watching=False;self.recover(100)
        self.tv.watching=True;self.tv.playing_recording='recording';self.recover(200)
        self.tv.start_stream.assert_not_called()
