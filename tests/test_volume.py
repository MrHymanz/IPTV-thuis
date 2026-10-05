import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from mijntv.store import Store
try:
    from mijntv.tv import TV
except ImportError:
    TV = None

@unittest.skipUnless(TV, 'tkinter nodig')
class VolumeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tv = TV.__new__(TV)
        self.tv.store = Store(self.directory.name)
        self.tv.volume = 95
        self.tv.muted = False
        self.tv.player = Mock()
        self.tv.root = Mock()
        self.tv.volume_label = Mock()
        self.tv.volume_timer = None

    def test_remote_keys_limits_and_saved_volume(self):
        for _ in range(3):
            self.assertEqual(self.tv.key(SimpleNamespace(keysym='XF86AudioRaiseVolume')), 'break')
        self.assertEqual(self.tv.volume, 100)
        for _ in range(21):
            self.tv.key(SimpleNamespace(keysym='XF86AudioLowerVolume'))
        self.assertEqual(self.tv.volume, 0)
        self.assertEqual(self.tv.store.setting('volume'), '0')
        self.tv.player.command.assert_any_call('set_property', 'volume', 0)
        self.tv.volume_label.configure.assert_called_with(text='Volume 0%')
        self.tv.root.after_cancel.assert_called()

    def test_mute_then_volume_restores_sound_without_player(self):
        self.tv.player = None
        self.tv.key(SimpleNamespace(keysym='XF86AudioMute'))
        self.assertTrue(self.tv.muted)
        self.tv.volume_label.configure.assert_called_with(text='Geluid uit')
        self.tv.key(SimpleNamespace(keysym='XF86AudioLowerVolume'))
        self.assertFalse(self.tv.muted)
        self.assertEqual(self.tv.volume, 90)
        self.tv.hide_volume()
        self.tv.volume_label.place_forget.assert_called_once()

if __name__ == '__main__':
    unittest.main()
