import importlib
import sys
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import Mock,patch
try:
    from mijntv.tv import TV
except ModuleNotFoundError as error:
    if error.name!='tkinter': raise
    with patch.dict(sys.modules,{'tkinter':ModuleType('tkinter')}):
        TV=importlib.import_module('mijntv.tv').TV


class RecordingGuardTests(unittest.TestCase):
    def setUp(self):
        self.tv=TV.__new__(TV)
        self.tv.menu_open=False
        self.tv.channels=[{'id':'one','label':'NPO 1','available':True},
                          {'id':'two','label':'NPO 2','available':True}]
        self.tv.selected=0
        self.tv.recordings=Mock()
        self.required={'id':'one','label':'NPO 1'}
        self.tv.recordings.required_channel.return_value=self.required
        self.tv.recordings.active='recording-id'
        self.tv.recording_zap_dialog=Mock();self.tv.recording_zap_text=Mock()
        self.tv.recording_zap_target=None;self.tv.recording_zap_choice=0;self.tv.recording_zap_stopping=False
        self.tv.root=Mock();self.tv.render_recording_zap=Mock()

    def test_zap_defaults_to_stay_without_stopping_or_switching(self):
        self.tv.choose(1)
        self.assertEqual(self.tv.selected,0)
        self.assertEqual(self.tv.recording_zap_target,'two')
        self.assertEqual(self.tv.recording_zap_choice,0)
        self.tv.recordings.cancel.assert_not_called()
        self.tv.key(SimpleNamespace(keysym='Return'))
        self.assertIsNone(self.tv.recording_zap_target)
        self.tv.recordings.cancel.assert_not_called()

    def test_explicit_confirmation_waits_for_recorder_before_switching(self):
        self.tv.choose(1)
        self.tv.choose=Mock()
        self.tv.confirm_recording_zap(1)
        self.tv.recordings.cancel.assert_called_once_with('recording-id')
        self.tv.choose.assert_not_called()
        self.tv.root.after.assert_called_once_with(100,self.tv.finish_recording_zap)
        self.tv.recordings.required_channel.return_value=None
        self.tv.finish_recording_zap()
        self.tv.choose.assert_called_once_with(1)

    def test_recording_prevents_automatic_source_switch(self):
        self.tv.watching=True;self.tv.playing_recording=None
        self.tv.show_error=Mock();self.tv.start_stream=Mock()
        self.tv.recover_stream('black',{})
        self.tv.start_stream.assert_not_called()
        self.tv.recordings.cancel.assert_not_called()
        self.assertTrue(self.tv.recording_recovery_needed)
