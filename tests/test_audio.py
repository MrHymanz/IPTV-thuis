import subprocess
import unittest
from unittest.mock import patch
from mijntv.audio import audio_device

class AudioTests(unittest.TestCase):
    @patch.dict('os.environ', {'IPTV_AUDIO_DEVICE':'auto'})
    @patch('mijntv.audio.subprocess.run')
    def test_selects_monitor_instead_of_hardcoded_connector(self, run):
        run.return_value.stdout='''card 0: HDMI [HDA Intel HDMI], device 3: HDMI 0 [LG TV]
card 0: HDMI [HDA Intel HDMI], device 7: HDMI 1 [HDMI 1]
card 0: HDMI [HDA Intel HDMI], device 8: HDMI 2 [HDMI 2]
'''
        self.assertEqual(audio_device(),'alsa/hdmi:CARD=HDMI,DEV=0')
        run.return_value.stdout='''card 0: HDMI [HDA Intel HDMI], device 3: HDMI 0 [HDMI 0]
card 0: HDMI [HDA Intel HDMI], device 8: HDMI 2 [Samsung TV]
'''
        self.assertEqual(audio_device(),'alsa/hdmi:CARD=HDMI,DEV=2')

    @patch.dict('os.environ', {'IPTV_AUDIO_DEVICE':'alsa/hdmi:CARD=HDMI,DEV=1'})
    @patch('mijntv.audio.subprocess.run')
    def test_manual_override(self, run):
        self.assertEqual(audio_device(),'alsa/hdmi:CARD=HDMI,DEV=1')
        run.assert_not_called()

    @patch.dict('os.environ', {'IPTV_AUDIO_DEVICE':'auto'})
    @patch('mijntv.audio.subprocess.run')
    def test_unconnected_or_missing_alsa(self, run):
        run.return_value.stdout='card 0: HDMI [HDA Intel HDMI], device 3: HDMI 0 [HDMI 0]\n'
        self.assertEqual(audio_device(),'')
        run.side_effect=FileNotFoundError()
        self.assertEqual(audio_device(),'')
